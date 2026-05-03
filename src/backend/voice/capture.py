"""Voice capture and transcription helpers."""

from __future__ import annotations

import os
import re
import tempfile
from typing import Any, Callable

from core.runtime_logger import log_error
from voice.pipeline import (
    get_active_whisper_language,
    hint_necesita_reintento_whisper,
    normalizar_confianza_transcript,
    normalizar_transcript_hint,
    reconstruir_transcripcion_por_pausas,
    transcribir_audio,
)


def transcribir_dudoso(
    audio_bytes: bytes,
    transcript_hint: str,
    whisper_model: Any,
    transcript_confidence=None,
    route_mode: str = "secure",
    *,
    fallback_transcriber: Callable[..., str] = transcribir_audio,
) -> str:
    """Use Whisper only when the frontend hint is too short or unreliable."""
    hint = normalizar_transcript_hint(transcript_hint)
    hint_conf = normalizar_confianza_transcript(transcript_confidence)
    hint_tokens = re.findall(r"[A-Za-z0-9áéíóúñüÁÉÍÓÚÑÜ]+", hint)
    has_clear_hint = bool(hint and len(hint_tokens) >= 3)
    needs_whisper = hint_necesita_reintento_whisper(hint, hint_conf)

    if route_mode == "fast_info" and hint:
        if has_clear_hint:
            return hint
        needs_whisper = bool(hint_conf is not None and hint_conf < 0.25)

    if needs_whisper and whisper_model:
        tmp_path = ""
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".wav")
            os.write(fd, audio_bytes)
            os.close(fd)
            beam_size = 1 if route_mode == "fast_info" else 2
            segments, _info = whisper_model.transcribe(
                tmp_path,
                language=get_active_whisper_language(),
                vad_filter=True,
                beam_size=beam_size,
                condition_on_previous_text=False,
            )
            result = normalizar_transcript_hint(
                reconstruir_transcripcion_por_pausas(list(segments))
            )
            if result:
                return result
        except Exception as e:
            log_error("transcribe_audio_failed", error=str(e))
        finally:
            if tmp_path:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except OSError:
                    pass

    return fallback_transcriber(
        audio_bytes,
        transcript_hint,
        whisper_model=whisper_model,
        transcript_confidence=hint_conf,
    )
