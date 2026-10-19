"""Single arbitration boundary for all JARVIS command channels."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from core.command_pipeline.execution import validate_plan_operations
from core.command_pipeline.models import (
    ActionPlan,
    CommandOutcome,
    CommandRequest,
    CommandResponse,
    ExecutionReceipt,
    PlanSource,
    ReceiptStatus,
)
from core.command_pipeline.reasoning import ReasoningMode
from core.errors import LLMServiceError, LLMUnavailableError

EventCallback = Callable[[dict[str, Any]], None]
MessageFactory = Callable[[CommandRequest, list[Any]], list[Any]]

_DIRECT_RESPONSE_REPLACEABLE_TOOLS = frozenset({"buscar_en_internet"})


class CommandOrchestrator:
    """Plan, execute, compose, and persist one command exactly once."""

    def __init__(
        self,
        *,
        deterministic,
        groq,
        executor,
        responses,
        history,
        message_factory: MessageFactory | None = None,
        reasoning_mode: ReasoningMode = ReasoningMode.HYBRID,
    ) -> None:
        self._deterministic = deterministic
        self._groq = groq
        self._executor = executor
        self._responses = responses
        self._history = history
        self._message_factory = message_factory
        self._reasoning_mode = ReasoningMode(reasoning_mode)

    @staticmethod
    def _validate_plan(request: CommandRequest, plan: ActionPlan) -> None:
        if plan.request_id != request.request_id:
            raise ValueError("plan_request_mismatch")
        if plan.direct_response and plan.steps:
            raise ValueError("mixed_action_plan")
        validate_plan_operations(plan.steps)
        seen_step_ids: set[str] = set()
        for step in plan.steps:
            if step.step_id in seen_step_ids:
                raise ValueError("duplicate_step_id")
            if any(dependency not in seen_step_ids for dependency in step.depends_on):
                raise ValueError("invalid_step_dependency")
            seen_step_ids.add(step.step_id)

    def _planner_messages(
        self,
        request: CommandRequest,
        history: list[Any],
    ) -> list[Any]:
        if self._message_factory is None:
            return history
        return self._message_factory(request, history)

    @staticmethod
    def _offline_plan(request: CommandRequest) -> ActionPlan:
        if request.language.startswith("es"):
            text = (
                "Estoy sin conexion al servicio de razonamiento. "
                "Reformula la solicitud como un comando local especifico."
            )
        else:
            text = "The reasoning service is offline. Rephrase the request as a specific local command."
        return ActionPlan(
            request_id=request.request_id,
            source=PlanSource.DETERMINISTIC,
            direct_response=text,
            direct_outcome=CommandOutcome.UNAVAILABLE,
            requires_follow_up=True,
            confidence=0.4,
        )

    @staticmethod
    def _keep_action_candidate(
        candidate: ActionPlan | None,
        planned: ActionPlan,
    ) -> bool:
        if candidate is None or not candidate.steps:
            return False
        if planned.steps or planned.requires_follow_up:
            return False
        if not planned.direct_response.strip():
            return False
        return any(step.tool_name not in _DIRECT_RESPONSE_REPLACEABLE_TOOLS for step in candidate.steps)

    def _select_plan(
        self,
        request: CommandRequest,
        history: list[Any],
        candidate: ActionPlan | None,
        send: EventCallback,
    ) -> ActionPlan:
        if candidate is not None:
            self._validate_plan(request, candidate)
            if candidate.conversation_only:
                return candidate

        if self._reasoning_mode is ReasoningMode.OFFLINE:
            return candidate or self._offline_plan(request)
        if self._reasoning_mode is ReasoningMode.HYBRID and candidate is not None:
            return candidate

        send(
            {
                "type": "status",
                "text": "planning",
                "request_id": request.request_id,
                "reasoning_mode": self._reasoning_mode.value,
                "candidate": candidate is not None,
            }
        )
        try:
            planned = self._groq.plan(
                request,
                self._planner_messages(request, history),
                candidate_plan=candidate,
            )
        except (LLMUnavailableError, LLMServiceError):
            if candidate is None:
                raise
            send(
                {
                    "type": "status",
                    "text": "reasoning degraded",
                    "request_id": request.request_id,
                    "reasoning_mode": self._reasoning_mode.value,
                    "fallback": "deterministic",
                }
            )
            return candidate

        self._validate_plan(request, planned)
        if candidate is not None and self._keep_action_candidate(candidate, planned):
            return candidate
        return planned

    def process(
        self,
        request: CommandRequest,
        emit: EventCallback | None = None,
    ) -> CommandResponse:
        t_start = time.perf_counter()
        send = emit or (lambda _event: None)
        print(f"\n[COMANDO] >> RECIBIDO: canal={request.channel} | perfil={request.profile_id}", flush=True)
        send(
            {
                "type": "status",
                "text": "understanding",
                "request_id": request.request_id,
            }
        )

        t_plan_start = time.perf_counter()
        history = self._history.get_history(request.profile_id)
        candidate = self._deterministic.plan(request)
        plan = self._select_plan(request, history, candidate, send)
        t_plan_end = time.perf_counter()
        plan_ms = round((t_plan_end - t_plan_start) * 1000.0, 1)
        self._validate_plan(request, plan)

        if plan.steps:
            print(f"[PLANIFICADOR] Plan con herramientas: {[s.tool_name for s in plan.steps]} (fuente={plan.source.value})", flush=True)
        elif plan.direct_response:
            print(f"[PLANIFICADOR] Plan de respuesta directa (fuente={plan.source.value})", flush=True)

        receipts: tuple[ExecutionReceipt, ...] = ()
        t_exec_start = time.perf_counter()
        if plan.steps:
            send(
                {
                    "type": "status",
                    "text": "executing",
                    "request_id": request.request_id,
                }
            )
            collected_receipts: list[ExecutionReceipt] = []
            receipts_by_step: dict[str, ExecutionReceipt] = {}
            for step in plan.steps:
                dependency_satisfied = all(
                    receipts_by_step[dependency].status
                    in {ReceiptStatus.SUCCEEDED, ReceiptStatus.DUPLICATE}
                    for dependency in step.depends_on
                )
                if dependency_satisfied:
                    receipt = self._executor.execute(request, step)
                else:
                    receipt = ExecutionReceipt(
                        request_id=request.request_id,
                        step_id=step.step_id,
                        tool_name=step.tool_name,
                        status=ReceiptStatus.BLOCKED,
                        result=None,
                        user_message=(
                            "No ejecuté esta acción porque falló un paso necesario."
                            if request.language.startswith("es")
                            else "I did not run this action because a required step failed."
                        ),
                        verified=False,
                        diagnostic_code="dependency_not_satisfied",
                    )
                collected_receipts.append(receipt)
                receipts_by_step[step.step_id] = receipt
            receipts = tuple(collected_receipts)
            for step, receipt in zip(plan.steps, receipts, strict=True):
                if (
                    receipt.request_id != request.request_id
                    or receipt.step_id != step.step_id
                    or receipt.tool_name != step.tool_name
                ):
                    raise ValueError("receipt_operation_mismatch")

        t_exec_end = time.perf_counter()
        exec_ms = round((t_exec_end - t_exec_start) * 1000.0, 1)
        total_ms = round((time.perf_counter() - t_start) * 1000.0, 1)

        provider_label = getattr(self._groq, "model_name", "LLM") if plan.source == PlanSource.GROQ else plan.source.value
        metrics = {
            "total_ms": total_ms,
            "planning_ms": plan_ms,
            "execution_ms": exec_ms,
            "source": plan.source.value,
            "provider": provider_label,
        }

        try:
            print(
                f"[METRICAS RENDIMIENTO] Total: {total_ms:.0f}ms | IA/Plan: {plan_ms:.0f}ms ({provider_label}) | Tools: {exec_ms:.0f}ms\n",
                flush=True,
            )
        except Exception:
            pass

        response = self._responses.compose(request, plan, receipts, metrics=metrics)
        try:
            print(f"[RESPUESTA JARVIS] << resultado={response.outcome.value}\n", flush=True)
        except Exception:
            pass
        self._history.append_interaction(request, response)
        send({"type": "done", **response.to_dict()})
        return response
