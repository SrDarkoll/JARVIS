from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


class StubTranscriber:
    def __init__(self, text: str = "", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls: list[tuple[bytes, str]] = []

    def transcribe(self, audio_bytes: bytes, language: str) -> str:
        self.calls.append((audio_bytes, language))
        if self.error:
            raise self.error
        return self.text


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


def test_coordinator_uses_reliable_browser_hint_first():
    from voice.transcription import TranscriptionCoordinator

    groq = StubTranscriber("remote")
    local = StubTranscriber("local")
    coordinator = TranscriptionCoordinator("auto", groq=groq, local=local)

    result = coordinator.transcribe(
        b"wav", "play relaxing music", 0.91, route_mode="secure", language="en"
    )

    assert (result.text, result.source) == ("play relaxing music", "browser")
    assert groq.calls == []
    assert local.calls == []


def test_coordinator_falls_from_groq_to_local():
    from voice.transcription import TranscriptionCoordinator

    groq = StubTranscriber(error=RuntimeError("provider detail must stay private"))
    local = StubTranscriber("local transcript")
    coordinator = TranscriptionCoordinator("auto", groq=groq, local=local)

    result = coordinator.transcribe(
        b"wav", "", None, route_mode="secure", language="en"
    )

    assert (result.text, result.source) == ("local transcript", "local")
    assert len(groq.calls) == 1
    assert len(local.calls) == 1


def test_explicit_provider_does_not_cross_provider_boundary():
    from voice.transcription import TranscriptionCoordinator

    groq = StubTranscriber("remote")
    local = StubTranscriber("local")
    coordinator = TranscriptionCoordinator("local", groq=groq, local=local)

    result = coordinator.transcribe(b"wav", "", None, language="es")

    assert (result.text, result.source) == ("local", "local")
    assert groq.calls == []


def test_all_provider_failures_return_controlled_unavailable():
    from voice.transcription import TranscriptionCoordinator

    coordinator = TranscriptionCoordinator(
        "auto",
        groq=StubTranscriber(error=ConnectionError("secret endpoint")),
        local=StubTranscriber(error=RuntimeError("model path")),
    )

    result = coordinator.transcribe(b"wav", "", None, language="en")

    assert (result.text, result.source) == ("", "unavailable")


def test_lazy_whisper_loads_once_for_concurrent_requests(tmp_path):
    from types import SimpleNamespace

    from voice.transcription import LazyWhisperTranscriber

    loads: list[tuple[str, str, str]] = []

    class FakeModel:
        def transcribe(self, _path, **_kwargs):
            return [SimpleNamespace(text=" local text ", start=0.0, end=1.0)], None

    def loader(model: str, device: str, compute_type: str):
        loads.append((model, device, compute_type))
        return FakeModel()

    local = LazyWhisperTranscriber(
        enabled=True,
        model_name="tiny",
        device="cpu",
        compute_type="int8",
        runtime_dir=tmp_path,
        model_loader=loader,
    )

    with ThreadPoolExecutor(max_workers=4) as pool:
        texts = list(pool.map(lambda _: local.transcribe(b"RIFFaudio", "en"), range(4)))

    assert texts == ["local text"] * 4
    assert loads == [("tiny", "cpu", "int8")]
    assert local.snapshot()["state"] == "loaded"


def test_voice_service_uses_transcription_coordinator_for_empty_hint(monkeypatch):
    from voice import service as voice_service
    from voice.transcription import TranscriptionResult

    calls = []

    class Coordinator:
        def transcribe(
            self, audio_bytes, transcript_hint, transcript_confidence, **kwargs
        ):
            calls.append((audio_bytes, transcript_hint, transcript_confidence, kwargs))
            return TranscriptionResult("backend transcript", "groq")

    monkeypatch.setattr(voice_service, "_transcription_service", Coordinator())
    result = voice_service._transcribe_command(
        b"wav", "", None, route_mode="secure", language="en"
    )

    assert result == TranscriptionResult("backend transcript", "groq")
    assert calls[0][1] == ""


def test_transcription_snapshot_contains_no_api_key():
    from core.jarvis_config import resolve_speech_to_text_config
    from voice.transcription import build_transcription_coordinator

    coordinator = build_transcription_coordinator(
        resolve_speech_to_text_config({}), "gsk_secret_value", "."
    )

    snapshot = coordinator.snapshot()
    assert snapshot["provider"] == "auto"
    assert snapshot["groq_configured"] is True
    assert "gsk_secret_value" not in repr(snapshot)
