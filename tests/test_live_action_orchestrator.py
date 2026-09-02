import asyncio
import threading
import time

import pytest
from voice.live_action_orchestrator import (
    ActionItem,
    ActionState,
    LiveActionOrchestrator,
    LiveActionPlan,
    LiveDiagnosticsCollector,
)
from voice.live_session import LiveSession, LiveSessionState


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("condition_not_reached")
        await asyncio.sleep(0.005)


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

    assert diag.record_turn_start() is True
    assert diag.record_turn_start() is False
    time.sleep(0.01)
    token_latency = diag.record_first_token()
    audio_latency = diag.record_first_audio()
    time.sleep(0.005)
    completed = diag.record_turn_complete()
    assert token_latency is not None and token_latency > 0
    assert audio_latency is not None and audio_latency > 0
    assert completed["total_ms"] >= completed["ttfa_ms"]
    assert completed["ttft_ms"] > 0

    diag.record_tool_latency(120.0)
    diag.record_tool_latency(180.0)
    diag.record_tool_latency(95.0)

    diag.record_barge_in(75.0)
    diag.record_barge_in(90.0)

    diag.record_cancellation()
    diag.record_reconnect()

    summary = diag.get_summary()
    assert summary["voice_latency"]["samples"] == 1
    assert summary["turn_latency"]["samples"] == 1
    assert summary["last_turn"] == completed
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
        assert session.diagnostics.get_summary()["barge_in"]["samples"] == 1

        interrupted_events = [e for e in emitted if e.get("type") == "interrupted"]
        assert len(interrupted_events) == 1

    asyncio.run(_run())


def test_running_action_cancellation_is_truthful_until_worker_finishes():
    async def _run():
        worker_started = threading.Event()
        release_worker = threading.Event()
        worker_finished = threading.Event()
        sent_responses = []

        def invoke_tool(_name, _args, _user_input, _source, _profile_id):
            worker_started.set()
            release_worker.wait(timeout=2)
            worker_finished.set()
            return "side effect completed"

        async def send_response(call_id, result):
            sent_responses.append((call_id, result))

        orchestrator = LiveActionOrchestrator(
            session_id="truthful-cancel",
            invoke_tool=invoke_tool,
            action_timeout_seconds=1.0,
        )
        action = orchestrator.enqueue_action("slow_tool", {}, "call-slow")
        action_task = asyncio.create_task(orchestrator.execute_action(action, send_response))
        action._task = action_task

        try:
            await _wait_until(worker_started.is_set)
            cancelled = await orchestrator.cancel_pending_actions("user_barge_in")

            assert cancelled == [action]
            assert action.state is ActionState.CANCELLATION_REQUESTED
            assert action.execution_may_continue is True
            assert worker_finished.is_set() is False
            assert action_task.done() is False

            release_worker.set()
            result = await action_task

            assert worker_finished.is_set() is True
            assert action.state is ActionState.COMPLETED_AFTER_CANCELLATION
            assert action.execution_may_continue is False
            assert "termin" in result.lower()
            assert sent_responses[-1][0] == "call-slow"
        finally:
            release_worker.set()
            await asyncio.gather(action_task, return_exceptions=True)

    asyncio.run(_run())


def test_action_timeout_reports_late_worker_completion_truthfully():
    async def _run():
        worker_started = threading.Event()
        release_worker = threading.Event()
        worker_finished = threading.Event()

        def invoke_tool(_name, _args, _user_input, _source, _profile_id):
            worker_started.set()
            release_worker.wait(timeout=2)
            worker_finished.set()
            return "late result"

        async def send_response(_call_id, _result):
            return None

        orchestrator = LiveActionOrchestrator(
            session_id="truthful-timeout",
            invoke_tool=invoke_tool,
            action_timeout_seconds=0.02,
        )
        action = orchestrator.enqueue_action("slow_tool", {}, "call-timeout")

        try:
            result = await orchestrator.execute_action(action, send_response)

            assert worker_started.is_set() is True
            assert worker_finished.is_set() is False
            assert action.state is ActionState.TIMED_OUT
            assert action.execution_may_continue is True
            assert "continuar" in result.lower()

            release_worker.set()
            await _wait_until(
                lambda: action.state is ActionState.COMPLETED_AFTER_TIMEOUT,
            )
            assert worker_finished.is_set() is True
            assert action.execution_may_continue is False
            assert action.result == "late result"
        finally:
            release_worker.set()
            if action._worker_task is not None:
                await asyncio.gather(action._worker_task, return_exceptions=True)

    asyncio.run(_run())


def test_concurrent_plans_are_retained_serialized_and_cancelled_together():
    async def _run():
        first_started = threading.Event()
        second_started = threading.Event()
        release_first = threading.Event()
        invocations = []

        def invoke_tool(name, _args, _user_input, _source, _profile_id):
            invocations.append(name)
            if name == "tool-a":
                first_started.set()
                release_first.wait(timeout=2)
            else:
                second_started.set()
            return f"{name} complete"

        async def send_response(_call_id, _result):
            return None

        orchestrator = LiveActionOrchestrator(
            session_id="serialized-plans",
            invoke_tool=invoke_tool,
            action_timeout_seconds=1.0,
        )
        plan_a = orchestrator.enqueue_batch([{"name": "tool-a", "id": "call-a"}])
        action_a = plan_a.actions[0]
        task_a = asyncio.create_task(orchestrator.execute_action(action_a, send_response))
        action_a._task = task_a

        try:
            await _wait_until(first_started.is_set)
            plan_b = orchestrator.enqueue_batch([{"name": "tool-b", "id": "call-b"}])
            action_b = plan_b.actions[0]
            task_b = asyncio.create_task(orchestrator.execute_action(action_b, send_response))
            action_b._task = task_b
            await asyncio.sleep(0.03)

            assert {plan.plan_id for plan in orchestrator.active_plans()} == {
                plan_a.plan_id,
                plan_b.plan_id,
            }
            assert second_started.is_set() is False

            cancelled = await orchestrator.cancel_pending_actions("user_barge_in")
            assert {action.action_id for action in cancelled} == {
                action_a.action_id,
                action_b.action_id,
            }
            assert action_a.state is ActionState.CANCELLATION_REQUESTED
            assert action_b.state is ActionState.CANCELLED

            release_first.set()
            await asyncio.gather(task_a, task_b)
            assert invocations == ["tool-a"]
        finally:
            release_first.set()
            tasks = [task_a]
            if "task_b" in locals():
                tasks.append(task_b)
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(_run())
