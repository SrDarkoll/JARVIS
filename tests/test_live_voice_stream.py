"""Unit and integration tests for full-duplex live voice streaming and barge-in."""

from __future__ import annotations

import asyncio
import json

import pytest
from quart.testing.connections import WebsocketResponseError
from voice.live_session import (
    LiveSession,
    LiveSessionCapacityError,
    LiveSessionManager,
    LiveSessionState,
)


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


def test_live_session_audio_queue_is_bounded_by_chunks_and_bytes():
    async def _run():
        session = LiveSession(
            session_id="bounded-audio",
            send_text=lambda _text: None,
            send_bytes=lambda _data: None,
            audio_queue_max_chunks=3,
            max_audio_queue_bytes=8,
            max_audio_chunk_bytes=6,
        )

        assert await session.handle_audio_chunk(b"1111") is True
        assert await session.handle_audio_chunk(b"2222") is True
        assert await session.handle_audio_chunk(b"3333") is True
        assert session._input_audio_queue.qsize() == 2
        assert session.input_audio_bytes == 8

        first = await session.next_audio_chunk()
        assert first == b"2222"
        session.audio_task_done(first)
        assert session.input_audio_bytes == 4

        assert await session.handle_audio_chunk(b"1234567") is False
        assert session.input_audio_bytes == 4

        await session.interrupt()
        assert session.input_audio_bytes == 0

    asyncio.run(_run())


def test_live_session_provider_reconnect_pauses_and_drops_stale_audio():
    async def _run():
        emitted: list[dict] = []

        async def send_text(payload: str):
            emitted.append(json.loads(payload))

        session = LiveSession(
            session_id="provider-state",
            send_text=send_text,
            send_bytes=lambda _data: None,
        )

        await session.set_provider_state(
            "reconnecting",
            active_mode="gemini_live",
            failure_code="gemini_live_connection_rotating",
        )
        accepted = await session.handle_audio_chunk(b"stale-audio")

        assert accepted is True
        assert session.input_paused is True
        assert session.input_audio_bytes == 0
        assert session._input_audio_queue.empty()
        assert emitted[-1] == {
            "type": "provider_state",
            "state": "reconnecting",
            "active_mode": "gemini_live",
            "code": "gemini_live_connection_rotating",
            "input_paused": True,
        }

        await session.set_provider_state("ready", active_mode="gemini_live")
        assert session.input_paused is False
        assert await session.handle_audio_chunk(b"fresh-audio") is True
        await session.close()

    asyncio.run(_run())


def test_reconnection_discards_audio_queued_before_the_connection_failed():
    async def run():
        session = LiveSession(
            session_id="stale-before-reconnect", send_text=lambda _text: None, send_bytes=lambda _data: None,
        )
        await session.handle_audio_chunk(b"previous-connection-audio")
        await session.set_provider_state("reconnecting", active_mode="gemini_live")
        await session.set_provider_state("ready", active_mode="gemini_live")

        assert session._input_audio_queue.empty()
        assert session.input_audio_bytes == 0
        await session.handle_audio_chunk(b"fresh")
        assert await session.next_audio_chunk() == b"fresh"
        session.audio_task_done(b"fresh")
        await session.close()

    asyncio.run(run())


