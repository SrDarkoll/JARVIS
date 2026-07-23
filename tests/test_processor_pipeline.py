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

    request = processor._build_command_request(
        "la primera",
        profile_id="admin",
        channel="voice",
    )

    assert request.channel == "voice"
    assert request.metadata["default_location"] == "Matamoros"
    assert request.metadata["spotify_pending_choices"] == (candidate,)


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
