"""Provider and native-boundary regressions without real accounts or input."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from modules.spotify import messages, service
from modules.spotify.api import client, playback
from modules.spotify.desktop import windows
from modules.spotify.desktop.controller import SpotifyDesktopController
from modules.spotify.desktop.models import DesktopResultStatus, SpotifyDesktopResult
from requests.exceptions import HTTPError
from spotipy.exceptions import SpotifyException


@pytest.fixture
def providers(monkeypatch):
    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "auto")
    monkeypatch.setattr(service, "_spotify_api_blocked_until", {})
    monkeypatch.setattr(service, "_spotify_has_valid_cached_token", lambda: True)
    monkeypatch.setattr(playback, "_spotify_ready", lambda: (True, None))
    monkeypatch.setattr(playback, "_spotify_dispositivo_objetivo", lambda: "test-device")
    monkeypatch.setattr(playback, "_spotify_activar_cliente", lambda: False)
    monkeypatch.setattr(playback._time, "sleep", lambda _seconds: None)
    api = MagicMock()
    monkeypatch.setattr(client, "sp", api)
    desktop = MagicMock()
    desktop.control.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.SUCCESS, message_key="spotify_control_complete"
    )
    desktop.play.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.SUCCESS, title="Test Track", artist="Test Artist"
    )
    monkeypatch.setattr(service, "_get_desktop_controller", lambda: desktop)
    return api, desktop


@pytest.mark.parametrize("action,method", [
    ("pause", "pause_playback"), ("resume", "start_playback"),
    ("next", "next_track"), ("previous", "previous_track"),
])
def test_premium_api_controls_do_not_also_run_desktop(providers, action, method):
    api, desktop = providers
    service.control(action)
    getattr(api, method).assert_called_once_with(device_id="test-device")
    desktop.control.assert_not_called()


@pytest.mark.parametrize("action,method", [
    ("pause", "pause_playback"), ("resume", "start_playback"),
    ("next", "next_track"), ("previous", "previous_track"),
])
def test_free_premium_restriction_uses_desktop(providers, action, method):
    api, desktop = providers
    getattr(api, method).side_effect = SpotifyException(403, -1, "Premium required")
    service.control(action)
    desktop.control.assert_called_once_with(action)
    assert service._spotify_api_temporarily_blocked("control")
    assert getattr(api, method).call_count == 1


def configure_playback(monkeypatch):
    track = {
        "id": "test-track", "uri": "spotify:track:test-track", "name": "Test Track",
        "artists": [{"name": "Test Artist"}],
    }
    monkeypatch.setattr(playback, "_spotify_search_tracks", lambda *_args, **_kwargs: [track])
    monkeypatch.setattr(playback, "_spotify_mejor_track", lambda *_args: track)
    monkeypatch.setattr(playback, "_spotify_get_all_devices", lambda: [
        {"id": "test-device", "is_active": True, "name": "Test Device"}
    ])
    monkeypatch.setattr(playback, "_post_playback_ok", lambda *_args, **_kwargs: "Playing Test Track.")


def test_premium_playback_uses_api(monkeypatch, providers):
    api, desktop = providers
    configure_playback(monkeypatch)
    outcome = service._play_outcome("Test Track by Test Artist")
    assert outcome.ok and outcome.provider == "api"
    api.start_playback.assert_called_once_with(device_id="test-device", uris=["spotify:track:test-track"])
    desktop.play.assert_not_called()


def test_free_track_playback_uses_desktop(monkeypatch, providers):
    api, desktop = providers
    configure_playback(monkeypatch)
    api.start_playback.side_effect = SpotifyException(403, -1, "Premium required")
    outcome = service._play_outcome("Test Track by Test Artist")
    assert outcome.ok and outcome.provider == "desktop"
    desktop.play.assert_called_once()


def test_unverified_desktop_playback_does_not_claim_success(monkeypatch, providers):
    api, desktop = providers
    configure_playback(monkeypatch)
    api.start_playback.side_effect = SpotifyException(403, -1, "Premium required")
    desktop.play.return_value = SpotifyDesktopResult(
        status=DesktopResultStatus.UNVERIFIED, message_key="spotify_playback_not_verified"
    )
    outcome = service._play_outcome("Test Track by Test Artist")
    assert not outcome.ok and outcome.provider == "desktop"


def test_missing_cached_token_never_starts_oauth(monkeypatch):
    oauth = MagicMock()
    oauth.cache_handler.get_cached_token.return_value = None
    monkeypatch.setattr(client, "sp", SimpleNamespace(auth_manager=oauth))
    assert not client._spotify_has_valid_cached_token()
    oauth.get_access_token.assert_not_called()
    oauth.get_authorize_url.assert_not_called()


def test_expired_cached_authorization_uses_desktop_without_api(monkeypatch, providers):
    api, desktop = providers
    monkeypatch.setattr(service, "_spotify_has_valid_cached_token", lambda: False)
    service.control("pause")
    api.pause_playback.assert_not_called()
    desktop.control.assert_called_once_with("pause")


@pytest.mark.parametrize("status,reason,expected", [
    (401, None, "auth"), (403, None, "forbidden"),
    (403, "PREMIUM_REQUIRED", "premium"), (429, None, "quota"),
])
def test_spotipy_http_status_and_reason_are_classified(status, reason, expected):
    assert client._spotify_classify_error(SpotifyException(status, -1, "Request rejected", reason=reason)) == expected


@pytest.mark.parametrize("status,expected", [(401, "auth"), (403, "forbidden"), (429, "quota")])
def test_requests_response_status_is_classified(status, expected):
    error = HTTPError("Request rejected", response=SimpleNamespace(status_code=status))
    assert client._spotify_classify_error(error) == expected


def test_spotipy_subclasses_keep_http_classification():
    class ProviderError(SpotifyException):
        pass

    assert client._spotify_classify_error(ProviderError(429, -1, "Request rejected")) == "quota"


@pytest.mark.parametrize("language", ["es", "en"])
@pytest.mark.parametrize("status", [401, 403])
def test_authorization_and_permission_failures_fall_back_in_both_languages(
    monkeypatch, providers, language, status
):
    api, desktop = providers
    monkeypatch.setattr(messages, "get_current_language", lambda: language)
    api.pause_playback.side_effect = SpotifyException(status, -1, "Request rejected")
    service.control("pause")
    desktop.control.assert_called_once_with("pause")
    assert api.pause_playback.call_count == 1


@pytest.mark.parametrize("language", ["es", "en"])
def test_generic_forbidden_playback_falls_back_without_suggesting_an_upgrade(monkeypatch, providers, language):
    api, desktop = providers
    monkeypatch.setattr(messages, "get_current_language", lambda: language)
    configure_playback(monkeypatch)
    api.start_playback.side_effect = SpotifyException(403, -1, "Request rejected")
    outcome = service._play_outcome("Test Track by Test Artist")
    assert outcome.ok and outcome.provider == "desktop"
    desktop.play.assert_called_once()
    assert "premium" not in playback._mensaje_error_spotify("forbidden", "Test Track", "Test Artist").lower()


def test_quota_failure_is_not_retried_as_another_api_call(providers):
    api, desktop = providers
    api.pause_playback.side_effect = SpotifyException(429, -1, "Request rejected")
    message = service.control("pause")
    assert api.pause_playback.call_count == 1
    desktop.control.assert_not_called()
    assert "too many" in message.lower() or "demasiadas" in message.lower()


@pytest.mark.parametrize("operation,method", [
    (playback._spotify_like_track_api, "current_user_saved_tracks_add"),
    (playback._spotify_unlike_track_api, "current_user_saved_tracks_delete"),
])
def test_library_permission_failures_allow_controlled_desktop_fallback(monkeypatch, providers, operation, method):
    api, _desktop = providers
    configure_playback(monkeypatch)
    getattr(api, method).side_effect = SpotifyException(403, -1, "Request rejected")
    result = operation("Test Track by Test Artist")
    assert not result.ok and result.capability_failure


@pytest.mark.parametrize("language", ["es", "en"])
def test_auth_recovery_requests_reauthorization_without_cache_deletion(monkeypatch, language):
    monkeypatch.setattr(messages, "get_current_language", lambda: language)
    message = playback._mensaje_error_spotify("auth", "Test Track", "Test Artist")
    assert ".cache-jarvis" not in message and "delete" not in message and "borr" not in message
    assert "authoriz" in message.lower() or "autoriza" in message.lower()


class NativeFunction:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


@pytest.fixture
def native_focus(monkeypatch):
    state = {"foreground": 123, "target_thread": 9, "attachments": [], "valid": True}
    user32 = SimpleNamespace(
        IsWindow=NativeFunction(lambda _handle: state["valid"]),
        IsIconic=NativeFunction(lambda _handle: False),
        ShowWindow=NativeFunction(lambda *_args: True),
        GetWindowThreadProcessId=NativeFunction(lambda *_args: state["target_thread"]),
        SetForegroundWindow=NativeFunction(lambda *_args: True),
        SetFocus=NativeFunction(lambda *_args: 0),
        GetForegroundWindow=NativeFunction(lambda: state["foreground"]),
        AttachThreadInput=NativeFunction(lambda *args: state["attachments"].append(args) or True),
    )
    kernel32 = SimpleNamespace(GetCurrentThreadId=NativeFunction(lambda: 9))
    monkeypatch.setattr(windows, "IS_WINDOWS", True)
    monkeypatch.setattr(windows.ctypes, "windll", SimpleNamespace(user32=user32, kernel32=kernel32), raising=False)
    return state, user32, kernel32


def test_focus_uses_kernel32_and_declares_handle_width(native_focus):
    _state, user32, kernel32 = native_focus
    assert windows._focus_window(123)
    assert kernel32.GetCurrentThreadId.restype is windows.wintypes.DWORD
    assert user32.SetFocus.restype is windows.wintypes.HWND


def test_focus_does_not_claim_success_without_foreground_readback(native_focus):
    state, _user32, _kernel32 = native_focus
    state["foreground"] = 456
    assert not windows._focus_window(123)


def test_focus_rejects_a_destroyed_window(native_focus):
    state, _user32, _kernel32 = native_focus
    state["valid"] = False
    assert not windows._focus_window(123)


def test_focus_detaches_thread_input_after_native_failure(native_focus):
    state, user32, _kernel32 = native_focus
    state["foreground"] = 456
    state["target_thread"] = 10

    def denied(*_args):
        raise OSError("test focus failure")

    user32.SetFocus.callback = denied
    assert not windows._focus_window(123)
    assert state["attachments"] == [(9, 10, True), (9, 10, False)]


def test_already_foreground_window_does_not_need_another_activation(native_focus):
    _state, user32, _kernel32 = native_focus
    user32.SetForegroundWindow.callback = lambda *_args: False
    assert windows._focus_window(123)


def test_focusing_a_visible_window_preserves_its_maximized_state(native_focus):
    _state, user32, _kernel32 = native_focus
    restores = []
    user32.ShowWindow.callback = lambda *_args: restores.append(True) or True
    assert windows._focus_window(123)
    assert restores == []


def test_control_rechecks_focus_after_reading_current_track():
    focus = {"ready": True}
    window = windows.SpotifyWindow(handle=123, pid=42, title="Spotify")
    adapter = MagicMock()
    adapter.ensure_window.return_value = window
    adapter.focus.return_value = True
    adapter.is_foreground.side_effect = lambda _window: focus["ready"]
    uia = MagicMock()

    def read_track(_handle):
        focus["ready"] = False
        return "Test Track", "Test Artist"

    uia.now_playing.side_effect = read_track
    uia.playback_state.return_value = "paused"
    controller = SpotifyDesktopController(adapter, uia, action_timeout=0.05)
    result = controller.control("resume")
    assert result.status is DesktopResultStatus.FAILED
    assert result.message_key == "spotify_focus_lost"
    uia.control.assert_not_called()


@pytest.mark.parametrize("language,marker", [("es", "primer plano"), ("en", "foreground")])
def test_focus_failure_explains_how_to_retry(monkeypatch, language, marker):
    monkeypatch.setattr(messages, "get_current_language", lambda: language)
    result = SpotifyDesktopResult(status=DesktopResultStatus.FAILED, message_key="spotify_focus_lost")
    assert marker in service._spotify_desktop_control_message("resume", result).lower()


def test_foreground_window_with_reused_handle_and_other_pid_is_rejected():
    inputs = []
    adapter = windows.WindowsSpotifyWindowAdapter(
        process_ids=lambda: {42},
        windows=lambda: [windows.SpotifyWindow(handle=123, pid=99, title="Other app")],
        focus_window=lambda handle: inputs.append(handle) or True,
        activate_process_window=lambda pid: inputs.append(pid) or True,
        foreground_window=lambda: 123,
    )
    target = windows.SpotifyWindow(handle=123, pid=42, title="Spotify")
    assert not adapter.focus(target)
    assert not adapter.is_foreground(target)
    assert inputs == []
