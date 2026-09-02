from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from modules.spotify import config, service
from modules.spotify.api import client, playback
from tools import _get_base_tools_impl


@pytest.fixture(autouse=True)
def _reset_spotify_state(monkeypatch):
    monkeypatch.setattr(config, "SPOTIFY_ENABLED", True)
    monkeypatch.setattr(config, "SPOTIFY_REDIRECT_ERROR", None)
    monkeypatch.setattr(client, "SPOTIFY_ENABLED", True)
    monkeypatch.setattr(client, "SPOTIFY_REDIRECT_ERROR", None)
    monkeypatch.setattr(service, "_spotify_has_valid_cached_token", lambda: True)


def test_spotify_extended_tools_are_exported_in_base_tools():
    tool_names = {getattr(tool, "name", "") for tool in _get_base_tools_impl()}
    assert "agregar_a_cola_spotify" in tool_names
    assert "dar_like_spotify" in tool_names
    assert "quitar_like_spotify" in tool_names
    assert "cancion_actual_spotify" in tool_names


def test_add_to_queue_success(monkeypatch):
    mock_sp = MagicMock()
    mock_sp.devices.return_value = {
        "devices": [{"id": "dev123", "is_active": True, "name": "Desktop"}]
    }
    mock_sp.current_playback.return_value = {
        "device": {"id": "dev123", "is_active": True, "name": "Desktop"}
    }
    mock_sp.search.return_value = {
        "tracks": {
            "items": [
                {
                    "name": "Blinding Lights",
                    "id": "trk1",
                    "uri": "spotify:track:trk1",
                    "artists": [{"name": "The Weeknd"}],
                    "is_playable": True,
                }
            ]
        }
    }
    monkeypatch.setattr(client, "sp", mock_sp)

    res = service.add_to_queue("Blinding Lights")
    assert "Añadido a la cola" in res or "Added to Spotify queue" in res
    assert "Blinding Lights" in res
    mock_sp.add_to_queue.assert_called_once_with(uri="spotify:track:trk1", device_id="dev123")


def test_add_to_queue_empty_song():
    res = service.add_to_queue("")
    assert "indique una canción" in res or "specify a song" in res


def test_like_current_track(monkeypatch):
    mock_sp = MagicMock()
    mock_sp.current_playback.return_value = {
        "item": {
            "name": "Starboy",
            "id": "starboy123",
            "uri": "spotify:track:starboy123",
            "artists": [{"name": "The Weeknd"}],
        }
    }
    monkeypatch.setattr(client, "sp", mock_sp)

    res = service.like_track("")
    assert "Me Gusta" in res or "Liked Songs" in res
    assert "Starboy" in res
    mock_sp.current_user_saved_tracks_add.assert_called_once_with(tracks=["starboy123"])


def test_like_specific_track(monkeypatch):
    mock_sp = MagicMock()
    mock_sp.search.return_value = {
        "tracks": {
            "items": [
                {
                    "name": "Numb",
                    "id": "numb123",
                    "uri": "spotify:track:numb123",
                    "artists": [{"name": "Linkin Park"}],
                    "is_playable": True,
                }
            ]
        }
    }
    monkeypatch.setattr(client, "sp", mock_sp)

    res = service.like_track("Numb de Linkin Park")
    assert "Guardado en tus Me Gusta" in res or "Saved to your Liked Songs" in res
    assert "Numb" in res
    mock_sp.current_user_saved_tracks_add.assert_called_once_with(tracks=["numb123"])


def test_unlike_current_track(monkeypatch):
    mock_sp = MagicMock()
    mock_sp.current_playback.return_value = {
        "item": {
            "name": "In the End",
            "id": "end123",
            "uri": "spotify:track:end123",
            "artists": [{"name": "Linkin Park"}],
        }
    }
    monkeypatch.setattr(client, "sp", mock_sp)

    res = service.unlike_track("")
    assert "Me Gusta" in res or "Liked Songs" in res
    assert "In the End" in res
    mock_sp.current_user_saved_tracks_delete.assert_called_once_with(tracks=["end123"])


