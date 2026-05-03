"""Voice intent classification helpers.

Keep route and service code away from ad-hoc language heuristics. These helpers
only decide how a voice utterance should be routed; they do not execute actions.
"""

from __future__ import annotations

import re

from voice.pipeline import normalizar_transcript_hint


_LETTER = r"A-Za-z0-9áéíóúñüÁÉÍÓÚÑÜ"
_NAME_INTRO_RE = re.compile(
    rf"\b(?:"
    rf"my\s+name\s+is|i\s+am|i'm|call\s+me|"
    rf"mi\s+nombre\s+es|me\s+llamo|yo\s+soy|soy"
    rf")\s+[{_LETTER}][{_LETTER}\s.'-]{{1,60}}",
    re.IGNORECASE,
)

_QUESTION_PREFIXES = (
    "como ",
    "cómo ",
    "que ",
    "qué ",
    "quien ",
    "quién ",
    "cual ",
    "cuál ",
    "donde ",
    "dónde ",
    "cuando ",
    "cuándo ",
    "por que ",
    "por qué ",
    "what ",
    "what's ",
    "whats ",
    "how ",
    "who ",
    "where ",
    "when ",
    "can ",
    "could ",
    "would ",
    "do ",
    "does ",
    "is ",
    "are ",
)

_INFO_MARKERS = (
    "clima",
    "tiempo",
    "temperatura",
    "weather",
    "temperature",
    "forecast",
    "hora",
    "fecha",
    "time",
    "date",
    "dia es hoy",
    "día es hoy",
    "cuanto cuesta",
    "cuánto cuesta",
    "a como esta",
    "a cómo está",
    "precio de",
    "sabes",
    "noticias",
    "news",
    "spotify",
)

_SELF_IDENTITY_MARKERS = (
    "quien soy",
    "quién soy",
    "soy yo",
    "me reconoces",
    "me reconociste",
    "who am i",
    "do you recognize me",
)

_ASSISTANT_IDENTITY_MARKERS = (
    "quien eres",
    "quién eres",
    "como te llamas",
    "cómo te llamas",
    "who are you",
    "what is your name",
    "what's your name",
)

_SECURE_ACTION_MARKERS = (
    "abre ",
    "abrir ",
    "cierra ",
    "cerrar ",
    "apaga ",
    "reinicia ",
    "bloquea ",
    "desbloquea ",
    "instala ",
    "desinstala ",
    "ejecuta ",
    "manda ",
    "manda un mensaje",
    "envia ",
    "envía ",
    "whatsapp",
    "telegram",
    "reproduce ",
    "pon ",
    "toca ",
    "play ",
    "pause ",
    "resume ",
    "skip ",
    "next ",
)


def _normalize(text: str) -> str:
    return normalizar_transcript_hint(text)


def es_presentacion_nombre_voz(texto: str) -> bool:
    return bool(_NAME_INTRO_RE.search(_normalize(texto)))


def es_pregunta_simple_voz(texto: str) -> bool:
    normalized = _normalize(texto)
    if not normalized or es_presentacion_nombre_voz(normalized):
        return False

    lower = normalized.lower()
    if "?" in normalized or "¿" in normalized:
        return True
    if lower.startswith(_QUESTION_PREFIXES):
        return True
    return any(marker in lower for marker in _INFO_MARKERS)


def clasificar_peticion_voz(texto: str) -> dict:
    normalized = _normalize(texto)
    lower = normalized.lower()
    tokens = re.findall(r"\w+", lower, flags=re.UNICODE)

    self_identity = any(phrase in lower for phrase in _SELF_IDENTITY_MARKERS)
    assistant_identity = any(phrase in lower for phrase in _ASSISTANT_IDENTITY_MARKERS)
    secure_action = any(phrase in lower for phrase in _SECURE_ACTION_MARKERS)

    looks_like_question = (
        "?" in normalized
        or "¿" in normalized
        or lower.startswith(_QUESTION_PREFIXES)
        or any(marker in lower for marker in _INFO_MARKERS)
    )

    if self_identity:
        return {"mode": "identity_query", "reason": "self_identity", "normalized": normalized}
    if secure_action:
        return {"mode": "secure", "reason": "secure_action", "normalized": normalized}
    if assistant_identity:
        return {"mode": "fast_info", "reason": "assistant_identity", "normalized": normalized}
    if looks_like_question and len(tokens) <= 12:
        return {"mode": "fast_info", "reason": "short_question", "normalized": normalized}
    return {"mode": "secure", "reason": "default", "normalized": normalized}
