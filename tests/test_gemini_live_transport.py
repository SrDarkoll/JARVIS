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
        assert setup["model"] == "models/gemini-3.1-flash-live-preview"
        assert setup["generationConfig"]["responseModalities"] == ["AUDIO"]
        assert setup["contextWindowCompression"] == {"slidingWindow": {}}
        assert "sessionResumption" not in setup

    asyncio.run(run())


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
