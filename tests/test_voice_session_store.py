from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from voice.session_store import VoiceSessionMapping, VoiceSessionStore


def test_voice_session_store_updates_and_pops_atomically() -> None:
    store = VoiceSessionStore(clock=lambda: 100.0)
    store.start(
        "127.0.0.1",
        {"stage": "awaiting_sample", "samples": 0},
    )

    first = store.update(
        "127.0.0.1",
        lambda value: {**value, "samples": value["samples"] + 1},
    )
    second = store.pop("127.0.0.1")

    assert first["samples"] == 1
    assert second is not None
    assert second["samples"] == 1
    assert store.get("127.0.0.1") is None


def test_voice_session_store_returns_independent_copies() -> None:
    store = VoiceSessionStore(clock=lambda: 100.0)
    created = store.start(
        "guest",
        {"samples": [{"id": 1}]},
    )

    created["samples"][0]["id"] = 99
    fetched = store.get("guest")

    assert fetched == {
        "samples": [{"id": 1}],
        "created_at": 100.0,
    }


def test_voice_session_store_serializes_concurrent_updates() -> None:
    store = VoiceSessionStore(clock=lambda: 100.0)
    store.start("guest", {"samples": 0})

    def increment() -> None:
        store.update(
            "guest",
            lambda value: {**value, "samples": value["samples"] + 1},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _index: increment(), range(100)))

    session = store.get("guest")
    assert session is not None
    assert session["samples"] == 100


def test_voice_session_store_cleans_only_expired_sessions() -> None:
    now = [100.0]
    store = VoiceSessionStore(clock=lambda: now[0], ttl_seconds=30.0)
    store.start("old", {"stage": "pending"})
    now[0] = 120.0
    store.start("current", {"stage": "pending"})
    now[0] = 131.0

    removed = store.cleanup_expired()

    assert removed == 1
    assert store.get("old") is None
    assert store.get("current") is not None


def test_voice_session_store_can_cancel_one_or_all_sessions() -> None:
    store = VoiceSessionStore(clock=lambda: 100.0)
    store.start("first", {})
    store.start("second", {})

    assert store.cancel("first") is True
    assert store.cancel("missing") is False
    assert store.cancel() is True
    assert store.cancel() is False


def test_voice_session_mapping_keeps_legacy_dict_access_thread_safe() -> None:
    store = VoiceSessionStore(clock=lambda: 100.0)
    sessions = VoiceSessionMapping(store)

    sessions["127.0.0.1"] = {
        "stage": "awaiting_name",
        "samples": [],
    }
    fetched = sessions["127.0.0.1"]
    fetched["samples"].append("mutated-copy")

    assert sessions.get("127.0.0.1") == {
        "stage": "awaiting_name",
        "samples": [],
        "created_at": 100.0,
    }
    assert sessions.pop("127.0.0.1", None)["stage"] == "awaiting_name"
    assert len(sessions) == 0


def test_voice_pipeline_legacy_helpers_share_the_session_store() -> None:
    from voice import pipeline

    pipeline.cancel_pending_voice_registration()
    pipeline.set_pending("127.0.0.2", {"stage": "awaiting_sample"})

    assert pipeline.get_pending("127.0.0.2")["stage"] == "awaiting_sample"
    assert "127.0.0.2" in pipeline._PENDING_VOICE_REGISTRATION
    assert pipeline.cancel_pending_voice_registration("127.0.0.2") == 1
