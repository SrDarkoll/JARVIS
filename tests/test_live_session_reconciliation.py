from __future__ import annotations

import asyncio
import json
import threading

import pytest
from core.command_pipeline.models import ActionStep, CommandRequest, ExecutionReceipt, ReceiptStatus
from core.command_pipeline.task_context import ActionExecutionView, TaskContext, contextual_execution_decision, contextual_execution_scope
from voice.live_action_orchestrator import ActionState, LiveActionOrchestrator
from voice.live_gemini import GeminiLiveStreamer
from voice.live_session import LiveSession
from voice.live_transport import LiveTransportError, supervise_transport_tasks


async def wait_for_worker(started: threading.Event) -> None:
    async with asyncio.timeout(2):
        while not started.is_set():
            await asyncio.sleep(0.005)


def blocked_worker_orchestrator(session_id: str):
    started, release = threading.Event(), threading.Event()
    calls = []

    def invoke(name, arguments, *_args):
        calls.append((name, arguments))
        started.set()
        if not release.wait(3):
            raise TimeoutError("test_worker_not_released")
        return ExecutionReceipt(
            request_id=f"live-{session_id}", step_id="worker", tool_name=name,
            status=ReceiptStatus.SUCCEEDED, user_message="Reproducción comprobada.",
            result="playing", verified=True, diagnostic_code="spotify_playback_started",
        )

    orchestrator = LiveActionOrchestrator(session_id, invoke_tool=invoke)
    text = "Reproduce Killer Queen en Spotify"
    turn = orchestrator.task_context.begin_turn(text)
    plan = orchestrator.enqueue_batch([
        {"name": "reproducir_en_spotify", "args": {"cancion": "Killer Queen"}, "id": "music"},
        {"name": "obtener_clima", "args": {"ciudad": "Matamoros"}, "id": "pending"},
    ], user_text=text, turn_id=turn)
    return orchestrator, plan, started, release, calls


def test_audio_barge_in_preserves_running_effect_and_cancels_unstarted_steps():
    async def run():
        orchestrator, plan, started, release, calls = blocked_worker_orchestrator("audio-only")
        session = LiveSession("audio-only", lambda _text: None, lambda _data: None)
        session.orchestrator = orchestrator
        responses = []
        action, pending = plan.actions
        task = asyncio.create_task(orchestrator.execute_action(action, lambda _id, payload: responses.append(json.loads(payload))))
        session.attach_tool_task(task)
        try:
            await wait_for_worker(started)
            await session.interrupt()
            assert action.state is ActionState.RUNNING
            assert not orchestrator.task_context.get_record(action.context_ref).closed
            assert pending.state is ActionState.CANCELLED
            release.set()
            await task
            assert action.state is ActionState.SUCCEEDED
            assert responses[-1]["verified"] is True
            assert responses[-1]["action_ref"] == action.context_ref
            assert len(calls) == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await session.close()
    asyncio.run(run())


def test_explicit_late_cancellation_preserves_structured_receipt():
    async def run():
        orchestrator, plan, started, release, calls = blocked_worker_orchestrator("explicit-stop")
        action = plan.actions[0]
        task = asyncio.create_task(orchestrator.execute_action(action, lambda *_args: None))
        try:
            await wait_for_worker(started)
            await orchestrator.cancel_pending_actions("user_contextual_cancellation")
            assert action.state is ActionState.CANCELLATION_REQUESTED
            release.set()
            payload = json.loads(await task)
            assert action.state is ActionState.COMPLETED_AFTER_CANCELLATION
            assert payload["outcome_state"] == "succeeded"
            assert payload["action_state"] == "completed_after_cancellation"
            assert payload["verified"] is True
            assert payload["action_ref"] == action.context_ref
            assert payload["diagnostic_code"] == "spotify_playback_started"
            assert len(calls) == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_provider_cancelled_call_keeps_worker_result_but_sends_no_stale_response():
    async def run():
        orchestrator, plan, started, release, calls = blocked_worker_orchestrator("provider-stop")
        session = LiveSession("provider-stop", lambda _text: None, lambda _data: None)
        session.orchestrator = orchestrator
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer._running = True
        sent = []

        class Socket:
            async def send(self, message):
                sent.append(json.loads(message))

        streamer._ws = Socket()
        action = plan.actions[0]
        task = asyncio.create_task(orchestrator.execute_action(action, streamer._send_tool_response))
        session.attach_tool_task(task)
        try:
            await wait_for_worker(started)
            await streamer._handle_downstream_message({"toolCallCancellation": {"ids": ["music"]}})
            assert action.state is ActionState.RUNNING
            assert not orchestrator.task_context.get_record(action.context_ref).closed
            release.set()
            await task
            assert action.receipt.status is ReceiptStatus.SUCCEEDED
            assert sent == []
            assert len(calls) == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await session.close()
    asyncio.run(run())


