"""Provider transport contracts for privacy-first Gemini Live sessions."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class LiveTransportState(StrEnum):
    IDLE = "idle"
    CONNECTING = "connecting"
    CONFIGURING = "configuring"
    READY = "ready"
    RECONNECTING = "reconnecting"
    FALLBACK = "fallback"
    FAILED = "failed"
    CLOSED = "closed"


class LiveTransportError(RuntimeError):
    """Controlled provider transport failure identified by a public code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SetupResult:
    state: LiveTransportState
    pending_messages: tuple[dict[str, Any], ...]


def reconnect_delay(
    attempt: int,
    *,
    base_seconds: float,
    max_seconds: float,
) -> float:
    return min(
        max(0.0, float(max_seconds)),
        max(0.0, float(base_seconds)) * (2 ** max(0, int(attempt) - 1)),
    )


class LiveTransportController:
    """Track provider state and a reconnect budget without provider I/O."""

    def __init__(
        self,
        *,
        max_reconnect_attempts: int,
        stable_connection_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_reconnect_attempts = max(0, int(max_reconnect_attempts))
        self.stable_connection_seconds = max(
            0.0,
            float(stable_connection_seconds),
        )
        self._clock = clock
        self.state = LiveTransportState.IDLE
        self.reconnect_attempts = 0
        self.connected_at: float | None = None
        self.turn_completed = False

    def transition(self, state: LiveTransportState) -> LiveTransportState:
        self.state = state
        return self.state

    def mark_connected(self) -> None:
        self.connected_at = self._clock()
        self.turn_completed = False

    def mark_turn_completed(self) -> None:
        self.turn_completed = True

    def next_reconnect_attempt(self) -> int:
        stable = (
            self.connected_at is not None
            and self._clock() - self.connected_at >= self.stable_connection_seconds
        )
        if stable or self.turn_completed:
            self.reconnect_attempts = 0
        self.connected_at = None
        self.turn_completed = False
        self.reconnect_attempts += 1
        if self.reconnect_attempts > self.max_reconnect_attempts:
            self.state = LiveTransportState.FAILED
            raise LiveTransportError("gemini_live_reconnect_exhausted")
        self.state = LiveTransportState.RECONNECTING
        return self.reconnect_attempts


class LiveResponseWatchdog:
    """Bound provider inactivity, not user speech, idle listening or tool execution."""

    def __init__(self, *, timeout_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.timeout_seconds = max(0.01, float(timeout_seconds))
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        self.speech_active = False
        self.awaiting_response = False
        self._busy_tools = False
        self._last_progress = self._clock()

    def speech_started(self) -> None:
        self.speech_active = True
        self.awaiting_response = False
        self._last_progress = self._clock()

    def speech_ended(self) -> None:
        self.speech_active = False
        self._last_progress = self._clock()

    def input_received(self) -> None:
        self.awaiting_response = True
        self._last_progress = self._clock()

    def output_received(self) -> None:
        self.awaiting_response = True
        self._last_progress = self._clock()

    def generation_complete(self) -> None:
        self.awaiting_response = False
        self._busy_tools = False

    def waiting_for_input(self) -> None:
        self.awaiting_response = False
        self._busy_tools = False

    def expired(self, *, busy_tools: bool = False) -> bool:
        if busy_tools:
            self._busy_tools = True
            self._last_progress = self._clock()
            return False
        if self._busy_tools:
            self._busy_tools = False
            self._last_progress = self._clock()
            return False
        return (
            self.awaiting_response and not self.speech_active
            and self._clock() - self._last_progress >= self.timeout_seconds
        )


async def await_setup_complete(
    socket,
    *,
    timeout_seconds: float,
) -> SetupResult:
    """Wait for Gemini setup acknowledgement and retain preceding safe messages."""

    pending: list[dict[str, Any]] = []

    async def receive_until_ready() -> SetupResult:
        while True:
            raw = await socket.recv()
            if isinstance(raw, bytes):
                try:
                    raw = raw.decode("utf-8")
                except UnicodeDecodeError:
                    raise LiveTransportError("gemini_live_protocol_error") from None
            try:
                message = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                raise LiveTransportError("gemini_live_protocol_error") from None
            if not isinstance(message, dict):
                raise LiveTransportError("gemini_live_protocol_error")
            if message.get("setupComplete") is not None:
                return SetupResult(LiveTransportState.READY, tuple(pending))
            if message.get("goAway") is not None:
                raise LiveTransportError("gemini_live_connection_rotating")
            if message.get("error") is not None:
                raise LiveTransportError("gemini_live_setup_rejected")
            pending.append(message)

    try:
        return await asyncio.wait_for(
            receive_until_ready(),
            timeout=max(0.001, float(timeout_seconds)),
        )
    except TimeoutError:
        raise LiveTransportError("gemini_live_setup_timeout") from None


async def supervise_transport_tasks(
    tasks: Sequence[asyncio.Task],
) -> None:
    """Propagate the first transport-loop failure and cancel sibling loops."""

    active = [task for task in tasks if task is not None]
    if not active:
        raise LiveTransportError("gemini_live_connect_failed")
    try:
        done, _ = await asyncio.wait(
            active,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            try:
                task.result()
            except asyncio.CancelledError:
                raise
            except LiveTransportError:
                raise
            except Exception:
                raise LiveTransportError("gemini_live_connect_failed") from None
        raise LiveTransportError("gemini_live_connect_failed")
    finally:
        for task in active:
            if not task.done():
                task.cancel()
        await asyncio.gather(*active, return_exceptions=True)


__all__ = [
    "LiveResponseWatchdog",
    "LiveTransportController",
    "LiveTransportError",
    "LiveTransportState",
    "SetupResult",
    "await_setup_complete",
    "reconnect_delay",
    "supervise_transport_tasks",
]