def test_current_track_info(monkeypatch):
    mock_sp = MagicMock()
    mock_sp.current_playback.return_value = {
        "is_playing": True,
        "progress_ms": 75000,
        "item": {
            "name": "Midnight City",
            "id": "mid123",
            "uri": "spotify:track:mid123",
            "artists": [{"name": "M83"}],
            "album": {"name": "Hurry Up, We're Dreaming"},
            "duration_ms": 240000,
        },
    }
    mock_sp.current_user_saved_tracks_contains.return_value = [True]
    monkeypatch.setattr(client, "sp", mock_sp)

    res = service.current_track()
    assert "Midnight City" in res
    assert "M83" in res
    assert "Hurry Up, We're Dreaming" in res
    assert "1:15 / 4:00" in res
    assert "❤️ En Me Gusta" in res


def test_control_like_and_info_aliases(monkeypatch):
    mock_sp = MagicMock()
    mock_sp.current_playback.return_value = {
        "is_playing": True,
        "progress_ms": 30000,
        "item": {
            "name": "One More Time",
            "id": "omt1",
            "uri": "spotify:track:omt1",
            "artists": [{"name": "Daft Punk"}],
            "album": {"name": "Discovery"},
            "duration_ms": 320000,
        },
    }
    mock_sp.current_user_saved_tracks_contains.return_value = [False]
    monkeypatch.setattr(client, "sp", mock_sp)

    res_like = service.control("me gusta")
    assert "Guardado en tus Me Gusta" in res_like or "Saved to your Liked Songs" in res_like

    res_info = service.control("cancion actual")
    assert "One More Time" in res_info
    assert "Daft Punk" in res_info


def test_like_track_desktop_mode(monkeypatch):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    mock_ctrl = MagicMock()
    from modules.spotify.desktop.models import DesktopResultStatus, SpotifyDesktopResult
    mock_ctrl.control.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.SUCCESS,
        title="Clint Eastwood",
        artist="Gorillaz",
        message_key="spotify_control_complete",
    )
    monkeypatch.setattr(service, "_get_desktop_controller", lambda: mock_ctrl)

    res = service.like_track("")
    assert "Me Gusta" in res or "Liked Songs" in res
    assert "Clint Eastwood" in res
    mock_ctrl.control.assert_called_with("like")


def test_unlike_track_desktop_mode(monkeypatch):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    mock_ctrl = MagicMock()
    from modules.spotify.desktop.models import DesktopResultStatus, SpotifyDesktopResult
    mock_ctrl.control.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.SUCCESS,
        title="Feel Good Inc",
        artist="Gorillaz",
        message_key="spotify_control_complete",
    )
    monkeypatch.setattr(service, "_get_desktop_controller", lambda: mock_ctrl)

    res = service.unlike_track("")
    assert "Me Gusta" in res or "Liked Songs" in res
    assert "Feel Good Inc" in res
    mock_ctrl.control.assert_called_with("unlike")


def test_like_track_auto_mode_fallback_when_api_unconfigured(monkeypatch):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "auto")
    monkeypatch.setattr(service, "_spotify_has_valid_cached_token", lambda: False)
    mock_ctrl = MagicMock()
    from modules.spotify.desktop.models import DesktopResultStatus, SpotifyDesktopResult
    mock_ctrl.control.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.SUCCESS,
        title="Dare",
        artist="Gorillaz",
        message_key="spotify_control_complete",
    )
    monkeypatch.setattr(service, "_get_desktop_controller", lambda: mock_ctrl)

    res = service.like_track("")
    assert "Me Gusta" in res or "Liked Songs" in res
    mock_ctrl.control.assert_called_with("like")


def test_add_to_queue_desktop_mode(monkeypatch):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    mock_ctrl = MagicMock()
    from modules.spotify.desktop.models import DesktopResultStatus, SpotifyDesktopResult
    mock_ctrl.queue.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.SUCCESS,
        title="19-2000",
        artist="Gorillaz",
        message_key="spotify_queue_added",
    )
    monkeypatch.setattr(service, "_get_desktop_controller", lambda: mock_ctrl)

    res = service.add_to_queue("19-2000")
    assert "Añadido a la cola" in res or "Added to Spotify Desktop queue" in res
    assert "19-2000" in res


