from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
from core.command_pipeline.models import ReceiptStatus, ToolExecutionResult
from modules.spotify import messages, service, tools
from modules.spotify.desktop.controller import SpotifyDesktopController
from modules.spotify.desktop.models import DesktopResultStatus, SpotifyDesktopResult
from voice.live_action_orchestrator import LiveActionOrchestrator


@pytest.mark.parametrize(("status", "code", "expected"), [
    (DesktopResultStatus.UNAVAILABLE, "spotify_start_timeout", ReceiptStatus.UNAVAILABLE),
    (DesktopResultStatus.FAILED, "spotify_focus_lost", ReceiptStatus.FAILED),
    (DesktopResultStatus.NOT_FOUND, "spotify_no_results", ReceiptStatus.FAILED),
    (DesktopResultStatus.FAILED, "spotify_search_unavailable", ReceiptStatus.FAILED),
    (DesktopResultStatus.SUCCESS, "spotify_playback_started", ReceiptStatus.SUCCEEDED),
])
def test_spotify_result_reaches_live_without_becoming_a_generic_or_permission_error(monkeypatch, status, code, expected):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    monkeypatch.setattr(service, "_spotify_desktop_result", lambda _song: SpotifyDesktopResult(
        status=status, message_key=code, title="Killer Queen", artist="Queen",
    ))

    async def run():
        replies = []
        orchestrator = LiveActionOrchestrator(session_id=f"receipt-{code}", profile_id="web_default", language="es")
        plan = orchestrator.enqueue_batch([
            {"id": f"call-{code}", "name": "reproducir_en_spotify", "args": {"cancion": "Killer Queen"}},
        ], user_text="Reproduce Killer Queen en Spotify.", language="es")
        await orchestrator.execute_action(plan.actions[0], lambda _id, value: replies.append(json.loads(value)))
        receipt = plan.actions[0].receipt
        assert receipt.status is expected
        assert receipt.diagnostic_code == code
        assert replies[-1]["diagnostic_code"] == code
        assert replies[-1]["state"] == expected.value
        assert replies[-1]["verified"] is (status is DesktopResultStatus.SUCCESS)
        assert replies[-1]["message"] != "The requested action failed."
        assert "petición explícita" not in replies[-1]["message"]
    asyncio.run(run())


def test_ambiguous_desktop_match_is_a_followup_not_a_permission_failure(monkeypatch):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    monkeypatch.setattr(service, "_spotify_desktop_result", lambda _song: SpotifyDesktopResult(
        status=DesktopResultStatus.AMBIGUOUS, message_key="spotify_ambiguous_results",
    ))
    result = tools.reproducir_en_spotify.invoke({"cancion": "Killer Queen"})
    assert isinstance(result, ToolExecutionResult)
    assert result.status is ReceiptStatus.PARTIAL
    assert result.diagnostic_code == "spotify_ambiguous_results"
    assert result.verified is False


def test_public_spotify_service_retains_its_text_interface(monkeypatch):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    monkeypatch.setattr(service, "_spotify_desktop_result", lambda _song: SpotifyDesktopResult(
        status=DesktopResultStatus.FAILED, message_key="spotify_focus_lost",
    ))
    monkeypatch.setattr(messages, "get_current_language", lambda: "es")
    assert isinstance(service.play("Killer Queen"), str)
    assert isinstance(service.play_mix("Queen"), str)


@pytest.mark.parametrize(("error", "code"), [
    (FileNotFoundError("synthetic-private-path"), "spotify_client_not_found"),
    (ImportError("synthetic-private-path"), "spotify_uia_unavailable"),
    (TimeoutError("synthetic-private-path"), "spotify_start_timeout"),
])
def test_native_startup_failure_is_distinct_and_logged_without_private_details(error, code, caplog):
    def unavailable(_timeout):
        raise error

    controller = SpotifyDesktopController(SimpleNamespace(ensure_window=unavailable), None)
    controller._logger = logging.getLogger("test.spotify.startup")
    with caplog.at_level(logging.INFO, logger="test.spotify.startup"):
        result = controller.play(service._spotify_desktop_request("Killer Queen"))
    assert result.status is DesktopResultStatus.UNAVAILABLE
    assert result.message_key == code
    assert code in caplog.text
    assert "synthetic-private-path" not in caplog.text