def test_empty_voice_activity_does_not_expire_the_task_input_turn():
    async def run():
        session = LiveSession("no-phantom-turn", lambda _text: None, lambda _data: None)
        streamer = GeminiLiveStreamer(session, api_key="fake")
        context = session.orchestrator.task_context
        original_turn = context.begin_turn("Reproduce Killer Queen")
        try:
            await streamer._handle_downstream_message({"voiceActivity": {"type": "ACTIVITY_START"}})
            await streamer._handle_downstream_message({"voiceActivity": {"type": "ACTIVITY_END"}})
            await streamer._handle_downstream_message({"serverContent": {"turnComplete": True}})
            assert context.current_turn == original_turn
            streamer.accept_user_transcript("otra vez")
            assert context.current_turn == original_turn + 1
        finally:
            await session.close()
    asyncio.run(run())


def test_serialized_receipt_keeps_reference_and_verification_after_a_long_message():
    async def run():
        session = LiveSession("bounded-receipt", lambda _text: None, lambda _data: None)
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer._running = True
        sent = []

        class Socket:
            async def send(self, message):
                sent.append(json.loads(message))

        streamer._ws = Socket()
        try:
            await streamer._send_tool_response("receipt", json.dumps({
                "message": "Resultado comprobado. " * 150, "state": "succeeded",
                "action_ref": "reference-kept", "verified": True,
                "diagnostic_code": "spotify_playback_started",
            }))
            receipt = json.loads(sent[-1]["toolResponse"]["functionResponses"][0]["response"]["result"])
            assert receipt["action_ref"] == "reference-kept"
            assert receipt["verified"] is True
            assert len(receipt["message"]) <= 2000
        finally:
            await session.close()
    asyncio.run(run())


def test_transport_supervision_retrieves_simultaneous_failures():
    async def run():
        class ObservedTask(asyncio.Task):
            retrieved = False

            def result(self):
                self.retrieved = True
                return super().result()

            def exception(self):
                self.retrieved = True
                return super().exception()

        async def fail():
            raise LiveTransportError("gemini_live_connect_failed")

        tasks = [ObservedTask(fail()), ObservedTask(fail())]
        try:
            await asyncio.sleep(0)
            with pytest.raises(LiveTransportError):
                await supervise_transport_tasks(tasks)
            assert all(task.retrieved for task in tasks)
        finally:
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


def test_cancelling_transport_supervision_stops_all_owned_loops():
    async def run():
        release = asyncio.Event()
        tasks = [asyncio.create_task(release.wait()), asyncio.create_task(release.wait())]
        supervisor = asyncio.create_task(supervise_transport_tasks(tasks))
        try:
            await asyncio.sleep(0)
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)
            assert all(task.done() and task.cancelled() for task in tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


def test_voice_activity_revokes_unstarted_permission_without_a_phantom_turn():
    async def run():
        session = LiveSession("revoke-at-speech", lambda _text: None, lambda _data: None, profile_id="admin")
        context = TaskContext("revoke-at-speech", "admin", lambda _id: ActionExecutionView("failed", ReceiptStatus.FAILED))
        session.orchestrator.task_context = context
        reference = context.record_action("failed", "reproducir_en_spotify", {"cancion": "Killer Queen"},
                                          context.begin_turn("Reproduce Killer Queen"))
        turn = context.begin_turn("otra vez")
        prepared = context.prepare("retry", reference, turn)
        streamer = GeminiLiveStreamer(session, api_key="fake")
        try:
            await streamer._handle_downstream_message({"voiceActivity": {"type": "ACTIVITY_START"}})
            assert context.current_turn == turn
            request = CommandRequest.create(text="otra vez", profile_id="admin", channel="gemini_live",
                                            request_id="live-revoke-at-speech")
            with contextual_execution_scope(prepared.permit):
                assert contextual_execution_decision(request, ActionStep("retry", prepared.tool_name, prepared.arguments)) is False
        finally:
            await session.close()
    asyncio.run(run())
