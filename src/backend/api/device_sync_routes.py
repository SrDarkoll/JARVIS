"""Profile-scoped routes for pairing and mobile JARVIS synchronization."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import Future
from typing import Any
from urllib.parse import urlsplit

from core import jarvis_brain
from core.device_sync.sync_hub import sync_hub
from core.device_sync.sync_models import DeviceType, SyncEventType
from core.jarvis_observability import obs_event
from core.runtime_logger import log_error, log_warning
from core.security.browser_profile import can_manage_browser_profiles, get_browser_profile_id
from quart import Blueprint, g, jsonify, request, websocket

sync_bp = Blueprint("sync", __name__)

MAX_COMMAND_CHARS = 4000
MAX_DEVICE_NAME_CHARS = 64
MAX_HISTORY_PAGE = 100
MAX_WS_MESSAGE_BYTES = 8192
REMOTE_COMMANDS_PER_MINUTE = 20
REMOTE_DISPATCH_CACHE_SIZE = 512
REMOTE_DISPATCH_CACHE_TTL_SECONDS = 600.0
_remote_dispatch_lock = threading.RLock()
_remote_dispatch_records: OrderedDict[str, tuple[str, Future, float]] = OrderedDict()
_remote_rate_lock = threading.Lock()
_remote_rate_events: dict[str, list[float]] = {}


def _remote_base_url() -> str:
    raw = str(os.getenv("JARVIS_REMOTE_BASE_URL") or "").strip()
    try:
        parsed = urlsplit(raw)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            return ""
        return f"https://{parsed.netloc}".rstrip("/")
    except ValueError:
        return ""


def _origin_matches_remote_base() -> bool:
    base = _remote_base_url()
    candidate = str(request.headers.get("Origin") or request.headers.get("Referer") or "").strip()
    if not base or not candidate:
        return False
    try:
        supplied = urlsplit(candidate)
        expected = urlsplit(base)
        return (
            supplied.scheme.lower() == "https"
            and supplied.hostname is not None
            and expected.hostname is not None
            and supplied.hostname.lower() == expected.hostname.lower()
            and (supplied.port or 443) == (expected.port or 443)
        )
    except ValueError:
        return False


def _json_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_device_token() -> str:
    """Device tokens are accepted only in the dedicated HTTP header."""
    return str(request.headers.get("X-JARVIS-DEVICE-TOKEN") or "").strip()


def _request_profile_id() -> str | None:
    token = _extract_device_token()
    if token:
        device = sync_hub.validate_device_token(token)
        return device.profile_id if device else None
    return get_browser_profile_id() or "admin"


def _pair_qr_data_url(url: str) -> str:
    # Keep the QR dependency lazy so core mode can boot without it.
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M
    from qrcode.image.svg import SvgPathImage

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=5,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    svg = qr.make_image(image_factory=SvgPathImage).to_string(encoding="utf-8")
    svg_bytes = svg if isinstance(svg, bytes) else str(svg).encode("utf-8")
    return "data:image/svg+xml;base64," + base64.b64encode(svg_bytes).decode("ascii")


def _plain_display_history(profile_id: str) -> list[dict[str, str]]:
    from langchain_core.messages import AIMessage, HumanMessage
    from services.memory_manager import memory_manager

    snapshot = memory_manager.snapshot(profile_id)
    messages: list[dict[str, str]] = []
    for index, item in enumerate(snapshot.history[-60:]):
        if isinstance(item, HumanMessage):
            role = "user"
        elif isinstance(item, AIMessage):
            role = "assistant"
        else:
            continue
        if isinstance(item.content, str) and item.content.strip():
            messages.append(
                {"id": f"snapshot-{index}", "role": role, "content": item.content[:4000]}
            )
    return messages


def _remote_dispatch_rate_limited(device_id: str) -> bool:
    now = time.monotonic()
    cutoff = now - 60.0
    with _remote_rate_lock:
        bucket = [tick for tick in _remote_rate_events.get(device_id, ()) if tick > cutoff]
        if len(bucket) >= REMOTE_COMMANDS_PER_MINUTE:
            _remote_rate_events[device_id] = bucket
            return True
        bucket.append(now)
        _remote_rate_events[device_id] = bucket
        if len(_remote_rate_events) > 2048:
            for key, events in list(_remote_rate_events.items()):
                if not events or events[-1] <= cutoff:
                    _remote_rate_events.pop(key, None)
        return False


def _run_remote_dispatch_once(key: str, signature: str, operation):
    now = time.monotonic()
    owner = False
    with _remote_dispatch_lock:
        for record_key, (_signature, future, created_at) in list(_remote_dispatch_records.items()):
            if now - created_at > REMOTE_DISPATCH_CACHE_TTL_SECONDS and future.done():
                _remote_dispatch_records.pop(record_key, None)
        existing = _remote_dispatch_records.get(key)
        if existing is not None:
            existing_signature, future, _created_at = existing
            if existing_signature != signature:
                raise ValueError("idempotency_key_reused")
            _remote_dispatch_records.move_to_end(key)
        else:
            if len(_remote_dispatch_records) >= REMOTE_DISPATCH_CACHE_SIZE:
                removable = next(
                    (
                        record_key
                        for record_key, (_sig, record_future, _created) in _remote_dispatch_records.items()
                        if record_future.done()
                    ),
                    None,
                )
                if removable is None:
                    raise RuntimeError("remote_dispatch_capacity_reached")
                _remote_dispatch_records.pop(removable, None)
            future = Future()
            _remote_dispatch_records[key] = (signature, future, now)
            owner = True

    if owner:
        try:
            result = operation()
        except BaseException as exc:
            future.set_exception(exc)
            raise
        future.set_result(result)
        return result, False
    return future.result(timeout=180.0), True


def _mobile_shell_available() -> bool:
    from core.jarvis_config import SRC_DIR

    return os.path.isfile(os.path.join(SRC_DIR, "frontend", "templates", "mobile.html"))


@sync_bp.route("/api/sync/pair/init", methods=["POST"])
async def init_pairing():
    """Generate a one-use pairing PIN and QR URL for the authenticated owner."""
    if not can_manage_browser_profiles():
        return jsonify({"error": "device_management_forbidden"}), 403
    remote_base = _remote_base_url()
    if not remote_base:
        return jsonify({"status": "error", "code": "remote_access_not_configured"}), 503
    if not _mobile_shell_available():
        return jsonify({"status": "error", "code": "mobile_shell_unavailable"}), 503
    profile_id = get_browser_profile_id() or "admin"
    pin, expires_at = sync_hub.generate_pairing_pin(created_by_profile=profile_id)
    pair_url = f"{remote_base}/mobile#pair={pin}"
    try:
        qr_data_url = _pair_qr_data_url(pair_url)
    except Exception as exc:
        log_warning("device_pair_qr_generation_failed", error=type(exc).__name__)
        return jsonify({"status": "error", "code": "pair_qr_unavailable"}), 503
    obs_event("device_pair_pin_generated", profile_id=profile_id)
    return jsonify(
        {
            "status": "success",
            "pin": pin,
            "expires_at": expires_at,
            "pair_url": pair_url,
            "qr_data_url": qr_data_url,
        }
    )


@sync_bp.route("/api/sync/pair/claim", methods=["POST"])
async def claim_pairing():
    """Claim a PIN only from the configured private HTTPS origin."""
    if not _origin_matches_remote_base():
        return jsonify({"status": "error", "message": "untrusted_pair_origin"}), 403
    data = _json_object(await request.get_json(silent=True))
    pin = str(data.get("pin") or "").strip().upper()
    if not re.fullmatch(r"[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}", pin):
        return jsonify({"status": "error", "message": "pin_required"}), 400
    device_name = " ".join(str(data.get("device_name") or "Móvil PWA").split())[:MAX_DEVICE_NAME_CHARS]
    raw_type = str(data.get("device_type") or DeviceType.MOBILE_PWA.value).strip().lower()
    if raw_type not in {DeviceType.MOBILE_PWA.value, DeviceType.TABLET.value}:
        raw_type = DeviceType.MOBILE_PWA.value
    requester_key = str(
        request.headers.get("Tailscale-User-Login")
        or request.remote_addr
        or "unknown"
    ).strip()[:128]
    result = sync_hub.claim_pairing_pin(
        pin=pin,
        device_name=device_name,
        device_type=DeviceType(raw_type),
        requester_key=requester_key,
    )
    if not result:
        obs_event("device_pair_pin_invalid")
        return jsonify({"status": "error", "message": "invalid_or_expired_pin"}), 401
    device, token_secret = result
    obs_event("device_paired_successfully", device_id=device.device_id)
    return jsonify(
        {
            "status": "success",
            "device_id": device.device_id,
            "device_token": token_secret,
            "name": device.name,
            "profile_id": device.profile_id,
        }
    )


@sync_bp.route("/api/sync/status", methods=["GET"])
async def sync_status():
    profile_id = _request_profile_id()
    if profile_id is None:
        return jsonify({"status": "error", "message": "device_auth_required"}), 401
    device = sync_hub.validate_device_token(_extract_device_token()) if _extract_device_token() else None
    devices = sync_hub.list_devices(profile_id=profile_id)
    return jsonify(
        {
            "status": "online",
            "is_paired": True,
            "current_device": device.to_dict() if device else None,
            "active_devices_count": len(devices),
            "remote_access_configured": bool(_remote_base_url()),
        }
    )


@sync_bp.route("/api/sync/devices", methods=["GET"])
async def list_paired_devices():
    if not can_manage_browser_profiles():
        return jsonify({"error": "device_management_forbidden"}), 403
    return jsonify({"devices": sync_hub.list_devices()})


@sync_bp.route("/api/sync/devices/revoke", methods=["POST"])
async def revoke_paired_device():
    if not can_manage_browser_profiles():
        return jsonify({"error": "device_management_forbidden"}), 403
    data = _json_object(await request.get_json(silent=True))
    device_id = str(data.get("device_id") or "").strip()[:64]
    if not device_id:
        return jsonify({"error": "device_id_required"}), 400
    if not sync_hub.revoke_device(device_id):
        return jsonify({"error": "device_not_found"}), 404
    obs_event("device_revoked", device_id=device_id)
    return jsonify({"status": "success", "device_id": device_id})


@sync_bp.route("/api/sync/devices/self/revoke", methods=["POST"])
async def revoke_current_device():
    device = sync_hub.validate_device_token(_extract_device_token())
    if device is None:
        return jsonify({"error": "device_auth_required"}), 401
    sync_hub.revoke_device(device.device_id)
    obs_event("device_self_revoked", device_id=device.device_id)
    return jsonify({"status": "success"})


@sync_bp.route("/api/sync/snapshot", methods=["GET"])
async def sync_snapshot():
    profile_id = _request_profile_id()
    if profile_id is None:
        return jsonify({"error": "device_auth_required"}), 401
    history_state = sync_hub.get_history(
        profile_id=profile_id,
        after_sequence=0,
        limit=1,
    )
    return jsonify(
        {
            "profile_id": profile_id,
            "messages": _plain_display_history(profile_id),
            "latest_sequence": history_state["latest_sequence"],
        }
    )


@sync_bp.route("/api/sync/history", methods=["GET"])
async def sync_history():
    profile_id = _request_profile_id()
    if profile_id is None:
        return jsonify({"error": "device_auth_required"}), 401
    try:
        after_sequence = int(request.args.get("after_sequence", "0"))
        limit = int(request.args.get("limit", "50"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_history_cursor"}), 400
    if after_sequence < 0 or limit < 1 or limit > MAX_HISTORY_PAGE:
        return jsonify({"error": "invalid_history_cursor"}), 400
    return jsonify(
        sync_hub.get_history(
            profile_id=profile_id,
            after_sequence=after_sequence,
            limit=limit,
        )
    )


@sync_bp.route("/api/sync/dispatch", methods=["POST"])
async def dispatch_remote_command():
    """Dispatch a profile-bound command through the normal JARVIS pipeline."""
    device = sync_hub.validate_device_token(_extract_device_token())
    if device is None:
        return jsonify({"error": "device_auth_required"}), 401
    data = _json_object(await request.get_json(silent=True))
    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "message_required"}), 400
    message = message.strip()
    if len(message) > MAX_COMMAND_CHARS:
        return jsonify({"error": "message_too_large"}), 413
    try:
        request_id = str(uuid.UUID(str(data.get("client_request_id") or "")))
    except (TypeError, ValueError, AttributeError):
        return jsonify({"error": "client_request_id_invalid"}), 400
    if _remote_dispatch_rate_limited(device.device_id):
        return jsonify({"error": "remote_command_rate_limited"}), 429

    request_key = f"{device.profile_id}:{device.device_id}:{request_id}"
    request_signature = hashlib.sha256(message.encode("utf-8")).hexdigest()

    def execute_once():
        sync_hub.broadcast_sync(
            SyncEventType.CHAT_MESSAGE,
            {
                "role": "user",
                "content": message,
                "origin_device": device.name,
                "client_request_id": request_id,
            },
            origin_device_id=device.device_id,
            profile_id=device.profile_id,
        )
        obs_event(
            "remote_dispatch_in",
            device_id=device.device_id,
            profile_id=device.profile_id,
            message_chars=len(message),
            request_id=request_id,
        )
        reply, should_listen = getattr(jarvis_brain, "procesar_mensaje")(  # noqa: B009
            message,
            profile_id=device.profile_id,
            request_id=request_id,
        )
        sync_hub.broadcast_sync(
            SyncEventType.CHAT_MESSAGE,
            {
                "role": "assistant",
                "content": str(reply or ""),
                "should_listen": bool(should_listen),
                "client_request_id": request_id,
            },
            origin_device_id="jarvis_core",
            profile_id=device.profile_id,
        )
        return {"response": str(reply or ""), "should_listen": bool(should_listen)}

    try:
        result, replayed = await asyncio.to_thread(
            _run_remote_dispatch_once,
            request_key,
            request_signature,
            execute_once,
        )
    except ValueError:
        return jsonify({"error": "idempotency_key_reused"}), 409
    except Exception as exc:
        log_error("remote_dispatch_failed", error=type(exc).__name__, device_id=device.device_id)
        return jsonify({"error": "remote_command_unavailable", "request_id": request_id}), 503
    return jsonify(
        {
            "status": "success",
            "response": result["response"],
            "should_listen": result["should_listen"],
            "device_id": device.device_id,
            "request_id": request_id,
            "replayed": replayed,
        }
    )


@sync_bp.route("/api/sync/ws-ticket", methods=["POST"])
async def issue_ws_ticket():
    """Exchange a device token for a short-lived route-scoped WebSocket ticket."""
    device = sync_hub.validate_device_token(_extract_device_token())
    if device is None:
        return jsonify({"error": "device_auth_required"}), 401
    data = _json_object(await request.get_json(silent=True))
    path = {
        "sync": "/api/sync/ws",
        "voice": "/api/voice/stream",
    }.get(str(data.get("purpose") or ""))
    if path is None:
        return jsonify({"error": "ticket_purpose_invalid"}), 400
    ticket = sync_hub.issue_websocket_ticket(device, path=path)
    if ticket is None:
        return jsonify({"error": "websocket_ticket_unavailable"}), 503
    return jsonify({"ticket": ticket, "expires_in_seconds": 30, "path": path})


@sync_bp.websocket("/api/sync/ws")
async def sync_websocket():
    """Full-duplex profile-scoped synchronization channel."""
    device = getattr(g, "jarvis_device_session", None)
    profile_id = device.profile_id if device else (get_browser_profile_id() or "admin")
    device_id = device.device_id if device else f"desktop:{profile_id}"
    queue = sync_hub.subscribe(device_id, profile_id=profile_id)
    await websocket.send(
        json.dumps(
            {
                "event_type": "handshake",
                "device_id": device_id,
                "profile_id": profile_id,
                "is_authenticated": True,
                "latest_sequence": sync_hub.get_history(profile_id=profile_id)["latest_sequence"],
                "timestamp": time.time(),
            },
            ensure_ascii=False,
        )
    )

    async def send_loop():
        try:
            while True:
                event = await queue.get()
                await websocket.send(json.dumps(event.to_dict(), ensure_ascii=False))
                if (
                    event.event_type is SyncEventType.DEVICE_DISCONNECTED
                    and event.target_device_id == device_id
                ):
                    return
        except asyncio.CancelledError:
            raise

    send_task = asyncio.create_task(send_loop())
    try:
        while not send_task.done():
            receive_task = asyncio.create_task(websocket.receive())
            done, _pending = await asyncio.wait(
                {receive_task, send_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if send_task in done:
                receive_task.cancel()
                await asyncio.gather(receive_task, return_exceptions=True)
                break
            raw_msg = receive_task.result()
            if not raw_msg:
                break
            if isinstance(raw_msg, bytes):
                if len(raw_msg) > MAX_WS_MESSAGE_BYTES:
                    break
                raw_msg = raw_msg.decode("utf-8", errors="replace")
            if len(str(raw_msg).encode("utf-8")) > MAX_WS_MESSAGE_BYTES:
                break
            try:
                data = json.loads(raw_msg)
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("type") != "ping":
                continue
            if device and not sync_hub.touch_device(device.device_id):
                break
            await websocket.send(json.dumps({"type": "pong", "timestamp": time.time()}))
    except asyncio.CancelledError:
        raise
    finally:
        send_task.cancel()
        await asyncio.gather(send_task, return_exceptions=True)
        sync_hub.unsubscribe(device_id, queue)
