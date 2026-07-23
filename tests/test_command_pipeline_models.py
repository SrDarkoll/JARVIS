from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest
from core.command_pipeline.models import (
    ActionPlan,
    ActionStep,
    CommandRequest,
    CommandResponse,
    ExecutionReceipt,
    PlanSource,
    ReceiptStatus,
)


def test_command_request_normalizes_input_and_rejects_empty_text() -> None:
    request = CommandRequest.create(
        text="  Play some jazz  ",
        profile_id="  OWNER  ",
        channel="  Voice  ",
        language="  EN  ",
        request_id="request-1",
        metadata={"locale": "en-US"},
    )

    assert request.request_id == "request-1"
    assert request.text == "Play some jazz"
    assert request.profile_id == "owner"
    assert request.channel == "voice"
    assert request.language == "en"
    assert request.metadata == {"locale": "en-US"}

    with pytest.raises(ValueError, match="command_text_required"):
        CommandRequest.create(text=" \t ", profile_id="owner", channel="chat")


def test_command_models_are_immutable_and_json_compatible() -> None:
    request = CommandRequest.create(
        text="What is the weather?",
        profile_id="owner",
        channel="chat",
    )
    step = ActionStep(
        step_id="weather-step",
        tool_name="weather",
        arguments={"city": "Monterrey"},
    )
    plan = ActionPlan(
        request_id=request.request_id,
        source=PlanSource.DETERMINISTIC,
        steps=(step,),
    )
    receipt = ExecutionReceipt(
        request_id=request.request_id,
        step_id=step.step_id,
        tool_name=step.tool_name,
        status=ReceiptStatus.SUCCEEDED,
        result="24 C",
        user_message="24 C",
        verified=True,
        diagnostic_code="",
    )
    response = CommandResponse(
        request_id=request.request_id,
        text="It is 24 C in Monterrey.",
        should_listen=False,
        outcome="succeeded",
        receipts=(receipt,),
    )

    with pytest.raises(FrozenInstanceError):
        request.text = "changed"  # type: ignore[misc]

    with pytest.raises(TypeError):
        step.arguments["city"] = "Saltillo"  # type: ignore[index]

    assert plan.steps == (step,)
    assert json.loads(json.dumps(receipt.to_dict()))["status"] == "succeeded"
    assert json.loads(json.dumps(response.to_dict()))["receipts"][0]["tool_name"] == "weather"
