from __future__ import annotations


def test_stt_config_defaults_to_auto_with_local_fallback():
    from core.jarvis_config import resolve_speech_to_text_config

    config = resolve_speech_to_text_config({})

    assert config.provider == "auto"
    assert config.groq_model == "whisper-large-v3-turbo"
    assert config.local_enabled is True
    assert config.local_model == "medium"
    assert config.local_device == "cpu"
    assert config.local_compute_type == "int8"
    assert config.timeout_seconds == 20.0


def test_stt_config_normalizes_invalid_values():
    from core.jarvis_config import resolve_speech_to_text_config

    config = resolve_speech_to_text_config(
        {
            "JARVIS_STT_PROVIDER": "unknown",
            "JARVIS_LOCAL_STT_ENABLED": "no",
            "JARVIS_STT_TIMEOUT_SECONDS": "500",
        }
    )

    assert config.provider == "auto"
    assert config.local_enabled is False
    assert config.timeout_seconds == 60.0


def test_stt_config_accepts_explicit_provider_modes():
    from core.jarvis_config import resolve_speech_to_text_config

    assert (
        resolve_speech_to_text_config({"JARVIS_STT_PROVIDER": "browser"}).provider
        == "browser"
    )
    assert (
        resolve_speech_to_text_config({"JARVIS_STT_PROVIDER": "groq"}).provider
        == "groq"
    )
    assert (
        resolve_speech_to_text_config({"JARVIS_STT_PROVIDER": "local"}).provider
        == "local"
    )
