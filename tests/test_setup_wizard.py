from core.setup_wizard import build_setup_status


def _status(env, *, platform_name="win32"):
    return build_setup_status(
        env=env,
        language="es",
        admin_voice_profiles=1,
        weather_location="Matamoros",
        platform_name=platform_name,
    )


def test_spotify_desktop_mode_does_not_require_api_credentials():
    status = _status({"SPOTIFY_PLAYBACK_MODE": "desktop"})

    assert status["items"]["spotify"]["configured"] is True
    assert status["items"]["spotify"]["mode"] == "desktop"


def test_spotify_api_mode_requires_client_credentials():
    status = _status({"SPOTIFY_PLAYBACK_MODE": "api"})

    assert status["items"]["spotify"]["configured"] is False
    assert status["items"]["spotify"]["mode"] == "api"


def test_spotify_auto_mode_uses_whichever_backend_is_available():
    assert _status({"SPOTIFY_PLAYBACK_MODE": "auto"})["items"]["spotify"][
        "configured"
    ]
    assert not _status(
        {"SPOTIFY_PLAYBACK_MODE": "auto"}, platform_name="linux"
    )["items"]["spotify"]["configured"]
    assert _status(
        {
            "SPOTIFY_PLAYBACK_MODE": "auto",
            "SPOTIPY_CLIENT_ID": "client",
            "SPOTIPY_CLIENT_SECRET": "secret",
        },
        platform_name="linux",
    )["items"]["spotify"]["configured"]


def test_explicit_empty_environment_does_not_read_process_secrets(monkeypatch):
    monkeypatch.setenv("SPOTIPY_CLIENT_ID", "process-client")
    monkeypatch.setenv("SPOTIPY_CLIENT_SECRET", "process-secret")

    status = _status({"SPOTIFY_PLAYBACK_MODE": "api"})

    assert not status["items"]["spotify"]["configured"]
