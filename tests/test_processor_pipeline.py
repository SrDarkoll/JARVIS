from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

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


def test_stream_yields_status_before_command_finishes(monkeypatch) -> None:
    """Catch regressions that buffer every SSE event until processing ends."""

    producer_started = threading.Event()
    release_producer = threading.Event()

    def delayed_process(request, *, emit=None):
        assert emit is not None
        emit({"type": "status", "text": "understanding"})
        producer_started.set()
        assert release_producer.wait(timeout=2.0)
        response = CommandResponse(
            request_id=request.request_id,
            text="real response",
            should_listen=False,
            outcome="succeeded",
        )
        emit({"type": "done", **response.to_dict()})
        return response

    monkeypatch.delenv("JARVIS_LEGACY_COMMAND_PIPELINE", raising=False)
    monkeypatch.setattr(processor, "process_command", delayed_process)
    stream = processor.stream_procesar_mensaje_events(
        "hola",
        profile_id="admin",
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_event_future = executor.submit(next, stream)
        assert producer_started.wait(timeout=1.0)
        try:
            first_event = first_event_future.result(timeout=0.2)
        finally:
            release_producer.set()

    assert first_event == {"type": "status", "text": "understanding"}
    remaining_events = list(stream)
    assert remaining_events[-1]["type"] == "done"
    assert remaining_events[-1]["text"] == "real response"


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


def _chain_models(calls, final_text):
    from langchain_core.messages import AIMessage

    def failing(name):
        class _Failing:
            def invoke(self, _messages):
                calls.append(name)
                raise RuntimeError(f"{name} unavailable")

        return _Failing()

    class _Working:
        def invoke(self, _messages):
            calls.append("f2")
            return AIMessage(content=final_text)

    return failing("primary"), failing("f1"), _Working()


def _install_chain(monkeypatch, primary, fallbacks, *, bound=True):
    from core.brain import brain_state
    from core.command_pipeline.tool_registry import ToolRegistryService

    registry = ToolRegistryService().snapshot()
    monkeypatch.setattr(processor, "_llm_calls_disabled_for_tests", lambda: False)
    monkeypatch.setattr(
        brain_state,
        "get_tooling_snapshot",
        lambda: (primary if bound else None, primary, registry),
    )
    monkeypatch.setattr(brain_state, "llm_fallbacks", tuple(fallbacks))
    monkeypatch.setattr(brain_state, "llm_fallback", None)


def _chain_request():
    from core.command_pipeline.models import CommandRequest

    return CommandRequest.create(
        text="hello",
        profile_id="admin",
        channel="web",
        language="en",
    )


def test_runtime_planner_walks_full_chain(monkeypatch) -> None:
    calls = []
    primary, f1, f2 = _chain_models(calls, "f2 answer")
    _install_chain(monkeypatch, primary, (f1, f2))

    plan = processor._RuntimeGroqPlanner().plan(_chain_request(), [])

    assert plan.direct_response == "f2 answer"
    assert calls == ["primary", "f1", "f2"]


def test_runtime_planner_raises_service_error_when_chain_exhausted(
    monkeypatch,
) -> None:
    import pytest

    from core.errors import LLMServiceError

    calls = []
    primary, f1, _f2 = _chain_models(calls, "unused")
    _install_chain(monkeypatch, primary, (f1,))

    with pytest.raises(LLMServiceError):
        processor._RuntimeGroqPlanner().plan(_chain_request(), [])
    assert calls == ["primary", "f1"]


def test_runtime_synthesizer_walks_full_chain(monkeypatch) -> None:
    from core.command_pipeline.models import ActionPlan, PlanSource

    calls = []
    primary, f1, f2 = _chain_models(calls, "f2 synthesis")
    _install_chain(monkeypatch, primary, (f1, f2), bound=False)
    request = _chain_request()
    plan = ActionPlan(
        request_id=request.request_id,
        source=PlanSource.GROQ,
        direct_response="",
    )

    result = processor._RuntimeResponseSynthesizer().synthesize(
        request, plan, (), "deterministic fallback"
    )

    assert result == "f2 synthesis"
    assert calls == ["primary", "f1", "f2"]


def test_primary_failure_event_names_provider(monkeypatch) -> None:
    from core.brain import brain_state

    events = []

    class Boom:
        def invoke(self, _messages):
            raise PermissionError("denied")

    class Ok:
        def invoke(self, _messages):
            from langchain_core.messages import AIMessage

            return AIMessage(content="ok")

    _install_chain(monkeypatch, Boom(), (Ok(),))
    monkeypatch.setattr(brain_state, "llm_chain_providers", ("deepseek", "gemini"))
    monkeypatch.setattr(
        processor,
        "obs_event",
        lambda name, **fields: events.append((name, fields)),
    )

    processor._RuntimeGroqPlanner().plan(_chain_request(), [])

    assert (
        "primary_planner_failed",
        {
            "provider": "deepseek",
            "error": "PermissionError",
            "request_id": events[0][1]["request_id"],
        },
    ) in events
    assert any(
        name == "llm_fallback_succeeded" and f.get("provider") == "gemini"
        for name, f in events
    )


def test_runtime_planner_binds_only_allowed_tools(monkeypatch) -> None:
    from types import SimpleNamespace

    import pytest
    from core.brain import brain_state
    from core.command_pipeline.models import CommandRequest
    from core.errors import LLMServiceError
    from langchain_core.messages import AIMessage

    bound_names = []

    class BoundModel:
        def invoke(self, _messages):
            return AIMessage(
                content="",
                tool_calls=[{"name": "b", "args": {}, "id": "call-1"}],
            )

    class PlainModel:
        def bind_tools(self, tools, **_kwargs):
            bound_names.append([tool.name for tool in tools])
            return BoundModel()

    tool_a = SimpleNamespace(name="a")
    tool_b = SimpleNamespace(name="b")
    registry = SimpleNamespace(
        tools=(tool_a, tool_b),
        by_name={"a": tool_a, "b": tool_b},
    )
    prebound_full = object()
    monkeypatch.setattr(processor, "_llm_calls_disabled_for_tests", lambda: False)
    monkeypatch.setattr(
        brain_state,
        "get_tooling_snapshot",
        lambda: (prebound_full, PlainModel(), registry),
    )
    monkeypatch.setattr(brain_state, "llm_fallbacks", ())
    monkeypatch.setattr(brain_state, "llm_fallback", None)

    request = CommandRequest.create(
        text="hello",
        profile_id="admin",
        channel="voice",
        language="en",
        allowed_tools={"a"},
    )
    with pytest.raises(LLMServiceError):
        processor._RuntimeGroqPlanner().plan(request, [])

    assert bound_names == [["a"]]
