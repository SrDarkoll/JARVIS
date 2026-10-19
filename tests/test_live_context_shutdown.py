from __future__ import annotations

import asyncio
import json

from voice.live_session import LiveSessionManager


def test_client_disconnect_during_provider_shutdown_always_releases_its_session(monkeypatch):
    async def run():
        import jarvis_backend
        from api import voice_routes
        from voice import live_gemini, live_session

        manager = LiveSessionManager()
        closing = asyncio.Event()
        release = asyncio.Event()
        identifiers = []
        closed_on_provider_shutdown = []

        async def provider_start(streamer):
            identifiers.append(streamer.session.session_id)
            await streamer.session.emit_json({"type": "session_ready"})
            try:
                await asyncio.Event().wait()
            finally:
                closed_on_provider_shutdown.append(streamer.session._closed)
                closing.set()
                await release.wait()

        monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "fake-shutdown-test-key")
        monkeypatch.setattr(live_session, "live_session_manager", manager)
        monkeypatch.setattr(live_gemini.GeminiLiveStreamer, "start", provider_start)
        monkeypatch.setattr(voice_routes, "_gemini_live_dependency_available", lambda: True)
        client = jarvis_backend.app.test_client()
        connection = client.websocket("/api/voice/stream", query_string={"mode": "gemini_live"},
                                      headers={"Origin": "http://127.0.0.1:5002"})
        async with connection as websocket:
            while json.loads(await asyncio.wait_for(websocket.receive(), 1)).get("type") != "session_ready":
                pass
            await websocket.send(json.dumps({"type": "stop"}))
            await asyncio.wait_for(closing.wait(), 1)
            await websocket.disconnect()
            await asyncio.sleep(0.05)
            release.set()
        for _attempt in range(100):
            if await manager.get_session(identifiers[0]) is None:
                break
            await asyncio.sleep(0.01)
        assert await manager.get_session(identifiers[0]) is None
        assert closed_on_provider_shutdown == [True]

    asyncio.run(run())
