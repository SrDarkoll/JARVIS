import asyncio
import json

from voice.live_gemini import GeminiLiveStreamer
from voice.live_session import LiveSession


def test_live_session_screen_frame_queue_and_overflow():
    async def _run():
        emitted = []
        session = LiveSession(
            session_id="screen-sess-1",
            send_text=emitted.append,
            send_bytes=lambda b: None,
        )

        assert session._input_image_queue.empty()

        # Enqueue first frame
        await session.handle_screen_frame("base64_frame_1", "image/jpeg")
        assert session._input_image_queue.qsize() == 1

        # Fill up to maxsize (10) + overflow
        for i in range(2, 15):
            await session.handle_screen_frame(f"base64_frame_{i}", "image/jpeg")

        # Size should be bounded at maxsize (10)
        assert session._input_image_queue.qsize() == 10

        # Oldest frames (1..4) should have been dropped, latest frames (5..14) preserved
        first_popped = await session._input_image_queue.get()
        assert first_popped[0] == "base64_frame_5"

    asyncio.run(_run())


def test_live_session_screen_queue_is_bounded_by_encoded_bytes():
    async def _run():
        session = LiveSession(
            session_id="screen-byte-budget",
            send_text=lambda _text: None,
            send_bytes=lambda _data: None,
            image_queue_max_frames=3,
            max_image_queue_bytes=8,
            max_image_frame_bytes=6,
        )

        assert await session.handle_screen_frame("1111", "image/jpeg") is True
        assert await session.handle_screen_frame("2222", "image/jpeg") is True
        assert await session.handle_screen_frame("3333", "image/jpeg") is True
        assert session._input_image_queue.qsize() == 2
        assert session.input_image_bytes == 8

        frame = await session.next_image_frame()
        assert frame[0] == "2222"
        session.image_task_done(frame)
        assert session.input_image_bytes == 4

        assert await session.handle_screen_frame("1234567", "image/jpeg") is False
        assert session.input_image_bytes == 4

        await session.interrupt()
        assert session.input_image_bytes == 0

    asyncio.run(_run())


def test_live_session_interrupt_clears_screen_frames():
    async def _run():
        session = LiveSession(
            session_id="screen-sess-2",
            send_text=lambda t: None,
            send_bytes=lambda b: None,
        )

        await session.handle_audio_chunk(b"pcm_audio_data")
        await session.handle_screen_frame("base64_screen_image", "image/jpeg")

        assert not session._input_audio_queue.empty()
        assert not session._input_image_queue.empty()

        await session.interrupt()

        assert session._input_audio_queue.empty()
        assert session._input_image_queue.empty()

    asyncio.run(_run())


def test_gemini_live_streamer_upstream_media_loop_formatting():
    async def _run():
        sent_ws_messages = []

        class FakeWebSocket:
            async def send(self, data):
                sent_ws_messages.append(json.loads(data))

        session = LiveSession(
            session_id="screen-sess-3",
            send_text=lambda t: None,
            send_bytes=lambda b: None,
        )
        streamer = GeminiLiveStreamer(session=session, api_key="fake-test-key")
        streamer._running = True
        streamer._ws = FakeWebSocket()

        # Start media loop in background
        media_task = asyncio.create_task(streamer._upstream_media_loop())

        # Push a raw base64 frame and one with data URL header
        await session.handle_screen_frame("raw_base64_frame", "image/jpeg")
        await session.handle_screen_frame("data:image/jpeg;base64,header_stripped_frame", "image/jpeg")

        # Wait for media loop to process
        await asyncio.sleep(0.05)

        streamer._running = False
        media_task.cancel()
        try:
            await media_task
        except asyncio.CancelledError:
            pass

        assert len(sent_ws_messages) == 2

        # Verify first message format
        msg1 = sent_ws_messages[0]
        assert "realtimeInput" in msg1
        video1 = msg1["realtimeInput"]["video"]
        assert video1["mimeType"] == "image/jpeg"
        assert video1["data"] == "raw_base64_frame"

        # Verify second message stripped header
        msg2 = sent_ws_messages[1]
        video2 = msg2["realtimeInput"]["video"]
        assert video2["data"] == "header_stripped_frame"

    asyncio.run(_run())


def test_live_session_screen_frame_deduplication():
    async def _run():
        session = LiveSession(
            session_id="screen-dedup-1",
            send_text=lambda _t: None,
            send_bytes=lambda _b: None,
        )

        # First frame enqueued
        assert await session.handle_screen_frame("frame_content_A", "image/jpeg") is True
        assert session._input_image_queue.qsize() == 1

        # Identical frame received immediately within heartbeat interval is skipped without queue churn
        assert await session.handle_screen_frame("frame_content_A", "image/jpeg") is True
        assert session._input_image_queue.qsize() == 1

        # Different frame is enqueued
        assert await session.handle_screen_frame("frame_content_B", "image/jpeg") is True
        assert session._input_image_queue.qsize() == 2

        # Forced frame bypasses deduplication
        assert await session.handle_screen_frame("frame_content_B", "image/jpeg", force=True) is True
        assert session._input_image_queue.qsize() == 3

    asyncio.run(_run())
