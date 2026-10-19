from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from core.jarvis_config import resolve_live_voice_config
from voice.live_gemini import GeminiLiveStreamer
from voice.live_session import LiveSession
from voice.live_transport import (
    LiveTransportController,
    LiveTransportError,
    LiveTransportState,
    await_setup_complete,
    reconnect_delay,
    supervise_transport_tasks,
)


class FakeSocket:
    def __init__(self, messages: list[dict | str]):
        self.messages: asyncio.Queue[str] = asyncio.Queue()
        for message in messages:
            payload = message if isinstance(message, str) else json.dumps(message)
            self.messages.put_nowait(payload)

    async def recv(self):
        return await self.messages.get()


def test_setup_complete_is_required_before_ready():
    async def run():
        socket = FakeSocket(
            [
                {"usageMetadata": {"promptTokenCount": 1}},
                {"setupComplete": {}},
            ]
        )

        result = await await_setup_complete(socket, timeout_seconds=0.1)

        assert result.state is LiveTransportState.READY
        assert result.pending_messages == (
            {"usageMetadata": {"promptTokenCount": 1}},
        )

    asyncio.run(run())


def test_setup_timeout_is_controlled():
    async def run():
        with pytest.raises(LiveTransportError, match="gemini_live_setup_timeout"):
            await await_setup_complete(FakeSocket([]), timeout_seconds=0.01)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ({"goAway": {"timeLeft": "3s"}}, "gemini_live_connection_rotating"),
        ({"error": {"message": "secret provider detail"}}, "gemini_live_setup_rejected"),
        ("not-json", "gemini_live_protocol_error"),
    ],
)
def test_setup_failures_use_stable_codes(message, code):
    async def run():
        with pytest.raises(LiveTransportError, match=code):
            await await_setup_complete(
                FakeSocket([message]),
                timeout_seconds=0.1,
            )

    asyncio.run(run())


def test_reconnect_delay_is_bounded():
    assert reconnect_delay(1, base_seconds=0.5, max_seconds=4.0) == 0.5
    assert reconnect_delay(4, base_seconds=0.5, max_seconds=4.0) == 4.0
    assert reconnect_delay(20, base_seconds=0.5, max_seconds=4.0) == 4.0


def test_reconnect_budget_resets_only_after_stability_or_completed_turn():
    now = [100.0]
    controller = LiveTransportController(
        max_reconnect_attempts=2,
        stable_connection_seconds=5.0,
        clock=lambda: now[0],
    )
    controller.mark_connected()

    assert controller.next_reconnect_attempt() == 1
    assert controller.next_reconnect_attempt() == 2
    with pytest.raises(LiveTransportError, match="gemini_live_reconnect_exhausted"):
        controller.next_reconnect_attempt()

    now[0] = 110.0
    controller.mark_connected()
    now[0] = 116.0
    assert controller.next_reconnect_attempt() == 1

    controller.mark_turn_completed()
    assert controller.next_reconnect_attempt() == 1


def test_transport_supervision_cancels_pending_and_propagates_failure():
    async def run():
        cancelled = False

        async def sleeper():
            nonlocal cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled = True
                raise

        async def fail():
            await asyncio.sleep(0)
            raise LiveTransportError("gemini_live_protocol_error")

        with pytest.raises(LiveTransportError, match="gemini_live_protocol_error"):
            await supervise_transport_tasks(
                [asyncio.create_task(sleeper()), asyncio.create_task(fail())]
            )
        assert cancelled is True

    asyncio.run(run())


def test_transport_supervision_treats_normal_loop_exit_as_failure():
    async def run():
        async def finished():
            return None

        with pytest.raises(LiveTransportError, match="gemini_live_connect_failed"):
            await supervise_transport_tasks([asyncio.create_task(finished())])

    asyncio.run(run())


def test_gemini_setup_uses_compression_without_session_resumption(monkeypatch):
    async def run():
        sent: list[dict] = []

        class Socket:
            async def send(self, payload):
                sent.append(json.loads(payload))

        session = LiveSession(
            session_id="setup-contract",
            send_text=lambda _text: None,
            send_bytes=lambda _data: None,
        )
        streamer = GeminiLiveStreamer(session=session, api_key="fake-key")
        streamer._ws = Socket()
        monkeypatch.setattr(streamer, "_get_function_declarations", lambda: [])

        await streamer._send_setup()

        setup = sent[0]["setup"]
        assert setup["model"] == "models/gemini-3.8-live"
        assert setup["generationConfig"]["responseModalities"] == ["AUDIO"]
        assert setup["inputAudioTranscription"] == {}
        assert setup["outputAudioTranscription"] == {}
        assert setup["contextWindowCompression"] == {
            "triggerTokens": 25000,
            "slidingWindow": {"targetTokens": 8000},
        }
        assert "sessionResumption" not in setup

    asyncio.run(run())


