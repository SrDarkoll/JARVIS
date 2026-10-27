from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from core.brain import tool_manager
from core.command_pipeline.models import ReceiptStatus
from modules.spotify import service
from modules.spotify.desktop.models import DesktopResultStatus, SpotifyDesktopResult
from voice.live_action_orchestrator import ActionState, LiveActionOrchestrator
from voice.live_gemini import GeminiLiveStreamer
from voice.live_session import LiveSession


async def failed_spotify_root(monkeypatch, *, profile="admin"):
    calls = []
    outcomes = iter([
        SpotifyDesktopResult(status=DesktopResultStatus.FAILED, message_key="spotify_search_unavailable"),
        SpotifyDesktopResult(status=DesktopResultStatus.SUCCESS, message_key="spotify_playback_started", title="Killer Queen", artist="Queen"),
    ])

    def desktop(song):
        calls.append(song)
        return next(outcomes)

    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    monkeypatch.setattr(service, "_spotify_desktop_result", desktop)
    orchestrator = LiveActionOrchestrator(f"context-{uuid4().hex}", profile_id=profile)
    turn = orchestrator.task_context.begin_turn("Reproduce Killer Queen en Spotify")
    plan = orchestrator.enqueue_batch([
        {"name": "reproducir_en_spotify", "id": "root", "args": {"cancion": "Killer Queen"}},
    ], user_text="Reproduce Killer Queen en Spotify", turn_id=turn)
    await orchestrator.execute_action(plan.actions[0], lambda *_args: None)
    assert plan.actions[0].receipt.status is ReceiptStatus.FAILED
    return orchestrator, plan.actions[0], calls


def test_contextual_retry_uses_the_real_central_executor_without_fabricating_user_input(monkeypatch):
    async def run():
        orchestrator, parent, calls = await failed_spotify_root(monkeypatch)
        observed = []
        original = tool_manager._tool_execution_service._invoke_once

        def observe(request, step):
            observed.append(request.text)
            return original(request, step)

        monkeypatch.setattr(tool_manager._tool_execution_service, "_invoke_once", observe)
        turn = orchestrator.task_context.begin_turn("Dale otra oportunidad a esa canción")
        responses = []
        await orchestrator.execute_contextual_call(
            "retry-1", "retry", parent.context_ref, turn,
            user_text="Dale otra oportunidad a esa canción", allowed_tools={"reproducir_en_spotify"},
            send_response_fn=lambda _id, payload: responses.append(json.loads(payload)),
        )
        assert calls == ["Killer Queen", "Killer Queen"]
        assert observed == ["Dale otra oportunidad a esa canción"]
        assert responses[-1]["state"] == "succeeded"
        assert responses[-1]["verified"] is True
        assert responses[-1]["action_ref"] != parent.context_ref
    asyncio.run(run())


def test_contextual_execution_does_not_override_a_security_denial(monkeypatch):
    async def run():
        orchestrator, parent, calls = await failed_spotify_root(monkeypatch)
        monkeypatch.setattr(tool_manager.security_manager, "_security_guard", lambda *_args, **_kw: (False, "Blocked by policy"))
        turn = orchestrator.task_context.begin_turn("otra vez")
        responses = []
        await orchestrator.execute_contextual_call(
            "retry-denied", "retry", parent.context_ref, turn, user_text="otra vez",
            allowed_tools={"reproducir_en_spotify"}, send_response_fn=lambda _id, payload: responses.append(json.loads(payload)),
        )
        assert calls == ["Killer Queen"]
        assert responses[-1]["state"] == "blocked"
        assert responses[-1]["verified"] is False
    asyncio.run(run())


def test_replayed_management_call_does_not_execute_the_effect_again(monkeypatch):
    async def run():
        orchestrator, parent, calls = await failed_spotify_root(monkeypatch)
        turn = orchestrator.task_context.begin_turn("otra vez")
        kwargs = {"user_text": "otra vez", "allowed_tools": {"reproducir_en_spotify"}, "send_response_fn": lambda *_args: None}
        for _attempt in range(2):
            await orchestrator.execute_contextual_call("same-call", "retry", parent.context_ref, turn, **kwargs)
        assert calls == ["Killer Queen", "Killer Queen"]
    asyncio.run(run())


