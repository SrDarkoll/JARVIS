"""Device synchronization hub for real-time multi-device coordination in J.A.R.V.I.S."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import socket
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.device_sync.sync_models import (
    DeviceSession,
    DeviceType,
    PairingSession,
    SyncEvent,
    SyncEventType,
)

PAIRING_PIN_TTL_SECONDS = 600.0  # 10 minutes
MAX_EVENT_HISTORY = 100
MAX_EVENT_PAYLOAD_BYTES = 64_000
MAX_SUBSCRIBER_QUEUE = 100
MAX_PAIRING_FAILURES = 5
PAIRING_FAILURE_WINDOW_SECONDS = 600.0
MAX_PAIRING_FAILURE_KEYS = 4096
WEBSOCKET_TICKET_TTL_SECONDS = 30.0
MAX_WEBSOCKET_TICKETS = 1024
WEBSOCKET_TICKET_PATHS = frozenset({"/api/sync/ws", "/api/voice/stream"})
DEVICE_STORAGE_FILE = "paired_devices.json"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_local_lan_ip() -> str:
    """Best-effort discovery of local LAN IP address for mobile pairing."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            # Connecting to a public address (doesn't send packets) to find routing interface
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


@dataclass(eq=False, slots=True)
class _Subscription:
    device_id: str
    profile_id: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[SyncEvent]


@dataclass(frozen=True, slots=True)
class _WebSocketTicket:
    device_id: str
    path: str
    expires_at: float


