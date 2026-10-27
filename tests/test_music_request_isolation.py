from __future__ import annotations

from types import SimpleNamespace

import pytest
from core.brain import tool_manager
from core.command_pipeline.execution import ToolExecutionService
from core.command_pipeline.models import ActionStep, CommandRequest, ReceiptStatus
from modules.spotify.desktop.models import SpotifyCandidate


@pytest.fixture
def guarded_executor(monkeypatch):
    calls = []
    fake = SimpleNamespace(invoke=lambda arguments: calls.append(arguments) or "playing")
    monkeypatch.setattr(tool_manager.security_manager, "_security_guard", lambda *_args, **_kwargs: (True, ""))
    return ToolExecutionService(tool_manager._invoke_tool_once), fake, calls


@pytest.mark.parametrize("channel", ["brain", "stream", "voice", "chat", "router", "router_dynamic", "planner", "gemini_live"])
@pytest.mark.parametrize("tool_name", ["reproducir_en_spotify", "reproducir_mix_spotify"])
def test_unrelated_folder_request_cannot_replay_music_from_any_interactive_channel(guarded_executor, channel, tool_name):
    executor, fake, calls = guarded_executor
    request = CommandRequest.create(text="Abre la carpeta MCP Godot del escritorio.", profile_id="admin", channel=channel,
                                    metadata={"_tool_map": {tool_name: fake}})
    arguments = {"cancion": "The Only Exception de Paramore"} if tool_name == "reproducir_en_spotify" else {"semilla": "Paramore"}
    receipt = executor.execute(request, ActionStep("unrequested-music", tool_name, arguments))
    assert receipt.status is ReceiptStatus.BLOCKED
    assert calls == []


@pytest.mark.parametrize("channel", ["brain", "stream", "voice", "router", "gemini_live"])
def test_explicit_music_still_executes_once(guarded_executor, channel):
    executor, fake, calls = guarded_executor
    request = CommandRequest.create(text="Reproduce The Only Exception de Paramore en Spotify.", profile_id="admin", channel=channel,
                                    metadata={"_tool_map": {"reproducir_en_spotify": fake}})
    receipt = executor.execute(request, ActionStep("requested-music", "reproducir_en_spotify", {"cancion": "The Only Exception de Paramore"}))
    assert receipt.status is ReceiptStatus.SUCCEEDED
    assert len(calls) == 1


@pytest.mark.parametrize("text,song,allowed", [
    ("La primera", "Killer Queen de Queen", True),
    ("Killer Queen", "Killer Queen de Queen", True),
    ("La primera", "The Only Exception de Paramore", False),
    ("Abre la carpeta 1", "Killer Queen de Queen", False),
])
def test_pending_selection_must_match_current_selection_and_exact_arguments(guarded_executor, text, song, allowed):
    executor, fake, calls = guarded_executor
    request = CommandRequest.create(text=text, profile_id="admin", channel="brain", metadata={
        "_tool_map": {"reproducir_en_spotify": fake},
        "spotify_pending_choices": (SpotifyCandidate("first", "Killer Queen", "Queen"),),
    })
    receipt = executor.execute(request, ActionStep("selection", "reproducir_en_spotify", {"cancion": song}))
    assert receipt.status is (ReceiptStatus.SUCCEEDED if allowed else ReceiptStatus.BLOCKED)
    assert len(calls) == int(allowed)