def test_scheduled_context_followup_does_not_start_after_streamer_stops(monkeypatch):
    async def run():
        orchestrator, parent, calls = await failed_spotify_root(monkeypatch)
        session = LiveSession("stopped-context", lambda _text: None, lambda _data: None)
        session.orchestrator = orchestrator
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer._declared_tool_names = {"gestionar_tarea_actual", "reproducir_en_spotify"}
        streamer.accept_user_transcript("otra vez")
        await streamer._execute_orchestrated_plan([{
            "id": "already-stopped", "name": "gestionar_tarea_actual",
            "args": {"operation": "retry", "action_ref": parent.context_ref},
        }])
        assert calls == ["Killer Queen"]
        await session.close()
    asyncio.run(run())


def test_management_cancel_cancels_a_pending_action_without_invoking_it():
    async def run():
        invoked, responses = [], []
        orchestrator = LiveActionOrchestrator("context-cancel", invoke_tool=lambda *_args: invoked.append(True))
        turn = orchestrator.task_context.begin_turn("Reproduce Killer Queen")
        plan = orchestrator.enqueue_batch([
            {"name": "reproducir_en_spotify", "args": {"cancion": "Killer Queen"}, "id": "pending"},
        ], user_text="Reproduce Killer Queen", turn_id=turn)
        action = plan.actions[0]
        turn = orchestrator.task_context.begin_turn("cancélalo")
        await orchestrator.execute_contextual_call(
            "cancel-1", "cancel", action.context_ref, turn, user_text="cancélalo",
            allowed_tools={"reproducir_en_spotify"}, send_response_fn=lambda _id, payload: responses.append(json.loads(payload)),
        )
        assert action.state is ActionState.CANCELLED
        assert responses[-1]["state"] == "cancelled"
        assert invoked == []
    asyncio.run(run())


@pytest.mark.parametrize("text", ["solo quiero hablar contigo", "no reproduzcas nada"])
def test_a_previous_real_failure_does_not_authorize_conversational_music(monkeypatch, text):
    from core.command_pipeline.task_context import ContextResolutionError

    async def run():
        orchestrator, parent, calls = await failed_spotify_root(monkeypatch)
        turn = orchestrator.task_context.begin_turn(text)
        with pytest.raises(ContextResolutionError, match="context_no_action_requested"):
            await orchestrator.execute_contextual_call(
                "not-a-command", "retry", parent.context_ref, turn, user_text=text,
                allowed_tools={"reproducir_en_spotify"}, send_response_fn=lambda *_args: None,
            )
        assert calls == ["Killer Queen"]
    asyncio.run(run())


def test_context_management_is_declared_locally_without_terminal_or_privilege_fields():
    session = LiveSession("context-declarations", lambda _text: None, lambda _data: None)
    streamer = GeminiLiveStreamer(session, api_key="fake")
    declarations = {item["name"]: item for item in streamer._get_function_declarations()}
    declaration = declarations["gestionar_tarea_actual"]
    assert set(declaration["parameters"]["properties"]) == {"operation", "action_ref", "changes_json", "replacement_tool"}
    assert "ejecutar_comando_terminal" not in declarations
    assert "ejecutar_comando_terminal" not in declaration["parameters"]["properties"]["replacement_tool"]["enum"]


def test_live_same_frame_followup_executes_after_transcript_buffers_are_reset(monkeypatch):
    async def run():
        orchestrator, parent, calls = await failed_spotify_root(monkeypatch)
        session = LiveSession(f"live-{uuid4().hex}", lambda _text: None, lambda _data: None)
        session.orchestrator = orchestrator
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer._running = True
        streamer._declared_tool_names = {"gestionar_tarea_actual", "reproducir_en_spotify"}
        replies = []

        async def collect(_id, result):
            replies.append(json.loads(result))

        monkeypatch.setattr(streamer, "_send_tool_response", collect)
        monkeypatch.setattr(streamer, "_persist_live_transcript", lambda *_args: None)
        await streamer._handle_downstream_message({
            "serverContent": {"inputTranscription": {"text": "Inténtalo otra vez"}, "turnComplete": True},
            "toolCall": {"functionCalls": [{"id": "meta-1", "name": "gestionar_tarea_actual", "args": {
                "operation": "retry", "action_ref": parent.context_ref,
            }}]},
        })
        for _attempt in range(300):
            if len(calls) >= 2 and replies:
                break
            await asyncio.sleep(0.01)
        assert calls == ["Killer Queen", "Killer Queen"]
        assert replies[-1]["state"] == "succeeded"
        await session.close()
    asyncio.run(run())


