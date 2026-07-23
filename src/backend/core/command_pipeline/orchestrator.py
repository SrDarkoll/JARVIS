"""Single arbitration boundary for all JARVIS command channels."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.command_pipeline.execution import validate_plan_operations
from core.command_pipeline.models import (
    ActionPlan,
    CommandRequest,
    CommandResponse,
    ExecutionReceipt,
)

EventCallback = Callable[[dict[str, Any]], None]
MessageFactory = Callable[[CommandRequest, list[Any]], list[Any]]


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
    ) -> None:
        self._deterministic = deterministic
        self._groq = groq
        self._executor = executor
        self._responses = responses
        self._history = history
        self._message_factory = message_factory

    def process(
        self,
        request: CommandRequest,
        emit: EventCallback | None = None,
    ) -> CommandResponse:
        send = emit or (lambda _event: None)
        send(
            {
                "type": "status",
                "text": "understanding",
                "request_id": request.request_id,
            }
        )

        history = self._history.get_history(request.profile_id)
        plan: ActionPlan | None = self._deterministic.plan(request)
        if plan is None:
            send(
                {
                    "type": "status",
                    "text": "planning",
                    "request_id": request.request_id,
                }
            )
            planner_messages = (
                self._message_factory(request, history)
                if self._message_factory is not None
                else history
            )
            plan = self._groq.plan(request, planner_messages)

        if plan.request_id != request.request_id:
            raise ValueError("plan_request_mismatch")
        if plan.direct_response and plan.steps:
            raise ValueError("mixed_action_plan")
        validate_plan_operations(plan.steps)

        receipts: tuple[ExecutionReceipt, ...] = ()
        if plan.steps:
            send(
                {
                    "type": "status",
                    "text": "executing",
                    "request_id": request.request_id,
                }
            )
            receipts = tuple(
                self._executor.execute(request, step) for step in plan.steps
            )
            for step, receipt in zip(plan.steps, receipts, strict=True):
                if (
                    receipt.request_id != request.request_id
                    or receipt.step_id != step.step_id
                    or receipt.tool_name != step.tool_name
                ):
                    raise ValueError("receipt_operation_mismatch")

        response = self._responses.compose(request, plan, receipts)
        self._history.append_interaction(request, response)
        send({"type": "done", **response.to_dict()})
        return response
