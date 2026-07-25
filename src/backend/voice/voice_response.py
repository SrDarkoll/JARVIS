"""Voice response/debug payload helpers."""

from __future__ import annotations


def to_float_safe(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def build_voice_debug(
    *,
    request_id: str,
    ip: str,
    client_profile_id: str,
    pending_stage,
    transcript_hint: str,
    transcript_confidence,
    audio_bytes: bytes,
    content_type: str,
    route_hint: dict,
) -> dict:
    """Build the stable debug block attached to voice API responses."""
    return {
        "request_id": request_id,
        "ip": ip,
        "client_profile_id": client_profile_id,
        "pending_stage": getattr(pending_stage, "value", str(pending_stage or "")),
        "transcript_hint": transcript_hint,
        "transcript_confidence": (transcript_confidence if transcript_confidence is not None else -1.0),
        "audio_bytes": len(audio_bytes or b""),
        "content_type": content_type,
        "route_mode": (route_hint or {}).get("mode") or "",
        "route_reason": (route_hint or {}).get("reason") or "",
        "conversion_ok": False,
        "wav_ok": False,
        "wav_valid": False,
        "identity_source": "unknown",
        "profile_id": "",
        "nombre": "",
        "similarity": 0.0,
        "soft_profile_id": "",
        "soft_nombre": "",
        "soft_similarity": 0.0,
        "identify_decision": "",
        "top_profile_id": "",
        "top_nombre": "",
        "top_similarity": 0.0,
        "top2_gap": 0.0,
        "transcript": "",
        "transcription_source": "unavailable",
    }


def set_voice_identity_for_debug(
    voice_debug: dict,
    source=None,
    profile=None,
    display_name=None,
    similarity_value=None,
) -> None:
    if source is not None:
        voice_debug["identity_source"] = str(source or "unknown")
    if profile is not None:
        voice_debug["profile_id"] = str(profile or "")
    if display_name is not None:
        voice_debug["nombre"] = str(display_name or "")
    if similarity_value is not None:
        voice_debug["similarity"] = to_float_safe(similarity_value)
