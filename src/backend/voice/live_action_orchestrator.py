"""Live Action Orchestrator for full-duplex conversational execution in J.A.R.V.I.S.

Provides multi-tool sequential execution, real-time action lifecycle states
(pending, running, completed, cancelled, failed, waiting_confirmation),
and conversational barge-in plan modification/cancellation.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.command_pipeline.models import ExecutionReceipt, ReceiptStatus
from core.runtime_logger import log_error, log_warning
from core.unified_log import write_log

from voice.live_tool_execution import live_action_state, live_tool_response

logger = logging.getLogger(__name__)


class ActionState(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    CANCELLATION_REQUESTED = "cancellation_requested"
    TIMED_OUT = "timed_out"
    COMPLETED_AFTER_CANCELLATION = "completed_after_cancellation"
    COMPLETED_AFTER_TIMEOUT = "completed_after_timeout"
    FAILED = "failed"
    WAITING_CONFIRMATION = "waiting_confirmation"
    SUCCEEDED = "succeeded"
    DUPLICATE = "duplicate"
    CONFIRMATION_REQUIRED = "confirmation_required"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


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
    execution_may_continue: bool = False
    worker_completed_at: float | None = None
    receipt: ExecutionReceipt | None = None
    _task: asyncio.Task | None = None
    _worker_task: asyncio.Task | None = None

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
            "execution_may_continue": self.execution_may_continue,
            "worker_completed_at": self.worker_completed_at,
            "receipt_status": self.receipt.status.value if self.receipt else "",
            "diagnostic_code": self.receipt.diagnostic_code if self.receipt else "",
            "verified": self.receipt.verified if self.receipt else False,
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
        completed_states = {
            ActionState.COMPLETED,
            ActionState.SUCCEEDED,
            ActionState.DUPLICATE,
            ActionState.COMPLETED_AFTER_CANCELLATION,
            ActionState.COMPLETED_AFTER_TIMEOUT,
        }
        return [a for a in self.actions if a.state in completed_states]

    def cancelled_actions(self) -> list[ActionItem]:
        return [a for a in self.actions if a.state == ActionState.CANCELLED]

    def active_action(self) -> ActionItem | None:
        active_states = {
            ActionState.RUNNING,
            ActionState.CANCELLATION_REQUESTED,
            ActionState.TIMED_OUT,
        }
        for a in self.actions:
            if a.state in active_states and (a._worker_task is None or not a._worker_task.done()):
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
        self._first_token_latencies_ms: list[float] = []
        self._turn_latencies_ms: list[float] = []
        self._tool_latencies_ms: list[float] = []
        self._barge_in_latencies_ms: list[float] = []

        self.turn_start_timestamp: float | None = None
        self.first_token_timestamp: float | None = None
        self.first_audio_timestamp: float | None = None
        self.last_barge_in_timestamp: float | None = None
        self.last_turn: dict[str, float] = {}
        self._turn_active = False

        self.total_turns: int = 0
        self.total_tools_executed: int = 0
        self.total_actions_cancelled: int = 0
        self.reconnect_count: int = 0

    def record_turn_start(self) -> bool:
        if self._turn_active:
            return False
        self._turn_active = True
        self.turn_start_timestamp = time.perf_counter()
        self.first_token_timestamp = None
        self.first_audio_timestamp = None
        self.total_turns += 1
        return True

    def record_first_token(self) -> float | None:
        if self.turn_start_timestamp and self.first_token_timestamp is None:
            now = time.perf_counter()
            self.first_token_timestamp = now
            latency_ms = (now - self.turn_start_timestamp) * 1000.0
            self._first_token_latencies_ms.append(latency_ms)
            if len(self._first_token_latencies_ms) > self.max_samples:
                self._first_token_latencies_ms.pop(0)
            return latency_ms
        return None

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

    def record_turn_complete(self) -> dict[str, float]:
        if not self._turn_active or self.turn_start_timestamp is None:
            return dict(self.last_turn)

        now = time.perf_counter()
        total_ms = max(0.0, (now - self.turn_start_timestamp) * 1000.0)
        ttft_ms = (
            max(0.0, (self.first_token_timestamp - self.turn_start_timestamp) * 1000.0)
            if self.first_token_timestamp is not None
            else 0.0
        )
        ttfa_ms = (
            max(0.0, (self.first_audio_timestamp - self.turn_start_timestamp) * 1000.0)
            if self.first_audio_timestamp is not None
            else 0.0
        )
        self._turn_latencies_ms.append(total_ms)
        if len(self._turn_latencies_ms) > self.max_samples:
            self._turn_latencies_ms.pop(0)
        self.last_turn = {
            "total_ms": round(total_ms, 1),
            "ttft_ms": round(ttft_ms, 1),
            "ttfa_ms": round(ttfa_ms, 1),
        }
        self._turn_active = False
        self.turn_start_timestamp = None
        self.first_token_timestamp = None
        self.first_audio_timestamp = None
        return dict(self.last_turn)

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
            "first_token_latency": {
                "p50_ms": self._percentile(self._first_token_latencies_ms, 0.50),
                "p95_ms": self._percentile(self._first_token_latencies_ms, 0.95),
                "samples": len(self._first_token_latencies_ms),
            },
            "turn_latency": {
                "p50_ms": self._percentile(self._turn_latencies_ms, 0.50),
                "p95_ms": self._percentile(self._turn_latencies_ms, 0.95),
                "samples": len(self._turn_latencies_ms),
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
            "last_turn": dict(self.last_turn),
        }


class LiveActionOrchestrator:
    """Manages conversational action queues, multi-tool scheduling, and barge-in plan modification."""

    def __init__(
        self,
        session_id: str,
        profile_id: str = "default",
        emit_json: Callable[[dict[str, Any]], Any] | None = None,
        diagnostics: LiveDiagnosticsCollector | None = None,
        invoke_tool: Callable[[str, dict[str, Any], str, str, str], Any] | None = None,
        action_timeout_seconds: float = 15.0,
    ) -> None:
        self.session_id = session_id
        self.profile_id = profile_id
        self.emit_json = emit_json
        self.diagnostics = diagnostics or LiveDiagnosticsCollector()
        self._invoke_tool = invoke_tool
        self.action_timeout_seconds = max(0.01, float(action_timeout_seconds))
        self.current_plan: LiveActionPlan | None = None
        self._lock = asyncio.Lock()
        self._execution_lock = asyncio.Lock()
        self._action_counter = 0
        self._plans: dict[str, LiveActionPlan] = {}
        self._worker_tasks: set[asyncio.Task] = set()

    def _register_plan(self, plan: LiveActionPlan) -> LiveActionPlan:
        self._plans[plan.plan_id] = plan
        self.current_plan = plan
        return plan

    def active_plans(self) -> list[LiveActionPlan]:
        return [plan for plan in self._plans.values() if plan.is_active]

    def _owner_plan(self, action: ActionItem) -> LiveActionPlan | None:
        for plan in self._plans.values():
            if any(candidate is action for candidate in plan.actions):
                return plan
        return None

    @staticmethod
    def _refresh_plan_activity(plan: LiveActionPlan | None) -> None:
        if plan is None:
            return
        active_states = {
            ActionState.PENDING,
            ActionState.WAITING_CONFIRMATION,
            ActionState.CONFIRMATION_REQUIRED,
            ActionState.RUNNING,
            ActionState.CANCELLATION_REQUESTED,
            ActionState.TIMED_OUT,
        }
        plan.is_active = any(action.state in active_states for action in plan.actions)

    async def _notify_plan_updated(self, plan: LiveActionPlan | None = None) -> None:
        target_plan = plan or self.current_plan
        if target_plan and self.emit_json:
            try:
                res = self.emit_json({
                    "type": "action_plan_updated",
                    "plan": target_plan.to_dict(),
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
            self._register_plan(LiveActionPlan(actions=[item]))
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

        return self._register_plan(LiveActionPlan(actions=actions))

    async def cancel_pending_actions(self, reason: str = "user_barge_in") -> list[ActionItem]:
        """Instantly cancels all pending and running actions in the current plan."""
        cancelled: list[ActionItem] = []
        async with self._lock:
            active_plans = self.active_plans()
            if not active_plans:
                return []

            changed_plans: list[LiveActionPlan] = []
            for plan in active_plans:
                plan_changed = False
                for action in plan.actions:
                    if action.state in (
                        ActionState.PENDING,
                        ActionState.WAITING_CONFIRMATION,
                        ActionState.CONFIRMATION_REQUIRED,
                    ):
                        action.state = ActionState.CANCELLED
                        action.cancellation_reason = reason
                        action.completed_at = time.time()
                        cancelled.append(action)
                        plan_changed = True
                        self.diagnostics.record_cancellation()
                    elif action.state == ActionState.RUNNING:
                        action.state = ActionState.CANCELLATION_REQUESTED
                        action.cancellation_reason = reason
                        action.execution_may_continue = bool(
                            action._worker_task and not action._worker_task.done()
                        )
                        cancelled.append(action)
                        plan_changed = True
                        self.diagnostics.record_cancellation()
                self._refresh_plan_activity(plan)
                if plan_changed:
                    changed_plans.append(plan)

            if cancelled:
                print(
                    f"\n[LIVE ACTION ORCHESTRADOR] 🛑 Cancelación solicitada para "
                    f"{len(cancelled)} acción(es) por: {reason}",
                    flush=True,
                )
                write_log(
                    "LIVE_ACTION_CANCELLATION_REQUESTED",
                    f"Cancellation requested for {len(cancelled)} action(s) due to {reason}",
                    cancelled_actions=[a.to_dict() for a in cancelled],
                    profile_id=self.profile_id,
                )
                for plan in changed_plans:
                    await self._notify_plan_updated(plan)

        return cancelled

    async def execute_action(
        self,
        action: ActionItem,
        send_response_fn: Callable[[str, str], Any],
    ) -> str:
        """Execute one action without overstating thread cancellation guarantees."""
        plan = self._owner_plan(action)
        tool_result = "Acción ejecutada."

        async with self._execution_lock:
            if action.state == ActionState.CANCELLED:
                tool_result = f"Acción '{action.name}' cancelada por el usuario antes de ejecutarse."
            elif self._has_unfinished_worker(exclude=action):
                action.state = ActionState.FAILED
                action.error = "previous_action_still_running"
                action.completed_at = time.time()
                tool_result = (
                    f"No inicié '{action.name}' porque una acción anterior todavía puede estar ejecutándose."
                )
            else:
                action.state = ActionState.RUNNING
                action.started_at = time.time()
                await self._notify_plan_updated(plan)

                print(
                    f"\n[LIVE ACTION ORCHESTRADOR] ▶️ EJECUTANDO [{action.action_id}]: "
                    f"{action.name}(args={action.args})",
                    flush=True,
                )
                write_log(
                    "LIVE_ACTION_START",
                    f"Executing {action.name}",
                    action_id=action.action_id,
                    args=action.args,
                    call_id=action.call_id,
                    profile_id=self.profile_id,
                )

                start_mono = time.perf_counter()
                worker_task = asyncio.create_task(
                    asyncio.to_thread(
                        self._invoke_tool_sync,
                        action,
                    )
                )
                action._worker_task = worker_task
                action.execution_may_continue = True
                self._worker_tasks.add(worker_task)
                worker_task.add_done_callback(
                    lambda completed, item=action, owner=plan: self._worker_finished(
                        item,
                        owner,
                        completed,
                    )
                )

                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(worker_task),
                        timeout=self.action_timeout_seconds,
                    )
                    receipt = self._coerce_receipt(action, result)
                    action.receipt = receipt
                    presentation = live_tool_response(receipt)
                    tool_result = json.dumps(presentation, ensure_ascii=False)

                    dur_ms = (time.perf_counter() - start_mono) * 1000.0
                    if action.state in {
                        ActionState.CANCELLATION_REQUESTED,
                        ActionState.COMPLETED_AFTER_CANCELLATION,
                    }:
                        action.state = ActionState.COMPLETED_AFTER_CANCELLATION
                        action.result = receipt.user_message
                        action.completed_at = action.completed_at or time.time()
                        action.execution_may_continue = False
                        tool_result = (
                            f"La cancelación de '{action.name}' se solicitó después de iniciar; "
                            f"la herramienta terminó: {receipt.user_message}"
                        )
                    else:
                        action.state = ActionState(live_action_state(receipt))
                        action.result = receipt.user_message
                        action.error = receipt.diagnostic_code or None
                        action.requires_confirmation = (
                            action.state is ActionState.CONFIRMATION_REQUIRED
                        )
                        action.completed_at = time.time()
                        action.execution_may_continue = False
                        self.diagnostics.record_tool_latency(dur_ms)

                    print(
                        f"[LIVE ACTION ORCHESTRADOR] ✅ FINALIZADO [{action.action_id}]: "
                        f"{action.name} ({dur_ms:.1f}ms) -> {tool_result[:100]}\n",
                        flush=True,
                    )
                    write_log(
                        "LIVE_ACTION_COMPLETE",
                        f"Completed {action.name}",
                        action_id=action.action_id,
                        duration_ms=dur_ms,
                        result=tool_result,
                        state=action.state.value,
                        profile_id=self.profile_id,
                    )
                except asyncio.CancelledError:
                    action.state = ActionState.CANCELLATION_REQUESTED
                    action.cancellation_reason = (
                        action.cancellation_reason or "tracking_task_cancelled"
                    )
                    action.execution_may_continue = not worker_task.done()
                    tool_result = (
                        f"Se solicitó cancelar '{action.name}', pero la herramienta ya había "
                        "iniciado y puede continuar en segundo plano."
                    )
                    print(
                        f"[LIVE ACTION ORCHESTRADOR] 🛑 CANCELACIÓN SOLICITADA "
                        f"[{action.action_id}]: {action.name}\n",
                        flush=True,
                    )
                except TimeoutError:
                    action.state = ActionState.TIMED_OUT
                    action.error = "timeout_worker_may_continue"
                    action.execution_may_continue = not worker_task.done()
                    tool_result = (
                        f"La herramienta {action.name} superó el tiempo de espera y puede "
                        "continuar ejecutándose en segundo plano."
                    )
                    if worker_task.done():
                        self._worker_finished(action, plan, worker_task)
                    log_warning(
                        "gemini_live_action_timeout_worker_may_continue",
                        action=action.name,
                    )
                    print(
                        f"[LIVE ACTION ORCHESTRADOR] ⚠️ TIMEOUT NO PREEMPTIVO "
                        f"[{action.action_id}]: {action.name}\n",
                        flush=True,
                    )
                except Exception as exc:
                    action.state = ActionState.FAILED
                    action.error = type(exc).__name__
                    action.completed_at = time.time()
                    action.execution_may_continue = False
                    tool_result = f"Error al ejecutar {action.name}."
                    log_error(
                        "gemini_live_action_failed",
                        action=action.name,
                        error=type(exc).__name__,
                    )
                    print(
                        f"[LIVE ACTION ORCHESTRADOR] ❌ ERROR [{action.action_id}]: "
                        f"{tool_result}\n",
                        flush=True,
                    )

            self._refresh_plan_activity(plan)
            await self._notify_plan_updated(plan)

        response = send_response_fn(action.call_id, tool_result)
        if asyncio.iscoroutine(response):
            await response
        return tool_result

    def _invoke_tool_sync(self, action: ActionItem) -> Any:
        invoker = self._invoke_tool
        if invoker is None:
            from core.brain.tool_manager import execute_tool_receipt

            return execute_tool_receipt(
                action.name,
                dict(action.args),
                f"Live voice command: {action.name}",
                "gemini_live",
                self.profile_id,
                f"live-{self.session_id}",
                action.action_id,
            )
        return invoker(
            action.name,
            dict(action.args),
            f"Live voice command: {action.name}",
            "gemini_live",
            self.profile_id,
        )

    def _coerce_receipt(
        self,
        action: ActionItem,
        result: Any,
    ) -> ExecutionReceipt:
        if isinstance(result, ExecutionReceipt):
            return result
        message = str(result or "")
        return ExecutionReceipt(
            request_id=f"live-{self.session_id}",
            step_id=action.action_id,
            tool_name=action.name,
            status=ReceiptStatus.SUCCEEDED,
            result=result,
            user_message=message,
            verified=True,
            diagnostic_code="",
        )

    def _has_unfinished_worker(self, *, exclude: ActionItem | None = None) -> bool:
        for plan in self._plans.values():
            for action in plan.actions:
                if action is exclude:
                    continue
                if action._worker_task is not None and not action._worker_task.done():
                    return True
        return False

    def _worker_finished(
        self,
        action: ActionItem,
        plan: LiveActionPlan | None,
        worker_task: asyncio.Task,
    ) -> None:
        self._worker_tasks.discard(worker_task)

        if worker_task.cancelled():
            action.execution_may_continue = True
            return

        action.worker_completed_at = time.time()

        try:
            result = worker_task.result()
        except Exception as exc:
            if action.state in {
                ActionState.CANCELLATION_REQUESTED,
                ActionState.TIMED_OUT,
            }:
                action.state = ActionState.FAILED
                action.error = type(exc).__name__
                action.completed_at = time.time()
                action.execution_may_continue = False
        else:
            receipt = self._coerce_receipt(action, result)
            action.receipt = receipt
            late_completion = action.state in {
                ActionState.CANCELLATION_REQUESTED,
                ActionState.TIMED_OUT,
            }
            if action.state == ActionState.CANCELLATION_REQUESTED:
                action.state = ActionState.COMPLETED_AFTER_CANCELLATION
            elif action.state == ActionState.TIMED_OUT:
                action.state = ActionState.COMPLETED_AFTER_TIMEOUT

            if late_completion:
                action.result = receipt.user_message
                action.completed_at = time.time()
                action.execution_may_continue = False
                if action.started_at is not None:
                    self.diagnostics.record_tool_latency(
                        max(0.0, (action.completed_at - action.started_at) * 1000.0)
                    )
                write_log(
                    "LIVE_ACTION_LATE_COMPLETION",
                    f"Late completion for {action.name}",
                    action_id=action.action_id,
                    state=action.state.value,
                    profile_id=self.profile_id,
                )

        self._refresh_plan_activity(plan)
        try:
            loop = asyncio.get_running_loop()
            if not loop.is_closed():
                loop.create_task(self._notify_plan_updated(plan))
        except RuntimeError:
            pass
