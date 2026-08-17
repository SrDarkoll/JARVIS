import asyncio
import pytest
import time
from voice.live_action_orchestrator import (
    ActionItem,
    ActionState,
    LiveActionOrchestrator,
    LiveActionPlan,
    LiveDiagnosticsCollector,
)
from voice.live_session import LiveSession, LiveSessionState


def test_action_item_duration_and_dict():
    item = ActionItem(
        action_id="act-1",
        name="ajustar_volumen",
        args={"nivel": 30},
        call_id="call-123",
        state=ActionState.PENDING,
    )
    assert item.state == ActionState.PENDING
    assert item.duration_ms is None

    item.started_at = 100.0
    item.completed_at = 100.250
    assert abs(item.duration_ms - 250.0) < 1e-3

    d = item.to_dict()
    assert d["action_id"] == "act-1"
    assert d["name"] == "ajustar_volumen"
    assert d["state"] == "pending"
    assert d["duration_ms"] == item.duration_ms


def test_live_action_plan_helpers():
    plan = LiveActionPlan(plan_id="test-plan")
    a1 = ActionItem(action_id="act-1", name="open_app", args={"app": "spotify"}, call_id="c1", state=ActionState.COMPLETED)
    a2 = ActionItem(action_id="act-2", name="play_music", args={"song": "am"}, call_id="c2", state=ActionState.RUNNING)
    a3 = ActionItem(action_id="act-3", name="set_volume", args={"level": 30}, call_id="c3", state=ActionState.PENDING)
    a4 = ActionItem(action_id="act-4", name="open_app", args={"app": "steam"}, call_id="c4", state=ActionState.PENDING)
    plan.actions.extend([a1, a2, a3, a4])

    assert len(plan.completed_actions()) == 1
    assert plan.active_action() == a2
    assert len(plan.pending_actions()) == 2
    assert plan.get_action("act-3") == a3

    summary = plan.to_dict()
    assert summary["total_actions"] == 4
    assert summary["completed_count"] == 1
    assert summary["pending_count"] == 2


def test_orchestrator_batch_enqueue_and_cancellation():
    async def _run():
        events = []
        orchestrator = LiveActionOrchestrator(
            session_id="sess-1",
            profile_id="owner",
            emit_json=lambda d: events.append(d),
        )

        batch = [
            {"name": "reproducir_en_spotify", "args": {"cancion": "Arctic Monkeys"}, "id": "c1"},
            {"name": "ajustar_volumen", "args": {"nivel": 30}, "id": "c2"},
            {"name": "abrir_aplicacion", "args": {"nombre": "Steam"}, "id": "c3"},
        ]

        plan = orchestrator.enqueue_batch(batch)
        assert len(plan.actions) == 3
        assert plan.actions[0].name == "reproducir_en_spotify"
        assert plan.actions[1].name == "ajustar_volumen"
        assert plan.actions[2].name == "abrir_aplicacion"

        # All initially PENDING
        assert all(a.state == ActionState.PENDING for a in plan.actions)

        # User interrupts before Steam executes (barge-in)
        cancelled = await orchestrator.cancel_pending_actions(reason="user_barge_in")
        assert len(cancelled) == 3
        assert all(a.state == ActionState.CANCELLED for a in plan.actions)
        assert all(a.cancellation_reason == "user_barge_in" for a in plan.actions)

        # Verify event was emitted to UI
        assert len(events) >= 1
        assert events[-1]["type"] == "action_plan_updated"
        assert events[-1]["plan"]["cancelled_count"] == 3

    asyncio.run(_run())


def test_cancelled_action_is_not_executed():
    async def _run():
        sent_responses = []

        async def fake_send_response(call_id: str, result_text: str):
            sent_responses.append((call_id, result_text))

        orchestrator = LiveActionOrchestrator(session_id="sess-2")
        action = orchestrator.enqueue_action(
            name="abrir_aplicacion",
            args={"nombre": "Steam"},
            call_id="call-steam",
        )

        # Cancel action before execution
        await orchestrator.cancel_pending_actions(reason="user_barge_in")
        assert action.state == ActionState.CANCELLED

        # Execute action
        result = await orchestrator.execute_action(action, fake_send_response)
        assert "cancelada" in result.lower()
        assert len(sent_responses) == 1
        assert sent_responses[0][0] == "call-steam"
        assert "cancelada" in sent_responses[0][1].lower()

    asyncio.run(_run())


def test_live_diagnostics_collector_metrics():
    diag = LiveDiagnosticsCollector()

    diag.record_turn_start()
    time.sleep(0.01)
    diag.record_first_token()
    audio_latency = diag.record_first_audio()
    assert audio_latency is not None and audio_latency > 0

    diag.record_tool_latency(120.0)
    diag.record_tool_latency(180.0)
    diag.record_tool_latency(95.0)

    diag.record_barge_in(75.0)
    diag.record_barge_in(90.0)

    diag.record_cancellation()
    diag.record_reconnect()

    summary = diag.get_summary()
    assert summary["voice_latency"]["samples"] == 1
    assert summary["tool_latency"]["samples"] == 3
    assert summary["tool_latency"]["p50_ms"] == 120.0
    assert summary["total_actions_cancelled"] == 1
    assert summary["reconnect_count"] == 1


def test_live_session_interrupt_cancels_orchestrator_actions():
    async def _run():
        emitted = []

        async def fake_send_text(txt):
            import json
            emitted.append(json.loads(txt))

        session = LiveSession(
            session_id="sess-test",
            send_text=fake_send_text,
            send_bytes=lambda b: None,
        )

        # Queue an action
        action = session.orchestrator.enqueue_action("reproducir_en_spotify", {"cancion": "Queen"}, "c1")
        assert action.state == ActionState.PENDING

        # Interrupt session
        await session.interrupt()

        assert action.state == ActionState.CANCELLED
        assert action.cancellation_reason == "user_barge_in"

        interrupted_events = [e for e in emitted if e.get("type") == "interrupted"]
        assert len(interrupted_events) == 1

    asyncio.run(_run())
