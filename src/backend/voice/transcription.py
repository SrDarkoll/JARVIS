"""Adaptive browser, Groq, and local Whisper speech transcription."""

from __future__ import annotations

import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from core.jarvis_config import SpeechToTextConfig
from core.runtime_logger import log_warning

from voice.pipeline import (
    WHISPER_BEAM_SIZE,
    hint_necesita_reintento_whisper,
    normalizar_transcript_hint,
    reconstruir_transcripcion_por_pausas,
)


class AudioTranscriber(Protocol):
    def transcribe(self, audio_bytes: bytes, language: str) -> str: ...


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    source: str


def _browser_hint_is_reliable(hint: str, confidence, route_mode: str) -> bool:
    normalized = normalizar_transcript_hint(hint)
    if not normalized:
        return False
    if route_mode == "fast_info" and len(normalized.split()) >= 3:
        return True
    return not hint_necesita_reintento_whisper(normalized, confidence)


class GroqAudioTranscriber:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        *,
        client: Any | None = None,
    ):
        self._api_key = str(api_key or "").strip()
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._api_key or self._client is not None)

    def _get_client(self):
        with self._lock:
            if self._client is None:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=self._api_key,
                    base_url="https://api.groq.com/openai/v1",
                    timeout=self._timeout_seconds,
                    max_retries=1,
                )
            return self._client

    def transcribe(self, audio_bytes: bytes, language: str) -> str:
        if not self.configured or not audio_bytes:
            return ""
        response = self._get_client().audio.transcriptions.create(
            file=("jarvis-voice.wav", audio_bytes, "audio/wav"),
            model=self._model,
            language=language,
            response_format="json",
            temperature=0,
        )
        if isinstance(response, dict):
            return normalizar_transcript_hint(response.get("text", ""))
        return normalizar_transcript_hint(getattr(response, "text", ""))


class LazyWhisperTranscriber:
    def __init__(
        self,
        *,
        enabled: bool,
        model_name: str,
        device: str,
        compute_type: str,
        runtime_dir: str | Path,
        model_loader: Callable[[str, str, str], Any] | None = None,
    ):
        self._enabled = bool(enabled)
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._runtime_dir = Path(runtime_dir)
        self._model_loader = model_loader or self._default_model_loader
        self._model = None
        self._state = "not_loaded" if self._enabled else "disabled"
        self._lock = threading.RLock()

    @staticmethod
    def _default_model_loader(model: str, device: str, compute_type: str):
        from faster_whisper import WhisperModel

        return WhisperModel(model, device=device, compute_type=compute_type)

    def _get_model(self):
        with self._lock:
            if not self._enabled or self._state == "unavailable":
                return None
            if self._model is not None:
                return self._model
            try:
                self._model = self._model_loader(
                    self._model_name,
                    self._device,
                    self._compute_type,
                )
            except Exception as exc:
                self._state = "unavailable"
                log_warning(
                    "local_voice_transcription_unavailable",
                    error=type(exc).__name__,
                )
                return None
            self._state = "loaded"
            return self._model

    def transcribe(self, audio_bytes: bytes, language: str) -> str:
        if not audio_bytes:
            return ""
        model = self._get_model()
        if model is None:
            return ""

        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="jarvis_stt_",
                suffix=".wav",
                dir=self._runtime_dir,
                delete=False,
            ) as temp_file:
                temp_file.write(audio_bytes)
                temp_path = Path(temp_file.name)

            segments, _ = model.transcribe(
                str(temp_path),
                language=language,
                vad_filter=True,
                beam_size=WHISPER_BEAM_SIZE,
                condition_on_previous_text=False,
            )
            return normalizar_transcript_hint(
                reconstruir_transcripcion_por_pausas(list(segments))
            )
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def snapshot(self) -> dict[str, bool | str]:
        with self._lock:
            return {"enabled": self._enabled, "state": self._state}


class TranscriptionCoordinator:
    def __init__(
        self,
        provider: str,
        *,
        groq: AudioTranscriber | None,
        local: AudioTranscriber | None,
    ):
        self.provider = (
            provider if provider in {"auto", "browser", "groq", "local"} else "auto"
        )
        self.groq = groq
        self.local = local

    def transcribe(
        self,
        audio_bytes: bytes,
        transcript_hint: str,
        transcript_confidence=None,
        *,
        route_mode: str = "secure",
        language: str = "en",
    ) -> TranscriptionResult:
        hint = normalizar_transcript_hint(transcript_hint)
        if _browser_hint_is_reliable(hint, transcript_confidence, route_mode):
            return TranscriptionResult(hint, "browser")

        providers = {
            "auto": (("groq", self.groq), ("local", self.local)),
            "browser": (),
            "groq": (("groq", self.groq),),
            "local": (("local", self.local),),
        }[self.provider]
        for source, transcriber in providers:
            if transcriber is None:
                continue
            try:
                text = normalizar_transcript_hint(
                    transcriber.transcribe(audio_bytes, language)
                )
            except Exception as exc:
                log_warning(
                    "voice_transcription_provider_failed",
                    provider=source,
                    error=type(exc).__name__,
                )
                continue
            if text:
                return TranscriptionResult(text, source)
        return TranscriptionResult("", "unavailable")

    def snapshot(self) -> dict[str, bool | str]:
        groq_configured = bool(
            self.groq is not None and getattr(self.groq, "configured", True)
        )
        local_snapshot = (
            self.local.snapshot()
            if self.local is not None and hasattr(self.local, "snapshot")
            else {"enabled": self.local is not None, "state": "not_loaded"}
        )
        return {
            "provider": self.provider,
            "groq_configured": groq_configured,
            "local_enabled": bool(local_snapshot["enabled"]),
            "local_state": str(local_snapshot["state"]),
        }


def build_transcription_coordinator(
    config: SpeechToTextConfig,
    groq_api_key: str,
    runtime_dir: str | Path,
) -> TranscriptionCoordinator:
    groq = GroqAudioTranscriber(
        groq_api_key,
        config.groq_model,
        config.timeout_seconds,
    )
    local = LazyWhisperTranscriber(
        enabled=config.local_enabled,
        model_name=config.local_model,
        device=config.local_device,
        compute_type=config.local_compute_type,
        runtime_dir=runtime_dir,
    )
    return TranscriptionCoordinator(config.provider, groq=groq, local=local)
