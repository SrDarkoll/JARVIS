from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from core.jarvis_config import resolve_live_voice_config
from voice.live_gemini import GeminiLiveStreamer
from voice.live_session import LiveSession, LiveSessionState


def session_with_events():
    events = []
    session = LiveSession("turn-stability", lambda text: events.append(json.loads(text)), lambda _data: None)
    return session, events


def test_generation_complete_finalizes_text_before_delayed_turn_complete(monkeypatch):
    async def run():
        session, events = session_with_events()
        streamer = GeminiLiveStreamer(session, api_key="fake")
        saved = []
        monkeypatch.setattr(streamer, "_persist_live_transcript", lambda *entry: saved.append(entry))
        await streamer._handle_downstream_message({"serverContent": {"outputTranscription": {"text": "Una variable guarda un valor."}}})
        await streamer._handle_downstream_message({"serverContent": {"generationComplete": True}})
        final = [event for event in events if event.get("role") == "assistant" and event.get("is_final")]
        assert [event["text"] for event in final] == ["Una variable guarda un valor."]
        await streamer._handle_downstream_message({"serverContent": {"turnComplete": True}})
        final = [event for event in events if event.get("role") == "assistant" and event.get("is_final")]
        assert len(final) == 1
        assert saved == [("assistant", "Una variable guarda un valor.")]
        await session.close()
    asyncio.run(run())


def test_waiting_for_input_does_not_leave_the_ui_processing():
    async def run():
        session, events = session_with_events()
        session.state = LiveSessionState.PROCESSING
        streamer = GeminiLiveStreamer(session, api_key="fake")
        await streamer._handle_downstream_message({"serverContent": {"waitingForInput": True}})
        assert session.state is LiveSessionState.LISTENING
        assert any(event.get("type") == "state_change" and event.get("state") == "listening" for event in events)
        await session.close()
    asyncio.run(run())


def test_new_speech_does_not_inherit_last_spotify_authorization():
    async def run():
        session, _events = session_with_events()
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer.accept_user_transcript("pon Killer Queen de Queen")
        await streamer._handle_downstream_message({"voiceActivity": {"type": "ACTIVITY_START"}})
        assert streamer._current_user_text == ""
        assert not streamer._user_text_ready.is_set()
        await session.close()
    asyncio.run(run())


def test_silence_audio_does_not_create_phantom_diagnostic_turns():
    async def run():
        session, _events = session_with_events()
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer._running = True
        class Socket:
            async def send(self, _data):
                streamer._running = False
        streamer._ws = Socket()
        await session.handle_audio_chunk(bytes(640))
        await streamer._upstream_audio_loop()
        assert session.diagnostics.total_turns == 0
        await session.close()
    asyncio.run(run())


def test_function_call_only_is_not_reported_as_assistant_speech():
    async def run():
        session, events = session_with_events()
        streamer = GeminiLiveStreamer(session, api_key="fake")
        await streamer._handle_downstream_message({"serverContent": {"modelTurn": {"parts": [
            {"functionCall": {"id": "unknown", "name": "unavailable", "args": {}}},
        ]}}})
        assert session.state is not LiveSessionState.SPEAKING
        assert not streamer.response_watchdog.awaiting_response
        assert not any(event.get("state") == "speaking" for event in events)
        await session.close()
    asyncio.run(run())


def test_interruption_preserves_current_speech_detection():
    async def run():
        session, _events = session_with_events()
        streamer = GeminiLiveStreamer(session, api_key="fake")
        await streamer._handle_downstream_message({"voiceActivity": {"type": "ACTIVITY_START"}})
        await streamer._handle_downstream_message({"serverContent": {"interrupted": True}})
        assert streamer.response_watchdog.speech_active
        await session.close()
    asyncio.run(run())


def test_late_previous_turn_completion_preserves_new_user_input(monkeypatch):
    async def run():
        session, _events = session_with_events()
        streamer = GeminiLiveStreamer(session, api_key="fake")
        monkeypatch.setattr(streamer, "_persist_live_transcript", lambda *_args: None)
        await streamer._handle_downstream_message({"serverContent": {"outputTranscription": {"text": "Hola."}}})
        await streamer._handle_downstream_message({"serverContent": {"generationComplete": True}})
        await streamer._handle_downstream_message({"voiceActivity": {"type": "ACTIVITY_START"}})
        streamer.accept_user_transcript("ahora explica una variable")
        await streamer._handle_downstream_message({"serverContent": {"turnComplete": True}})
        assert streamer._current_user_text == "ahora explica una variable"
        assert streamer.response_watchdog.awaiting_response
        assert streamer.response_watchdog.speech_active
        await session.close()
    asyncio.run(run())


