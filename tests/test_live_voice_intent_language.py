from __future__ import annotations

import asyncio
import json

import pytest
from core.brain import router, tool_manager
from core.command_pipeline.models import CommandRequest
from voice.live_context import LiveContextPolicy, build_live_context
from voice.live_gemini import GeminiLiveStreamer
from voice.live_session import LiveSession


@pytest.mark.parametrize("text", [
    "es que tengo una duda", "solo quiero hablar contigo", "me refiero al idioma",
    "solo quiero que me expliques esto", "can you show me an example", "show me your capabilities",
    "can you open the settings", "quiero que me respondas en español", "no quiero que pongas música",
    "no reproduzcas nada en Spotify", "pon un ejemplo en español", "pon atención a lo que te digo",
    "reproduce el error del programa", "play chess with me",
    "te explico que puedes poner música", "el mensaje dice can you play a song",
    "Jarvis, no quiero que pongas música",
])
def test_conversation_is_not_a_music_request(text):
    assert router._extract_music_request(text) == ""
    request = CommandRequest.create(text=text, profile_id="admin", channel="voice", language="es")
    plan = router.plan_hybrid(request)
    assert plan is None or not any(step.tool_name in {"reproducir_en_spotify", "reproducir_mix_spotify"} for step in plan.steps)


@pytest.mark.parametrize("text", ["pon Killer Queen de Queen", "reproduce Dreams de Fleetwood Mac", "can you play Billie Jean by Michael Jackson"])
def test_explicit_music_commands_remain_supported(text):
    assert router._extract_music_request(text)


def test_explicit_song_title_is_not_confused_with_a_programming_command():
    assert router._extract_music_request("pon la canción Error de The Warning") == "error de the warning"


def test_live_setup_explicitly_configures_complete_automatic_speech_turns(monkeypatch):
    async def run():
        messages = []
        class Socket:
            async def send(self, payload):
                messages.append(json.loads(payload))
        session = LiveSession("vad-config", lambda _text: None, lambda _data: None)
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer._ws = Socket()
        monkeypatch.setattr(streamer, "_get_function_declarations", lambda: [])
        monkeypatch.setattr(streamer, "_build_live_system_instruction", lambda *_args: "Español")
        await streamer._send_setup()
        detection = messages[0]["setup"]["realtimeInputConfig"]["automaticActivityDetection"]
        assert detection["disabled"] is False
        assert detection["silenceDurationMs"] == 700
        assert detection["prefixPaddingMs"] > 0
        assert detection["endOfSpeechSensitivity"] == "END_SENSITIVITY_LOW"
        assert "languageCode" not in messages[0]["setup"]["generationConfig"]["speechConfig"]
        await session.close()
    asyncio.run(run())


@pytest.mark.parametrize("source", ["gemini_live", "live_voice", "planner"])
def test_live_music_guard_requires_the_real_music_request(source):
    assert not tool_manager._tool_permitida_por_contexto("reproducir_en_spotify", "solo quiero hablar contigo", source)
    assert not tool_manager._tool_permitida_por_contexto("reproducir_en_spotify", "Live voice command: reproducir_en_spotify", source)
    assert tool_manager._tool_permitida_por_contexto("reproducir_en_spotify", "pon Killer Queen de Queen", source)


def test_language_directive_survives_a_long_custom_instruction():
    context = build_live_context(base_instruction="x" * 10000, language="es", profile_name="User",
                                 profile_facts="", history=(), allowed_tool_names=(),
                                 policy=LiveContextPolicy(max_chars=1000))
    assert "ESPAÑOL" in context


def test_memory_failure_does_not_drop_live_language_instruction(monkeypatch):
    from services.memory_manager import memory_manager
    monkeypatch.setattr(memory_manager, "snapshot", lambda _pid: (_ for _ in ()).throw(RuntimeError("offline")))
    session = LiveSession("language-fallback", lambda _text: None, lambda _data: None, language="es")
    streamer = GeminiLiveStreamer(session, api_key="fake")

    assert "ESPAÑOL" in streamer._build_live_system_instruction()


