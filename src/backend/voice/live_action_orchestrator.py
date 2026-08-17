"""Live Action Orchestrator for full-duplex conversational execution in J.A.R.V.I.S.

Provides multi-tool sequential execution, real-time action lifecycle states
(pending, running, completed, cancelled, failed, waiting_confirmation),
and conversational barge-in plan modification/cancellation.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.runtime_logger import log_error, log_warning
from core.unified_log import write_log

logger = logging.getLogger(__name__)


class ActionState(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    WAITING_CONFIRMATION = "waiting_confirmation"


@dataclass
class ActionItem:
    """Represents a single executable tool action in a conversational plan."""

    action_id: str
    name: str
    args: dict[str, Any]
    call_id: str
    state: ActionState = ActionState.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    result: str | None = None
    error: str | None = None
    requires_confirmation: bool = False
    cancellation_reason: str | None = None
    _task: asyncio.Task | None = None

    @property
    def duration_ms(self) -> float | None:
        if self.started_at is not None and self.completed_at is not None:
            return max(0.0, (self.completed_at - self.started_at) * 1000.0)
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "name": self.name,
            "args": self.args,
            "call_id": self.call_id,
            "state": self.state.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "result": self.result,
            "error": self.error,
            "requires_confirmation": self.requires_confirmation,
            "cancellation_reason": self.cancellation_reason,
        }


@dataclass
class LiveActionPlan:
    """Represents a structured sequence of actions planned during a live turn."""

    plan_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    actions: list[ActionItem] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    is_active: bool = True

    def get_action(self, action_id: str) -> ActionItem | None:
        for a in self.actions:
            if a.action_id == action_id:
                return a
        return None

    def pending_actions(self) -> list[ActionItem]:
        return [a for a in self.actions if a.state in (ActionState.PENDING, ActionState.WAITING_CONFIRMATION)]

    def completed_actions(self) -> list[ActionItem]:
        return [a for a in self.actions if a.state == ActionState.COMPLETED]

    def cancelled_actions(self) -> list[ActionItem]:
        return [a for a in self.actions if a.state == ActionState.CANCELLED]

    def active_action(self) -> ActionItem | None:
        for a in self.actions:
            if a.state == ActionState.RUNNING:
                return a
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "total_actions": len(self.actions),
            "pending_count": len(self.pending_actions()),
            "completed_count": len(self.completed_actions()),
            "cancelled_count": len(self.cancelled_actions()),
            "actions": [a.to_dict() for a in self.actions],
        }


class LiveDiagnosticsCollector:
    """High-precision telemetry collector for voice, tool, and barge-in latencies."""

    def __init__(self, max_samples: int = 50) -> None:
        self.max_samples = max_samples
        self._voice_latencies_ms: list[float] = []
        self._tool_latencies_ms: list[float] = []
        self._barge_in_latencies_ms: list[float] = []

        self.turn_start_timestamp: float | None = None
        self.first_token_timestamp: float | None = None
        self.first_audio_timestamp: float | None = None
        self.last_barge_in_timestamp: float | None = None

        self.total_turns: int = 0
        self.total_tools_executed: int = 0
        self.total_actions_cancelled: int = 0
        self.reconnect_count: int = 0

    def record_turn_start(self) -> None:
        self.turn_start_timestamp = time.perf_counter()
        self.first_token_timestamp = None
        self.first_audio_timestamp = None
        self.total_turns += 1

    def record_first_token(self) -> None:
        if self.turn_start_timestamp and self.first_token_timestamp is None:
            self.first_token_timestamp = time.perf_counter()

    def record_first_audio(self) -> float | None:
        if self.turn_start_timestamp and self.first_audio_timestamp is None:
            now = time.perf_counter()
            self.first_audio_timestamp = now
            latency_ms = (now - self.turn_start_timestamp) * 1000.0
            self._voice_latencies_ms.append(latency_ms)
            if len(self._voice_latencies_ms) > self.max_samples:
                self._voice_latencies_ms.pop(0)
            return latency_ms
        return None

    def record_tool_latency(self, duration_ms: float) -> None:
        self.total_tools_executed += 1
        self._tool_latencies_ms.append(duration_ms)
        if len(self._tool_latencies_ms) > self.max_samples:
            self._tool_latencies_ms.pop(0)

    def record_barge_in(self, response_time_ms: float) -> None:
        self.last_barge_in_timestamp = time.time()
        self._barge_in_latencies_ms.append(response_time_ms)
        if len(self._barge_in_latencies_ms) > self.max_samples:
            self._barge_in_latencies_ms.pop(0)

    def record_cancellation(self) -> None:
        self.total_actions_cancelled += 1

    def record_reconnect(self) -> None:
        self.reconnect_count += 1

    @staticmethod
    def _percentile(values: list[float], p: float) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        idx = int(round((len(sorted_vals) - 1) * p))
        return round(sorted_vals[idx], 1)

    def get_summary(self) -> dict[str, Any]:
        return {
            "voice_latency": {
                "p50_ms": self._percentile(self._voice_latencies_ms, 0.50),
                "p95_ms": self._percentile(self._voice_latencies_ms, 0.95),
                "samples": len(self._voice_latencies_ms),
            },
            "tool_latency": {
                "p50_ms": self._percentile(self._tool_latencies_ms, 0.50),
                "p95_ms": self._percentile(self._tool_latencies_ms, 0.95),
                "samples": len(self._tool_latencies_ms),
            },
            "barge_in": {
                "p50_ms": self._percentile(self._barge_in_latencies_ms, 0.50),
                "samples": len(self._barge_in_latencies_ms),
            },
            "total_turns": self.total_turns,
            "total_tools_executed": self.total_tools_executed,
            "total_actions_cancelled": self.total_actions_cancelled,
            "reconnect_count": self.reconnect_count,
        }


class LiveActionOrchestrator:
    """Manages conversational action queues, multi-tool scheduling, and barge-in plan modification."""

    def __init__(
        self,
        session_id: str,
        profile_id: str = "default",
        emit_json: Callable[[dict[str, Any]], Any] | None = None,
        diagnostics: LiveDiagnosticsCollector | None = None,
    ) -> None:
        self.session_id = session_id
        self.profile_id = profile_id
        self.emit_json = emit_json
        self.diagnostics = diagnostics or LiveDiagnosticsCollector()
        self.current_plan: LiveActionPlan | None = None
        self._lock = asyncio.Lock()
        self._action_counter = 0

    async def _notify_plan_updated(self) -> None:
        if self.current_plan and self.emit_json:
            try:
                res = self.emit_json({
                    "type": "action_plan_updated",
                    "plan": self.current_plan.to_dict(),
                })
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.debug("Failed to emit action_plan_updated: %s", e)

    def enqueue_action(self, name: str, args: dict[str, Any], call_id: str) -> ActionItem:
        """Enqueue a single tool call into the current plan."""
        self._action_counter += 1
        action_id = f"act-{self._action_counter}"
        item = ActionItem(
            action_id=action_id,
            name=name,
            args=args,
            call_id=call_id,
            state=ActionState.PENDING,
        )
        if self.current_plan is None or not self.current_plan.is_active:
            self.current_plan = LiveActionPlan(actions=[item])
        else:
            self.current_plan.actions.append(item)
        return item

    def enqueue_batch(self, function_calls: list[dict[str, Any]]) -> LiveActionPlan:
        """Enqueue a batch of sequential function calls from Gemini Live into a unified plan."""
        actions: list[ActionItem] = []
        for fc in function_calls:
            self._action_counter += 1
            action_id = f"act-{self._action_counter}"
            item = ActionItem(
                action_id=action_id,
                name=fc.get("name", ""),
                args=fc.get("args") or {},
                call_id=fc.get("id", ""),
                state=ActionState.PENDING,
            )
            actions.append(item)

        self.current_plan = LiveActionPlan(actions=actions)
        return self.current_plan

    async def cancel_pending_actions(self, reason: str = "user_barge_in") -> list[ActionItem]:
        """Instantly cancels all pending and running actions in the current plan."""
        cancelled: list[ActionItem] = []
        async with self._lock:
            if not self.current_plan:
                return []

            for action in self.current_plan.actions:
                if action.state in (ActionState.PENDING, ActionState.WAITING_CONFIRMATION):
                    action.state = ActionState.CANCELLED
                    action.cancellation_reason = reason
                    action.completed_at = time.time()
                    cancelled.append(action)
                    self.diagnostics.record_cancellation()
                elif action.state == ActionState.RUNNING:
                    # Cancel running task if any
                    action.state = ActionState.CANCELLED
                    action.cancellation_reason = reason
                    action.completed_at = time.time()
                    if action._task and not action._task.done():
                        action._task.cancel()
                    cancelled.append(action)
                    self.diagnostics.record_cancellation()

            if cancelled:
                print(f"\n[LIVE ACTION ORCHESTRADOR] 🛑 Canceladas {len(cancelled)} acción(es) pendientes por: {reason}", flush=True)
                write_log(
                    "LIVE_ACTION_CANCELLED",
                    f"Cancelled {len(cancelled)} action(s) due to {reason}",
                    cancelled_actions=[a.to_dict() for a in cancelled],
                    profile_id=self.profile_id,
                )
                await self._notify_plan_updated()

        return cancelled

    async def execute_action(
        self,
        action: ActionItem,
        send_response_fn: Callable[[str, str], Any],
    ) -> str:
        """Execute an individual action item and deliver the response back."""
        if action.state == ActionState.CANCELLED:
            # Action was cancelled before starting
            cancellation_msg = f"Acción '{action.name}' cancelada por el usuario antes de ejecutarse."
            await send_response_fn(action.call_id, cancellation_msg)
            return cancellation_msg

        action.state = ActionState.RUNNING
        action.started_at = time.time()
        await self._notify_plan_updated()

        print(f"\n[LIVE ACTION ORCHESTRADOR] ▶️ EJECUTANDO [{action.action_id}]: {action.name}(args={action.args})", flush=True)
        write_log(
            "LIVE_ACTION_START",
            f"Executing {action.name}",
            action_id=action.action_id,
            args=action.args,
            call_id=action.call_id,
            profile_id=self.profile_id,
        )

        tool_result = "Acción ejecutada."
        start_mono = time.perf_counter()
        try:
            from core.brain.tool_manager import _invocar_tool_entry

            res = await asyncio.wait_for(
                asyncio.to_thread(
                    _invocar_tool_entry,
                    action.name,
                    action.args,
                    f"Live voice command: {action.name}",
                    source="gemini_live",
                    profile_id=self.profile_id,
                ),
                timeout=15.0,
            )
            if res:
                tool_result = str(res)

            dur_ms = (time.perf_counter() - start_mono) * 1000.0
            self.diagnostics.record_tool_latency(dur_ms)

            # Check if it got cancelled while running
            if action.state == ActionState.CANCELLED:
                tool_result = f"Acción '{action.name}' fue cancelada durante la ejecución."
            else:
                action.state = ActionState.COMPLETED
                action.result = tool_result
                action.completed_at = time.time()

            print(f"[LIVE ACTION ORCHESTRADOR] ✅ COMPLETADO [{action.action_id}]: {action.name} ({dur_ms:.1f}ms) -> {tool_result[:100]}\n", flush=True)
            write_log(
                "LIVE_ACTION_COMPLETE",
                f"Completed {action.name}",
                action_id=action.action_id,
                duration_ms=dur_ms,
                result=tool_result,
                profile_id=self.profile_id,
            )
        except asyncio.CancelledError:
            action.state = ActionState.CANCELLED
            action.cancellation_reason = "cancelled_during_execution"
            action.completed_at = time.time()
            tool_result = f"Acción '{action.name}' abortada por el usuario."
            print(f"[LIVE ACTION ORCHESTRADOR] 🛑 ABORTADO [{action.action_id}]: {action.name}\n", flush=True)
        except TimeoutError:
            action.state = ActionState.FAILED
            action.error = "timeout"
            action.completed_at = time.time()
            tool_result = f"La herramienta {action.name} tardó demasiado tiempo en responder."
            log_warning("gemini_live_action_timeout", action=action.name)
            print(f"[LIVE ACTION ORCHESTRADOR] ⚠️ TIMEOUT [{action.action_id}]: {action.name}\n", flush=True)
        except Exception as e:
            action.state = ActionState.FAILED
            action.error = str(e)
            action.completed_at = time.time()
            tool_result = f"Error al ejecutar {action.name}: {e}"
            log_error("gemini_live_action_failed", action=action.name, error=str(e))
            print(f"[LIVE ACTION ORCHESTRADOR] ❌ ERROR [{action.action_id}]: {tool_result}\n", flush=True)

        await self._notify_plan_updated()

        # Send tool response back to Gemini Bidi stream
        await send_response_fn(action.call_id, tool_result)
        return tool_result