def test_current_track_desktop_mode(monkeypatch):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    mock_ctrl = MagicMock()
    from modules.spotify.desktop.models import DesktopResultStatus, SpotifyDesktopResult

    mock_ctrl.current.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.SUCCESS,
        title="On Melancholy Hill",
        artist="Gorillaz",
        playback_state="playing",
        message_key="spotify_current_track",
    )
    monkeypatch.setattr(service, "_get_desktop_controller", lambda: mock_ctrl)

    res = service.current_track()
    assert "On Melancholy Hill" in res
    assert "Gorillaz" in res
    assert "Reproduciendo" in res or "Playing" in res


def test_control_desktop_repeat_volume_mute(monkeypatch):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    mock_ctrl = MagicMock()
    from modules.spotify.desktop.models import DesktopResultStatus, SpotifyDesktopResult
    mock_ctrl.control.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.SUCCESS,
        message_key="spotify_control_complete",
    )
    monkeypatch.setattr(service, "_get_desktop_controller", lambda: mock_ctrl)

    res_rep = service.control("activar repeticion")
    assert "repeticion activado" in res_rep or "Repeat mode enabled" in res_rep
    mock_ctrl.control.assert_called_with("repeat_on")

    res_vol_up = service.control("subir volumen")
    assert "aumentado" in res_vol_up or "increased" in res_vol_up
    mock_ctrl.control.assert_called_with("volume_up")

    res_vol_dn = service.control("bajar volumen")
    assert "disminuido" in res_vol_dn or "decreased" in res_vol_dn
    mock_ctrl.control.assert_called_with("volume_down")

    res_mute = service.control("silenciar")
    assert "silenciado" in res_mute or "muted" in res_mute
    mock_ctrl.control.assert_called_with("mute")


def test_desktop_queue_unverified_does_not_claim_added_or_play_track(monkeypatch):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    mock_ctrl = MagicMock()
    from modules.spotify.desktop.models import DesktopResultStatus, SpotifyDesktopResult

    mock_ctrl.queue.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.UNVERIFIED,
        title="19-2000",
        artist="Gorillaz",
        message_key="spotify_queue_sent_unverified",
    )
    monkeypatch.setattr(service, "_get_desktop_controller", lambda: mock_ctrl)

    message = service.add_to_queue("19-2000")

    assert "no pude verificar" in message.lower() or "could not verify" in message.lower()
    assert "reproduciendo" not in message.lower()
    assert "now playing" not in message.lower()


def test_named_unlike_uses_exact_candidate_action_without_current_control(monkeypatch):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    mock_ctrl = MagicMock()
    from modules.spotify.desktop.models import DesktopResultStatus, SpotifyDesktopResult

    mock_ctrl.library.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.UNVERIFIED,
        title="Numb",
        artist="Linkin Park",
        message_key="spotify_library_sent_unverified",
    )
    monkeypatch.setattr(service, "_get_desktop_controller", lambda: mock_ctrl)

    message = service.unlike_track("Numb de Linkin Park")

    mock_ctrl.library.assert_called_once()
    mock_ctrl.control.assert_not_called()
    assert "no pude verificar" in message.lower() or "could not verify" in message.lower()


def test_unknown_desktop_playback_state_does_not_claim_playing(monkeypatch):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    mock_ctrl = MagicMock()
    from modules.spotify.desktop.models import DesktopResultStatus, SpotifyDesktopResult

    mock_ctrl.current.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.UNVERIFIED,
        title="On Melancholy Hill",
        artist="Gorillaz",
        message_key="spotify_playback_state_unknown",
    )
    monkeypatch.setattr(service, "_get_desktop_controller", lambda: mock_ctrl)

    message = service.current_track()

    assert "On Melancholy Hill" in message
    assert "reproduciendo" not in message.lower()
    assert "playing" not in message.lower()


def test_unverified_current_like_does_not_claim_saved(monkeypatch):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    mock_ctrl = MagicMock()
    from modules.spotify.desktop.models import DesktopResultStatus, SpotifyDesktopResult

    mock_ctrl.control.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.UNVERIFIED,
        title="Clint Eastwood",
        artist="Gorillaz",
        message_key="spotify_control_sent_unverified",
    )
    monkeypatch.setattr(service, "_get_desktop_controller", lambda: mock_ctrl)

    message = service.like_track("")

    assert "no pude verificar" in message.lower() or "could not verify" in message.lower()
    assert "guardado" not in message.lower()
    assert "saved" not in message.lower()
