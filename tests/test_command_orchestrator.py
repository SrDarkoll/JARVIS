from __future__ import annotations

from dataclasses import replace

import pytest
from core.command_pipeline.models import (
    ActionPlan,
    ActionStep,
    CommandRequest,
    ExecutionReceipt,
    PlanSource,
    ReceiptStatus,
)
from core.command_pipeline.orchestrator import CommandOrchestrator
from core.command_pipeline.reasoning import ReasoningMode
from core.command_pipeline.responses import ResponseComposer
from core.errors import LLMServiceError, LLMUnavailableError


def _request(request_id: str = "request-1") -> CommandRequest:
    return CommandRequest.create(
        text="clima en Matamoros",
        profile_id="admin",
        channel="chat",
        language="es",
        request_id=request_id,
    )


class FakeDeterministicPlanner:
    def __init__(self, plan: ActionPlan | None) -> None:
        self.plan_value = plan
        self.calls = 0

    def plan(self, _request: CommandRequest) -> ActionPlan | None:
        self.calls += 1
        return self.plan_value


class FakeGroqPlanner:
    def __init__(self, plan: ActionPlan) -> None:
        self.plan_value = plan
        self.calls = []

    def plan(
        self,
        command_request: CommandRequest,
        history: list,
        *,
        candidate_plan: ActionPlan | None = None,
    ) -> ActionPlan:
        self.calls.append(
            (
                command_request.request_id,
                list(history),
                candidate_plan,
            )
        )
        return self.plan_value


class FailingGroqPlanner:
    def plan(
        self,
        _request: CommandRequest,
        _history: list,
        *,
        candidate_plan: ActionPlan | None = None,
    ) -> ActionPlan:
        raise AssertionError("Groq must not run")


class ErrorGroqPlanner:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def plan(
        self,
        _request: CommandRequest,
        _history: list,
        *,
        candidate_plan: ActionPlan | None = None,
    ) -> ActionPlan:
        raise self.error


class RecordingExecutor:
    def __init__(
        self,
        result: str = "Soleado, 28 C",
        *,
        status: ReceiptStatus = ReceiptStatus.SUCCEEDED,
    ) -> None:
        self.result = result
        self.status = status
        self.calls = []

    def execute(
        self,
        command_request: CommandRequest,
        step: ActionStep,
    ) -> ExecutionReceipt:
        self.calls.append(
            (command_request.request_id, step.step_id, step.tool_name)
        )
        return ExecutionReceipt(
            request_id=command_request.request_id,
            step_id=step.step_id,
            tool_name=step.tool_name,
            status=self.status,
            result=self.result if self.status is ReceiptStatus.SUCCEEDED else None,
            user_message=self.result,
            verified=self.status is ReceiptStatus.SUCCEEDED,
            diagnostic_code="" if self.status is ReceiptStatus.SUCCEEDED else "blocked",
        )


class FakeHistory:
    def __init__(self) -> None:
        self.history = ["previous"]
        self.interactions = []

    def get_history(self, _profile_id: str) -> list:
        return list(self.history)

    def append_interaction(
        self,
        command_request: CommandRequest,
        response,
    ) -> None:
        self.interactions.append((command_request, response))


def _orchestrator(
    *,
    deterministic,
    groq,
    executor,
    history: FakeHistory | None = None,
    message_factory=None,
    reasoning_mode: ReasoningMode = ReasoningMode.HYBRID,
) -> CommandOrchestrator:
    return CommandOrchestrator(
        deterministic=deterministic,
        groq=groq,
        executor=executor,
        responses=ResponseComposer(),
        history=history or FakeHistory(),
        message_factory=message_factory,
        reasoning_mode=reasoning_mode,
    )


def test_deterministic_direct_response_skips_groq_and_tools() -> None:
    plan = ActionPlan(
        request_id="request-1",
        source=PlanSource.DETERMINISTIC,
        direct_response="Son las 10:30.",
    )
    executor = RecordingExecutor()
    orchestrator = _orchestrator(
        deterministic=FakeDeterministicPlanner(plan),
        groq=FailingGroqPlanner(),
        executor=executor,
    )

    response = orchestrator.process(_request())

    assert response.text == "Son las 10:30."
    assert response.outcome == "succeeded"
    assert executor.calls == []


