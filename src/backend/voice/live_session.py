"""Live bidirectional voice streaming session manager for J.A.R.V.I.S.

Manages real-time audio streams, full-duplex conversational states,
and instant interruption (barge-in) cancellation.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
from collections.abc import Callable
from typing import Any

from core.runtime_logger import log_warning

logger = logging.getLogger(__name__)


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
    ) -> None:
        from voice.live_action_orchestrator import LiveActionOrchestrator, LiveDiagnosticsCollector

        self.session_id = session_id
        self._send_text = send_text
        self._send_bytes = send_bytes
        self.profile_id = profile_id
        self.language = language
        self.mode = mode
        self.state = LiveSessionState.IDLE
        self._lock = asyncio.Lock()
        self._transport_tasks: set[asyncio.Task] = set()
        self._turn_task: asyncio.Task | None = None
        self._playback_task: asyncio.Task | None = None
        self._tool_tasks: set[asyncio.Task] = set()
        self._input_audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._closed = False

        self.diagnostics = LiveDiagnosticsCollector()
        self.orchestrator = LiveActionOrchestrator(
            session_id=session_id,
            profile_id=profile_id,
            emit_json=self.emit_json,
            diagnostics=self.diagnostics,
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

    async def handle_audio_chunk(self, chunk: bytes) -> None:
        """Receive an incoming audio chunk from the client."""
        if self._closed or not chunk:
            return
        await self._input_audio_queue.put(chunk)

    async def interrupt(self) -> None:
        """Handle a barge-in event from client or voice activity detector.

        Cancels active turn processing, pending tool actions, and audio playback tasks.
        """
        async with self._lock:
            # 1. Cancel active conversational actions in the orchestrator
            await self.orchestrator.cancel_pending_actions(reason="user_barge_in")

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

            # 4. Cancel active tool execution tasks
            for t in self._tool_tasks:
                if not t.done():
                    t.cancel()
            self._tool_tasks.clear()

            # 5. Clear any unprocessed input queue
            while not self._input_audio_queue.empty():
                try:
                    self._input_audio_queue.get_nowait()
                    self._input_audio_queue.task_done()
                except (asyncio.QueueEmpty, ValueError):
                    break

            await self.set_state(LiveSessionState.INTERRUPTED)
            await self.emit_json({
                "type": "interrupted",
                "message": "Assistant output and pending actions cancelled by user barge-in",
            })
            await self.set_state(LiveSessionState.LISTENING)

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

    def attach_task(self, task: asyncio.Task) -> None:
        """Backwards-compatible alias to attach the active turn task."""
        self.attach_turn_task(task)

    async def close(self) -> None:
        """Cleanly terminate the session and all associated tasks."""
        self._closed = True
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
        self.state = LiveSessionState.CLOSED


class LiveSessionManager:
    """Manages active live voice streaming sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, LiveSession] = {}
        self._lock = asyncio.Lock()

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
            session = LiveSession(
                session_id=session_id,
                send_text=send_text,
                send_bytes=send_bytes,
                profile_id=profile_id,
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
