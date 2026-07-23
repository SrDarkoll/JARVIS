"""One-round Groq planner that cannot execute tools."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from core.brain import brain_utils
from core.command_pipeline.execution import validate_plan_operations
from core.command_pipeline.models import (
    ActionPlan,
    ActionStep,
    CommandRequest,
    PlanSource,
)


class GroqPlanner:
    """Convert one model response into a validated, execution-free plan."""

    def __init__(
        self,
        model: Any,
        *,
        allowed_tools: Iterable[str],
        max_steps: int = 5,
    ) -> None:
        if max_steps < 1:
            raise ValueError("invalid_groq_max_steps")
        self._model = model
        self._allowed_tools = frozenset(
            str(name).strip() for name in allowed_tools if str(name).strip()
        )
        self._max_steps = int(max_steps)

    def plan(
        self,
        request: CommandRequest,
        messages: list[Any],
    ) -> ActionPlan:
        """Invoke the model once and validate its proposed response or steps."""
        response = self._model.invoke(messages)
        tool_calls = tuple(getattr(response, "tool_calls", None) or ())
        content = brain_utils._limpiar_thinking(
            str(getattr(response, "content", "") or "")
        )

        if not tool_calls:
            return ActionPlan(
                request_id=request.request_id,
                source=PlanSource.GROQ,
                direct_response=content,
            )
        if content:
            raise ValueError("mixed_groq_plan")
        if len(tool_calls) > self._max_steps:
            raise ValueError("groq_plan_too_large")

        steps = tuple(
            self._parse_tool_call(tool_call, index=index)
            for index, tool_call in enumerate(tool_calls, start=1)
        )
        validate_plan_operations(steps)
        return ActionPlan(
            request_id=request.request_id,
            source=PlanSource.GROQ,
            steps=steps,
        )

    def _parse_tool_call(
        self,
        tool_call: Any,
        *,
        index: int,
    ) -> ActionStep:
        if not isinstance(tool_call, dict):
            raise ValueError("invalid_groq_tool_call")

        name = str(tool_call.get("name") or "").strip()
        arguments = tool_call.get("args")
        if name not in self._allowed_tools or not isinstance(arguments, dict):
            raise ValueError("invalid_groq_tool_call")

        return ActionStep(
            step_id=str(tool_call.get("id") or f"groq-{index}"),
            tool_name=name,
            arguments=arguments,
        )
