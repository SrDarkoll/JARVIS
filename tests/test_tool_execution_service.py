from __future__ import annotations

import threading
import time

import pytest
from core.command_pipeline.execution import (
    ToolExecutionService,
    validate_plan_operations,
)
from core.command_pipeline.models import ActionStep, CommandRequest, ReceiptStatus


def _request(
    request_id: str = "request-1",
    *,
    language: str = "en",
) -> CommandRequest:
    return CommandRequest.create(
        text="pon musica",
        profile_id="admin",
        channel="chat",
        language=language,
        request_id=request_id,
    )


def test_executor_replays_completed_operation_without_invoking_twice() -> None:
    calls: list[str] = []
    service = ToolExecutionService(
        lambda _request, _step: calls.append("called") or "playing"
    )
    step = ActionStep(
        step_id="step-1",
        tool_name="reproducir_en_spotify",
        arguments={"cancion": "Monster"},
    )

    first = service.execute(_request(), step)
    second = service.execute(_request(), step)

    assert calls == ["called"]
    assert first.status is ReceiptStatus.SUCCEEDED
    assert second.status is ReceiptStatus.DUPLICATE
    assert second.result == "playing"
    assert first.status is ReceiptStatus.SUCCEEDED


def test_executor_coalesces_concurrent_attempts() -> None:
    calls: list[str] = []
    barrier = threading.Barrier(3)

    def invoke(_request: CommandRequest, _step: ActionStep) -> str:
        calls.append("called")
        time.sleep(0.05)
        return "done"

    service = ToolExecutionService(invoke)
    step = ActionStep(
        step_id="step-1",
        tool_name="obtener_clima",
        arguments={"ciudad": "Matamoros"},
    )
    receipts = []

    def worker() -> None:
        barrier.wait()
        receipts.append(service.execute(_request(), step))

    threads = [threading.Thread(target=worker) for _index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert calls == ["called"]
    assert sorted(receipt.status.value for receipt in receipts) == [
        "duplicate",
        "succeeded",
    ]


def test_executor_returns_controlled_blocked_receipt() -> None:
    def invoke(_request: CommandRequest, _step: ActionStep) -> None:
        raise PermissionError("raw authorization details")

    service = ToolExecutionService(invoke)
    receipt = service.execute(
        _request(language="es"),
        ActionStep(step_id="step-1", tool_name="shutdown"),
    )

    assert receipt.status is ReceiptStatus.BLOCKED
    assert receipt.result is None
    assert receipt.verified is False
    assert receipt.diagnostic_code == "tool_blocked"
    assert receipt.user_message == (
        "Necesito confirmacion explicita antes de realizar esa accion."
    )
    assert "raw authorization details" not in receipt.user_message


def test_executor_returns_controlled_unavailable_receipt() -> None:
    def invoke(_request: CommandRequest, _step: ActionStep) -> None:
        raise LookupError("raw registry details")

    service = ToolExecutionService(invoke)
    receipt = service.execute(
        _request(),
        ActionStep(step_id="step-1", tool_name="missing_tool"),
    )

    assert receipt.status is ReceiptStatus.UNAVAILABLE
    assert receipt.result is None
    assert receipt.verified is False
    assert receipt.diagnostic_code == "tool_unavailable"
    assert receipt.user_message == "The requested tool is unavailable."
    assert "raw registry details" not in receipt.user_message


def test_executor_returns_controlled_failed_receipt() -> None:
    def invoke(_request: CommandRequest, _step: ActionStep) -> None:
        raise RuntimeError("raw provider details")

    service = ToolExecutionService(invoke)
    receipt = service.execute(
        _request(),
        ActionStep(step_id="step-1", tool_name="unstable_tool"),
    )

    assert receipt.status is ReceiptStatus.FAILED
    assert receipt.result is None
    assert receipt.verified is False
    assert receipt.diagnostic_code == "tool_execution_failed"
    assert receipt.user_message == "The requested action failed."
    assert "raw provider details" not in receipt.user_message


def test_plan_validation_rejects_same_canonical_operation_with_new_step_id() -> None:
    steps = (
        ActionStep(
            step_id="step-1",
            tool_name="obtener_clima",
            arguments={"ciudad": "Matamoros", "unidad": "celsius"},
        ),
        ActionStep(
            step_id="step-2",
            tool_name="obtener_clima",
            arguments={"unidad": "celsius", "ciudad": "Matamoros"},
        ),
    )

    with pytest.raises(ValueError, match="^duplicate_plan_operation$"):
        validate_plan_operations(steps)


def test_plan_validation_allows_explicitly_repeatable_operation() -> None:
    steps = (
        ActionStep(
            step_id="step-1",
            tool_name="send_message",
            arguments={"recipient": "owner", "text": "status"},
        ),
        ActionStep(
            step_id="step-2",
            tool_name="send_message",
            arguments={"text": "status", "recipient": "owner"},
        ),
    )

    validate_plan_operations(steps, repeatable_tools=frozenset({"send_message"}))


def test_executor_evicts_old_completed_receipts_at_cache_bound() -> None:
    calls: list[str] = []
    service = ToolExecutionService(
        lambda _request, step: calls.append(step.step_id) or step.step_id,
        max_records=32,
    )
    request = _request()
    steps = [
        ActionStep(
            step_id=f"step-{index}",
            tool_name="lookup",
            arguments={"index": index},
        )
        for index in range(33)
    ]

    for step in steps:
        service.execute(request, step)
    replay_after_eviction = service.execute(request, steps[0])
    duplicate_after_refresh = service.execute(request, steps[0])

    assert calls.count("step-0") == 2
    assert replay_after_eviction.status is ReceiptStatus.SUCCEEDED
    assert duplicate_after_refresh.status is ReceiptStatus.DUPLICATE
