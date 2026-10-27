from __future__ import annotations

import asyncio
import json

import pytest
from core.command_pipeline.models import ExecutionReceipt, ReceiptStatus
from voice.live_action_orchestrator import ActionState, LiveActionOrchestrator
from voice.live_tool_execution import live_action_state, live_tool_response


def _receipt(
    status: ReceiptStatus,
    diagnostic_code: str = "",
    *,
    result=None,
    verified: bool = False,
) -> ExecutionReceipt:
    return ExecutionReceipt(
        request_id="req-1",
        step_id="step-1",
        tool_name="abrir_aplicacion",
        status=status,
        result=result,
        user_message="controlled message",
        verified=verified,
        diagnostic_code=diagnostic_code,
    )


def test_confirmation_and_blocked_receipts_never_become_success():
    confirmation = _receipt(
        ReceiptStatus.BLOCKED,
        "tool_confirmation_required",
    )
    blocked = _receipt(ReceiptStatus.BLOCKED, "tool_policy_blocked")

    assert live_action_state(confirmation) == "confirmation_required"
    assert live_action_state(blocked) == "blocked"
    assert live_tool_response(confirmation)["ok"] is False
    assert live_tool_response(blocked)["ok"] is False


def test_receipt_terminal_states_remain_distinct():
    assert live_action_state(_receipt(ReceiptStatus.SUCCEEDED)) == "succeeded"
    assert live_action_state(_receipt(ReceiptStatus.DUPLICATE)) == "duplicate"
    assert live_action_state(_receipt(ReceiptStatus.UNAVAILABLE)) == "unavailable"
    assert live_action_state(_receipt(ReceiptStatus.FAILED)) == "failed"


def test_live_tool_response_is_bounded_and_does_not_echo_raw_result():
    receipt = _receipt(
        ReceiptStatus.SUCCEEDED,
        result={"token": "AIza-secret-value", "payload": "x" * 5000},
        verified=True,
    )

    response = live_tool_response(receipt)

    assert response == {
        "ok": True,
        "state": "succeeded",
        "message": "controlled message",
        "diagnostic_code": "",
        "verified": True,
    }
    assert "AIza-secret-value" not in repr(response)


def test_tool_manager_exposes_typed_receipt_entrypoint(monkeypatch):
    from core.brain import tool_manager

    expected = _receipt(ReceiptStatus.SUCCEEDED, verified=True)
    captured = {}

    def execute(request, step):
        captured["request"] = request
        captured["step"] = step
        return expected

    monkeypatch.setattr(tool_manager._tool_execution_service, "execute", execute)

    result = tool_manager.execute_tool_receipt(
        "obtener_clima",
        {"ubicacion": "Matamoros"},
        "consulta el clima",
        source="gemini_live",
        profile_id="admin",
        request_id="req-live",
        step_id="step-live",
    )

    assert result is expected
    assert captured["request"].request_id == "req-live"
    assert captured["request"].channel == "gemini_live"
    assert captured["step"].step_id == "step-live"


def test_legacy_tool_value_uses_public_message_instead_of_structured_metadata():
    from core.brain import tool_manager

    receipt = _receipt(ReceiptStatus.PARTIAL, result={"routine": "work", "receipts": []})
    assert tool_manager._receipt_value(receipt) == "controlled message"


@pytest.mark.parametrize(
    ("receipt", "expected_state"),
    [
        (_receipt(ReceiptStatus.SUCCEEDED, verified=True), ActionState.SUCCEEDED),
        (_receipt(ReceiptStatus.DUPLICATE), ActionState.DUPLICATE),
        (_receipt(ReceiptStatus.BLOCKED, "tool_policy_blocked"), ActionState.BLOCKED),
        (_receipt(ReceiptStatus.UNAVAILABLE), ActionState.UNAVAILABLE),
        (_receipt(ReceiptStatus.FAILED), ActionState.FAILED),
        (_receipt(ReceiptStatus.PARTIAL), ActionState.PARTIAL),
    ],
)
def test_live_orchestrator_preserves_receipt_state(receipt, expected_state):
    async def run():
        sent = []

        async def send_response(call_id, result):
            sent.append((call_id, json.loads(result)))

        orchestrator = LiveActionOrchestrator(
            session_id="typed-receipt",
            invoke_tool=lambda *_args: receipt,
        )
        action = orchestrator.enqueue_action("tool", {}, "call-1")

        await orchestrator.execute_action(action, send_response)

        assert action.state is expected_state
        assert action.receipt is receipt
        assert sent[-1][1]["state"] == expected_state.value

    asyncio.run(run())