def test_live_action_captures_original_text_and_session_language(monkeypatch):
    from utils.jarvis_i18n import get_current_language
    captured = {}
    def execute(*args):
        captured.update(text=args[2], language=get_current_language())
        return "ok"
    monkeypatch.setattr(tool_manager, "execute_tool_receipt", execute)

    async def run():
        session = LiveSession("language-action", lambda _text: None, lambda _data: None, language="es")
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer._running = True
        streamer._current_user_text = "pon Killer Queen de Queen"
        await streamer._execute_orchestrated_plan([{"name": "reproducir_en_spotify", "args": {"cancion": "Killer Queen"}, "id": "call-1"}])
        await session.close()
    asyncio.run(run())
    assert captured == {"text": "pon Killer Queen de Queen", "language": "es"}


def test_same_frame_transcript_is_available_before_tool_execution(monkeypatch):
    async def run():
        session = LiveSession("same-frame", lambda _text: None, lambda _data: None)
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer._running = True
        streamer._declared_tool_names = {"reproducir_en_spotify"}
        monkeypatch.setattr(streamer, "_persist_live_transcript", lambda *_args: None)
        captured = []
        async def execute(calls, **kwargs):
            captured.append(kwargs.get("user_text", streamer._current_user_text))
        monkeypatch.setattr(streamer, "_execute_orchestrated_plan", execute)
        await streamer._handle_downstream_message({
            "serverContent": {"inputTranscription": {"text": "pon Killer Queen de Queen"}},
            "toolCall": {"functionCalls": [{"id": "call-1", "name": "reproducir_en_spotify", "args": {"cancion": "Killer Queen"}}]},
        })
        await asyncio.sleep(0)
        assert captured == ["pon Killer Queen de Queen"]
        await session.close()
    asyncio.run(run())


def test_incremental_user_transcripts_keep_the_full_request(monkeypatch):
    async def run():
        emitted = []
        session = LiveSession("input-chunks", lambda text: emitted.append(json.loads(text)), lambda _data: None)
        streamer = GeminiLiveStreamer(session, api_key="fake")
        monkeypatch.setattr(streamer, "_persist_live_transcript", lambda *_args: None)
        for part in ("pon", "Killer Queen", "de Queen"):
            await streamer._handle_downstream_message({"serverContent": {"inputTranscription": {"text": part}}})
        assert streamer._current_user_text == "pon Killer Queen de Queen"
        await session.close()
    asyncio.run(run())


@pytest.mark.parametrize("text", ["no pongas un mix de Queen", "me gusta el mix de Queen", "explícame la playlist de Queen"])
def test_mentioning_or_negating_a_mix_does_not_start_it(text):
    assert router._extract_spotify_mix_request(text) == ""
    assert not tool_manager._tool_permitida_por_contexto("reproducir_mix_spotify", text, "gemini_live")


def test_late_transcript_is_awaited_without_executing_anonymous_music(monkeypatch):
    async def run():
        captured = []
        session = LiveSession("late-input", lambda _text: None, lambda _data: None)
        session.orchestrator._invoke_tool = lambda _name, _args, text, *_rest: captured.append(text) or "ok"
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer._running = True
        streamer._declared_tool_names = {"reproducir_en_spotify"}
        monkeypatch.setattr(streamer, "_persist_live_transcript", lambda *_args: None)
        await streamer._handle_downstream_message({"toolCall": {"functionCalls": [{"id": "call-1", "name": "reproducir_en_spotify", "args": {"cancion": "Killer Queen"}}]}})
        await asyncio.sleep(0)
        assert captured == []
        await streamer._handle_downstream_message({"serverContent": {"inputTranscription": {"text": "pon Killer Queen de Queen"}}})
        for _attempt in range(30):
            if captured:
                break
            await asyncio.sleep(0.01)
        assert captured == ["pon Killer Queen de Queen"]
        await session.close()
    asyncio.run(run())