def test_deterministic_tool_plan_wins_without_calling_groq() -> None:
    plan = ActionPlan(
        request_id="request-1",
        source=PlanSource.DETERMINISTIC,
        steps=(
            ActionStep(
                step_id="step-1",
                tool_name="obtener_clima",
                arguments={"ciudad": "Matamoros"},
            ),
        ),
    )
    executor = RecordingExecutor()
    orchestrator = _orchestrator(
        deterministic=FakeDeterministicPlanner(plan),
        groq=FailingGroqPlanner(),
        executor=executor,
    )

    response = orchestrator.process(_request())

    assert executor.calls == [
        ("request-1", "step-1", "obtener_clima")
    ]
    assert response.text == "Soleado, 28 C"


def test_unresolved_command_calls_groq_once_with_profile_history() -> None:
    groq = FakeGroqPlanner(
        ActionPlan(
            request_id="request-1",
            source=PlanSource.GROQ,
            direct_response="Respuesta conversacional.",
        )
    )
    history = FakeHistory()
    orchestrator = _orchestrator(
        deterministic=FakeDeterministicPlanner(None),
        groq=groq,
        executor=RecordingExecutor(),
        history=history,
    )

    response = orchestrator.process(_request())

    assert groq.calls == [("request-1", ["previous"], None)]
    assert response.text == "Respuesta conversacional."
    assert len(history.interactions) == 1


def test_unresolved_command_uses_message_factory_without_changing_history() -> None:
    groq = FakeGroqPlanner(
        ActionPlan(
            request_id="request-1",
            source=PlanSource.GROQ,
            direct_response="Respuesta con contexto.",
        )
    )
    history = FakeHistory()
    factory_calls = []

    def message_factory(command_request, profile_history):
        factory_calls.append(
            (command_request.request_id, list(profile_history))
        )
        return ["system", *profile_history, "current"]

    orchestrator = _orchestrator(
        deterministic=FakeDeterministicPlanner(None),
        groq=groq,
        executor=RecordingExecutor(),
        history=history,
        message_factory=message_factory,
    )

    orchestrator.process(_request())

    assert factory_calls == [("request-1", ["previous"])]
    assert groq.calls == [
        ("request-1", ["system", "previous", "current"], None)
    ]
    assert history.history == ["previous"]


def test_blocked_receipt_marks_failure_and_requests_follow_up() -> None:
    plan = ActionPlan(
        request_id="request-1",
        source=PlanSource.DETERMINISTIC,
        steps=(ActionStep("step-1", "controlar_pc"),),
    )
    orchestrator = _orchestrator(
        deterministic=FakeDeterministicPlanner(plan),
        groq=FailingGroqPlanner(),
        executor=RecordingExecutor(
            "Necesito confirmacion explicita.",
            status=ReceiptStatus.BLOCKED,
        ),
    )

    response = orchestrator.process(_request())

    assert response.outcome == "failed"
    assert response.should_listen is True
    assert response.text == "Necesito confirmacion explicita."


def test_duplicate_receipts_do_not_repeat_success_message() -> None:
    request = _request()
    plan = ActionPlan(
        request_id=request.request_id,
        source=PlanSource.DETERMINISTIC,
        steps=(
            ActionStep("step-1", "obtener_clima"),
            ActionStep("step-2", "obtener_hora"),
        ),
    )
    succeeded = ExecutionReceipt(
        request_id=request.request_id,
        step_id="step-1",
        tool_name="obtener_clima",
        status=ReceiptStatus.SUCCEEDED,
        result="Listo.",
        user_message="Listo.",
        verified=True,
        diagnostic_code="",
    )
    duplicate = replace(
        succeeded,
        step_id="step-2",
        tool_name="obtener_hora",
        status=ReceiptStatus.DUPLICATE,
    )

    response = ResponseComposer().compose(
        request,
        plan,
        (succeeded, duplicate),
    )

    assert response.text == "Listo."
    assert response.outcome == "succeeded"


def test_event_and_direct_results_share_the_same_final_response() -> None:
    plan = ActionPlan(
        request_id="request-1",
        source=PlanSource.DETERMINISTIC,
        direct_response="Todo listo.",
    )
    events = []
    orchestrator = _orchestrator(
        deterministic=FakeDeterministicPlanner(plan),
        groq=FailingGroqPlanner(),
        executor=RecordingExecutor(),
    )

    response = orchestrator.process(_request(), emit=events.append)

    assert events[-1]["type"] == "done"
    assert events[-1]["text"] == response.text
    assert events[-1]["outcome"] == response.outcome


