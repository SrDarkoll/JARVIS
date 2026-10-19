from __future__ import annotations

import asyncio
import json

import pytest
from api import chat_routes, status_routes, voice_routes
from services.reminder_service import ReminderService


def test_chat_ignores_client_profile_and_keeps_legacy_web_namespace(monkeypatch):
    async def run():
        import jarvis_backend

        seen = []
        monkeypatch.setattr(chat_routes, "_rate_limit_response", lambda *_args: None)
        monkeypatch.setattr(chat_routes.jarvis_brain, "procesar_mensaje", lambda _text, **kwargs: (seen.append(kwargs["profile_id"]) or "ok", False))
        client = jarvis_backend.app.test_client()
        response = await client.post("/api/chat", json={"message": "hola", "profile_id": "admin"})

        assert response.status_code == 200
        assert seen == ["web_default"]
        assert (await response.get_json())["profile_id"] == "web_default"

    asyncio.run(run())


def test_signed_profile_is_shared_by_chat_and_stream_and_not_other_clients(monkeypatch):
    async def run():
        import jarvis_backend

        seen = []
        monkeypatch.setattr(chat_routes, "_rate_limit_response", lambda *_args: None)
        monkeypatch.setattr(chat_routes.jarvis_brain, "procesar_mensaje", lambda _text, **kwargs: (seen.append(kwargs["profile_id"]) or "ok", False))
        monkeypatch.setattr(chat_routes.jarvis_brain, "stream_procesar_mensaje_events", lambda _text, **kwargs: iter([{"type": "done", "profile_id": seen.append(kwargs["profile_id"]) or kwargs["profile_id"]}]))
        first = jarvis_backend.app.test_client()
        second = jarvis_backend.app.test_client()
        async with first.session_transaction() as browser_session:
            browser_session["profile_id"] = "guest_alex"

        await first.post("/api/chat", json={"message": "hola", "profile_id": "admin"})
        streamed = await first.post("/api/chat/stream", json={"message": "hola", "profile_id": "victim"})
        await streamed.get_data()
        await second.post("/api/chat", json={"message": "hola", "profile_id": "guest_alex"})

        assert seen == ["guest_alex", "guest_alex", "web_default"]

    asyncio.run(run())


def test_reminder_claim_and_ack_cannot_cross_session_profile(monkeypatch, tmp_path):
    async def run():
        import jarvis_backend

        reminders = ReminderService(tmp_path / "isolated-reminders.db")
        monkeypatch.setattr(jarvis_backend.services, "reminder_service", reminders)
        own = reminders.add("guest reminder", 0, "guest_alex")
        other = reminders.add("owner private reminder", 0, "admin")
        client = jarvis_backend.app.test_client()
        async with client.session_transaction() as browser_session:
            browser_session["profile_id"] = "guest_alex"

        listed = await client.get("/api/reminders?profile_id=admin")
        assert [item["id"] for item in (await listed.get_json())["reminders"]] == [own.reminder_id]
        claimed = await client.post("/api/reminders/claim", json={"profile_id": "admin"})
        assert [item["id"] for item in (await claimed.get_json())["reminders"]] == [own.reminder_id]
        denied = await client.post(f"/api/reminders/{other.reminder_id}/ack", json={"profile_id": "admin"})
        assert denied.status_code == 404
        acknowledged = await client.post(f"/api/reminders/{own.reminder_id}/ack", json={"profile_id": "admin"})
        assert acknowledged.status_code == 200
        assert reminders.list_active("admin")[0].reminder_id == other.reminder_id

    asyncio.run(run())


def test_server_voice_result_binds_profile_and_ignores_browser_hint(monkeypatch):
    async def run():
        import jarvis_backend

        seen = []

        def identify(_audio, voice_request):
            seen.append(voice_request["client_profile_id"])
            return {"response": "hola", "profile_id": "guest_alex", "identity_source": "biometric_match"}

        monkeypatch.setattr(voice_routes.voice_service, "process_voice", identify)
        client = jarvis_backend.app.test_client()
        response = await client.post("/api/voice", data=b"audio", headers={"Content-Type": "audio/wav", "X-Profile-Id": "admin"})
        assert response.status_code == 200
        profile = await client.get("/api/session/profile")
        assert seen == ["web_default"]
        assert (await profile.get_json())["profile_id"] == "guest_alex"

    asyncio.run(run())


def test_guest_profile_management_cannot_read_or_mutate_other_profiles(monkeypatch):
    async def run():
        import jarvis_backend

        monkeypatch.setattr(status_routes, "_profile_memory", {"admin": {"facts": "private"}, "guest_alex": {"facts": "own"}})
        client = jarvis_backend.app.test_client()
        async with client.session_transaction() as browser_session:
            browser_session["profile_id"] = "guest_alex"
        response = await client.get("/api/profiles")
        assert set((await response.get_json())["profiles"]) == {"guest_alex"}
        for method in (client.get, client.patch, client.delete):
            denied = await method("/api/profiles/admin")
            assert denied.status_code == 403
        for path in ("/api/observability", "/api/security", "/api/plugins"):
            denied = await client.get(path)
            assert denied.status_code == 403

    asyncio.run(run())


