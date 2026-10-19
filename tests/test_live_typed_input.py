import asyncio
import json

import pytest
from voice.live_context import resolve_live_language
from voice.live_gemini import GeminiLiveStreamer
from voice.live_session import LiveSession


def test_native_text_is_one_real_provider_turn_and_survives_interrupt_ack(monkeypatch):
    async def run():
        events, sent, persisted = [], [], []
        class Socket:
            async def send(self, payload):
                sent.append(json.loads(payload))
        session = LiveSession("typed", lambda text: events.append(json.loads(text)), lambda _: None)
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer._ws, streamer._running = Socket(), True
        await session.set_provider_state("ready", active_mode="gemini_live")
        monkeypatch.setattr(streamer, "_persist_live_transcript", lambda *args: persisted.append(args))
        streamer.accept_user_transcript("abre la carpeta")
        previous_turn = session.orchestrator.task_context.current_turn

        assert await streamer.send_user_text("Iamramidev") is True
        assert sent == [{"clientContent": {"turns": [{"role": "user", "parts": [{"text": "Iamramidev"}]}], "turnComplete": True}}]
        assert session.orchestrator.task_context.current_turn == previous_turn + 1
        typed_turn = streamer._task_input_turn
        await streamer._handle_downstream_message({"serverContent": {"turnComplete": True}})
        assert streamer._current_user_text == "Iamramidev"
        await streamer._handle_downstream_message({"serverContent": {"interrupted": True}})
        assert streamer._current_user_text == "Iamramidev"
        assert streamer._task_input_turn == typed_turn
        assert persisted == [("user", "Iamramidev")]
        assert len([event for event in events if event.get("source") == "live_typed_text"]) == 1
        await session.close()
    asyncio.run(run())


def test_language_preference_persists_across_subsequent_native_turns():
    async def run():
        session = LiveSession("language-preference", lambda _: None, lambda _: None, language="es")
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer.accept_user_transcript("Respond in English from now on")
        assert session.language == "en"
        streamer._reset_turn_transcripts()
        streamer.accept_user_transcript("hola")
        assert session.language == "en"
        await session.close()
    asyncio.run(run())


@pytest.mark.parametrize("text", ["", " " * 3, {"bad": "shape"}, "x" * 41])
def test_invalid_native_text_has_no_turn_or_provider_effect(text):
    async def run():
        session = LiveSession("invalid-typed", lambda _: None, lambda _: None, max_user_text_chars=40)
        streamer = GeminiLiveStreamer(session, api_key="fake")
        assert await streamer.send_user_text(text) is False
        assert session.orchestrator.task_context.current_turn == 0
        await session.close()
    asyncio.run(run())


def test_reconnecting_provider_does_not_accept_or_replay_typed_input():
    async def run():
        session = LiveSession("not-ready", lambda _: None, lambda _: None)
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer._running = True
        await session.set_provider_state("reconnecting", active_mode="gemini_live")
        assert await streamer.send_user_text("otra vez") is False
        assert session.orchestrator.task_context.current_turn == 0
        await session.close()
    asyncio.run(run())


@pytest.mark.parametrize("text", ["Please Please Please", "hello", "what is this", "नमस्ते", "pon Englishman in New York"])
def test_incidental_language_does_not_change_the_conversational_preference(text):
    assert resolve_live_language(text, "es") == "es"


@pytest.mark.parametrize(("text", "default", "expected"), [
    ("Respóndeme en inglés, por favor", "es", "en"),
    ("Háblame en español", "en", "es"),
    ("Please respond in English from now on", "es", "en"),
    ("No cambies a inglés", "es", "es"),
    ("La canción se llama Speak Spanish", "en", "en"),
    ("Traduce 'answer in English' al español", "es", "es"),
])
def test_only_explicit_language_preferences_switch_language(text, default, expected):
    assert resolve_live_language(text, default) == expected


def test_native_route_delivers_explicit_text_even_when_provider_transcription_enabled(monkeypatch):
    async def run():
        import jarvis_backend
        from api import voice_routes
        delivered = []
        async def start(streamer):
            await streamer.session.set_provider_state("ready", active_mode="gemini_live")
            await streamer.session.emit_json({"type": "session_ready"})
            await asyncio.Event().wait()
        async def send(streamer, text):
            delivered.append((streamer.session.session_id, text))
            await streamer.session.emit_json({"type": "typed_probe"})
            return True
        monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "fake")
        monkeypatch.setenv("JARVIS_GEMINI_LIVE_INPUT_TRANSCRIPTION", "true")
        monkeypatch.setattr(voice_routes, "_gemini_live_dependency_available", lambda: True)
        monkeypatch.setattr(GeminiLiveStreamer, "start", start)
        monkeypatch.setattr(GeminiLiveStreamer, "send_user_text", send)
        async with jarvis_backend.app.test_client().websocket(
            "/api/voice/stream", query_string={"mode": "gemini_live"},
            headers={"Origin": "http://127.0.0.1:5002"},
        ) as websocket:
            while json.loads(await asyncio.wait_for(websocket.receive(), 2)).get("type") != "session_ready":
                pass
            await websocket.send(json.dumps({"type": "user_text", "text": "Iamramidev"}))
            while json.loads(await asyncio.wait_for(websocket.receive(), 2)).get("type") != "typed_probe":
                pass
            await websocket.send(json.dumps({"type": "stop"}))
        assert len(delivered) == 1
        assert delivered[0][1] == "Iamramidev"
    asyncio.run(run())