def test_stalled_turn_reconnects_without_replaying_audio_or_actions(monkeypatch):
    async def run():
        events, sockets, invoked = [], [], []
        restored = asyncio.Event()

        async def send_text(payload):
            event = json.loads(payload)
            events.append(event)
            if event.get("type") == "session_ready":
                if event.get("connection_restored"):
                    restored.set()
                else:
                    await session.handle_audio_chunk(bytes(640))

        class Socket:
            def __init__(self, messages):
                self.messages = asyncio.Queue()
                self.sent = []
                for message in messages:
                    self.messages.put_nowait(json.dumps(message))

            async def send(self, payload):
                self.sent.append(json.loads(payload))

            async def recv(self):
                return await self.messages.get()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        def connect(_uri):
            messages = [{"setupComplete": {}}]
            if not sockets:
                messages.extend([
                    {"voiceActivity": {"type": "ACTIVITY_START"}},
                    {"serverContent": {"inputTranscription": {"text": "qué clima hace"}}},
                    {"voiceActivity": {"type": "ACTIVITY_END"}},
                    {"toolCall": {"functionCalls": [{"id": "once", "name": "obtener_clima", "args": {}}]}},
                ])
            socket = Socket(messages)
            sockets.append(socket)
            return socket

        async def execute_once(calls, **_kwargs):
            invoked.extend(call["id"] for call in calls)

        session = LiveSession("timeout-reconnect", send_text, lambda _data: None)
        config = replace(resolve_live_voice_config({}), response_timeout_seconds=0.01,
                         reconnect_base_seconds=0.001, reconnect_max_seconds=0.001)
        streamer = GeminiLiveStreamer(session, api_key="fake", config=config, connect_factory=connect)
        monkeypatch.setattr(streamer, "_get_function_declarations", lambda: [{"name": "obtener_clima"}])
        monkeypatch.setattr(streamer, "_build_live_system_instruction", lambda *_args: "Synthetic QA")
        monkeypatch.setattr(streamer, "_persist_live_transcript", lambda *_args: None)
        monkeypatch.setattr(streamer, "_execute_orchestrated_plan", execute_once)
        task = asyncio.create_task(streamer.start())
        try:
            await asyncio.wait_for(restored.wait(), timeout=3)
            assert len(sockets) == 2
            assert invoked == ["once"]
            assert any("realtimeInput" in message for message in sockets[0].sent)
            assert all("realtimeInput" not in message and "toolResponse" not in message for message in sockets[1].sent)
            assert any(event.get("type") == "session_reconnecting" and
                       event.get("code") == "gemini_live_turn_timeout" for event in events)
            assert not streamer.response_watchdog.awaiting_response
        finally:
            streamer._running = False
            await session.close()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert all(task.done() for task in session._transport_tasks)
    asyncio.run(run())


def test_response_timeout_configuration_is_bounded():
    default = resolve_live_voice_config({})
    small = resolve_live_voice_config({"JARVIS_LIVE_RESPONSE_TIMEOUT_SECONDS": "-10"})
    large = resolve_live_voice_config({"JARVIS_LIVE_RESPONSE_TIMEOUT_SECONDS": "99999"})
    assert default.response_timeout_seconds == 20
    assert small.response_timeout_seconds == 5
    assert large.response_timeout_seconds == 90


def test_default_playback_buffer_accepts_bursts_but_remains_bounded():
    assert resolve_live_voice_config({}).max_playback_queue_ms == 15000
    assert resolve_live_voice_config({"JARVIS_LIVE_PLAYBACK_QUEUE_MS": "999999"}).max_playback_queue_ms == 30000
    assert resolve_live_voice_config({"JARVIS_LIVE_PLAYBACK_QUEUE_MS": "1500"}).max_playback_queue_ms == 1500


def test_watchdog_never_times_out_idle_or_ongoing_speech():
    from voice.live_transport import LiveResponseWatchdog
    now = [0.0]
    watchdog = LiveResponseWatchdog(timeout_seconds=5, clock=lambda: now[0])
    now[0] = 100
    assert not watchdog.expired()
    watchdog.speech_started()
    watchdog.input_received()
    now[0] = 200
    assert not watchdog.expired()
    watchdog.speech_ended()
    now[0] = 204.9
    assert not watchdog.expired()
    now[0] = 205
    assert watchdog.expired()


def test_watchdog_progress_and_tool_work_get_a_fresh_response_budget():
    from voice.live_transport import LiveResponseWatchdog
    now = [0.0]
    watchdog = LiveResponseWatchdog(timeout_seconds=5, clock=lambda: now[0])
    watchdog.input_received()
    now[0] = 4
    watchdog.output_received()
    now[0] = 8
    assert not watchdog.expired()
    now[0] = 50
    assert not watchdog.expired(busy_tools=True)
    now[0] = 60
    assert not watchdog.expired(busy_tools=False)
    now[0] = 64.9
    assert not watchdog.expired()
    now[0] = 65
    assert watchdog.expired()


@pytest.mark.parametrize("terminal", ["generation_complete", "waiting_for_input", "reset"])
def test_watchdog_stops_when_no_more_generation_is_expected(terminal):
    from voice.live_transport import LiveResponseWatchdog
    now = [0.0]
    watchdog = LiveResponseWatchdog(timeout_seconds=5, clock=lambda: now[0])
    watchdog.input_received()
    getattr(watchdog, terminal)()
    now[0] = 100
    assert not watchdog.expired()
