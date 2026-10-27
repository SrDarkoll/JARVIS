from __future__ import annotations

import asyncio
import json
import threading

from core.device_sync.sync_hub import MAX_EVENT_HISTORY, DeviceSyncHub
from core.device_sync.sync_models import DeviceType, SyncEventType


def test_events_and_history_are_scoped_to_subscriber_profile(tmp_path):
    hub = DeviceSyncHub(str(tmp_path))

    async def scenario():
        owner_queue = hub.subscribe("phone-owner", profile_id="owner")
        telegram_queue = hub.subscribe("phone-telegram", profile_id="telegram_user")
        hub.broadcast_sync(
            SyncEventType.CHAT_MESSAGE,
            {"role": "user", "content": "hello"},
            profile_id="owner",
            origin_device_id="desktop",
        )

        event = await asyncio.wait_for(owner_queue.get(), timeout=0.5)
        assert event.profile_id == "owner"
        assert event.payload["content"] == "hello"
        assert telegram_queue.empty()
        owner_history = hub.get_history(profile_id="owner")
        assert all(item["profile_id"] == "owner" for item in owner_history["events"])
        assert hub.get_history(profile_id="telegram_user")["events"] == []

    asyncio.run(scenario())


def test_publish_from_background_thread_wakes_loop_owned_queue(tmp_path):
    hub = DeviceSyncHub(str(tmp_path))

    async def scenario():
        queue = hub.subscribe("phone-owner", profile_id="owner")
        worker = threading.Thread(
            target=lambda: hub.broadcast_sync(
                SyncEventType.CHAT_MESSAGE,
                {"role": "assistant", "content": "reply"},
                profile_id="owner",
                origin_device_id="telegram",
            )
        )
        worker.start()
        event = await asyncio.wait_for(queue.get(), timeout=0.5)
        await asyncio.to_thread(worker.join)
        assert event.payload["content"] == "reply"

    asyncio.run(scenario())


def test_overflow_drops_stale_events_and_requests_profile_resync(tmp_path):
    hub = DeviceSyncHub(str(tmp_path))

    async def scenario():
        queue = hub.subscribe("phone-owner", profile_id="owner")
        for index in range(MAX_EVENT_HISTORY + 2):
            hub.broadcast_sync(
                SyncEventType.SYSTEM_STATE,
                {"index": index},
                profile_id="owner",
            )
        await asyncio.sleep(0.01)
        first_queued = await queue.get()
        page = hub.get_history(profile_id="owner", after_sequence=0)

        assert first_queued.sequence == 3
        assert page["resync_required"] is True
        assert len(page["events"]) == 0

    asyncio.run(scenario())


def test_history_pagination_returns_the_next_page_without_skipping(tmp_path):
    hub = DeviceSyncHub(str(tmp_path))
    for index in range(150):
        hub.broadcast_sync(
            SyncEventType.SYSTEM_STATE,
            {"index": index},
            profile_id="owner",
        )

    page = hub.get_history(profile_id="owner", after_sequence=50, limit=20)
    sequences = [event["sequence"] for event in page["events"]]

    assert sequences == list(range(51, 71))
    assert page["resync_required"] is False

    next_page = hub.get_history(profile_id="owner", after_sequence=70, limit=20)
    assert [event["sequence"] for event in next_page["events"]] == list(range(71, 91))


def test_history_after_restart_requires_resync(tmp_path):
    fresh_hub = DeviceSyncHub(str(tmp_path))

    page = fresh_hub.get_history(profile_id="owner", after_sequence=120)

    assert page["latest_sequence"] == 0
    assert page["resync_required"] is True
    assert page["events"] == []


def test_default_storage_uses_the_runtime_directory():
    from core.jarvis_config import RUNTIME_PATHS

    hub = DeviceSyncHub()

    assert hub._storage_file.parent == RUNTIME_PATHS.home / "devices"


def test_pairing_token_is_one_time_and_only_hash_is_persisted(tmp_path):
    hub = DeviceSyncHub(str(tmp_path))
    pin, _expires_at = hub.generate_pairing_pin(created_by_profile="owner")

    paired = hub.claim_pairing_pin(pin, "phone", DeviceType.MOBILE_PWA)

    assert paired is not None
    device, token = paired
    assert device.profile_id == "owner"
    assert hub.validate_device_token(token).device_id == device.device_id
    assert hub.claim_pairing_pin(pin, "second-phone", DeviceType.MOBILE_PWA) is None
    saved = json.loads(hub._storage_file.read_text(encoding="utf-8"))
    saved_text = json.dumps(saved)
    assert token not in saved_text
    assert device.token_hash in saved_text


def test_pairing_invalid_attempts_are_throttled_per_source_and_pin(tmp_path):
    hub = DeviceSyncHub(str(tmp_path))
    pin, _expires_at = hub.generate_pairing_pin(created_by_profile="owner")

    for _ in range(5):
        assert hub.claim_pairing_pin("invalid", "phone", requester_key="tailnet-user") is None

    # The source bucket is exhausted even though the real PIN has not been tried.
    assert hub.claim_pairing_pin(pin, "phone", requester_key="tailnet-user") is None
    # A different client can claim the PIN if it was not itself guessed five times.
    assert hub.claim_pairing_pin(pin, "phone", requester_key="other-client") is not None


def test_websocket_ticket_is_one_use_and_bound_to_one_path(tmp_path):
    hub = DeviceSyncHub(str(tmp_path))
    pin, _expires_at = hub.generate_pairing_pin(created_by_profile="owner")
    device, token = hub.claim_pairing_pin(pin, "phone", DeviceType.MOBILE_PWA)
    device = hub.validate_device_token(token)

    wrong_path_ticket = hub.issue_websocket_ticket(device, path="/api/sync/ws")
    assert hub.consume_websocket_ticket(wrong_path_ticket, path="/api/voice/stream") is None
    valid_ticket = hub.issue_websocket_ticket(device, path="/api/sync/ws")

    authenticated = hub.consume_websocket_ticket(valid_ticket, path="/api/sync/ws")
    assert authenticated.device_id == device.device_id
    assert authenticated.profile_id == "owner"
    assert hub.consume_websocket_ticket(valid_ticket, path="/api/sync/ws") is None


def test_websocket_ticket_expires(monkeypatch, tmp_path):
    from importlib import import_module

    sync_hub_module = import_module("core.device_sync.sync_hub")

    now = [1_000.0]
    monkeypatch.setattr(sync_hub_module.time, "time", lambda: now[0])
    hub = DeviceSyncHub(str(tmp_path))
    pin, _expires_at = hub.generate_pairing_pin(created_by_profile="owner")
    device, token = hub.claim_pairing_pin(pin, "phone", DeviceType.MOBILE_PWA)
    device = hub.validate_device_token(token)
    ticket = hub.issue_websocket_ticket(device, path="/api/voice/stream")

    now[0] += 31.0

    assert hub.consume_websocket_ticket(ticket, path="/api/voice/stream") is None
