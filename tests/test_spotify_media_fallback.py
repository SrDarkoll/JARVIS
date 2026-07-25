from __future__ import annotations

from modules.spotify import service
from modules.spotify.desktop import (
    DesktopResultStatus,
    SpotifyDesktopResult,
    windows,
)


def test_media_key_is_not_sent_when_spotify_is_closed(monkeypatch):
    monkeypatch.setattr(windows, "IS_WINDOWS", True)
    monkeypatch.setattr(windows, "_spotify_process_ids", set)

    assert windows.send_media_key_event("pause") is False


def test_global_media_key_fallback_is_reported_as_unverified(monkeypatch):
    class Controller:
        @staticmethod
        def control(_action):
            return SpotifyDesktopResult(
                status=DesktopResultStatus.UNAVAILABLE,
                message_key="spotify_automation_unavailable",
            )

    monkeypatch.setattr(service, "_get_desktop_controller", Controller)
    monkeypatch.setattr(windows, "send_media_key_event", lambda _action: True)

    result = service._spotify_control_desktop("pausa")

    normalized = result.lower()
    assert "not verify" in normalized or "no pude verificar" in normalized
    assert "spotify" not in normalized or "global" in normalized