def test_stale_scheduled_followup_cannot_borrow_a_new_input_turn(monkeypatch):
    async def run():
        orchestrator, parent, calls = await failed_spotify_root(monkeypatch)
        session = LiveSession("stale-context", lambda _text: None, lambda _data: None)
        session.orchestrator = orchestrator
        streamer = GeminiLiveStreamer(session, api_key="fake")
        streamer._running = True
        streamer.accept_user_transcript("otra vez")
        captured_turn = streamer._task_input_turn
        await streamer._handle_downstream_message({"voiceActivity": {"type": "ACTIVITY_START"}})
        streamer.accept_user_transcript("solo quiero hablar")
        responses = []

        async def collect(_id, response):
            responses.append(json.loads(response))

        monkeypatch.setattr(streamer, "_send_tool_response", collect)
        await streamer._execute_orchestrated_plan([
            {"id": "late", "name": "gestionar_tarea_actual", "args": {"operation": "retry", "action_ref": parent.context_ref}},
        ], user_text="otra vez", task_turn_id=captured_turn)
        assert calls == ["Killer Queen"]
        assert responses[-1]["verified"] is False
        await session.close()
    asyncio.run(run())


@pytest.mark.parametrize("arguments", [
    {"operation": "retry", "action_ref": "id", "confirmed": True},
    {"operation": "retry", "action_ref": "id", "changes_json": "[]"},
    {"operation": "retry", "action_ref": "id", "changes_json": {"cancion": "other"}},
    {"operation": "execute_terminal", "action_ref": "id"},
    {"operation": {}, "action_ref": "id"},
    {"operation": [], "action_ref": "id"},
    {"operation": "retry", "action_ref": "id", "changes_json": False},
    {"operation": "retry", "action_ref": "id", "changes_json": {}},
])
def test_management_parser_rejects_untrusted_protocol_fields(arguments):
    from core.command_pipeline.task_context import ContextResolutionError
    from voice.live_task_context import parse_context_proposal

    with pytest.raises(ContextResolutionError):
        parse_context_proposal(arguments)


def test_contextual_cancel_reports_a_timed_out_worker_truthfully():
    async def run():
        release = asyncio.Event()
        orchestrator = LiveActionOrchestrator("timed-out-context")
        turn = orchestrator.task_context.begin_turn("Reproduce Killer Queen")
        plan = orchestrator.enqueue_batch([
            {"name": "reproducir_en_spotify", "id": "slow", "args": {"cancion": "Killer Queen"}},
        ], user_text="Reproduce Killer Queen", turn_id=turn)
        action = plan.actions[0]
        action.state = ActionState.TIMED_OUT
        action._worker_task = asyncio.create_task(release.wait())
        action.execution_may_continue = True
        responses = []
        try:
            await orchestrator.execute_contextual_call(
                "cancel-slow", "cancel", "current", orchestrator.task_context.begin_turn("cancélalo"),
                user_text="cancélalo", allowed_tools={"reproducir_en_spotify"},
                send_response_fn=lambda _id, response: responses.append(json.loads(response)),
            )
            assert action.state is ActionState.CANCELLATION_REQUESTED
            assert action.execution_may_continue is True
            assert responses[-1]["state"] == "cancellation_requested"
            assert responses[-1]["verified"] is False
        finally:
            release.set()
            await action._worker_task
    asyncio.run(run())


def test_status_reports_the_actual_failure_without_reexecuting_and_allows_clarification(monkeypatch):
    async def run():
        orchestrator, parent, calls = await failed_spotify_root(monkeypatch)
        replies = []
        await orchestrator.execute_contextual_call(
            "status-1", "status", parent.context_ref, orchestrator.task_context.begin_turn("¿Por qué no?"),
            user_text="¿Por qué no?", allowed_tools={"reproducir_en_spotify"},
            send_response_fn=lambda _id, payload: replies.append(json.loads(payload)),
        )
        assert calls == ["Killer Queen"]
        assert replies[-1]["state"] == "failed"
        assert replies[-1]["diagnostic_code"] == "spotify_search_unavailable"
        assert replies[-1]["action_ref"] == parent.context_ref
        assert replies[-1]["verified"] is False
        await orchestrator.execute_contextual_call(
            "after-status", "retry", parent.context_ref, orchestrator.task_context.begin_turn("otra vez"),
            user_text="otra vez", allowed_tools={"reproducir_en_spotify"},
            send_response_fn=lambda _id, payload: replies.append(json.loads(payload)),
        )
        assert calls == ["Killer Queen", "Killer Queen"]
        assert replies[-1]["verified"] is True
    asyncio.run(run())
