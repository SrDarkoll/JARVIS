from __future__ import annotations

import asyncio
import json

from api.voice_routes import (
    LiveModeSelection,
    build_live_voice_status,
    select_live_mode,
)
from voice.live_transport import LiveTransportError


def test_explicit_gemini_never_silently_falls_back():
    selection = select_live_mode(
        requested_mode="gemini_live",
        gemini_configured=False,
        gemini_dependency_available=True,
        hybrid_available=True,
    )

    assert selection == LiveModeSelection(
        mode="unavailable",
        code="gemini_live_unconfigured",
    )


def test_auto_uses_hybrid_when_gemini_dependency_is_missing():
    selection = select_live_mode(
        requested_mode="auto",
        gemini_configured=True,
        gemini_dependency_available=False,
        hybrid_available=True,
    )

    assert selection == LiveModeSelection(
        mode="hybrid",
        code="gemini_live_dependency_missing",
    )


def test_auto_prefers_ready_gemini():
    selection = select_live_mode(
        requested_mode="auto",
        gemini_configured=True,
        gemini_dependency_available=True,
        hybrid_available=True,
    )

    assert selection == LiveModeSelection(mode="gemini_live")


def test_hybrid_request_never_contacts_gemini():
    selection = select_live_mode(
        requested_mode="hybrid",
        gemini_configured=True,
        gemini_dependency_available=True,
        hybrid_available=True,
    )

    assert selection == LiveModeSelection(mode="hybrid")


def test_no_provider_available_returns_controlled_unavailable():
    selection = select_live_mode(
        requested_mode="auto",
        gemini_configured=False,
        gemini_dependency_available=False,
        hybrid_available=False,
    )

    assert selection.mode == "unavailable"
    assert selection.code == "live_voice_unavailable"


def test_live_status_separates_configuration_dependency_and_handshake():
    status = build_live_voice_status(
        gemini_configured=True,
        dependency_available=True,
        hybrid_available=True,
        requested_mode="auto",
        active_mode="",
        handshake_state="idle",
    )

    assert status["state"] == "available"
    assert status["configured"] is True
    assert status["dependency_available"] is True
    assert status["handshake_state"] == "idle"
    assert status["runtime_ready"] is False
    assert status["provider_preview"] is True


def test_live_status_reports_controlled_hybrid_degradation():
    status = build_live_voice_status(
        gemini_configured=True,
        dependency_available=False,
        hybrid_available=True,
        requested_mode="auto",
        active_mode="hybrid_stream",
        handshake_state="ready",
        last_failure_code="gemini_live_dependency_missing",
    )

    assert status["state"] == "degraded"
    assert status["active_mode"] == "hybrid_stream"
    assert status["fallback_available"] is True
    assert status["runtime_ready"] is True
    assert status["code"] == "gemini_live_dependency_missing"


def test_runtime_capabilities_publish_live_voice_as_equal_pillar(monkeypatch):
    from api import status_routes

    monkeypatch.setenv("GEMINI_API_KEY", "configured-test-key")
    capabilities = status_routes._runtime_capabilities(
        monitoring={"configured": False, "available": False, "running": False},
        speech_to_text={
            "provider": "auto",
            "groq_configured": False,
            "local_enabled": True,
            "local_state": "not_loaded",
        },
        admin_voice_profiles=0,
    )

    assert capabilities["live_voice"]["state"] == "available"
    assert capabilities["live_voice"]["code"] in {
        "gemini_live_configured",
        "gemini_live_ready",
    }
    assert "session_resumption=disabled" in capabilities["live_voice"]["detail"]


def test_auto_mode_activates_hybrid_when_gemini_task_fails(monkeypatch):
    async def run():
        import jarvis_backend
        from voice import live_gemini

        async def fail_provider(_self):
            raise LiveTransportError("gemini_live_setup_timeout")

        monkeypatch.setenv("GEMINI_API_KEY", "configured-test-key")
        monkeypatch.setattr(live_gemini.GeminiLiveStreamer, "start", fail_provider)
        client = jarvis_backend.app.test_client()

        async with client.websocket(
            "/api/voice/stream",
            query_string={"lang": "es", "mode": "auto"},
            headers={"Origin": "http://127.0.0.1:5002"},
        ) as websocket:
            events = []
            for _ in range(5):
                event = json.loads(
                    await asyncio.wait_for(websocket.receive(), timeout=0.5)
                )
                events.append(event)
                if event.get("type") == "live_fallback_active":
                    break

            assert any(
                event.get("type") == "session_ready"
                and event.get("mode") == "hybrid_stream"
                for event in events
            )
            fallback = next(
                event
                for event in events
                if event.get("type") == "live_fallback_active"
            )
            assert fallback["code"] == "gemini_live_setup_timeout"
            await websocket.send(json.dumps({"type": "stop"}))

    asyncio.run(run())


def test_explicit_gemini_failure_does_not_activate_hybrid(monkeypatch):
    async def run():
        import jarvis_backend
        from voice import live_gemini

        async def fail_provider(_self):
            raise LiveTransportError("gemini_live_setup_rejected")

        monkeypatch.setenv("GEMINI_API_KEY", "configured-test-key")
        monkeypatch.setattr(live_gemini.GeminiLiveStreamer, "start", fail_provider)
        client = jarvis_backend.app.test_client()

        async with client.websocket(
            "/api/voice/stream",
            query_string={"lang": "es", "mode": "gemini_live"},
            headers={"Origin": "http://127.0.0.1:5002"},
        ) as websocket:
            event = json.loads(
                await asyncio.wait_for(websocket.receive(), timeout=0.5)
            )
            assert event["type"] == "error"
            assert event["code"] == "gemini_live_setup_rejected"
            assert event.get("mode") != "hybrid_stream"

    asyncio.run(run())