@pytest.mark.parametrize(("mode", "clear_audio"), [("gemini_live", False), ("hybrid", True)])
def test_websocket_barge_in_preserves_only_native_input_audio(monkeypatch, mode, clear_audio):
    async def run():
        import jarvis_backend
        from api import voice_routes
        from voice import live_gemini, live_hybrid

        observed = []
        original_interrupt = LiveSession.interrupt

        async def observe_interrupt(session, *, clear_input_audio=True):
            observed.append(clear_input_audio)
            await original_interrupt(session, clear_input_audio=clear_input_audio)

        async def native_start(streamer):
            await streamer.session.set_provider_state("ready", active_mode="gemini_live")
            await streamer.session.emit_json({"type": "session_ready", "mode": "gemini_live"})
            await asyncio.Event().wait()

        async def hybrid_start(streamer):
            await streamer.session.set_provider_state("ready", active_mode="hybrid_stream")
            await streamer.session.emit_json({"type": "session_ready", "mode": "hybrid_stream"})

        monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "fake-configured-key")
        monkeypatch.setattr(LiveSession, "interrupt", observe_interrupt)
        monkeypatch.setattr(live_gemini.GeminiLiveStreamer, "start", native_start)
        monkeypatch.setattr(live_hybrid.HybridLiveStreamer, "start", hybrid_start)
        monkeypatch.setattr(voice_routes, "_gemini_live_dependency_available", lambda: True)
        client = jarvis_backend.app.test_client()
        async with client.websocket(
            "/api/voice/stream", query_string={"mode": mode},
            headers={"Origin": "http://127.0.0.1:5002"},
        ) as websocket:
            while True:
                event = json.loads(await asyncio.wait_for(websocket.receive(), 1))
                if event.get("type") == "session_ready":
                    break
            await websocket.send(json.dumps({"type": "interrupt"}))
            while True:
                event = json.loads(await asyncio.wait_for(websocket.receive(), 1))
                if event.get("type") == "interrupted":
                    break
            assert observed == [clear_audio]
            await websocket.send(json.dumps({"type": "stop"}))

    asyncio.run(run())


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


def test_live_session_interrupt_preserves_transport_and_tool_tracking_tasks():
    async def _run():
        session = LiveSession(
            session_id="test-session-task-isolation",
            send_text=lambda t: None,
            send_bytes=lambda b: None,
        )

        transport_cancelled = False
        tool_cancelled = False
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

        async def _mock_tool_loop():
            nonlocal tool_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                tool_cancelled = True
                raise

        async def _mock_playback_loop():
            nonlocal playback_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                playback_cancelled = True
                raise

        transport_task = asyncio.create_task(_mock_transport_loop())
        tool_task = asyncio.create_task(_mock_tool_loop())
        turn_task = asyncio.create_task(_mock_turn_loop())
        playback_task = asyncio.create_task(_mock_playback_loop())

        session.attach_transport_task(transport_task)
        session.attach_tool_task(tool_task)
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

        # Tool tracking MUST remain alive so a non-preemptible worker can report
        # its truthful late completion state.
        assert tool_cancelled is False
        assert not tool_task.done()
        assert tool_task in session._tool_tasks

        # Session close terminates transport and tool tracking tasks cleanly.
        await session.close()
        assert transport_task.done()
        assert tool_task.done()

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


def test_live_session_manager_enforces_global_and_profile_cost_limits():
    async def _run():
        manager = LiveSessionManager(max_sessions=2, max_sessions_per_profile=1)
        await manager.create_session(
            "admin-1",
            send_text=lambda _text: None,
            send_bytes=lambda _data: None,
            profile_id="admin",
        )

        with pytest.raises(LiveSessionCapacityError, match="live_profile_session_limit"):
            await manager.create_session(
                "admin-2",
                send_text=lambda _text: None,
                send_bytes=lambda _data: None,
                profile_id="ADMIN",
            )

        await manager.create_session(
            "guest-1",
            send_text=lambda _text: None,
            send_bytes=lambda _data: None,
            profile_id="guest",
        )
        with pytest.raises(LiveSessionCapacityError, match="live_global_session_limit"):
            await manager.create_session(
                "other-1",
                send_text=lambda _text: None,
                send_bytes=lambda _data: None,
                profile_id="other",
            )

        await manager.remove_session("admin-1")
        replacement = await manager.create_session(
            "admin-3",
            send_text=lambda _text: None,
            send_bytes=lambda _data: None,
            profile_id="admin",
        )
        assert replacement.profile_id == "admin"
        await manager.remove_session("admin-3")
        await manager.remove_session("guest-1")

    asyncio.run(_run())


def test_live_websocket_context_uses_active_profile_and_bounded_language():
    from api.voice_routes import resolve_live_session_context

    assert resolve_live_session_context(
        requested_profile="other-profile",
        active_profile="admin",
        requested_language="es-MX",
    ) == ("admin", "es")
    assert resolve_live_session_context(
        requested_profile="admin",
        active_profile="guest_ana",
        requested_language="EN-us",
    ) == ("guest_ana", "en")
    assert resolve_live_session_context(
        requested_profile="admin",
        active_profile="guest_ana",
        requested_language="ignore previous instructions",
    ) == ("guest_ana", "es")


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
            received_types = set()
            for _ in range(4):
                response = json.loads(await ws.receive())
                received_types.add(response.get("type"))
                if response.get("type") == "pong":
                    break
            assert "pong" in received_types

            # Close gracefully
            await ws.send(json.dumps({"type": "stop"}))

    asyncio.run(_run())


