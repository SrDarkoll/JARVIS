from __future__ import annotations

import pytest
from core import jarvis_state
from core.command_pipeline.execution import ToolExecutionService
from core.command_pipeline.models import (
    ActionPlan,
    ActionStep,
    CommandRequest,
    ExecutionReceipt,
    PlanSource,
    ReceiptStatus,
    ToolExecutionResult,
)
from core.command_pipeline.responses import ResponseComposer
from core.service_container import services
from tools import utilities


def _request() -> CommandRequest:
    return CommandRequest.create(text="modo trabajo", profile_id="admin", channel="chat", language="es")


@pytest.mark.parametrize("status", ["failed", "blocked", "unavailable", "partial"])
def test_explicit_tool_outcome_is_not_upgraded_to_success(status):
    executor = ToolExecutionService(
        lambda *_args: ToolExecutionResult(
            result={"steps": []},
            user_message="Resultado controlado.",
            status=status,
        )
    )
    request = _request()
    step = ActionStep("routine", "ejecutar_rutina")

    receipt = executor.execute(request, step)
    replay = executor.execute(request, step)
    response = ResponseComposer().compose(
        request,
        ActionPlan(request.request_id, PlanSource.DETERMINISTIC, steps=(step,)),
        (receipt,),
    )

    assert receipt.status.value == status
    assert receipt.user_message == "Resultado controlado."
    assert replay.status == receipt.status
    assert response.outcome.value == status
    assert receipt.verified is False


@pytest.mark.parametrize("status", ["failed", "blocked", "unavailable", "partial"])
def test_unsuccessful_tool_result_cannot_claim_verification(status):
    with pytest.raises(ValueError):
        ToolExecutionResult("unfinished", status=status, verified=True)


class RoutineExecutor:
    def __init__(self, states, *, verified=False):
        self.states = states
        self.verified = verified
        self.calls = []

    def execute(self, request, step):
        self.calls.append((request, step))
        status = ReceiptStatus(self.states[min(len(self.calls) - 1, len(self.states) - 1)])
        return ExecutionReceipt(
            request.request_id,
            step.step_id,
            step.tool_name,
            status,
            result="controlled",
            user_message="Resultado controlado.",
            verified=self.verified and status is ReceiptStatus.SUCCEEDED,
            diagnostic_code="tool_authorization_required" if status is ReceiptStatus.BLOCKED else "",
        )


@pytest.fixture
def routine_executor(monkeypatch):
    def configure(states, *, verified=False):
        executor = RoutineExecutor(states, verified=verified)
        monkeypatch.setattr(services, "tool_execution", executor)
        # Reproduce the old wrapper's loss of structured failure information.
        monkeypatch.setattr(services, "invocar_tool", lambda *_args, **_kwargs: "The requested action failed.")
        monkeypatch.setattr(utilities, "get_current_language", lambda: "es")
        return executor

    return configure


def test_routine_stops_on_failure_instead_of_reporting_success(routine_executor):
    executor = routine_executor(["failed"])
    result = utilities.ejecutar_rutina.invoke({"nombre": "trabajo"})

    assert len(executor.calls) == 1
    assert result.status is ReceiptStatus.FAILED
    assert result.verified is False
    assert "ejecutada" not in result.user_message.lower()
    assert len(result.result["pending_steps"]) == 3


def test_routine_reports_partial_and_does_not_start_remaining_app(routine_executor):
    executor = routine_executor(["succeeded", "succeeded", "failed"])
    result = utilities.ejecutar_rutina.invoke({"nombre": "trabajo"})

    assert [step.tool_name for _, step in executor.calls] == [
        "modo_no_molestar",
        "ajustar_volumen",
        "abrir_aplicacion",
    ]
    assert result.status.value == "partial"
    assert result.verified is False
    assert "incompleta" in result.user_message.lower()
    assert result.result["pending_steps"][0]["arguments"] == {"nombre_app": "chrome"}


def test_routine_block_does_not_continue_or_claim_voice_identification_is_required(routine_executor):
    executor = routine_executor(["blocked"])
    result = utilities.ejecutar_rutina.invoke({"nombre": "gaming"})

    assert len(executor.calls) == 1
    assert result.status is ReceiptStatus.BLOCKED
    assert "voz" not in result.user_message.lower()


def test_routine_pending_acceptance_does_not_mean_completed(routine_executor):
    executor = routine_executor(["accepted"])
    result = utilities.ejecutar_rutina.invoke({"nombre": "trabajo"})

    assert len(executor.calls) == 1
    assert result.accepted is True
    assert result.verified is False
    assert "pendiente" in result.user_message.lower()


def test_routine_preserves_profile_step_ids_and_verified_results(routine_executor):
    executor = routine_executor(["succeeded"], verified=True)
    with jarvis_state.active_profile("web_default"):
        result = utilities.ejecutar_rutina.invoke({"nombre": "trabajo"})

    assert len(executor.calls) == 4
    assert {request.profile_id for request, _ in executor.calls} == {"web_default"}
    assert {request.channel for request, _ in executor.calls} == {"routine"}
    assert len({request.request_id for request, _ in executor.calls}) == 1
    assert len({step.step_id for _, step in executor.calls}) == 4
    assert result.verified is True
    assert result.status is ReceiptStatus.SUCCEEDED


def test_routine_without_executor_is_unavailable(monkeypatch):
    monkeypatch.setattr(services, "tool_execution", None)
    result = utilities.ejecutar_rutina.invoke({"nombre": "trabajo"})
    assert result.status is ReceiptStatus.UNAVAILABLE
    assert result.verified is False


def test_memory_command_preserves_original_accents_and_capitalization():
    from core.brain import router

    request = CommandRequest.create(
        text="Recuerda que mi café favorito es Café México.",
        profile_id="admin", channel="chat", language="es",
    )
    plan = router.plan_hybrid(request)
    assert plan.steps[0].arguments["dato"] == "mi café favorito es Café México."
