"""
APIRoutes: Definition of modular Quart routes.
Helps reduce the size of jarvis_backend.py by moving state and configuration routes.
"""

import hashlib
import os
import uuid
from datetime import datetime

import psutil
from core.runtime_logger import log_info, log_warning
from core.security.api_auth import (
    LOCAL_UI_AUTH_COOKIE,
    configured_api_token,
    local_ui_cookie_value,
)
from quart import Blueprint, jsonify, make_response, render_template, request, send_from_directory

api_bp = Blueprint("api", __name__)

_services = None
_root_dir = None
_browser_mode = None
_tts_engine = None
_biometrics_enabled = False
_INSTANCE_ID = str(uuid.uuid4())
_STARTUP_NONCE = str(os.getenv("JARVIS_STARTUP_NONCE") or "")
_STARTUP_NONCE_SHA256 = (
    hashlib.sha256(_STARTUP_NONCE.encode("utf-8")).hexdigest()
    if _STARTUP_NONCE
    else ""
)

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_LOOPBACK_PEERS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def _is_loopback_ui_request() -> bool:
    raw_host = str(request.host or "").strip().lower()
    if raw_host.startswith("["):
        host = raw_host[1:].split("]", 1)[0]
    else:
        host = raw_host.split(":", 1)[0]
    peer = str(request.remote_addr or "").strip().lower()
    return host in _LOOPBACK_HOSTS and peer in _LOOPBACK_PEERS


def init_api_routes(
    services,
    browser_mode,
    root_dir,
    *,
    tts_engine=None,
    biometrics_enabled: bool = False,
):
    global _services, _root_dir, _browser_mode, _tts_engine, _biometrics_enabled
    _services = services
    _root_dir = root_dir
    _browser_mode = browser_mode
    _tts_engine = tts_engine
    _biometrics_enabled = bool(biometrics_enabled)


def build_config_info(browser_mode, *, tts_engine=None, biometrics_enabled=False) -> dict:
    return {
        "browser_mode": browser_mode,
        "tts_enabled": bool(getattr(tts_engine, "voice", None)),
        "biometrics_active": bool(biometrics_enabled),
    }


@api_bp.route("/", methods=["GET"])
async def index():
    try:
        rendered = await render_template("index.html")
        response = await make_response(rendered)
        token = configured_api_token()
        if token and _is_loopback_ui_request():
            response.set_cookie(
                LOCAL_UI_AUTH_COOKIE,
                local_ui_cookie_value(token),
                httponly=True,
                secure=request.scheme == "https",
                samesite="Strict",
                path="/",
            )
        return response
    except Exception as e:
        log_warning("template_render_failed", error=str(e), template="index.html")
        return jsonify(
            {
                "status": "ok",
                "message": "JARVIS backend online. UI may be unavailable via Quart templates.",
            }
        )


@api_bp.route("/media/<path:filename>", methods=["GET"])
async def media_files(filename: str):
    return await send_from_directory(os.path.join(_root_dir, "media"), filename)


@api_bp.route("/manifest.json", methods=["GET"])
async def manifest_json():
    return await send_from_directory(os.path.join(_root_dir, "src", "frontend", "static"), "manifest.json")


@api_bp.route("/sw.js", methods=["GET"])
async def service_worker():
    response = await make_response(
        await send_from_directory(os.path.join(_root_dir, "src", "frontend", "static"), "sw.js")
    )
    response.headers["Cache-Control"] = "no-cache"
    return response


@api_bp.route("/api/health", methods=["GET"])
def health():
    return jsonify(
        {
            "status": "online",
            "service": "jarvis",
            "api_version": 1,
            "instance_id": _INSTANCE_ID,
            "startup_nonce_sha256": _STARTUP_NONCE_SHA256,
            "timestamp": datetime.now().isoformat(),
            "cpu": psutil.cpu_percent(),
            "ram": psutil.virtual_memory().percent,
        }
    )


@api_bp.route("/api/frontend/log", methods=["POST"])
async def frontend_log():
    data = (await request.get_json(silent=True)) or {}
    message = " ".join(str(data.get("message") or "").split())
    source = " ".join(str(data.get("source") or "ui").split())
    if not message:
        return jsonify({"ok": False, "error": "empty_message"}), 400
    log_info(
        "JARVIS UI",
        journal_category="FRONTEND",
        source=source[:80],
        ui_message=message[:2000],
        ip=request.remote_addr,
    )
    return jsonify({"ok": True})


@api_bp.route("/api/config/info", methods=["GET"])
def config_info():
    if not _browser_mode:
        return jsonify({"error": "Configuration not initialized"}), 500
    return jsonify(
        build_config_info(
            _browser_mode,
            tts_engine=_tts_engine,
            biometrics_enabled=_biometrics_enabled,
        )
    )


@api_bp.route("/api/memory/graph", methods=["GET"])
async def memory_graph():
    from core.security.browser_profile import get_browser_profile_id
    from modules.memory.service import unified_memory

    profile_id = get_browser_profile_id()
    try:
        limit = min(100, max(1, int(request.args.get("limit", 50))))
    except ValueError:
        limit = 50

    try:
        nodes = unified_memory.store.list_cognitive_nodes(profile_id=profile_id, limit=limit)
        skills = unified_memory.store.list_skills(profile_id=profile_id, limit=25)

        nodes_data = []
        links_data = []

        for n in nodes:
            nodes_data.append(
                {
                    "id": n.node_id,
                    "title": n.title,
                    "type": n.node_type.value,
                    "content": n.content[:200],
                    "tags": list(n.tags),
                }
            )
            for e in n.edges:
                links_data.append(
                    {
                        "source": n.title,
                        "target": e.target_title,
                        "relation": e.relation,
                        "weight": e.weight,
                    }
                )

        for s in skills:
            skill_id = f"skill_{s.name.lower().replace(' ', '_')}"
            nodes_data.append(
                {
                    "id": skill_id,
                    "title": s.name,
                    "type": "skill",
                    "content": f"{len(s.steps)} pasos | {', '.join(s.trigger_phrases)}",
                    "tags": ["skill", "procedural"],
                }
            )

        return jsonify(
            {
                "ok": True,
                "profile_id": profile_id,
                "count": len(nodes_data),
                "nodes": nodes_data,
                "links": links_data,
            }
        )
    except Exception as exc:
        log_warning(
            "memory_graph_failed",
            error_type=type(exc).__name__,
            profile_id=profile_id,
        )
        return jsonify({"ok": False, "error": "memory_graph_unavailable"}), 500