def test_live_session_uses_bound_profile_not_query_profile(monkeypatch):
    async def run():
        import jarvis_backend
        from voice.live_hybrid import HybridLiveStreamer

        seen = []

        async def ready(streamer):
            seen.append(streamer.session.profile_id)
            await streamer.session.emit_json({"type": "session_ready", "mode": "hybrid_stream"})

        monkeypatch.setattr(HybridLiveStreamer, "start", ready)
        client = jarvis_backend.app.test_client()
        async with client.session_transaction() as browser_session:
            browser_session["profile_id"] = "guest_alex"
        async with client.websocket("/api/voice/stream", query_string={"mode": "hybrid", "profile_id": "admin"}, headers={"Origin": "http://127.0.0.1:5002"}) as websocket:
            await asyncio.wait_for(websocket.receive(), timeout=1)
            await websocket.send(json.dumps({"type": "stop"}))
        assert seen == ["guest_alex"]

    asyncio.run(run())


def test_quick_actions_keep_profile_context_and_guest_cannot_toggle_security(monkeypatch):
    async def run():
        import jarvis_backend
        from api import security_routes
        from core import jarvis_state

        seen = []
        monkeypatch.setattr(security_routes, "_ejecutar_accion_control", lambda _action: seen.append(jarvis_state.get_active_profile_id()) or "ok")
        client = jarvis_backend.app.test_client()
        async with client.session_transaction() as browser_session:
            browser_session["profile_id"] = "guest_alex"
        action = await client.post("/api/control/quick", json={"action": "rutina_trabajo"})
        denied = await client.post("/api/control/quick", json={"action": "security_strict_toggle"})

        assert action.status_code == 200
        assert seen == ["guest_alex"]
        assert denied.status_code == 403
        assert jarvis_state.get_active_profile_id() != "guest_alex"

    asyncio.run(run())


@pytest.mark.parametrize("test_mode", [False, True])
def test_real_chat_pipeline_without_model_reports_unconfigured_not_success(monkeypatch, test_mode):
    async def run():
        import jarvis_backend
        from core.brain import brain_state, processor

        monkeypatch.setattr(chat_routes, "_rate_limit_response", lambda *_args: None)
        monkeypatch.setattr(brain_state, "llm", None)
        monkeypatch.setattr(brain_state, "llm_with_tools", None)
        monkeypatch.setattr(brain_state, "llm_fallback", None)
        monkeypatch.setattr(processor, "_llm_calls_disabled_for_tests", lambda: test_mode)
        monkeypatch.delenv("JARVIS_LEGACY_COMMAND_PIPELINE", raising=False)
        client = jarvis_backend.app.test_client()
        message = "Explica detalladamente la combustion de una estrella"

        response = await client.post("/api/chat", json={"message": message})
        assert response.status_code == 503
        assert (await response.get_json())["error"] == "llm_unconfigured"

        streamed = await client.post("/api/chat/stream", json={"message": message})
        body = await streamed.get_data(as_text=True)
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        assert any(event.get("code") == "llm_unconfigured" for event in events)
        assert not any(event.get("type") == "done" for event in events)
        assert "Brain not initialized" not in body

    asyncio.run(run())


@pytest.mark.parametrize(("message", "language"), [("hola", "es"), ("¿Quién eres?", "es"), ("hello", "en"), ("who are you?", "en")])
def test_local_greeting_and_identity_are_available_without_ai_model(monkeypatch, message, language):
    async def run():
        import jarvis_backend
        from core.brain import brain_state, social_engine

        monkeypatch.setattr(chat_routes, "_rate_limit_response", lambda *_args: None)
        monkeypatch.setattr(brain_state, "llm", None)
        monkeypatch.setattr(brain_state, "llm_with_tools", None)
        monkeypatch.setattr(social_engine, "get_current_language", lambda: language)
        client = jarvis_backend.app.test_client()
        response = await client.post("/api/chat", json={"message": message})

        assert response.status_code == 200
        reply = (await response.get_json())["response"]
        assert reply and "Brain not initialized" not in reply

    asyncio.run(run())


def test_social_greeting_does_not_hide_an_unhandled_ai_request(monkeypatch):
    async def run():
        import jarvis_backend
        from core.brain import brain_state, social_engine

        monkeypatch.setattr(chat_routes, "_rate_limit_response", lambda *_args: None)
        monkeypatch.setattr(brain_state, "llm", None)
        monkeypatch.setattr(brain_state, "llm_with_tools", None)
        monkeypatch.setattr(social_engine, "get_current_language", lambda: "en")
        client = jarvis_backend.app.test_client()
        response = await client.post("/api/chat", json={"message": "hello write a poem"})
        assert response.status_code == 503
        assert (await response.get_json())["error"] == "llm_unconfigured"

    asyncio.run(run())
