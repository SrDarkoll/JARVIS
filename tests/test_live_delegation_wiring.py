import asyncio
import json
import threading
import time
from datetime import UTC, datetime

from core.brain import brain_state
from core.command_pipeline.models import ExecutionReceipt, ReceiptStatus
from voice import live_delegation
from voice.live_action_orchestrator import (
    ActionItem,
    ActionState,
    LiveActionOrchestrator,
)
from voice.live_delegation import DELEGATION_EXTRA_TOOLS, DELEGATION_TOOL_NAME
from voice.live_gemini import (
    DELEGATION_INSTRUCTION,
    LIVE_VOICE_TOOL_ALLOWLIST,
    GeminiLiveStreamer,
)
from voice.live_session import LiveSession


def _streamer() -> GeminiLiveStreamer:
    session = LiveSession(
        session_id="wiring",
        send_text=lambda _text: None,
        send_bytes=lambda _data: None,
    )
    return GeminiLiveStreamer(session=session, api_key="fake-key")


def _receipt(message: str, status: ReceiptStatus = ReceiptStatus.SUCCEEDED) -> ExecutionReceipt:
    now = datetime.now(UTC)
    return ExecutionReceipt(
        request_id="r",
        step_id="c1",
        tool_name=DELEGATION_TOOL_NAME,
        status=status,
        result=message,
        user_message=message,
        verified=False,
        diagnostic_code="",
        started_at=now,
        finished_at=now,
    )


def _action(name=DELEGATION_TOOL_NAME, args=None, call_id="c1", user_text="revisa la red"):
    return ActionItem(
        action_id="act-1",
        name=name,
        args=args if args is not None else {"peticion": "x"},
        call_id=call_id,
        user_text=user_text,
        language="es",
    )


def _run_action(orchestrator, action, responses):
    async def _go():
        orchestrator.enqueue_batch([], user_text="")  # registers an empty plan
        orchestrator._plans[orchestrator.current_plan.plan_id].actions.append(action)
        await orchestrator.execute_action(action, lambda cid, payload: responses.append((cid, payload)))

    asyncio.run(_go())


def test_declarations_include_delegation_when_brain_configured(monkeypatch):
    streamer = _streamer()
    monkeypatch.setattr(brain_state, "llm", object())
    names = {d["name"] for d in streamer._get_function_declarations()}
    assert DELEGATION_TOOL_NAME in names
    monkeypatch.setattr(brain_state, "llm", None)
    names = {d["name"] for d in streamer._get_function_declarations()}
    assert DELEGATION_TOOL_NAME not in names
    assert DELEGATION_TOOL_NAME not in LIVE_VOICE_TOOL_ALLOWLIST
    assert DELEGATION_EXTRA_TOOLS & LIVE_VOICE_TOOL_ALLOWLIST == frozenset()


def test_system_instruction_mentions_delegation_only_when_declared():
    streamer = _streamer()
    with_tool = streamer._build_live_system_instruction((DELEGATION_TOOL_NAME,))
    without = streamer._build_live_system_instruction(())
    assert DELEGATION_INSTRUCTION in with_tool
    assert DELEGATION_INSTRUCTION not in without


def test_delegation_action_invokes_run_delegated_request(monkeypatch):
    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        return _receipt("listo")

    monkeypatch.setattr(live_delegation, "run_delegated_request", fake_run)
    orchestrator = LiveActionOrchestrator(session_id="s1", profile_id="owner", language="es")
    responses = []
    _run_action(orchestrator, _action(), responses)

    assert len(calls) == 1
    assert calls[0]["user_text"] == "revisa la red"
    assert calls[0]["hint"] == "x"
    assert calls[0]["session_id"] == "s1"
    assert calls[0]["call_id"] == "c1"
    assert calls[0]["profile_id"] == "owner"
    assert json.loads(responses[0][1])["message"] == "listo"


def test_delegation_without_user_transcript_does_not_delegate(monkeypatch):
    calls = []
    monkeypatch.setattr(live_delegation, "run_delegated_request", lambda **kw: calls.append(kw))
    orchestrator = LiveActionOrchestrator(session_id="s1", language="es")
    responses = []
    action = _action(user_text="")
    _run_action(orchestrator, action, responses)

    assert calls == []
    payload = json.loads(responses[0][1])
    assert payload["ok"] is False
    assert action.state is ActionState.FAILED


def _patch_slow(monkeypatch, *, telegram, delivered, event):
    monkeypatch.setattr(live_delegation, "delegation_timeout_seconds", lambda: 0.05)

    def slow(**_kwargs):
        time.sleep(0.2)
        return _receipt("resultado")

    monkeypatch.setattr(live_delegation, "run_delegated_request", slow)
    monkeypatch.setattr(live_delegation, "telegram_ready", lambda: telegram)

    def deliver(text):
        delivered.append(text)
        event.set()
        return True

    monkeypatch.setattr(live_delegation, "deliver_late_result", deliver)


def test_delegation_timeout_reports_background_and_delivers_late(monkeypatch):
    delivered, event = [], threading.Event()
    _patch_slow(monkeypatch, telegram=True, delivered=delivered, event=event)
    orchestrator = LiveActionOrchestrator(session_id="s1", language="es")
    responses = []
    _run_action(orchestrator, _action(), responses)

    payload = json.loads(responses[0][1])
    assert payload["state"] == "running_in_background"
    assert payload["ok"] is True
    assert "Telegram" in payload["message"]
    assert event.wait(2)
    assert delivered == ["resultado"]


def test_late_delegation_result_delivered_after_session_loop_gone(monkeypatch):
    delivered, event = [], threading.Event()
    _patch_slow(monkeypatch, telegram=True, delivered=delivered, event=event)
    monkeypatch.setattr(
        live_delegation, "run_delegated_request",
        lambda **_kw: (time.sleep(0.4), _receipt("resultado"))[1],
    )
    orchestrator = LiveActionOrchestrator(session_id="s1", language="es")
    responses = []
    _run_action(orchestrator, _action(), responses)
    assert event.wait(2)
    assert delivered == ["resultado"]


def test_background_delegation_does_not_block_next_action(monkeypatch):
    delivered, event = [], threading.Event()
    _patch_slow(monkeypatch, telegram=True, delivered=delivered, event=event)
    invoked = []

    def invoker(name, args, user_text, source, profile_id):
        invoked.append(name)
        return "ok"

    orchestrator = LiveActionOrchestrator(session_id="s1", language="es", invoke_tool=invoker)
    responses = []
    follow_up = _action(name="obtener_clima", args={}, call_id="c2", user_text="clima")
    follow_up.action_id = "act-2"

    async def _go():
        first = _action()
        orchestrator.enqueue_batch([], user_text="")
        plan = orchestrator.current_plan
        plan.actions.extend([first, follow_up])
        await orchestrator.execute_action(first, lambda cid, p: responses.append((cid, p)))
        await orchestrator.execute_action(follow_up, lambda cid, p: responses.append((cid, p)))

    asyncio.run(_go())
    assert follow_up.error != "previous_action_still_running"
    assert follow_up.state is not ActionState.FAILED
    assert invoked == ["obtener_clima"]


def test_late_delegation_without_telegram_does_not_send(monkeypatch):
    delivered, event = [], threading.Event()
    _patch_slow(monkeypatch, telegram=False, delivered=delivered, event=event)
    orchestrator = LiveActionOrchestrator(session_id="s1", language="es")
    responses = []
    _run_action(orchestrator, _action(), responses)

    payload = json.loads(responses[0][1])
    assert "historial" in payload["message"]
    assert not event.wait(0.5)
    assert delivered == []
