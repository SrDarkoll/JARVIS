"""Unit and integration tests for full-duplex live voice streaming and barge-in."""

from __future__ import annotations

import asyncio
import json

from voice.live_session import LiveSession, LiveSessionManager, LiveSessionState


def test_live_session_lifecycle_and_state_emission():
    async def _run():
        emitted_texts: list[str] = []
        emitted_bytes: list[bytes] = []

        async def _send_text(txt: str):
            emitted_texts.append(txt)

        async def _send_bytes(b: bytes):
            emitted_bytes.append(b)

        session = LiveSession(
            session_id="test-session-1",
            send_text=_send_text,
            send_bytes=_send_bytes,
            language="es",
        )

        assert session.state == LiveSessionState.IDLE

        # State transition
        await session.set_state(LiveSessionState.LISTENING)
        assert session.state == LiveSessionState.LISTENING
        assert len(emitted_texts) == 1
        event = json.loads(emitted_texts[-1])
        assert event["type"] == "state_change"
        assert event["state"] == "listening"

        # Audio chunk emission
        test_pcm = b"\x00\x01\x02\x03" * 10
        await session.emit_audio_chunk(test_pcm)
        assert len(emitted_bytes) == 1
        assert emitted_bytes[0] == test_pcm

    asyncio.run(_run())


def test_live_session_interrupt_barge_in_cancels_active_task():
    async def _run():
        emitted_texts: list[str] = []

        async def _send_text(txt: str):
            emitted_texts.append(txt)

        session = LiveSession(
            session_id="test-session-interrupt",
            send_text=_send_text,
            send_bytes=lambda b: None,
        )

        # Queue some pending audio
        await session.handle_audio_chunk(b"pending_audio_1")
        await session.handle_audio_chunk(b"pending_audio_2")
        assert not session._input_audio_queue.empty()

        # Create a long-running generation task
        task_cancelled = False

        async def _mock_speech_task():
            nonlocal task_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                task_cancelled = True
                raise

        task = asyncio.create_task(_mock_speech_task())
        session.attach_task(task)
        await asyncio.sleep(0.01)

        await session.set_state(LiveSessionState.SPEAKING)

        # Trigger user barge-in / interrupt
        await session.interrupt()

        assert task_cancelled is True
        assert task.done()
        assert session._input_audio_queue.empty()
        assert session.state == LiveSessionState.LISTENING

        # Verify event types emitted
        types = [json.loads(t)["type"] for t in emitted_texts]
        assert "interrupted" in types

    asyncio.run(_run())


def test_live_session_manager_registration_and_cleanup():
    async def _run():
        manager = LiveSessionManager()

        session = await manager.create_session(
            session_id="s-123",
            send_text=lambda t: None,
            send_bytes=lambda b: None,
        )
        assert session.session_id == "s-123"

        retrieved = await manager.get_session("s-123")
        assert retrieved is session

        await manager.remove_session("s-123")
        assert session.state == LiveSessionState.CLOSED
        assert await manager.get_session("s-123") is None

    asyncio.run(_run())


def test_live_voice_status_route_returns_capabilities():
    async def _run():
        import jarvis_backend
        client = jarvis_backend.app.test_client()
        res = await client.get("/api/voice/live/status")
        assert res.status_code == 200
        data = await res.get_json()
        assert data["ok"] is True
        assert data["live_supported"] is True
        assert "gemini_live_available" in data
        assert "preferred_mode" in data

    asyncio.run(_run())


def test_live_voice_websocket_connect():
    async def _run():
        import jarvis_backend
        client = jarvis_backend.app.test_client()
        async with client.websocket("/api/voice/stream", query_string={"lang": "es", "mode": "hybrid"}, headers={"Origin": "http://127.0.0.1:5002"}) as ws:
            # Receive session_ready or state_change
            msg = await ws.receive()
            data = json.loads(msg)
            assert data["type"] in {"state_change", "session_ready"}

            # Send ping
            await ws.send(json.dumps({"type": "ping"}))
            resp = await ws.receive()
            pong_data = json.loads(resp)
            assert pong_data.get("type") in {"pong", "state_change", "session_ready"}

            # Close gracefully
            await ws.send(json.dumps({"type": "stop"}))

    asyncio.run(_run())