@pytest.mark.parametrize("model", ["gemini-3.8-live", "gemini-3.1-flash-live-preview", "gemini-4.0-live-preview"])
def test_setup_preserves_requested_model_and_blocking_tool_execution(monkeypatch, model):
    async def run():
        sent = []

        class Socket:
            async def send(self, payload):
                sent.append(json.loads(payload))

        config = resolve_live_voice_config({"JARVIS_GEMINI_LIVE_MODEL": model})
        session = LiveSession(
            session_id="model-compatibility",
            send_text=lambda _text: None,
            send_bytes=lambda _data: None,
        )
        streamer = GeminiLiveStreamer(session=session, api_key="fake-key", config=config)
        streamer._ws = Socket()
        declaration = {"name": "obtener_clima", "parameters": {"type": "object", "properties": {}}}
        monkeypatch.setattr(streamer, "_get_function_declarations", lambda: [declaration])

        await streamer._send_setup()

        setup = sent[0]["setup"]
        assert setup["model"] == f"models/{model}"
        assert setup["tools"][0]["functionDeclarations"][0]["behavior"] == "BLOCKING"
        assert "thinkingConfig" not in setup["generationConfig"]
        assert "proactivity" not in setup
        assert "enableAffectiveDialog" not in setup
        assert "behavior" not in declaration

    asyncio.run(run())


def test_invalid_live_model_never_opens_provider_transport():
    async def run():
        events = []

        async def send_text(payload):
            events.append(json.loads(payload))

        def connect(_uri):
            pytest.fail("Invalid model configuration must not contact the provider")

        session = LiveSession(
            session_id="invalid-model",
            send_text=send_text,
            send_bytes=lambda _data: None,
        )
        config = resolve_live_voice_config({"JARVIS_GEMINI_LIVE_MODEL": "gemini-3.5-flash-lite"})
        streamer = GeminiLiveStreamer(session=session, api_key="fake-key", config=config, connect_factory=connect)

        await streamer.start()

        assert streamer.is_available() is False
        assert events[-1]["code"] == "gemini_live_model_invalid"

    asyncio.run(run())


def test_gemini_setup_can_disable_transcription_cost(monkeypatch):
    async def run():
        sent: list[dict] = []

        class Socket:
            async def send(self, payload):
                sent.append(json.loads(payload))

        config = resolve_live_voice_config(
            {
                "JARVIS_LIVE_INPUT_TRANSCRIPTION_ENABLED": "false",
                "JARVIS_LIVE_OUTPUT_TRANSCRIPTION_ENABLED": "false",
            }
        )
        session = LiveSession(
            session_id="setup-no-transcription",
            send_text=lambda _text: None,
            send_bytes=lambda _data: None,
        )
        streamer = GeminiLiveStreamer(
            session=session,
            api_key="fake-key",
            config=config,
        )
        streamer._ws = Socket()
        monkeypatch.setattr(streamer, "_get_function_declarations", lambda: [])

        await streamer._send_setup()

        setup = sent[0]["setup"]
        assert "inputAudioTranscription" not in setup
        assert "outputAudioTranscription" not in setup

    asyncio.run(run())


