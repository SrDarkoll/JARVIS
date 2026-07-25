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
    assert status["items"]["spotify"]["state"] == "available"


def test_spotify_api_mode_requires_client_credentials():
    status = _status({"SPOTIFY_PLAYBACK_MODE": "api"})

    assert status["items"]["spotify"]["configured"] is False
    assert status["items"]["spotify"]["mode"] == "api"


def test_spotify_auto_mode_uses_whichever_backend_is_available():
    assert _status({"SPOTIFY_PLAYBACK_MODE": "auto"})["items"]["spotify"]["configured"]
    assert not _status({"SPOTIFY_PLAYBACK_MODE": "auto"}, platform_name="linux")["items"]["spotify"]["configured"]
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


def test_core_mode_does_not_require_optional_voice_or_api_token():
    status = build_setup_status(
        env={
            "JARVIS_CORE_MODE": "true",
            "GROQ_API_KEY": "configured",
        },
        language="en",
        admin_voice_profiles=0,
        weather_location="Malibu",
        platform_name="win32",
    )

    assert status["items"]["admin_voice"]["optional"] is True
    assert status["items"]["admin_voice"]["state"] == "disabled"
    assert status["items"]["api_token"]["optional"] is True
    assert status["complete"] is True


def test_missing_llm_key_keeps_functional_setup_incomplete():
    status = build_setup_status(
        env={"JARVIS_CORE_MODE": "true"},
        language="en",
        admin_voice_profiles=0,
        weather_location="Malibu",
        platform_name="win32",
    )

    assert status["items"]["llm"]["configured"] is False
    assert status["items"]["llm"]["state"] == "unconfigured"
    assert status["items"]["llm"]["code"] == "llm_key_missing"
    assert status["complete"] is False


def test_full_mode_keeps_admin_voice_enrollment_disabled_by_default():
    status = build_setup_status(
        env={
            "JARVIS_CORE_MODE": "false",
            "GROQ_API_KEY": "configured",
        },
        language="en",
        admin_voice_profiles=0,
        weather_location="Malibu",
        platform_name="win32",
    )

    assert status["items"]["admin_voice"]["optional"] is True
    assert status["items"]["admin_voice"]["code"] == "voice_id_disabled"
    assert status["complete"] is True


def test_gemini_only_configuration_is_valid():
    status = _status(
        {
            "GEMINI_API_KEY": "configured",
            "JARVIS_LLM_PROVIDER": "gemini",
        }
    )

    llm = status["items"]["llm"]
    assert llm["configured"] is True
    assert llm["code"] == "gemini_configured"
    assert llm["primary_provider"] == "gemini"
    assert llm["fallback_provider"] == ""


def test_default_provider_uses_gemini_primary_and_groq_fallback():
    status = _status(
        {
            "GEMINI_API_KEY": "gemini-key",
            "GROQ_API_KEY": "groq-key",
        }
    )

    llm = status["items"]["llm"]
    assert llm["configured"] is True
    assert llm["primary_provider"] == "gemini"
    assert llm["fallback_provider"] == "groq"


def test_unconfigured_setup_items_use_matching_diagnostic_codes():
    status = build_setup_status(
        env={
            "JARVIS_CORE_MODE": "false",
            "JARVIS_TELEGRAM_ENABLED": "true",
        },
        language="",
        admin_voice_profiles=0,
        weather_location="",
        platform_name="linux",
    )

    assert status["items"]["language"]["code"] == "language_missing"
    assert status["items"]["admin_voice"]["code"] == "voice_id_disabled"
    assert status["items"]["telegram"]["code"] == "telegram_unconfigured"
    assert status["items"]["weather_location"]["code"] == ("weather_location_missing")