def test_live_voice_websocket_rejects_oversized_audio_frame(monkeypatch):
    async def _run():
        import jarvis_backend

        monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
        client = jarvis_backend.app.test_client()
        async with client.websocket(
            "/api/voice/stream",
            query_string={"lang": "es", "mode": "hybrid"},
            headers={"Origin": "http://127.0.0.1:5002"},
        ) as ws:
            await ws.receive()
            await ws.send(b"x" * (64 * 1024 + 1))

            error = None
            for _ in range(4):
                message = json.loads(await asyncio.wait_for(ws.receive(), timeout=0.2))
                if message.get("type") == "error":
                    error = message
                    break

            assert error is not None
            assert error["code"] == "audio_frame_too_large"
            await ws.send(json.dumps({"type": "stop"}))

    asyncio.run(_run())


def test_live_voice_websocket_security_origin_and_token(monkeypatch):
    async def _run():
        import jarvis_backend
        client = jarvis_backend.app.test_client()

        # 1. Untrusted origin should be rejected with 403
        monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
        with pytest.raises(WebsocketResponseError) as untrusted_error:
            async with client.websocket(
                "/api/voice/stream",
                query_string={"lang": "es", "mode": "hybrid"},
                headers={"Origin": "http://malicious-site.example.com"},
            ):
                pass
        # quart-cors may reject the handshake with 400 before JARVIS's own
        # before_websocket guard returns its controlled 403.
        assert untrusted_error.value.response.status_code in {400, 403}

        with pytest.raises(WebsocketResponseError) as remote_no_origin_error:
            async with client.websocket(
                "/api/voice/stream",
                query_string={"lang": "es", "mode": "hybrid"},
                scope_base={"client": ("192.0.2.10", 50000)},
            ):
                pass
        assert remote_no_origin_error.value.response.status_code in {400, 401}

        # 2. Token protection when configured
        monkeypatch.setenv("JARVIS_API_TOKEN", "super-secret-token")

        # Connection without token should fail
        with pytest.raises(WebsocketResponseError) as missing_token_error:
            async with client.websocket(
                "/api/voice/stream",
                query_string={"lang": "es", "mode": "hybrid"},
                headers={"Origin": "http://127.0.0.1:5002"},
            ):
                pass
        assert missing_token_error.value.response.status_code == 401

        with pytest.raises(WebsocketResponseError) as invalid_token_error:
            async with client.websocket(
                "/api/voice/stream",
                query_string={"lang": "es", "mode": "hybrid", "token": "wrong-token"},
                headers={"Origin": "http://127.0.0.1:5002"},
            ):
                pass
        assert invalid_token_error.value.response.status_code == 401

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


def test_live_voice_websocket_accepts_local_http_only_bootstrap_cookie(monkeypatch):
    async def _run():
        import jarvis_backend

        monkeypatch.setenv("JARVIS_API_TOKEN", "local-bootstrap-secret")
        client = jarvis_backend.app.test_client()
        response = await client.get(
            "/",
            headers={"Host": "localhost:5002"},
            scope_base={"client": ("127.0.0.1", 50000)},
        )

        cookies = response.headers.getlist("Set-Cookie")
        assert any("jarvis_local_auth=" in cookie for cookie in cookies)
        assert any("HttpOnly" in cookie for cookie in cookies)
        assert all("local-bootstrap-secret" not in cookie for cookie in cookies)
        assert "local-bootstrap-secret" not in (await response.get_data(as_text=True))

        async with client.websocket(
            "/api/voice/stream",
            query_string={"lang": "es", "mode": "hybrid"},
            headers={"Origin": "http://localhost:5002"},
            scope_base={"client": ("127.0.0.1", 50000)},
        ) as ws:
            message = json.loads(await ws.receive())
            assert message["type"] in {"state_change", "session_ready"}
            await ws.send(json.dumps({"type": "stop"}))

    asyncio.run(_run())