class DeviceSyncHub:
    """Central registry and pub/sub message hub for all paired devices."""

    def __init__(self, storage_dir: str | None = None) -> None:
        self._lock = threading.RLock()
        self._devices: dict[str, DeviceSession] = {}
        self._token_to_device_id: dict[str, str] = {}  # token_hash -> device_id
        self._active_pins: dict[str, PairingSession] = {}  # pin -> PairingSession
        self._history: dict[str, deque[SyncEvent]] = {}
        self._profile_sequences: dict[str, int] = {}
        self._subscribers: dict[str, set[_Subscription]] = {}
        self._pin_failures_by_source: dict[str, deque[float]] = {}
        self._pin_failures_by_code: dict[str, deque[float]] = {}
        self._websocket_tickets: dict[str, _WebSocketTicket] = {}

        # Persistent storage setup. Runtime paths are injectable so tests and
        # portable installs never fall back to the developer's AppData.
        from core.jarvis_config import RUNTIME_PATHS

        self._storage_dir = Path(storage_dir) if storage_dir else Path(RUNTIME_PATHS.home) / "devices"
        self._storage_file = self._storage_dir / DEVICE_STORAGE_FILE
        self._load_paired_devices()

    def _load_paired_devices(self) -> None:
        with self._lock:
            if not self._storage_file.exists():
                return
            try:
                data = json.loads(self._storage_file.read_text(encoding="utf-8"))
                for item in data.get("devices", []):
                    try:
                        session = DeviceSession(
                            device_id=item["device_id"],
                            name=item.get("name", "Dispositivo Móvil"),
                            device_type=DeviceType(item.get("device_type", "mobile_pwa")),
                            paired_at=float(item.get("paired_at", time.time())),
                            last_seen=float(item.get("last_seen", time.time())),
                            profile_id=item.get("profile_id", "default"),
                            token_hash=item.get("token_hash", ""),
                            is_active=bool(item.get("is_active", True)),
                        )
                        self._devices[session.device_id] = session
                        if session.token_hash:
                            self._token_to_device_id[session.token_hash] = session.device_id
                    except (KeyError, ValueError):
                        continue
            except Exception:
                pass

    def _save_paired_devices(self) -> None:
        with self._lock:
            temp_path: Path | None = None
            try:
                self._storage_dir.mkdir(parents=True, exist_ok=True)
                try:
                    os.chmod(self._storage_dir, 0o700)
                except OSError:
                    pass
                payload = {
                    "devices": [
                        {
                            "device_id": d.device_id,
                            "name": d.name,
                            "device_type": d.device_type.value,
                            "paired_at": d.paired_at,
                            "last_seen": d.last_seen,
                            "profile_id": d.profile_id,
                            "token_hash": d.token_hash,
                            "is_active": d.is_active,
                        }
                        for d in self._devices.values()
                    ]
                }
                descriptor, raw_temp = tempfile.mkstemp(
                    prefix=".paired-devices-",
                    suffix=".tmp",
                    dir=self._storage_dir,
                )
                temp_path = Path(raw_temp)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.chmod(temp_path, 0o600)
                except OSError:
                    pass
                os.replace(temp_path, self._storage_file)
                temp_path = None
            except Exception:
                pass
            finally:
                if temp_path is not None:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except OSError:
                        pass

    def generate_pairing_pin(self, created_by_profile: str = "admin") -> tuple[str, float]:
        """Generate a short 6-character PIN for pairing a mobile device."""
        profile = str(created_by_profile or "admin").strip()[:128] or "admin"
        with self._lock:
            # Purge expired pins
            now = time.time()
            expired = [pin for pin, session in self._active_pins.items() if session.is_expired]
            for pin in expired:
                del self._active_pins[pin]

            # Replacing a pairing request invalidates the older QR for this profile.
            for pin, session in list(self._active_pins.items()):
                if session.created_by_profile == profile:
                    del self._active_pins[pin]

            # Generate memorable 6-char PIN (letters + digits, no ambiguous chars)
            alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
            pin = "".join(secrets.choice(alphabet) for _ in range(6))
            expires_at = now + PAIRING_PIN_TTL_SECONDS
            self._active_pins[pin] = PairingSession(
                pin=pin,
                created_at=now,
                expires_at=expires_at,
                created_by_profile=profile,
            )
            return pin, expires_at

    @staticmethod
    def _trim_failure_bucket(bucket: deque[float], now: float) -> None:
        cutoff = now - PAIRING_FAILURE_WINDOW_SECONDS
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

    def _pairing_attempt_is_limited(self, source_key: str, pin_key: str, now: float) -> bool:
        source_bucket = self._pin_failures_by_source.get(source_key)
        pin_bucket = self._pin_failures_by_code.get(pin_key)
        if source_bucket is not None:
            self._trim_failure_bucket(source_bucket, now)
        if pin_bucket is not None:
            self._trim_failure_bucket(pin_bucket, now)
        return bool(
            (source_bucket and len(source_bucket) >= MAX_PAIRING_FAILURES)
            or (pin_bucket and len(pin_bucket) >= MAX_PAIRING_FAILURES)
        )

    def _record_pairing_failure(self, source_key: str, pin_key: str, now: float) -> None:
        for key, buckets in (
            (source_key, self._pin_failures_by_source),
            (pin_key, self._pin_failures_by_code),
        ):
            bucket = buckets.setdefault(key, deque())
            self._trim_failure_bucket(bucket, now)
            bucket.append(now)
            if len(buckets) > MAX_PAIRING_FAILURE_KEYS:
                for stale_key, stale_bucket in list(buckets.items()):
                    self._trim_failure_bucket(stale_bucket, now)
                    if not stale_bucket:
                        buckets.pop(stale_key, None)
                    if len(buckets) <= MAX_PAIRING_FAILURE_KEYS:
                        break

    def claim_pairing_pin(
        self,
        pin: str,
        device_name: str,
        device_type: DeviceType = DeviceType.MOBILE_PWA,
        assigned_profile_id: str | None = None,
        requester_key: str = "unknown",
    ) -> tuple[DeviceSession, str] | None:
        """Claim a pairing PIN to register a new device and receive an auth token."""
        normalized_pin = pin.strip().upper()
        source_key = str(requester_key or "unknown").strip()[:128] or "unknown"
        pin_key = _hash_token(normalized_pin)
        with self._lock:
            now = time.time()
            if self._pairing_attempt_is_limited(source_key, pin_key, now):
                return None
            session = self._active_pins.get(normalized_pin)
            if not session or session.is_expired:
                self._record_pairing_failure(source_key, pin_key, now)
                return None

            # Mark consumed
            session.consumed = True
            del self._active_pins[normalized_pin]

            # Create device session and secret token
            device_id = f"dev_{secrets.token_hex(6)}"
            token_secret = f"jrv_dev_{secrets.token_urlsafe(32)}"
            token_hash = _hash_token(token_secret)
            now = time.time()

            dev_session = DeviceSession(
                device_id=device_id,
                name=device_name.strip() or "Móvil PWA",
                device_type=device_type,
                paired_at=now,
                last_seen=now,
                profile_id=assigned_profile_id or session.created_by_profile or "admin",
                token_hash=token_hash,
                is_active=True,
            )

            self._devices[device_id] = dev_session
            self._token_to_device_id[token_hash] = device_id
            self._save_paired_devices()
            self._pin_failures_by_source.pop(source_key, None)
            self._pin_failures_by_code.pop(pin_key, None)

            # Broadcast device connected event
            self.broadcast_sync(
                SyncEventType.DEVICE_CONNECTED,
                {"device": dev_session.to_dict()},
                origin_device_id=device_id,
                profile_id=dev_session.profile_id,
            )

            return dev_session, token_secret

    def validate_device_token(self, supplied_token: str | None) -> DeviceSession | None:
        """Validate a supplied device token and return the associated DeviceSession."""
        if not supplied_token:
            return None
        candidate = supplied_token.strip()
        if not candidate.startswith("jrv_dev_"):
            return None

        token_hash = _hash_token(candidate)
        with self._lock:
            device_id = self._token_to_device_id.get(token_hash)
            if not device_id:
                return None
            device = self._devices.get(device_id)
            if not device or not device.is_active:
                return None
            device.last_seen = time.time()
            return device

    def issue_websocket_ticket(self, device: DeviceSession, *, path: str) -> str | None:
        """Issue a one-use, short-lived ticket for a specific WebSocket route."""
        if path not in WEBSOCKET_TICKET_PATHS:
            return None
        with self._lock:
            current = self._devices.get(device.device_id)
            if (
                not current
                or not current.is_active
                or current.token_hash != device.token_hash
                or current.profile_id != device.profile_id
            ):
                return None
            now = time.time()
            for key, ticket in list(self._websocket_tickets.items()):
                if ticket.expires_at <= now:
                    self._websocket_tickets.pop(key, None)
            if len(self._websocket_tickets) >= MAX_WEBSOCKET_TICKETS:
                return None
            secret = secrets.token_urlsafe(32)
            self._websocket_tickets[_hash_token(secret)] = _WebSocketTicket(
                device_id=current.device_id,
                path=path,
                expires_at=now + WEBSOCKET_TICKET_TTL_SECONDS,
            )
            return secret

    def consume_websocket_ticket(self, supplied_ticket: str | None, *, path: str) -> DeviceSession | None:
        """Consume a route-bound WebSocket ticket exactly once."""
        if not supplied_ticket:
            return None
        ticket_hash = _hash_token(supplied_ticket.strip())
        with self._lock:
            ticket = self._websocket_tickets.pop(ticket_hash, None)
            if not ticket or ticket.path != path or ticket.expires_at <= time.time():
                return None
            device = self._devices.get(ticket.device_id)
            if not device or not device.is_active:
                return None
            device.last_seen = time.time()
            return device

    def revoke_device(self, device_id: str) -> bool:
        """Revoke and deactivate a paired device."""
        with self._lock:
            device = self._devices.get(device_id)
            if not device:
                return False
            device.is_active = False
            if device.token_hash in self._token_to_device_id:
                del self._token_to_device_id[device.token_hash]
            self._save_paired_devices()

            self.broadcast_sync(
                SyncEventType.DEVICE_DISCONNECTED,
                {"device_id": device_id, "name": device.name},
                origin_device_id="desktop",
                target_device_id=device_id,
                profile_id=device.profile_id,
            )
            return True

    def list_devices(self, profile_id: str | None = None) -> list[dict[str, Any]]:
        """Return list of all paired devices with status."""
        with self._lock:
            now = time.time()
            return [
                {
                    **device.to_dict(),
                    "is_online": (now - device.last_seen) < 120.0,
                }
                for device in self._devices.values()
                if device.is_active and (profile_id is None or device.profile_id == profile_id)
            ]

    def touch_device(self, device_id: str) -> bool:
        with self._lock:
            device = self._devices.get(str(device_id or ""))
            if not device or not device.is_active:
                return False
            device.last_seen = time.time()
            return True

    def subscribe(
        self,
        device_id: str,
        *,
        profile_id: str = "admin",
    ) -> asyncio.Queue[SyncEvent]:
        """Subscribe an active connection to receive real-time sync events."""
        queue: asyncio.Queue[SyncEvent] = asyncio.Queue(maxsize=MAX_SUBSCRIBER_QUEUE)
        subscription = _Subscription(
            device_id=str(device_id or "desktop"),
            profile_id=str(profile_id or "admin").strip()[:128] or "admin",
            loop=asyncio.get_running_loop(),
            queue=queue,
        )
        with self._lock:
            self._subscribers.setdefault(subscription.device_id, set()).add(subscription)
        return queue

    def unsubscribe(self, device_id: str, queue: asyncio.Queue[SyncEvent]) -> None:
        """Unsubscribe a connection queue."""
        with self._lock:
            if device_id in self._subscribers:
                subscribers = self._subscribers[device_id]
                removed = [item for item in subscribers if item.queue is queue]
                for item in removed:
                    subscribers.discard(item)
                if not self._subscribers[device_id]:
                    del self._subscribers[device_id]

    @staticmethod
    def _deliver_event(subscription: _Subscription, event: SyncEvent) -> None:
        queue = subscription.queue
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # The client detects a sequence gap and requests a replay/snapshot.
            pass

    def broadcast_sync(
        self,
        event_type: SyncEventType,
        payload: dict[str, Any],
        origin_device_id: str = "desktop",
        target_device_id: str | None = None,
        profile_id: str = "admin",
    ) -> SyncEvent:
        """Record a bounded event and schedule delivery on matching profile loops."""
        if not isinstance(payload, dict):
            raise TypeError("sync_event_payload_must_be_object")
        try:
            payload_size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise TypeError("sync_event_payload_not_json_serializable") from exc
        if payload_size > MAX_EVENT_PAYLOAD_BYTES:
            raise ValueError("sync_event_payload_too_large")

        normalized_profile = str(profile_id or "admin").strip()[:128] or "admin"
        with self._lock:
            sequence = self._profile_sequences.get(normalized_profile, 0) + 1
            self._profile_sequences[normalized_profile] = sequence
            event = SyncEvent(
                event_id=f"evt_{secrets.token_hex(12)}",
                event_type=event_type,
                payload=json.loads(json.dumps(payload, ensure_ascii=False)),
                profile_id=normalized_profile,
                sequence=sequence,
                timestamp=time.time(),
                origin_device_id=origin_device_id,
                target_device_id=target_device_id,
            )
            self._history.setdefault(
                normalized_profile,
                deque(maxlen=MAX_EVENT_HISTORY),
            ).append(event)
            subscribers = [
                subscription
                for device_id, connections in self._subscribers.items()
                if target_device_id is None or device_id == target_device_id
                for subscription in connections
                if subscription.profile_id == normalized_profile
            ]

        for subscription in subscribers:
            try:
                subscription.loop.call_soon_threadsafe(
                    self._deliver_event,
                    subscription,
                    event,
                )
            except RuntimeError:
                self.unsubscribe(subscription.device_id, subscription.queue)
        return event

    async def broadcast(
        self,
        event_type: SyncEventType,
        payload: dict[str, Any],
        origin_device_id: str = "desktop",
        target_device_id: str | None = None,
        profile_id: str = "admin",
    ) -> SyncEvent:
        """Asynchronous broadcast wrapper."""
        return self.broadcast_sync(
            event_type=event_type,
            payload=payload,
            origin_device_id=origin_device_id,
            target_device_id=target_device_id,
            profile_id=profile_id,
        )

    def get_history(
        self,
        *,
        profile_id: str = "admin",
        after_sequence: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Return profile-scoped events after a cursor or signal a required resync."""
        normalized_profile = str(profile_id or "admin").strip()[:128] or "admin"
        bounded_limit = min(100, max(1, int(limit)))
        cursor = max(0, int(after_sequence))
        with self._lock:
            history = list(self._history.get(normalized_profile, ()))
            latest_sequence = self._profile_sequences.get(normalized_profile, 0)

        first_sequence = history[0].sequence if history else 0
        resync_required = bool(
            (history and cursor < first_sequence - 1)
            or cursor > latest_sequence
            or (not history and cursor > 0)
        )
        events = [] if resync_required else [
            event.to_dict() for event in history if event.sequence > cursor
        ][:bounded_limit]
        return {
            "events": events,
            "resync_required": resync_required,
            "latest_sequence": latest_sequence,
        }


# Global singleton instance
sync_hub = DeviceSyncHub()
