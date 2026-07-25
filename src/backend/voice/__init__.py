"""Paquete de reconocimiento de voz para J.A.R.V.I.S.

Exports principales:
  from voice import voice_id_motor, VOICE_ID_DISPONIBLE
  from voice.pipeline import transcribir_audio, normalizar_a_wav, ...
  from voice.identifier import VoiceIdentifier
"""

from voice.identifier import VOICE_ID_DISPONIBLE, VoiceIdentifier, voice_id_motor
from voice.pipeline import (
    OWNER_SIMILARITY_OVERRIDE,
    RESERVED_OWNER_ALIASES,
    bytes_es_wav_valido,
    cancel_pending_voice_registration,
    cleanup_pending_voice_registration,
    es_alias_owner,
    get_pending,
    normalizar_a_wav,
    normalizar_nombre_invitado,
    normalizar_transcript_hint,
    pop_pending,
    reconstruir_transcripcion_por_pausas,
    set_pending,
    slugify_guest_name,
    transcribir_audio,
)

__all__ = [
    "VoiceIdentifier",
    "voice_id_motor",
    "VOICE_ID_DISPONIBLE",
    "transcribir_audio",
    "normalizar_a_wav",
    "bytes_es_wav_valido",
    "normalizar_transcript_hint",
    "reconstruir_transcripcion_por_pausas",
    "slugify_guest_name",
    "normalizar_nombre_invitado",
    "es_alias_owner",
    "cleanup_pending_voice_registration",
    "cancel_pending_voice_registration",
    "get_pending",
    "set_pending",
    "pop_pending",
    "RESERVED_OWNER_ALIASES",
    "OWNER_SIMILARITY_OVERRIDE",
]