def test_live_orchestrator_marks_confirmation_as_waiting_not_completed():
    async def run():
        receipt = _receipt(
            ReceiptStatus.BLOCKED,
            "tool_confirmation_required",
        )
        orchestrator = LiveActionOrchestrator(
            session_id="confirmation-receipt",
            invoke_tool=lambda *_args: receipt,
        )
        action = orchestrator.enqueue_action("critical-tool", {}, "call-confirm")

        await orchestrator.execute_action(action, lambda *_args: None)

        assert action.state is ActionState.CONFIRMATION_REQUIRED
        assert action.requires_confirmation is True
        assert action not in orchestrator.current_plan.completed_actions()

    asyncio.run(run())


def test_live_orchestrator_uses_provider_call_id_for_exactly_once_receipt(monkeypatch):
    from core.brain import tool_manager

    captured = {}

    def execute_tool_receipt(*_args):
        captured["request_id"] = _args[-2]
        captured["step_id"] = _args[-1]
        return _receipt(ReceiptStatus.SUCCEEDED, verified=True)

    monkeypatch.setattr(tool_manager, "execute_tool_receipt", execute_tool_receipt)
    orchestrator = LiveActionOrchestrator(session_id="stable-session")
    action = orchestrator.enqueue_action("obtener_clima", {}, "provider-call-42")

    orchestrator._invoke_tool_sync(action)

    assert captured == {
        "request_id": "live-stable-session",
        "step_id": "provider-call-42",
    }


@pytest.mark.parametrize("tool_name", ["reproducir_en_spotify", "reproducir_mix_spotify", "agregar_a_cola_spotify"])
def test_spotify_live_action_budget_covers_desktop_startup_and_verification(monkeypatch, tool_name):
    from core import jarvis_config

    monkeypatch.setattr(jarvis_config, "SPOTIFY_DESKTOP_START_TIMEOUT", 20.0)
    monkeypatch.setattr(jarvis_config, "SPOTIFY_DESKTOP_ACTION_TIMEOUT", 8.0)

    async def run():
        timeouts = []
        real_wait_for = asyncio.wait_for

        async def tracked_wait_for(awaitable, *, timeout):
            timeouts.append(timeout)
            return await real_wait_for(awaitable, timeout=timeout)

        monkeypatch.setattr(asyncio, "wait_for", tracked_wait_for)
        orchestrator = LiveActionOrchestrator(session_id="spotify-time-budget", invoke_tool=lambda *_args: _receipt(ReceiptStatus.SUCCEEDED))
        action = orchestrator.enqueue_action(tool_name, {}, "spotify-budget")
        await orchestrator.execute_action(action, lambda *_args: None)
        assert timeouts and 86 <= timeouts[0] <= 120
        assert action.state is ActionState.SUCCEEDED
    asyncio.run(run())


def test_explicit_live_action_timeout_is_preserved_for_spotify(monkeypatch):
    async def run():
        timeouts = []
        real_wait_for = asyncio.wait_for

        async def tracked_wait_for(awaitable, *, timeout):
            timeouts.append(timeout)
            return await real_wait_for(awaitable, timeout=timeout)

        monkeypatch.setattr(asyncio, "wait_for", tracked_wait_for)
        orchestrator = LiveActionOrchestrator(session_id="custom-time-budget", action_timeout_seconds=0.1,
                                             invoke_tool=lambda *_args: _receipt(ReceiptStatus.SUCCEEDED))
        action = orchestrator.enqueue_action("reproducir_en_spotify", {}, "custom-budget")
        await orchestrator.execute_action(action, lambda *_args: None)
        assert timeouts == [0.1]
    asyncio.run(run())
