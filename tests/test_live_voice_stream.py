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


def test_live_session_interrupt_preserves_transport_tasks_while_canceling_turn_and_playback():
    async def _run():
        session = LiveSession(
            session_id="test-session-task-isolation",
            send_text=lambda t: None,
            send_bytes=lambda b: None,
        )

        transport_cancelled = False
        turn_cancelled = False
        playback_cancelled = False

        async def _mock_transport_loop():
            nonlocal transport_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                transport_cancelled = True
                raise

        async def _mock_turn_loop():
            nonlocal turn_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                turn_cancelled = True
                raise

        async def _mock_playback_loop():
            nonlocal playback_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                playback_cancelled = True
                raise

        transport_task = asyncio.create_task(_mock_transport_loop())
        turn_task = asyncio.create_task(_mock_turn_loop())
        playback_task = asyncio.create_task(_mock_playback_loop())

        session.attach_transport_task(transport_task)
        session.attach_turn_task(turn_task)
        session.attach_playback_task(playback_task)
        await asyncio.sleep(0.01)

        # Trigger barge-in interrupt
        await session.interrupt()

        # Turn and playback tasks MUST be cancelled
        assert turn_cancelled is True
        assert turn_task.done()
        assert playback_cancelled is True
        assert playback_task.done()

        # Transport receiver loop MUST REMAIN RUNNING
        assert transport_cancelled is False
        assert not transport_task.done()

        # Session close terminates transport tasks cleanly
        await session.close()
        assert transport_task.done()

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


def test_live_voice_status_route_returns_capabilities(monkeypatch):
    async def _run():
        monkeypatch.setenv("JARVIS_LLM_PROVIDER", "groq")
        monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from core.llm_providers import resolve_gemini_api_key, resolve_groq_api_key
        assert resolve_gemini_api_key() == "test-gemini-key"
        assert resolve_groq_api_key() == "test-groq-key"

        import jarvis_backend
        client = jarvis_backend.app.test_client()
        res = await client.get("/api/voice/live/status")
        assert res.status_code == 200
        data = await res.get_json()
        assert data["ok"] is True
        assert data["live_supported"] is True
        # Gemini Live MUST be available even though Groq is primary chat LLM
        assert data["gemini_live_available"] is True
        assert data["preferred_mode"] == "gemini_live"

    asyncio.run(_run())


def test_live_voice_websocket_connect():
    async def _run():
        import jarvis_backend
        client = jarvis_backend.app.test_client()
        async with client.websocket(
            "/api/voice/stream",
            query_string={"lang": "es", "mode": "hybrid"},
            headers={"Origin": "http://127.0.0.1:5002"},
        ) as ws:
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


def test_live_voice_websocket_security_origin_and_token(monkeypatch):
    async def _run():
        import jarvis_backend
        client = jarvis_backend.app.test_client()

        # 1. Untrusted origin should be rejected with 403
        try:
            async with client.websocket(
                "/api/voice/stream",
                query_string={"lang": "es", "mode": "hybrid"},
                headers={"Origin": "http://malicious-site.example.com"},
            ) as ws:
                assert False, "Untrusted origin should not establish websocket"
        except Exception:
            pass  # Expected rejection

        # 2. Token protection when configured
        monkeypatch.setenv("JARVIS_API_TOKEN", "super-secret-token")

        # Connection without token should fail
        try:
            async with client.websocket(
                "/api/voice/stream",
                query_string={"lang": "es", "mode": "hybrid"},
                headers={"Origin": "http://127.0.0.1:5002"},
            ) as ws:
                assert False, "Websocket without required token should fail"
        except Exception:
            pass  # Expected rejection

        # Connection with valid token in query param should succeed
        async with client.websocket(
            "/api/voice/stream",
            query_string={"lang": "es", "mode": "hybrid", "token": "super-secret-token"},
            headers={"Origin": "http://127.0.0.1:5002"},
        ) as ws:
            msg = await ws.receive()
            data = json.loads(msg)
            assert data["type"] in {"state_change", "session_ready"}
            await ws.send(json.dumps({"type": "stop"}))

    asyncio.run(_run())