def test_downstream_transcriptions_are_merged_emitted_and_persisted(monkeypatch):
    async def run():
        emitted: list[dict] = []
        persisted: list[tuple[str, str]] = []

        async def send_text(payload):
            emitted.append(json.loads(payload))

        session = LiveSession(
            session_id="transcript-contract",
            send_text=send_text,
            send_bytes=lambda _data: None,
        )
        streamer = GeminiLiveStreamer(session=session, api_key="fake-key")
        streamer._running = True
        monkeypatch.setattr(
            streamer,
            "_persist_live_transcript",
            lambda role, text: persisted.append((role, text)),
            raising=False,
        )

        await streamer._handle_downstream_message(
            {
                "serverContent": {
                    "interimInputTranscription": {"text": "Hola Jar"},
                }
            }
        )
        await streamer._handle_downstream_message(
            {
                "serverContent": {
                    "inputTranscription": {"text": "Hola Jarvis"},
                }
            }
        )
        await streamer._handle_downstream_message(
            {
                "serverContent": {
                    "outputTranscription": {"text": "Hola"},
                }
            }
        )
        await streamer._handle_downstream_message(
            {
                "serverContent": {
                    "outputTranscription": {"text": "Administrador"},
                }
            }
        )
        await streamer._handle_downstream_message(
            {"serverContent": {"turnComplete": True}}
        )

        transcripts = [event for event in emitted if event.get("type") == "transcript"]
        assert transcripts == [
            {
                "type": "transcript",
                "role": "user",
                "text": "Hola Jar",
                "is_final": False,
                "source": "gemini_live",
            },
            {
                "type": "transcript",
                "role": "user",
                "text": "Hola Jarvis",
                "is_final": True,
                "source": "gemini_live",
            },
            {
                "type": "transcript",
                "role": "assistant",
                "text": "Hola",
                "is_final": False,
                "source": "gemini_live",
            },
            {
                "type": "transcript",
                "role": "assistant",
                "text": "Hola Administrador",
                "is_final": False,
                "source": "gemini_live",
            },
            {
                "type": "transcript",
                "role": "assistant",
                "text": "Hola Administrador",
                "is_final": True,
                "source": "gemini_live",
            },
        ]
        assert persisted == [
            ("user", "Hola Jarvis"),
            ("assistant", "Hola Administrador"),
        ]
        await session.close()

    asyncio.run(run())


def test_provider_barge_in_preserves_new_user_audio():
    async def run():
        session = LiveSession(
            session_id="native-barge-in", send_text=lambda _text: None, send_bytes=lambda _data: None,
        )
        streamer = GeminiLiveStreamer(session=session, api_key="fake-key")
        await session.handle_audio_chunk(b"new-phrase-start")
        await session.handle_audio_chunk(b"new-phrase-end")

        await streamer._handle_downstream_message({"serverContent": {"interrupted": True}})

        assert session._input_audio_queue.qsize() == 2
        assert await session.next_audio_chunk() == b"new-phrase-start"
        session.audio_task_done(b"new-phrase-start")
        assert await session.next_audio_chunk() == b"new-phrase-end"
        session.audio_task_done(b"new-phrase-end")
        assert session.input_audio_bytes == 0
        await session.close()

    asyncio.run(run())


def test_tool_response_includes_provider_call_identity():
    async def run():
        sent: list[dict] = []

        class Socket:
            async def send(self, payload):
                sent.append(json.loads(payload))

        session = LiveSession(
            session_id="tool-response-contract",
            send_text=lambda _text: None,
            send_bytes=lambda _data: None,
        )
        streamer = GeminiLiveStreamer(session=session, api_key="fake-key")
        streamer._running = True
        streamer._ws = Socket()
        streamer._tool_call_names["call-1"] = "obtener_clima"

        await streamer._send_tool_response("call-1", "Soleado")

        response = sent[0]["toolResponse"]["functionResponses"][0]
        assert response == {
            "id": "call-1",
            "name": "obtener_clima",
            "response": {"result": "Soleado"},
        }
        await session.close()

    asyncio.run(run())


def test_provider_tool_cancellation_targets_only_matching_call():
    async def run():
        emitted: list[dict] = []

        async def send_text(payload):
            emitted.append(json.loads(payload))

        session = LiveSession(
            session_id="tool-cancel-contract",
            send_text=send_text,
            send_bytes=lambda _data: None,
        )
        streamer = GeminiLiveStreamer(session=session, api_key="fake-key")
        plan = session.orchestrator.enqueue_batch(
            [
                {"id": "keep", "name": "obtener_clima", "args": {}},
                {"id": "cancel", "name": "ajustar_volumen", "args": {}},
            ]
        )

        await streamer._handle_downstream_message(
            {"toolCallCancellation": {"ids": ["cancel"]}}
        )

        assert plan.actions[0].state.value == "pending"
        assert plan.actions[1].state.value == "cancelled"
        cancellation = next(
            event for event in emitted if event.get("type") == "action_cancelled"
        )
        assert cancellation["call_ids"] == ["cancel"]
        assert cancellation["actions"][0]["call_id"] == "cancel"
        await session.close()

    asyncio.run(run())