def test_barge_in_keeps_new_transcript_but_drops_old_turn(monkeypatch):
    async def run():
        session = LiveSession("barge-language", lambda _text: None, lambda _data: None)
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer._current_user_text = "pon Killer Queen"
        monkeypatch.setattr(streamer, "_persist_live_transcript", lambda *_args: None)
        await streamer._handle_downstream_message({"serverContent": {"interrupted": True, "inputTranscription": {"text": "no, solo quiero hablar"}}})
        assert streamer._current_user_text == "no, solo quiero hablar"
        await session.close()
    asyncio.run(run())


def test_language_scope_isolated_across_workers_and_restored():
    from utils.jarvis_i18n import get_current_language, language_scope
    original = get_current_language()
    async def run():
        async def worker(lang):
            with language_scope(lang):
                await asyncio.sleep(0)
                return await asyncio.to_thread(get_current_language)
        assert await asyncio.gather(worker("es"), worker("en")) == ["es", "en"]
    asyncio.run(run())
    assert get_current_language() == original


def test_same_frame_turn_complete_does_not_lose_captured_tool_request(monkeypatch):
    async def run():
        captured = []
        session = LiveSession("combined-final", lambda _text: None, lambda _data: None)
        session.orchestrator._invoke_tool = lambda _name, _args, text, *_rest: captured.append(text) or "ok"
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer._running = True
        streamer._declared_tool_names = {"reproducir_en_spotify"}
        monkeypatch.setattr(streamer, "_persist_live_transcript", lambda *_args: None)
        await streamer._handle_downstream_message({
            "toolCall": {"functionCalls": [{"id": "call-1", "name": "reproducir_en_spotify", "args": {"cancion": "Killer Queen"}}]},
            "serverContent": {"inputTranscription": {"text": "pon Killer Queen de Queen"}, "turnComplete": True},
        })
        for _attempt in range(30):
            if captured:
                break
            await asyncio.sleep(0.01)
        assert captured == ["pon Killer Queen de Queen"]
        await session.close()
    asyncio.run(run())


def test_hybrid_fallback_keeps_configured_language_until_explicit_preference(monkeypatch):
    from types import SimpleNamespace
    from utils.jarvis_i18n import get_current_language
    from voice.live_hybrid import HybridLiveStreamer
    captured = {}
    class Orchestrator:
        def process(self, request):
            captured.update(language=request.language, scoped_language=get_current_language())
            return SimpleNamespace(text="Una variable guarda un valor.")
    async def run():
        session = LiveSession("hybrid-es", lambda _text: None, lambda _data: None, language="en")
        streamer = HybridLiveStreamer(session, command_orchestrator=Orchestrator())
        async def no_audio(_text):
            return None
        monkeypatch.setattr(streamer, "_stream_synthesized_clauses", no_audio)
        await streamer._execute_streaming_turn("Respóndeme en español, explícame qué es una variable")
        await session.close()
    asyncio.run(run())
    assert captured == {"language": "es", "scoped_language": "es"}


@pytest.mark.parametrize("text", ["solo quiero hablar contigo", "es que tengo una duda", "no quiero que pongas música"])
def test_hallucinated_live_playback_never_reaches_the_executor(monkeypatch, text):
    async def run():
        invoked, responses = [], []
        class Socket:
            async def send(self, payload):
                responses.append(json.loads(payload))
        session = LiveSession("hallucinated-music", lambda _text: None, lambda _data: None)
        session.orchestrator._invoke_tool = lambda *_args: invoked.append(True)
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer._ws = Socket()
        streamer._running = True
        streamer._declared_tool_names = {"reproducir_en_spotify"}
        monkeypatch.setattr(streamer, "_persist_live_transcript", lambda *_args: None)
        await streamer._handle_downstream_message({
            "serverContent": {"inputTranscription": {"text": text}},
            "toolCall": {"functionCalls": [{"id": "hallucination-1", "name": "reproducir_en_spotify", "args": {"cancion": "inventada"}}]},
        })
        for _attempt in range(30):
            if responses:
                break
            await asyncio.sleep(0.01)
        assert invoked == []
        response = json.loads(responses[0]["toolResponse"]["functionResponses"][0]["response"]["result"])
        assert response["state"] == "blocked"
        assert response["diagnostic_code"] == "live_music_intent_required"
        assert not response["verified"]
        await session.close()
    asyncio.run(run())
