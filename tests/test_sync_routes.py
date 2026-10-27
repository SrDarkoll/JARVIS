from __future__ import annotations

import asyncio
import base64
import json
import uuid
from urllib.parse import urlsplit

from api import device_sync_routes as sync_routes
from core.device_sync.sync_hub import DeviceSyncHub
from core.device_sync.sync_models import DeviceType, SyncEventType
from quart import Quart

REMOTE_URL = "https://jarvis.tail123.ts.net"
REMOTE_SCOPE = {
    "scheme": "https",
    "server": ("jarvis.tail123.ts.net", 443),
    "client": ("127.0.0.1", 12345),
}


def _app():
    app = Quart(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(sync_routes.sync_bp)
    return app


def test_pair_init_uses_https_fragment_qr_and_does_not_log_pin(monkeypatch, tmp_path):
    hub = DeviceSyncHub(str(tmp_path))
    monkeypatch.setattr(sync_routes, "sync_hub", hub)
    monkeypatch.setattr(sync_routes, "get_browser_profile_id", lambda: "owner")
    monkeypatch.setattr(sync_routes, "_mobile_shell_available", lambda: True)
    monkeypatch.setenv("JARVIS_REMOTE_BASE_URL", REMOTE_URL)
    observed = []
    monkeypatch.setattr(sync_routes, "obs_event", lambda name, **data: observed.append((name, data)))

    async def scenario():
        response = await _app().test_client().post("/api/sync/pair/init")
        return response.status_code, await response.get_json()

    status, data = asyncio.run(scenario())
    assert status == 200
    parsed = urlsplit(data["pair_url"])
    assert parsed.scheme == "https"
    assert parsed.netloc == "jarvis.tail123.ts.net"
    assert parsed.path == "/mobile"
    assert parsed.query == ""
    assert parsed.fragment == f"pair={data['pin']}"
    assert data["qr_data_url"].startswith("data:image/svg+xml;base64,")
    qr_svg = base64.b64decode(data["qr_data_url"].split(",", 1)[1]).decode("utf-8")
    assert qr_svg.lstrip().startswith("<svg")
    assert data["pin"] not in json.dumps(observed)


def test_pair_claim_requires_the_configured_origin_and_consumes_pin_once(monkeypatch, tmp_path):
    hub = DeviceSyncHub(str(tmp_path))
    monkeypatch.setattr(sync_routes, "sync_hub", hub)
    monkeypatch.setenv("JARVIS_REMOTE_BASE_URL", REMOTE_URL)
    pin, _ = hub.generate_pairing_pin(created_by_profile="owner")

    async def scenario():
        client = _app().test_client()
        denied = await client.post(
            "/api/sync/pair/claim",
            scope_base=REMOTE_SCOPE,
            headers={"Origin": "https://attacker.invalid"},
            json={"pin": pin, "device_name": "phone"},
        )
        accepted = await client.post(
            "/api/sync/pair/claim",
            scope_base=REMOTE_SCOPE,
            headers={"Origin": REMOTE_URL},
            json={"pin": pin, "device_name": "phone"},
        )
        repeated = await client.post(
            "/api/sync/pair/claim",
            scope_base=REMOTE_SCOPE,
            headers={"Origin": REMOTE_URL},
            json={"pin": pin, "device_name": "second-phone"},
        )
        return (
            denied.status_code,
            accepted.status_code,
            await accepted.get_json(),
            repeated.status_code,
        )

    denied, accepted, data, repeated = asyncio.run(scenario())
    assert denied == 403
    assert accepted == 200
    assert data["profile_id"] == "owner"
    assert data["device_token"].startswith("jrv_dev_")
    assert repeated == 401


def test_history_is_filtered_by_the_authenticated_device_profile(monkeypatch, tmp_path):
    hub = DeviceSyncHub(str(tmp_path))
    monkeypatch.setattr(sync_routes, "sync_hub", hub)
    monkeypatch.setenv("JARVIS_REMOTE_BASE_URL", REMOTE_URL)
    owner_pin, _ = hub.generate_pairing_pin(created_by_profile="owner")
    owner, owner_token = hub.claim_pairing_pin(owner_pin, "owner phone", DeviceType.MOBILE_PWA)
    hub.broadcast_sync(
        SyncEventType.CHAT_MESSAGE,
        {"role": "user", "content": "owner-only"},
        profile_id="owner",
    )
    hub.broadcast_sync(
        SyncEventType.CHAT_MESSAGE,
        {"role": "user", "content": "telegram-only"},
        profile_id="telegram_user",
    )

    async def scenario():
        response = await _app().test_client().get(
            "/api/sync/history?after_sequence=0",
            scope_base=REMOTE_SCOPE,
            headers={
                "Origin": REMOTE_URL,
                "X-JARVIS-DEVICE-TOKEN": owner_token,
            },
        )
        return response.status_code, await response.get_json()

    status, data = asyncio.run(scenario())
    assert status == 200
    chat_events = [item for item in data["events"] if item["event_type"] == "chat_message"]
    assert {item["payload"]["content"] for item in chat_events} == {"owner-only"}
    assert all(item["profile_id"] == owner.profile_id for item in data["events"])


def test_dispatch_replays_same_request_without_running_brain_twice(monkeypatch, tmp_path):
    hub = DeviceSyncHub(str(tmp_path))
    monkeypatch.setattr(sync_routes, "sync_hub", hub)
    pin, _ = hub.generate_pairing_pin(created_by_profile="owner")
    device, token = hub.claim_pairing_pin(pin, "phone", DeviceType.MOBILE_PWA)
    calls = []

    def fake_brain(message, *, profile_id, request_id):
        calls.append((message, profile_id, request_id))
        return "done", False

    monkeypatch.setattr(sync_routes.jarvis_brain, "procesar_mensaje", fake_brain)
    request_id = str(uuid.uuid4())
    request_body = {
        "message": "reproduce Poker Face",
        "client_request_id": request_id,
    }

    async def scenario():
        client = _app().test_client()
        headers = {"Origin": REMOTE_URL, "X-JARVIS-DEVICE-TOKEN": token}
        first = await client.post("/api/sync/dispatch", scope_base=REMOTE_SCOPE, headers=headers, json=request_body)
        second = await client.post("/api/sync/dispatch", scope_base=REMOTE_SCOPE, headers=headers, json=request_body)
        conflict = await client.post(
            "/api/sync/dispatch",
            scope_base=REMOTE_SCOPE,
            headers=headers,
            json={**request_body, "message": "something else"},
        )
        return (
            first.status_code,
            await first.get_json(),
            second.status_code,
            await second.get_json(),
            conflict.status_code,
        )

    first_status, first, second_status, second, conflict_status = asyncio.run(scenario())
    assert first_status == second_status == 200
    assert first["response"] == second["response"] == "done"
    assert second["replayed"] is True
    assert conflict_status == 409
    assert calls == [("reproduce Poker Face", "owner", request_id)]
    assert device.profile_id == "owner"


def test_device_can_exchange_token_for_path_scoped_voice_ticket(monkeypatch, tmp_path):
    hub = DeviceSyncHub(str(tmp_path))
    monkeypatch.setattr(sync_routes, "sync_hub", hub)
    pin, _ = hub.generate_pairing_pin(created_by_profile="owner")
    device, token = hub.claim_pairing_pin(pin, "phone", DeviceType.MOBILE_PWA)

    async def scenario():
        response = await _app().test_client().post(
            "/api/sync/ws-ticket",
            scope_base=REMOTE_SCOPE,
            headers={"Origin": REMOTE_URL, "X-JARVIS-DEVICE-TOKEN": token},
            json={"purpose": "voice"},
        )
        return response.status_code, await response.get_json()

    status, data = asyncio.run(scenario())
    assert status == 200
    assert data["path"] == "/api/voice/stream"
    resolved = hub.consume_websocket_ticket(data["ticket"], path=data["path"])
    assert resolved.device_id == device.device_id
    assert hub.consume_websocket_ticket(data["ticket"], path=data["path"]) is None
