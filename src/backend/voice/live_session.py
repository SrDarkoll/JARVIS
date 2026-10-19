"""Live bidirectional voice streaming session manager for J.A.R.V.I.S.

Manages real-time audio streams, full-duplex conversational states,
and instant interruption (barge-in) cancellation.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from core.runtime_logger import log_warning

logger = logging.getLogger(__name__)


def _positive_env_int(name: str, default: int) -> int:
    try:
        return max(1, int((os.getenv(name) or str(default)).strip()))
    except (TypeError, ValueError):
        return default


DEFAULT_AUDIO_QUEUE_MAX_CHUNKS = _positive_env_int("JARVIS_LIVE_AUDIO_QUEUE_CHUNKS", 64)
DEFAULT_MAX_AUDIO_QUEUE_BYTES = _positive_env_int(
    "JARVIS_LIVE_AUDIO_QUEUE_BYTES",
    512 * 1024,
)
DEFAULT_MAX_AUDIO_CHUNK_BYTES = _positive_env_int(
    "JARVIS_LIVE_AUDIO_CHUNK_BYTES",
    64 * 1024,
)
DEFAULT_IMAGE_QUEUE_MAX_FRAMES = _positive_env_int("JARVIS_LIVE_IMAGE_QUEUE_FRAMES", 10)
DEFAULT_MAX_IMAGE_QUEUE_BYTES = _positive_env_int(
    "JARVIS_LIVE_IMAGE_QUEUE_BYTES",
    4 * 1024 * 1024,
)
DEFAULT_MAX_IMAGE_FRAME_BYTES = _positive_env_int(
    "JARVIS_LIVE_IMAGE_FRAME_BYTES",
    1536 * 1024,
)
DEFAULT_MAX_CONTROL_MESSAGE_BYTES = _positive_env_int(
    "JARVIS_LIVE_WS_MESSAGE_BYTES",
    2 * 1024 * 1024,
)
DEFAULT_MAX_USER_TEXT_CHARS = _positive_env_int("JARVIS_LIVE_USER_TEXT_CHARS", 16_000)
DEFAULT_MAX_LIVE_SESSIONS = _positive_env_int("JARVIS_LIVE_MAX_SESSIONS", 2)
DEFAULT_MAX_LIVE_SESSIONS_PER_PROFILE = _positive_env_int(
    "JARVIS_LIVE_MAX_SESSIONS_PER_PROFILE",
    1,
)


class LiveSessionCapacityError(RuntimeError):
    """Stable refusal when opening another provider-backed Live session."""


class LiveSessionState(enum.StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    CLOSED = "closed"


class LiveSession:
    """Represents a single active full-duplex voice WebSocket session."""

    def __init__(
        self,
        session_id: str,
        send_text: Callable[[str], Any],
        send_bytes: Callable[[bytes], Any],
        profile_id: str = "default",
        language: str = "es",
        mode: str = "auto",
        audio_queue_max_chunks: int = DEFAULT_AUDIO_QUEUE_MAX_CHUNKS,
        max_audio_queue_bytes: int = DEFAULT_MAX_AUDIO_QUEUE_BYTES,
        max_audio_chunk_bytes: int = DEFAULT_MAX_AUDIO_CHUNK_BYTES,
        image_queue_max_frames: int = DEFAULT_IMAGE_QUEUE_MAX_FRAMES,
        max_image_queue_bytes: int = DEFAULT_MAX_IMAGE_QUEUE_BYTES,
        max_image_frame_bytes: int = DEFAULT_MAX_IMAGE_FRAME_BYTES,
        max_control_message_bytes: int = DEFAULT_MAX_CONTROL_MESSAGE_BYTES,
        max_user_text_chars: int = DEFAULT_MAX_USER_TEXT_CHARS,
    ) -> None:
        from voice.live_action_orchestrator import LiveActionOrchestrator, LiveDiagnosticsCollector

        self.session_id = session_id
        self._send_text = send_text
        self._send_bytes = send_bytes
        self.profile_id = profile_id
        self.language = language
        self.mode = mode
        self.requested_mode = mode
        self.active_mode = ""
        self.handshake_state = "idle"
        self.last_failure_code = ""
        self.input_paused = False
        self.state = LiveSessionState.IDLE
        self._lock = asyncio.Lock()
        self._transport_tasks: set[asyncio.Task] = set()
        self._turn_task: asyncio.Task | None = None
        self._playback_task: asyncio.Task | None = None
        self._tool_tasks: set[asyncio.Task] = set()
        self.max_audio_queue_bytes = max(1, int(max_audio_queue_bytes))
        self.max_audio_chunk_bytes = max(1, int(max_audio_chunk_bytes))
        self.max_image_queue_bytes = max(1, int(max_image_queue_bytes))
        self.max_image_frame_bytes = max(1, int(max_image_frame_bytes))
        self.max_control_message_bytes = max(1, int(max_control_message_bytes))
        self.max_user_text_chars = max(1, int(max_user_text_chars))
        self._input_audio_queue: asyncio.Queue[bytes] = asyncio.Queue(
            maxsize=max(1, int(audio_queue_max_chunks))
        )
        self._input_image_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(
            maxsize=max(1, int(image_queue_max_frames))
        )
        self._input_audio_bytes = 0
        self._input_image_bytes = 0
        self._closed = False

        self.diagnostics = LiveDiagnosticsCollector()
        self._last_image_frame_data: str | None = None
        self._last_image_frame_time: float = 0.0
        self.frame_diff_min_interval: float = 8.0
        self.orchestrator = LiveActionOrchestrator(
            session_id=session_id,
            profile_id=profile_id,
            emit_json=self.emit_json,
            diagnostics=self.diagnostics,
            language=language,
        )

    async def emit_json(self, data: dict[str, Any]) -> None:
        """Send a JSON control message to the connected client."""
        if self._closed:
            return
        try:
            payload = json.dumps(data, ensure_ascii=False)
            res = self._send_text(payload)
            if asyncio.iscoroutine(res):
                await res
        except Exception as e:
            log_warning("live_session_emit_failed", session_id=self.session_id, error=str(e))

    async def emit_audio_chunk(self, pcm_bytes: bytes) -> None:
        """Send a raw PCM audio chunk to the connected client."""
        if self._closed or not pcm_bytes:
            return
        try:
            # Track first audio chunk latency
            self.diagnostics.record_first_audio()
            res = self._send_bytes(pcm_bytes)
            if asyncio.iscoroutine(res):
                await res
        except Exception as e:
            log_warning("live_session_audio_emit_failed", session_id=self.session_id, error=str(e))

    async def set_state(self, new_state: LiveSessionState) -> None:
        """Update session state and notify client."""
        self.state = new_state
        await self.emit_json({"type": "state_change", "state": new_state.value})

    async def set_provider_state(
        self,
        handshake_state: str,
        *,
        active_mode: str | None = None,
        failure_code: str = "",
    ) -> None:
        """Publish sanitized provider lifecycle without changing user profile."""

        self.handshake_state = str(handshake_state or "idle").strip().lower()
        if active_mode is not None:
            self.active_mode = str(active_mode or "").strip().lower()
        self.last_failure_code = str(failure_code or "").strip().lower()
        self.input_paused = self.handshake_state in {
            "connecting",
            "configuring",
            "reconnecting",
        }
        if self.input_paused:
            while self._drop_oldest_audio_chunk():
                pass
        await self.emit_json(
            {
                "type": "provider_state",
                "state": self.handshake_state,
                "active_mode": self.active_mode,
                "code": self.last_failure_code,
                "input_paused": self.input_paused,
            }
        )

    @property
    def input_audio_bytes(self) -> int:
        return self._input_audio_bytes

    @property
    def input_image_bytes(self) -> int:
        return self._input_image_bytes

    def _drop_oldest_audio_chunk(self) -> bool:
        try:
            dropped = self._input_audio_queue.get_nowait()
            self._input_audio_bytes = max(0, self._input_audio_bytes - len(dropped))
            self._input_audio_queue.task_done()
            return True
        except (asyncio.QueueEmpty, ValueError):
            return False

    def _drop_oldest_image_frame(self) -> bool:
        try:
            dropped, _mime_type = self._input_image_queue.get_nowait()
            self._input_image_bytes = max(
                0,
                self._input_image_bytes - len(dropped.encode("utf-8")),
            )
            self._input_image_queue.task_done()
            return True
        except (asyncio.QueueEmpty, ValueError):
            return False

    async def handle_audio_chunk(self, chunk: bytes) -> bool:
        """Receive an incoming audio chunk from the client."""
        if self._closed or not chunk:
            return False
        if self.input_paused:
            return True
        chunk_size = len(chunk)
        if chunk_size > self.max_audio_chunk_bytes or chunk_size > self.max_audio_queue_bytes:
            return False
        while self._input_audio_queue.full() or (
            self._input_audio_bytes + chunk_size > self.max_audio_queue_bytes
        ):
            if not self._drop_oldest_audio_chunk():
                return False
        await self._input_audio_queue.put(chunk)
        self._input_audio_bytes += chunk_size
        return True

    async def next_audio_chunk(self) -> bytes:
        chunk = await self._input_audio_queue.get()
        self._input_audio_bytes = max(0, self._input_audio_bytes - len(chunk))
        return chunk

    def audio_task_done(self, _chunk: bytes) -> None:
        self._input_audio_queue.task_done()

    async def handle_screen_frame(
        self,
        data: str,
        mime_type: str = "image/jpeg",
        force: bool = False,
    ) -> bool:
        """Receive an incoming screen or camera frame with deduplication for unchanged frames."""
        if self._closed or not data:
            return False
        frame_size = len(data.encode("utf-8"))
        if frame_size > self.max_image_frame_bytes or frame_size > self.max_image_queue_bytes:
            return False

        now = time.monotonic()
        if not force and self._last_image_frame_data == data:
            if (now - self._last_image_frame_time) < self.frame_diff_min_interval:
                # Frame is unchanged and heartbeat interval has not elapsed; skip queueing
                return True

        while self._input_image_queue.full() or (
            self._input_image_bytes + frame_size > self.max_image_queue_bytes
        ):
            if not self._drop_oldest_image_frame():
                return False
        await self._input_image_queue.put((data, mime_type))
        self._input_image_bytes += frame_size
        self._last_image_frame_data = data
        self._last_image_frame_time = now
        return True

    async def next_image_frame(self) -> tuple[str, str]:
        frame = await self._input_image_queue.get()
        self._input_image_bytes = max(
            0,
            self._input_image_bytes - len(frame[0].encode("utf-8")),
        )
        return frame

    def image_task_done(self, _frame: tuple[str, str]) -> None:
        self._input_image_queue.task_done()

    async def interrupt(self, *, clear_input_audio: bool = True) -> None:
        """Handle a barge-in event from client or voice activity detector.

        Cancels active turn processing, pending tool actions, and audio playback tasks.
        """
        started_at = time.perf_counter()
        async with self._lock:
            self.orchestrator.task_context.revoke_proposals()
            # Stop obsolete queued work, not external effects already running.
            await self.orchestrator.cancel_pending_actions(reason="user_barge_in", include_running=False)

            # 2. Cancel active turn task if any
            if self._turn_task and not self._turn_task.done():
                self._turn_task.cancel()
                try:
                    await self._turn_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.debug("Turn task cancelled with exception: %s", e)
                self._turn_task = None

            # 3. Cancel active playback task if any
            if self._playback_task and not self._playback_task.done():
                self._playback_task.cancel()
                try:
                    await self._playback_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.debug("Playback task cancelled with exception: %s", e)
                self._playback_task = None

            # Keep tracking synchronous workers; stopping speech cannot undo them.
            self._tool_tasks = {task for task in self._tool_tasks if not task.done()}

            # Native barge-in belongs to the new input turn, not the old output.
            if clear_input_audio:
                while self._drop_oldest_audio_chunk():
                    pass

            while not self._input_image_queue.empty():
                if not self._drop_oldest_image_frame():
                    break

            # Reset frame deduplication cache
            self._last_image_frame_data = None
            self._last_image_frame_time = 0.0

            await self.set_state(LiveSessionState.INTERRUPTED)
            await self.emit_json({
                "type": "interrupted",
                "message": "Assistant output and pending actions cancelled by user barge-in",
            })
            await self.set_state(LiveSessionState.LISTENING)
            self.diagnostics.record_barge_in(
                max(0.0, (time.perf_counter() - started_at) * 1000.0)
            )
            self.diagnostics.record_turn_complete()

    def attach_transport_task(self, task: asyncio.Task) -> None:
        """Track long-lived transport and receiver loop tasks."""
        self._transport_tasks = {t for t in self._transport_tasks if not t.done()}
        self._transport_tasks.add(task)

    def attach_turn_task(self, task: asyncio.Task) -> None:
        """Track the currently executing generation or turn task."""
        self._turn_task = task

    def attach_playback_task(self, task: asyncio.Task) -> None:
        """Track the currently executing audio playback task."""
        self._playback_task = task

    def attach_tool_task(self, task: asyncio.Task) -> None:
        """Track an asynchronous tool execution task."""
        self._tool_tasks = {t for t in self._tool_tasks if not t.done()}
        self._tool_tasks.add(task)

    @property
    def has_active_tool_work(self) -> bool:
        return any(not task.done() for task in self._tool_tasks) or self.orchestrator.has_active_workers

    def attach_task(self, task: asyncio.Task) -> None:
        """Backwards-compatible alias to attach the active turn task."""
        self.attach_turn_task(task)

    def begin_close(self) -> None:
        """Revoke new input and contextual execution before awaiting transport shutdown."""
        self._closed = True
        self.orchestrator.task_context.invalidate()

    async def close(self) -> None:
        """Cleanly terminate the session and all associated tasks."""
        self.begin_close()
        all_tasks = set(self._transport_tasks) | set(self._tool_tasks)
        if self._turn_task:
            all_tasks.add(self._turn_task)
        if self._playback_task:
            all_tasks.add(self._playback_task)

        for task in all_tasks:
            if not task.done():
                task.cancel()

        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)

        self._transport_tasks.clear()
        self._tool_tasks.clear()
        self._turn_task = None
        self._playback_task = None
        while self._drop_oldest_audio_chunk():
            pass
        while self._drop_oldest_image_frame():
            pass
        self.state = LiveSessionState.CLOSED


class LiveSessionManager:
    """Manages active live voice streaming sessions."""

    def __init__(
        self,
        *,
        max_sessions: int = DEFAULT_MAX_LIVE_SESSIONS,
        max_sessions_per_profile: int = DEFAULT_MAX_LIVE_SESSIONS_PER_PROFILE,
    ) -> None:
        self._sessions: dict[str, LiveSession] = {}
        self._lock = asyncio.Lock()
        self.max_sessions = max(1, int(max_sessions))
        self.max_sessions_per_profile = max(
            1,
            min(int(max_sessions_per_profile), self.max_sessions),
        )

    async def create_session(
        self,
        session_id: str,
        send_text: Callable[[str], Any],
        send_bytes: Callable[[bytes], Any],
        profile_id: str = "default",
        language: str = "es",
        mode: str = "auto",
    ) -> LiveSession:
        async with self._lock:
            self._sessions = {
                known_id: known_session
                for known_id, known_session in self._sessions.items()
                if not known_session._closed
            }
            normalized_profile = (
                str(profile_id or "default").strip().lower()[:64]
                or "default"
            )
            profile_sessions = sum(
                1
                for known_session in self._sessions.values()
                if known_session.profile_id == normalized_profile
            )
            if profile_sessions >= self.max_sessions_per_profile:
                raise LiveSessionCapacityError("live_profile_session_limit")
            if len(self._sessions) >= self.max_sessions:
                raise LiveSessionCapacityError("live_global_session_limit")
            session = LiveSession(
                session_id=session_id,
                send_text=send_text,
                send_bytes=send_bytes,
                profile_id=normalized_profile,
                language=language,
                mode=mode,
            )
            self._sessions[session_id] = session
            return session

    async def get_session(self, session_id: str) -> LiveSession | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def remove_session(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                await session.close()


live_session_manager = LiveSessionManager()
