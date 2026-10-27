from __future__ import annotations

import pytest
from core.jarvis_config import resolve_live_voice_config


def test_live_voice_config_uses_privacy_first_bounded_defaults():
    config = resolve_live_voice_config({})

    assert config.model == "gemini-3.8-live"
    assert config.provider_preview is False
    assert config.model_configuration_error == ""
    assert config.voice_name == "Charon"
    assert config.session_resumption_enabled is False
    assert config.context_compression_enabled is True
    assert config.setup_timeout_seconds == 10.0
    assert config.max_reconnect_attempts == 3
    assert config.input_chunk_ms == 20
    assert config.recent_turns == 6
    assert config.context_char_limit == 6000
    assert config.context_compression_trigger_tokens == 25000
    assert config.context_compression_target_tokens == 8000
    assert config.input_transcription_enabled is True
    assert config.output_transcription_enabled is True
    assert config.auto_fallback_enabled is True


def test_live_voice_config_clamps_values_and_rejects_unknown_model():
    config = resolve_live_voice_config(
        {
            "JARVIS_GEMINI_LIVE_MODEL": "../../other",
            "JARVIS_LIVE_SETUP_TIMEOUT_SECONDS": "999",
            "JARVIS_LIVE_RECONNECT_ATTEMPTS": "-4",
            "JARVIS_LIVE_INPUT_CHUNK_MS": "200",
            "JARVIS_LIVE_RECENT_TURNS": "500",
            "JARVIS_LIVE_COMPRESSION_TRIGGER_TOKENS": "100",
            "JARVIS_LIVE_COMPRESSION_TARGET_TOKENS": "999999",
        }
    )

    assert config.model == "gemini-3.8-live"
    assert config.model_configuration_error == "gemini_live_model_invalid"
    assert config.setup_timeout_seconds == 30.0
    assert config.max_reconnect_attempts == 0
    assert config.input_chunk_ms == 40
    assert config.recent_turns == 12
    assert config.context_compression_trigger_tokens == 8000
    assert config.context_compression_target_tokens == 8000


def test_live_voice_config_accepts_models_prefix_and_bounded_overrides():
    config = resolve_live_voice_config(
        {
            "JARVIS_GEMINI_LIVE_MODEL": "models/gemini-3.1-flash-live-preview",
            "JARVIS_GEMINI_LIVE_VOICE": "Kore",
            "JARVIS_LIVE_CONTEXT_CHARS": "8000",
            "JARVIS_LIVE_PLAYBACK_QUEUE_MS": "1500",
            "JARVIS_LIVE_MAX_AUDIO_DROPS": "12",
            "JARVIS_LIVE_AUTO_FALLBACK_ENABLED": "false",
            "JARVIS_LIVE_INPUT_TRANSCRIPTION_ENABLED": "false",
            "JARVIS_LIVE_OUTPUT_TRANSCRIPTION_ENABLED": "false",
        }
    )

    assert config.model == "gemini-3.1-flash-live-preview"
    assert config.voice_name == "Kore"
    assert config.context_char_limit == 8000
    assert config.max_playback_queue_ms == 1500
    assert config.max_consecutive_audio_drops == 12
    assert config.auto_fallback_enabled is False
    assert config.input_transcription_enabled is False
    assert config.output_transcription_enabled is False


@pytest.mark.parametrize(
    "model",
    [
        "gemini-3.8-live",
        "gemini-3.8-live-extended-thinking",
        "gemini-3.1-flash-live-preview",
        "gemini-4.0-live-preview",
    ],
)
def test_live_voice_config_preserves_requested_conversational_models(model):
    config = resolve_live_voice_config({"JARVIS_GEMINI_LIVE_MODEL": f"models/{model}"})

    assert config.model == model
    assert config.model_configuration_error == ""


@pytest.mark.parametrize(
    "model",
    ["gemini-3.5-flash-lite", "gemini-3.5-transcribe-live", "https://invalid/live?key=private"],
)
def test_non_conversational_or_malformed_live_models_are_reported(model):
    config = resolve_live_voice_config({"JARVIS_GEMINI_LIVE_MODEL": model})

    assert config.model_configuration_error == "gemini_live_model_invalid"
    assert config.model == "gemini-3.8-live"
