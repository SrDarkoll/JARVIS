from __future__ import annotations

from core.brain import processor
from core.command_pipeline.models import CommandResponse
from modules.spotify.desktop.models import SpotifyCandidate


class RecordingOrchestrator:
    def __init__(self) -> None:
        self.calls = []

    def process(self, request, emit=None):
        self.calls.append(request)
        response = CommandResponse(
            request_id=request.request_id,
            text=f"handled:{request.channel}",
            should_listen=False,
            outcome="succeeded",
        )
        if emit is not None:
            emit({"type": "status", "text": "understanding"})
            emit({"type": "done", **response.to_dict()})
        return response


def test_direct_and_stream_facades_share_one_orchestrator(
    monkeypatch,
) -> None:
    orchestrator = RecordingOrchestrator()
    monkeypatch.delenv("JARVIS_LEGACY_COMMAND_PIPELINE", raising=False)
    monkeypatch.setattr(
        processor,
        "_get_command_orchestrator",
        lambda: orchestrator,
    )
    monkeypatch.setattr(
        processor,
        "_record_pipeline_response",
        lambda *_args, **_kwargs: None,
    )

    reply, should_listen = processor.procesar_mensaje(
        "hola",
        profile_id="admin",
    )
    events = list(
        processor.stream_procesar_mensaje_events(
            "hola",
            profile_id="admin",
        )
    )

    assert (reply, should_listen) == ("handled:brain", False)
    assert [request.channel for request in orchestrator.calls] == [
        "brain",
        "stream",
    ]
    assert events[-1]["type"] == "done"
    assert events[-1]["text"] == "handled:stream"


def test_runtime_planner_retries_with_configured_fallback(monkeypatch) -> None:
    from core.brain import brain_state
    from core.command_pipeline.models import CommandRequest
    from core.command_pipeline.tool_registry import ToolRegistryService
    from langchain_core.messages import AIMessage

    calls = []

    class PrimaryModel:
        def invoke(self, _messages):
            calls.append("primary")
            raise RuntimeError("primary unavailable")

    class FallbackModel:
        def invoke(self, _messages):
            calls.append("fallback")
            return AIMessage(content="fallback answer")

    registry = ToolRegistryService().snapshot()
    monkeypatch.setattr(processor, "_llm_calls_disabled_for_tests", lambda: False)
    monkeypatch.setattr(
        brain_state,
        "get_tooling_snapshot",
        lambda: (PrimaryModel(), PrimaryModel(), registry),
    )
    monkeypatch.setattr(brain_state, "llm_fallback", FallbackModel())

    request = CommandRequest.create(
        text="hello",
        profile_id="admin",
        channel="web",
        language="en",
    )
    plan = processor._RuntimeGroqPlanner().plan(request, [])

    assert plan.direct_response == "fallback answer"
    assert calls == ["primary", "fallback"]


def test_runtime_response_synthesizer_retries_with_fallback(monkeypatch) -> None:
    from core.brain import brain_state
    from core.command_pipeline.models import ActionPlan, CommandRequest, PlanSource
    from core.command_pipeline.tool_registry import ToolRegistryService
    from langchain_core.messages import AIMessage

    calls = []

    class PrimaryModel:
        def invoke(self, _messages):
            calls.append("primary")
            raise RuntimeError("primary unavailable")

    class FallbackModel:
        def invoke(self, _messages):
            calls.append("fallback")
            return AIMessage(content="fallback synthesis")

    registry = ToolRegistryService().snapshot()
    monkeypatch.setattr(processor, "_llm_calls_disabled_for_tests", lambda: False)
    monkeypatch.setattr(
        brain_state,
        "get_tooling_snapshot",
        lambda: (None, PrimaryModel(), registry),
    )
    monkeypatch.setattr(brain_state, "llm_fallback", FallbackModel())

    request = CommandRequest.create(
        text="summarize",
        profile_id="admin",
        channel="web",
        language="en",
    )
    plan = ActionPlan(
        request_id=request.request_id,
        source=PlanSource.GROQ,
        direct_response="",
    )
    result = processor._RuntimeResponseSynthesizer().synthesize(
        request,
        plan,
        (),
        "deterministic fallback",
    )

    assert result == "fallback synthesis"
    assert calls == ["primary", "fallback"]


def test_command_request_contains_runtime_snapshot(monkeypatch) -> None:
    candidate = SpotifyCandidate("1", "Monster", "Meg and Dia")
    monkeypatch.setattr(
        processor,
        "get_default_location",
        lambda: "Matamoros",
    )
    monkeypatch.setattr(
        processor.pending_spotify_selections,
        "snapshot",
        lambda _profile_id: (candidate,),
    )
    monkeypatch.setattr(
        processor,
        "get_last_media_source",
        lambda _profile_id: "youtube",
    )

    request = processor._build_command_request(
        "la primera",
        profile_id="admin",
        channel="voice",
    )

    assert request.channel == "voice"
    assert request.metadata["default_location"] == "Matamoros"
    assert request.metadata["spotify_pending_choices"] == (candidate,)
    assert request.metadata["last_media_source"] == "youtube"


def test_legacy_pipeline_is_only_used_when_explicitly_enabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("JARVIS_LEGACY_COMMAND_PIPELINE", "true")
    monkeypatch.setattr(
        processor,
        "_procesar_mensaje_legacy",
        lambda *_args, **_kwargs: ("legacy", True),
    )

    assert processor.procesar_mensaje("hola") == ("legacy", True)