def test_plan_for_another_request_is_rejected_before_execution() -> None:
    plan = ActionPlan(
        request_id="wrong-request",
        source=PlanSource.DETERMINISTIC,
        steps=(ActionStep("step-1", "obtener_clima"),),
    )
    executor = RecordingExecutor()
    orchestrator = _orchestrator(
        deterministic=FakeDeterministicPlanner(plan),
        groq=FailingGroqPlanner(),
        executor=executor,
    )

    with pytest.raises(ValueError, match="plan_request_mismatch"):
        orchestrator.process(_request())

    assert executor.calls == []


def test_always_mode_asks_groq_to_validate_deterministic_candidate() -> None:
    candidate = ActionPlan(
        request_id="request-1",
        source=PlanSource.DETERMINISTIC,
        steps=(
            ActionStep(
                "candidate-step",
                "buscar_en_internet",
                {"query": "capacidad de razonar"},
            ),
        ),
    )
    groq_plan = ActionPlan(
        request_id="request-1",
        source=PlanSource.GROQ,
        direct_response="Si. Puedo analizar y planificar solicitudes.",
    )
    groq = FakeGroqPlanner(groq_plan)
    executor = RecordingExecutor()
    orchestrator = _orchestrator(
        deterministic=FakeDeterministicPlanner(candidate),
        groq=groq,
        executor=executor,
        reasoning_mode=ReasoningMode.ALWAYS,
    )

    response = orchestrator.process(_request())

    assert groq.calls == [("request-1", ["previous"], candidate)]
    assert response.text == groq_plan.direct_response
    assert executor.calls == []


def test_always_mode_executes_groq_plan_instead_of_candidate() -> None:
    candidate = ActionPlan(
        request_id="request-1",
        source=PlanSource.DETERMINISTIC,
        steps=(ActionStep("candidate-step", "buscar_en_internet"),),
    )
    groq_plan = ActionPlan(
        request_id="request-1",
        source=PlanSource.GROQ,
        steps=(
            ActionStep(
                "groq-step",
                "obtener_clima",
                {"ciudad": "Matamoros"},
            ),
        ),
    )
    executor = RecordingExecutor()
    orchestrator = _orchestrator(
        deterministic=FakeDeterministicPlanner(candidate),
        groq=FakeGroqPlanner(groq_plan),
        executor=executor,
        reasoning_mode=ReasoningMode.ALWAYS,
    )

    orchestrator.process(_request())

    assert executor.calls == [
        ("request-1", "groq-step", "obtener_clima")
    ]


def test_offline_mode_returns_controlled_reply_when_router_is_unresolved() -> None:
    orchestrator = _orchestrator(
        deterministic=FakeDeterministicPlanner(None),
        groq=FailingGroqPlanner(),
        executor=RecordingExecutor(),
        reasoning_mode=ReasoningMode.OFFLINE,
    )

    response = orchestrator.process(_request())

    assert response.should_listen is True
    assert "sin conexion" in response.text.lower()


@pytest.mark.parametrize(
    "error",
    [LLMUnavailableError(), LLMServiceError()],
)
def test_always_mode_falls_back_to_candidate_on_provider_error(error) -> None:
    candidate = ActionPlan(
        request_id="request-1",
        source=PlanSource.DETERMINISTIC,
        direct_response="Respuesta local.",
    )
    events = []
    orchestrator = _orchestrator(
        deterministic=FakeDeterministicPlanner(candidate),
        groq=ErrorGroqPlanner(error),
        executor=RecordingExecutor(),
        reasoning_mode=ReasoningMode.ALWAYS,
    )

    response = orchestrator.process(_request(), emit=events.append)

    assert response.text == "Respuesta local."
    assert any(event.get("text") == "reasoning degraded" for event in events)


def test_always_mode_propagates_provider_error_without_candidate() -> None:
    orchestrator = _orchestrator(
        deterministic=FakeDeterministicPlanner(None),
        groq=ErrorGroqPlanner(LLMUnavailableError()),
        executor=RecordingExecutor(),
        reasoning_mode=ReasoningMode.ALWAYS,
    )

    with pytest.raises(LLMUnavailableError):
        orchestrator.process(_request())