def test_live_tool_declarations_exclude_critical_and_experimental_surface():
    from core.security.tool_policy import get_tool_policy

    session = LiveSession(
        session_id="tool-surface-contract",
        send_text=lambda _text: None,
        send_bytes=lambda _data: None,
    )
    streamer = GeminiLiveStreamer(session=session, api_key="fake-key")

    names = {declaration["name"] for declaration in streamer._get_function_declarations()}

    assert "reproducir_en_spotify" in names
    assert "poner_recordatorio" in names
    assert "obtener_clima" in names
    assert "computer_click" not in names
    assert "borrar_memoria" not in names
    assert "analizar_pantalla" not in names
    assert "gestionar_tarea_actual" in names
    assert get_tool_policy("gestionar_tarea_actual").risk_level == "unclassified"
    assert all(
        get_tool_policy(name).risk_level in {"public", "elevated"}
        for name in names - {"gestionar_tarea_actual"}
    )


def test_incomplete_transcript_state_can_be_cleared_before_reconnect():
    session = LiveSession(
        session_id="transcript-reset-contract",
        send_text=lambda _text: None,
        send_bytes=lambda _data: None,
    )
    streamer = GeminiLiveStreamer(session=session, api_key="fake-key")
    streamer._current_user_text = "old user"
    streamer._current_turn_text = "partial old answer"
    streamer._model_turn_text = "partial model fallback"

    streamer._reset_turn_transcripts()

    assert streamer._current_user_text == ""
    assert streamer._current_turn_text == ""
    assert streamer._model_turn_text == ""


def test_streamer_emits_ready_only_after_provider_setup_complete():
    async def run():
        setup_gate = asyncio.Event()
        emitted: list[dict] = []

        async def send_text(payload):
            emitted.append(json.loads(payload))

        class Socket:
            def __init__(self):
                self.sent = []
                self.receive_count = 0

            async def send(self, payload):
                self.sent.append(payload)

            async def recv(self):
                self.receive_count += 1
                if self.receive_count == 1:
                    await setup_gate.wait()
                    return json.dumps({"setupComplete": {}})
                await asyncio.Future()

        socket = Socket()

        class Connection:
            async def __aenter__(self):
                return socket

            async def __aexit__(self, *_args):
                return False

        session = LiveSession(
            session_id="ready-contract",
            send_text=send_text,
            send_bytes=lambda _data: None,
        )
        streamer = GeminiLiveStreamer(
            session=session,
            api_key="fake-key",
            config=resolve_live_voice_config({}),
            connect_factory=lambda _uri: Connection(),
        )
        task = asyncio.create_task(streamer.start())
        await asyncio.sleep(0.02)
        assert not any(event.get("type") == "session_ready" for event in emitted)

        setup_gate.set()
        for _ in range(20):
            if any(event.get("type") == "session_ready" for event in emitted):
                break
            await asyncio.sleep(0.01)

        ready = [event for event in emitted if event.get("type") == "session_ready"]
        assert len(ready) == 1
        assert ready[0]["context_source"] == "local"
        assert ready[0]["connection_restored"] is False

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await session.close()

    asyncio.run(run())


def test_streamer_exhausts_reconnects_for_short_lived_connections():
    async def run():
        emitted: list[dict] = []
        connections = 0

        async def send_text(payload):
            emitted.append(json.loads(payload))

        class Socket:
            def __init__(self):
                self.receive_count = 0

            async def send(self, _payload):
                return None

            async def recv(self):
                self.receive_count += 1
                if self.receive_count == 1:
                    return json.dumps({"setupComplete": {}})
                raise ConnectionError("provider URL key=AIza-secret")

        class Connection:
            def __init__(self):
                self.socket = Socket()

            async def __aenter__(self):
                return self.socket

            async def __aexit__(self, *_args):
                return False

        def connect(_uri):
            nonlocal connections
            connections += 1
            return Connection()

        config = replace(
            resolve_live_voice_config({}),
            max_reconnect_attempts=1,
            reconnect_base_seconds=0.0,
            reconnect_max_seconds=0.0,
        )
        session = LiveSession(
            session_id="exhaust-contract",
            send_text=send_text,
            send_bytes=lambda _data: None,
        )
        streamer = GeminiLiveStreamer(
            session=session,
            api_key="fake-key",
            config=config,
            connect_factory=connect,
        )

        with pytest.raises(
            LiveTransportError,
            match="gemini_live_reconnect_exhausted",
        ):
            await asyncio.wait_for(streamer.start(), timeout=1)

        assert connections == 2
        assert "AIza-secret" not in json.dumps(emitted)
        await session.close()

    asyncio.run(run())
