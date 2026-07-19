"""Voice domain service.

This module owns voice identity/enrollment decisions. HTTP adapters should pass
plain values in and turn the returned payload/status into framework responses.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
from typing import Any

from core.api_contracts import validate_voice_response
from core.jarvis_state import DEFAULT_PROFILE_ID as _OWNER_PID
from core.runtime_logger import log_error, log_warning
from utils.jarvis_i18n import get_current_language

from voice.capture import transcribir_dudoso as _capture_transcribir_dudoso
from voice.guest_registration import (
    persist_guest_profile_registration as _guest_persist_profile_registration,
)
from voice.intent_classifier import (
    clasificar_peticion_voz as _intent_clasificar_peticion_voz,
)
from voice.intent_classifier import (
    es_pregunta_simple_voz as _intent_es_pregunta_simple_voz,
)
from voice.intent_classifier import (
    es_presentacion_nombre_voz as _intent_es_presentacion_nombre_voz,
)
from voice.pipeline import (
    get_active_whisper_language,
)
from voice.pipeline import (
    hint_necesita_reintento_whisper as _hint_necesita_whisper,
)
from voice.pipeline import (
    normalizar_confianza_transcript as _norm_conf,
)
from voice.pipeline import (
    normalizar_transcript_hint as _norm_hint,
)
from voice.pipeline import (
    reconstruir_transcripcion_por_pausas as _reconstruir_pausas,
)
from voice.pipeline import (
    transcribir_audio as _real_transcribir_audio,
)
from voice.state_machine import VoiceStage, normalize_stage
from voice.voice_response import build_voice_debug as _response_build_voice_debug

_UNVERIFIED_GUEST_PID = "guest_unverified"


class VoiceService:
    """Framework-independent voice service backed by the current runtime state."""

    def __init__(self, runtime_getter: Callable[[], Any]):
        self._runtime_getter = runtime_getter

    @property
    def runtime(self):
        return self._runtime_getter()

    def _owner_exists(self) -> bool:
        r = self.runtime
        if not r._voice_id_motor:
            return False
        return _OWNER_PID in getattr(r._voice_id_motor, "perfiles_voz", {})

    def admin_enrollment_authorized(
        self, client_profile_id: str | None, pending: dict | None = None
    ) -> bool:
        r = self.runtime
        if (
            pending
            and normalize_stage(pending.get("stage")) == VoiceStage.ADMIN_ENROLLMENT
            and pending.get("bootstrap") is True
        ):
            return True

        if not self._owner_exists():
            return True

        client_pid = str(client_profile_id or "").strip().lower()
        if client_pid != _OWNER_PID or not r._verificar_autorizacion:
            return False
        return bool(r._verificar_autorizacion(_OWNER_PID))

    def cancel_registration(self, ip: str):
        r = self.runtime
        cancelled = r._cancel_pending(ip)
        if not cancelled:
            cancelled = r._cancel_pending(None)
        if cancelled:
            return {
                "ok": True,
                "response": "Registro cancelado.",
                "identity_source": "registration_cancelled",
            }, 200
        return {
            "ok": True,
            "response": "No habia registro activo para cancelar.",
            "identity_source": "none",
        }, 200

    def start_voice_registration(self, ip: str):
        r = self.runtime
        r._cleanup_pending()
        if not r._biometria_activa or r._voice_id_motor is None:
            return {"ok": False, "error": "Biometria de voz no available en este entorno."}, 503
        if getattr(r._voice_id_motor, "encoder", None) is None:
            return {"ok": False, "error": "Motor biometrico inicializando. Intente de nuevo en unos segundos."}, 503

        r._pending_voice_registration[ip] = {
            "audio": None,
            "stage": VoiceStage.AWAITING_SAMPLE.value,
            "pending_question": "",
            "created_at": r._time_mod.time(),
        }
        return {
            "ok": True,
            "stage": VoiceStage.AWAITING_SAMPLE.value,
            "message": "Registro de voz iniciado. Capture una sample y luego diga su nombre. Se registrara como invitado.",
            "should_listen": True,
        }, 200

    def start_guest_enrollment(self, ip: str, nombre_raw: str):
        r = self.runtime
        if not r._biometria_activa or r._voice_id_motor is None:
            return {"ok": False, "error": "Biometria no available."}, 503
        if getattr(r._voice_id_motor, "encoder", None) is None:
            return {"ok": False, "error": "Motor biometrico inicializando. Intente en unos segundos."}, 503

        nombre_invitado = r._norm_nombre_invitado((nombre_raw or "").strip() or "Invitado")
        pid_invitado = f"guest_{r._slugify_guest_name(nombre_invitado)}"
        r._pending_voice_registration[ip] = {
            "audio": None,
            "stage": VoiceStage.INVITADO_ENROLLMENT.value,
            "samples_collected": [],
            "nombre_invitado": nombre_invitado,
            "pid_invitado": pid_invitado,
            "created_at": r._time_mod.time(),
        }
        return {
            "ok": True,
            "stage": VoiceStage.INVITADO_ENROLLMENT.value,
            "profile_id": pid_invitado,
            "nombre": nombre_invitado,
            "target_samples": 3,
            "message": f"Registro de {nombre_invitado} iniciado. Capture 3 samples de voz.",
            "should_listen": True,
        }, 200

    def start_admin_enrollment(self, ip: str, client_profile_id: str):
        r = self.runtime
        if not r._biometria_activa or r._voice_id_motor is None:
            return {"ok": False, "error": "Biometria no available."}, 503
        if getattr(r._voice_id_motor, "encoder", None) is None:
            return {"ok": False, "error": "Motor biometrico inicializando. Intente de nuevo en unos segundos."}, 503

        bootstrap = not self._owner_exists()
        if not self.admin_enrollment_authorized(client_profile_id):
            return {"ok": False, "error": "Solo el administrador puede iniciar enrollment de voz."}, 401

        r._pending_voice_registration[ip] = {
            "audio": None,
            "stage": VoiceStage.ADMIN_ENROLLMENT.value,
            "samples_collected": [],
            "bootstrap": bootstrap,
            "created_at": r._time_mod.time(),
        }
        target_samples = 5
        return {
            "ok": True,
            "stage": VoiceStage.ADMIN_ENROLLMENT.value,
            "target_samples": target_samples,
            "message": f"Listo para enrollment del administrador. Se requieren {target_samples} samples.",
        }, 200

    def capture_admin_sample(self, ip: str, client_profile_id: str, audio_bytes: bytes):
        r = self.runtime
        if not r._biometria_activa or r._voice_id_motor is None:
            return {"ok": False, "error": "Biometria no available."}, 503

        pending = r._get_pending(ip)
        if normalize_stage(pending.get("stage")) != VoiceStage.ADMIN_ENROLLMENT:
            return {"ok": False, "error": "No hay sesion de enrollment activa. Inicie el proceso primero."}, 409
        if not self.admin_enrollment_authorized(client_profile_id, pending):
            return {"ok": False, "error": "Solo el administrador puede capturar samples de voz."}, 401

        wav_bytes, wav_ok = r._norm_a_wav(audio_bytes)
        if not wav_ok or not r._bytes_es_wav_valido(wav_bytes):
            return {"ok": False, "error": "No se pudo procesar el audio. Intente de nuevo."}, 400

        target_samples = 5
        samples = pending.get("samples_collected") or []
        if len(samples) == 0 and hasattr(r._voice_id_motor, "reset_owner_profile"):
            r._voice_id_motor.reset_owner_profile()
            print("[BIO OWNER] Previous profile cleared. Starting clean enrollment.")

        ok = r._voice_id_motor.registrar_voz(wav_bytes, r._brain.DEFAULT_PROFILE_ID, "Administrador")
        if not ok:
            return {"ok": False, "error": "No se pudo extraer huella de voz. Intente de nuevo."}, 400

        samples.append(r._time_mod.time())
        r._pending_voice_registration[ip] = {**pending, "samples_collected": samples}
        collected = len(samples)
        if collected >= target_samples:
            r._pop_pending(ip)
            stats = {}
            if hasattr(r._voice_id_motor, "get_profile_stats"):
                stats = r._voice_id_motor.get_profile_stats().get(r._brain.DEFAULT_PROFILE_ID, {})
            return {
                "ok": True,
                "done": True,
                "message": f"Enrollment completado con {collected} samples. Administrador registrado.",
                "n_samples": collected,
                "stats": stats,
            }, 200

        return {
            "ok": True,
            "done": False,
            "collected": collected,
            "target": target_samples,
            "message": f"Muestra {collected}/{target_samples} capturada correctamente.",
        }, 200

    def capture_guest_sample(self, ip: str, audio_bytes: bytes):
        r = self.runtime
        if not r._biometria_activa or r._voice_id_motor is None:
            return {"ok": False, "error": "Biometria no available."}, 503

        pending = r._get_pending(ip)
        if normalize_stage(pending.get("stage")) != VoiceStage.INVITADO_ENROLLMENT:
            return {"ok": False, "error": "No hay sesion de enrollment activa. Inicie el proceso primero."}, 409

        wav_bytes, wav_ok = r._norm_a_wav(audio_bytes)
        if not wav_ok or not r._bytes_es_wav_valido(wav_bytes):
            return {"ok": False, "error": "No se pudo procesar el audio. Intente de nuevo."}, 400

        target_samples = 1
        samples = pending.get("samples_collected") or []
        nombre_invitado = pending.get("nombre_invitado", "Invitado")
        pid_invitado = pending.get("pid_invitado")
        if not pid_invitado:
            return {"ok": False, "error": "Sin perfil de invitado asociado."}, 400

        if r._biometria_activa and hasattr(r._voice_id_motor, "similitud_con_perfil"):
            sim_owner = r._voice_id_motor.similitud_con_perfil(wav_bytes, _OWNER_PID)
            if sim_owner > 0.85:
                r._pop_pending(ip)
                return {
                    "ok": False,
                    "error": "Esa voz corresponde al Administrador. No se registrara como invitado.",
                    "identity_source": "admin_similarity_override",
                    "similarity": round(float(sim_owner), 4),
                }, 403

        ok = r._voice_id_motor.registrar_voz(wav_bytes, pid_invitado, nombre_invitado)
        if not ok:
            return {"ok": False, "error": "No se pudo extraer huella de voz. Intente de nuevo."}, 400

        samples.append(r._time_mod.time())
        r._pending_voice_registration[ip] = {**pending, "samples_collected": samples}
        collected = len(samples)
        if collected >= target_samples:
            r._pop_pending(ip)
            r._activar_perfil_invitado(pid_invitado, nombre_invitado)
            stats = {}
            if hasattr(r._voice_id_motor, "get_profile_stats"):
                stats = r._voice_id_motor.get_profile_stats().get(pid_invitado, {})
            return {
                "ok": True,
                "done": True,
                "message": f"Registro completado con {collected} samples. {nombre_invitado} registrado como invitado.",
                "n_samples": collected,
                "profile_id": pid_invitado,
                "nombre": nombre_invitado,
                "stats": stats,
            }, 200

        return {
            "ok": True,
            "done": False,
            "collected": collected,
            "target": target_samples,
            "message": f"Muestra {collected}/{target_samples} capturada correctamente.",
        }, 200

    def process_voice(self, audio_bytes: bytes, request_data: dict):
        """Run the voice processor behind a framework-neutral interface."""
        _sync_runtime_globals(self.runtime)
        return _process_voice_sync(audio_bytes, request_data)

# Runtime-backed processor dependencies. These are synchronized from the
# API module immediately before service calls so legacy tests that monkeypatch
# api.voice_routes keep working while the domain logic lives here.
_voice_id_motor = None
_biometria_activa = False
_pending_voice_registration = None
_norm_a_wav = None
_bytes_es_wav_valido = None
_cleanup_pending = None
_cancel_pending = None
_get_pending = None
_pop_pending = None
_norm_nombre_invitado = None
_slugify_guest_name = None
_es_alias_owner = None
_reserved_owner_aliases = None
_owner_similarity_override = None
_verificar_autorizacion = None
_autorizar_por_biometria = None
_revocar_autorizacion = None
_activar_perfil_invitado = None
_whisper_model = None
_brain = None
_obs_event = None
_obs_snapshot = None
_reparar_unicode = None
_normalizar_tratamiento_admin = None
_time_mod = None


def _sync_runtime_globals(runtime) -> None:
    global _voice_id_motor, _biometria_activa, _pending_voice_registration
    global _norm_a_wav, _bytes_es_wav_valido
    global _cleanup_pending, _cancel_pending, _get_pending, _pop_pending
    global _norm_nombre_invitado, _slugify_guest_name, _es_alias_owner
    global _reserved_owner_aliases, _owner_similarity_override
    global _verificar_autorizacion, _autorizar_por_biometria
    global _revocar_autorizacion, _activar_perfil_invitado
    global _whisper_model, _brain, _obs_event, _obs_snapshot
    global _reparar_unicode, _normalizar_tratamiento_admin, _time_mod

    _voice_id_motor = runtime._voice_id_motor
    _biometria_activa = runtime._biometria_activa
    _pending_voice_registration = runtime._pending_voice_registration
    _norm_a_wav = runtime._norm_a_wav
    _bytes_es_wav_valido = runtime._bytes_es_wav_valido
    _cleanup_pending = runtime._cleanup_pending
    _cancel_pending = runtime._cancel_pending
    _get_pending = runtime._get_pending
    _pop_pending = runtime._pop_pending
    _norm_nombre_invitado = runtime._norm_nombre_invitado
    _slugify_guest_name = runtime._slugify_guest_name
    _es_alias_owner = runtime._es_alias_owner
    _reserved_owner_aliases = runtime._reserved_owner_aliases
    _owner_similarity_override = runtime._owner_similarity_override
    _verificar_autorizacion = runtime._verificar_autorizacion
    _autorizar_por_biometria = runtime._autorizar_por_biometria
    _revocar_autorizacion = runtime._revocar_autorizacion
    _activar_perfil_invitado = runtime._activar_perfil_invitado
    _whisper_model = runtime._whisper_model
    _brain = runtime._brain
    _obs_event = runtime._obs_event
    _obs_snapshot = runtime._obs_snapshot
    _reparar_unicode = runtime._reparar_unicode
    _normalizar_tratamiento_admin = runtime._normalizar_tratamiento_admin
    _time_mod = runtime._time_mod

def _voice_is_english() -> bool:
    return get_current_language().startswith("en")


def _voice_text(en: str, es: str) -> str:
    return en if _voice_is_english() else es


def _voice_display_name(profile_id: str | None) -> str:
    return _voice_text("Administrator", "Administrador") if profile_id == _brain.DEFAULT_PROFILE_ID else _voice_text("Guest", "Invitado")


_NAME_INTRO_RE = re.compile(
    r"\b(?:my\s+name\s+is|i\s+am|i'm|call\s+me|mi\s+nombre\s+es|me\s+llamo|yo\s+soy|soy)\s+[A-Za-zÃ¡Ã©Ã­Ã³ÃºÃ±Ã¼ÃÃ‰ÃÃ“ÃšÃ‘Ãœ]{2,}",
    re.IGNORECASE,
)


def _es_presentacion_nombre_voz(texto: str) -> bool:
    return bool(_NAME_INTRO_RE.search(_norm_hint(texto)))


def _es_pregunta_simple_voz(texto: str) -> bool:
    normalized = _norm_hint(texto)
    if not normalized or _es_presentacion_nombre_voz(normalized):
        return False

    lower = normalized.lower()
    if "?" in normalized or "Â¿" in normalized:
        return True

    question_prefixes = (
        "cÃ³mo ", "como ", "quÃ© ", "que ", "quiÃ©n ", "quien ", "cuÃ¡l ", "cual ",
        "dÃ³nde ", "donde ", "cuÃ¡ndo ", "cuando ", "por quÃ© ", "porque ",
        "what ", "what's ", "whats ", "how ", "who ", "where ", "when ",
        "can ", "could ", "would ", "do ", "does ",
    )
    if lower.startswith(question_prefixes):
        return True

    info_markers = (
        "clima", "tiempo", "temperatura", "weather", "temperature", "forecast",
        "hora", "fecha", "time", "date", "noticias", "news", "spotify",
    )
    return any(marker in lower for marker in info_markers)


def _transcribir_dudoso(
    audio_bytes,
    transcript_hint,
    whisper_model,
    transcript_confidence=None,
    route_mode="secure",
):
    """Shim that re-transcribes via Whisper when hint is questionable."""
    wm = whisper_model
    hint = _norm_hint(transcript_hint)
    hint_conf = _norm_conf(transcript_confidence)
    hint_tokens = re.findall(r"[A-Za-z0-9Ã¡Ã©Ã­Ã³ÃºÃ±Ã¼ÃÃ‰ÃÃ“ÃšÃ‘Ãœ]+", hint)
    has_clear_hint = bool(hint and len(hint_tokens) >= 3)
    es_dudoso = "?" in hint or "Â¿" in hint or len(hint_tokens) < 3
    es_dudoso = _hint_necesita_whisper(hint, hint_conf)
    if route_mode == "fast_info" and hint:
        if has_clear_hint:
            return hint
        es_dudoso = False if has_clear_hint else bool(hint_conf is not None and hint_conf < 0.25)
    if es_dudoso and wm:
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".wav")
            os.write(fd, audio_bytes)
            os.close(fd)
            try:
                beam_size = 1 if route_mode == "fast_info" else 2
                segments, _ = wm.transcribe(
                    tmp_path,
                    language=get_active_whisper_language(),
                    vad_filter=True,
                    beam_size=beam_size,
                    condition_on_previous_text=False,
                )
                res_segments = list(segments)
                res = _norm_hint(_reconstruir_pausas(res_segments))
                if res:
                    return res
            finally:
                try:
                    if tmp_path and os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except OSError:
                    pass
        except Exception as e:
            log_error("transcribe_audio_failed", error=str(e))
    return _real_transcribir_audio(
        audio_bytes,
        transcript_hint,
        whisper_model=wm,
        transcript_confidence=hint_conf,
    )


def _owner_exists():
    if not _voice_id_motor:
        return False
    return _OWNER_PID in getattr(_voice_id_motor, "perfiles_voz", {})


def _admin_enrollment_authorized(client_profile_id: str | None, pending: dict | None = None) -> bool:
    """Allow first owner bootstrap, then require an authorized owner session."""
    if (
        pending
        and normalize_stage(pending.get("stage")) == VoiceStage.ADMIN_ENROLLMENT
        and pending.get("bootstrap") is True
    ):
        return True

    if not _owner_exists():
        return True

    client_pid = str(client_profile_id or "").strip().lower()
    if client_pid != _OWNER_PID or not _verificar_autorizacion:
        return False
    return bool(_verificar_autorizacion(_OWNER_PID))


def _persist_guest_profile_registration(profile_id: str, nombre: str, user_text: str, reply: str) -> None:
    try:
        from langchain_core.messages import AIMessage, HumanMessage
        from services.memory_manager import memory_manager
        from tools.memory import _fusionar_facts_memoria, guardar_memoria_async

        pdata = memory_manager.get_profile_data(profile_id)
        fact = f"Nombre del usuario: {nombre}"
        facts = _fusionar_facts_memoria(pdata.get("facts", ""), [fact])
        memory_manager.set_facts(profile_id, facts)
        memory_manager.append_history(
            profile_id,
            [
                HumanMessage(content=user_text),
                AIMessage(content=reply),
            ],
        )
        guardar_memoria_async(profile_id)
    except Exception as e:
        log_warning("guest_profile_memory_persist_failed", profile_id=profile_id, error=str(e))



def _to_float_safe(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def _clasificar_peticion_voz(texto: str) -> dict:
    normalized = _norm_hint(texto)
    lower = normalized.lower()
    tokens = re.findall(r"\w+", lower, flags=re.UNICODE)
    token_count = len(tokens)

    self_identity = any(
        phrase in lower
        for phrase in [
            "quien soy",
            "quiÃ©n soy",
            "soy yo",
            "me reconoces",
            "me reconociste",
            "who am i",
            "do you recognize me",
        ]
    )
    assistant_identity = any(
        phrase in lower
        for phrase in [
            "quien eres",
            "quiÃ©n eres",
            "como te llamas",
            "cÃ³mo te llamas",
            "who are you",
            "what is your name",
            "what's your name",
        ]
    )
    secure_action = any(
        phrase in lower
        for phrase in [
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
            "envÃ­a ",
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
        ]
    )
    looks_like_question = (
        "?" in normalized
        or any(
            lower.startswith(prefix)
            for prefix in ("que ", "quÃ© ", "como ", "cÃ³mo ", "quien ", "quiÃ©n ", "cual ", "cuÃ¡l ", "donde ", "dÃ³nde ", "cuando ", "cuÃ¡ndo ", "a cuanto ", "a cuÃ¡nto ", "what ", "what's ", "whats ", "how ", "who ", "where ", "when ", "can ", "could ", "would ", "do ", "does ", "is ", "are ")
        )
        or any(
            phrase in lower
            for phrase in [
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
                "dÃ­a es hoy",
                "cuanto cuesta",
                "cuÃ¡nto cuesta",
                "a como esta",
                "a cÃ³mo estÃ¡",
                "precio de",
                "sabes",
                "noticias",
            ]
        )
    )

    if self_identity:
        return {"mode": "identity_query", "reason": "self_identity", "normalized": normalized}
    if secure_action:
        return {"mode": "secure", "reason": "secure_action", "normalized": normalized}
    if assistant_identity:
        return {"mode": "fast_info", "reason": "assistant_identity", "normalized": normalized}
    if looks_like_question and token_count <= 12:
        return {"mode": "fast_info", "reason": "short_question", "normalized": normalized}
    return {"mode": "secure", "reason": "default", "normalized": normalized}


def _resolver_perfil_rapido(client_profile_id_norm: str, owner_session_active: bool) -> tuple[str, str]:
    if owner_session_active:
        pid = _brain.DEFAULT_PROFILE_ID
    elif client_profile_id_norm and client_profile_id_norm != _brain.DEFAULT_PROFILE_ID:
        pid = client_profile_id_norm
    else:
        pid = _UNVERIFIED_GUEST_PID
    nombre = _voice_display_name(pid)
    return pid, nombre


def _emit_voice_event_for_debug(voice_debug: dict, event_name: str, **extra) -> None:
    try:
        _obs_event(
            event_name,
            request_id=voice_debug.get("request_id"),
            ip=voice_debug.get("ip"),
            client_profile_id=voice_debug.get("client_profile_id"),
            pending_stage=voice_debug.get("pending_stage"),
            **extra,
        )
    except Exception as e:
        log_error("voice_event_emission_failed", event=event_name, error=str(e))


def _set_voice_identity_for_debug(
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
        voice_debug["similarity"] = _to_float_safe(similarity_value)


def _voice_response_for_debug(voice_debug: dict, payload, status=200):
    body = dict(payload or {})
    source = str(body.get("identity_source") or voice_debug.get("identity_source") or "unknown")
    body["identity_source"] = source
    profile_out = str(body.get("profile_id") or voice_debug.get("profile_id") or "")
    nombre_out = str(body.get("nombre") or voice_debug.get("nombre") or "")
    sim_out = _to_float_safe(voice_debug.get("similarity"), 0.0)
    soft_sim_out = _to_float_safe(voice_debug.get("soft_similarity"), 0.0)
    top_sim_out = _to_float_safe(voice_debug.get("top_similarity"), 0.0)
    top_gap_out = _to_float_safe(voice_debug.get("top2_gap"), 0.0)
    body["identity_debug"] = {
        "request_id": voice_debug.get("request_id"),
        "source": source,
        "profile_id": profile_out,
        "nombre": nombre_out,
        "similarity": round(sim_out, 4),
        "soft_profile_id": voice_debug.get("soft_profile_id") or "",
        "soft_nombre": voice_debug.get("soft_nombre") or "",
        "soft_similarity": round(soft_sim_out, 4),
        "conversion_ok": bool(voice_debug.get("conversion_ok")),
        "wav_ok": bool(voice_debug.get("wav_ok")),
        "wav_valid": bool(voice_debug.get("wav_valid")),
        "route_mode": voice_debug.get("route_mode") or "",
        "route_reason": voice_debug.get("route_reason") or "",
        "transcript_confidence": round(
            _to_float_safe(voice_debug.get("transcript_confidence"), -1.0), 3
        ),
        "identify_decision": voice_debug.get("identify_decision") or "",
        "top_profile_id": voice_debug.get("top_profile_id") or "",
        "top_nombre": voice_debug.get("top_nombre") or "",
        "top_similarity": round(top_sim_out, 4),
        "top2_gap": round(top_gap_out, 4),
    }
    _emit_voice_event_for_debug(
        voice_debug,
        "voice_response_out",
        status=int(status),
        identity_source=source,
        profile_id=profile_out,
        nombre=nombre_out,
        should_listen=bool(body.get("should_listen", False)),
        similarity=round(sim_out, 4),
        soft_profile_id=voice_debug.get("soft_profile_id") or "",
        soft_similarity=round(soft_sim_out, 4),
        conversion_ok=bool(voice_debug.get("conversion_ok")),
        route_mode=voice_debug.get("route_mode") or "",
        route_reason=voice_debug.get("route_reason") or "",
        identify_decision=voice_debug.get("identify_decision") or "",
        transcript=(voice_debug.get("transcript") or "")[:200],
    )
    contract = validate_voice_response(body)
    if not contract.ok:
        _emit_voice_event_for_debug(
            voice_debug,
            "api_contract_violation",
            endpoint="/api/voice",
            side="response",
            error=contract.error,
        )
        body = {
            "identity_source": "contract_violation",
            "error": contract.error,
            "should_listen": False,
        }
        status = 500
    if status == 200:
        return body, 200
    return body, int(status)


def _build_voice_debug(
    *,
    request_id: str,
    ip: str,
    client_profile_id: str,
    pending_stage: VoiceStage,
    transcript_hint: str,
    transcript_confidence,
    audio_bytes: bytes,
    content_type: str,
    route_hint: dict,
) -> dict:
    return {
        "request_id": request_id,
        "ip": ip,
        "client_profile_id": client_profile_id,
        "pending_stage": pending_stage.value,
        "transcript_hint": transcript_hint,
        "transcript_confidence": transcript_confidence if transcript_confidence is not None else -1.0,
        "audio_bytes": len(audio_bytes or b""),
        "content_type": content_type,
        "route_mode": route_hint.get("mode") or "",
        "route_reason": route_hint.get("reason") or "",
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
    }



def _process_voice_sync(audio_bytes: bytes, voice_request: dict):
    transcript_hint = voice_request.get("transcript_hint", "")
    transcript_confidence = voice_request.get("transcript_confidence")
    client_profile_id = voice_request.get("client_profile_id", "")
    client_profile_id_norm = client_profile_id.lower()
    ip = voice_request.get("ip") or "unknown"

    _cleanup_pending()
    pending = _get_pending(ip)
    pending_stage = normalize_stage(pending.get("stage"))
    esperando_sample = pending_stage == VoiceStage.AWAITING_SAMPLE
    esperando_nombre = pending_stage == VoiceStage.AWAITING_NAME
    esperando_confirmacion = pending_stage == VoiceStage.AWAITING_CONFIRMATION
    route_hint = _intent_clasificar_peticion_voz(transcript_hint)

    content_type = voice_request.get("content_type", "")
    magic_hex = audio_bytes[:16].hex() if len(audio_bytes) >= 16 else audio_bytes.hex()

    voice_request_id = f"voice-{int(_time_mod.time() * 1000)}-{ip.replace(':', '_').replace('.', '_')}"
    voice_debug = _response_build_voice_debug(
        request_id=voice_request_id,
        ip=ip,
        client_profile_id=client_profile_id_norm,
        pending_stage=pending_stage,
        transcript_hint=transcript_hint,
        transcript_confidence=transcript_confidence,
        audio_bytes=audio_bytes,
        content_type=content_type,
        route_hint=route_hint,
    )

    def _emit_voice_event(event_name, **extra):
        return _emit_voice_event_for_debug(voice_debug, event_name, **extra)

    def _set_voice_identity(source=None, profile=None, display_name=None, similarity_value=None):
        return _set_voice_identity_for_debug(
            voice_debug,
            source=source,
            profile=profile,
            display_name=display_name,
            similarity_value=similarity_value,
        )

    def _voice_response(payload, status=200):
        return _voice_response_for_debug(voice_debug, payload, status=status)

    def _registrar_guest_desde_nombre(texto_nombre: str, audio_candidates: list[bytes], source: str):
        nombre_nuevo = _norm_nombre_invitado(texto_nombre)
        if _es_alias_owner(nombre_nuevo) or nombre_nuevo == "Invitado":
            _pending_voice_registration[ip] = {
                "audio": wav_purificado,
                "stage": VoiceStage.AWAITING_NAME.value,
                "pending_question": "",
                "created_at": _time_mod.time(),
            }
            _set_voice_identity("guest_name_rejected", similarity_value=0.0)
            return _voice_response(
                {
                    "response": _voice_text(
                        "I cannot use that as a guest name. Tell me your real name.",
                        "No puedo usar ese nombre para un invitado. Dime tu nombre real.",
                    ),
                    "profile_id": None,
                    "nueva_voz": True,
                    "identity_source": "guest_name_rejected",
                    "should_listen": True,
                }
            )

        pid_nuevo = f"guest_{_slugify_guest_name(nombre_nuevo)}"
        registro_ok = False
        ultimo_error = None
        if _biometria_activa and hasattr(_voice_id_motor, "registrar_voz"):
            for idx, audio_candidate in enumerate(audio_candidates, start=1):
                if not audio_candidate:
                    continue
                try:
                    ok = bool(_voice_id_motor.registrar_voz(audio_candidate, pid_nuevo, nombre_nuevo))
                    if ok:
                        registro_ok = True
                        print(f"[VOICE ID] Guest profile registered: {pid_nuevo} ({nombre_nuevo}) [sample {idx}]")
                        break
                    ultimo_error = "voice_register_false"
                except Exception as e:
                    ultimo_error = str(e)
                    log_error("voice_sample_registration_failed", sample_idx=idx, error=str(e))

        if _biometria_activa and not registro_ok:
            _pending_voice_registration[ip] = {
                "audio": wav_purificado,
                "stage": VoiceStage.AWAITING_NAME.value,
                "pending_question": "",
                "created_at": _time_mod.time(),
            }
            log_error("voice_registration_failed", profile_id=pid_nuevo, last_error=ultimo_error)
            _set_voice_identity("retry", profile=pid_nuevo, display_name=nombre_nuevo, similarity_value=0.0)
            return _voice_response(
                {
                    "response": _voice_text(
                        "I could not save that voiceprint clearly enough. Please repeat your name.",
                        "No pude guardar esa huella de voz con suficiente claridad. Repite tu nombre.",
                    ),
                    "profile_id": None,
                    "nueva_voz": True,
                    "identity_source": "retry",
                    "should_listen": True,
                },
                status=409,
            )

        _pop_pending(ip)
        _revocar_autorizacion(_brain.DEFAULT_PROFILE_ID)
        _activar_perfil_invitado(pid_nuevo, nombre_nuevo)
        reply = _voice_text(
            f"Done. I registered your voice as guest, {nombre_nuevo}.",
            f"Perfecto. He registrado tu voz como invitado, {nombre_nuevo}.",
        )
        _guest_persist_profile_registration(pid_nuevo, nombre_nuevo, texto_nombre, reply)
        _set_voice_identity(source=source, profile=pid_nuevo, display_name=nombre_nuevo, similarity_value=similitud)
        return _voice_response(
            {
                "response": reply,
                "profile_id": pid_nuevo,
                "nombre": nombre_nuevo,
                "identity_source": source,
                "should_listen": False,
            }
        )

    _emit_voice_event(
        "voice_request_in",
        bytes=int(len(audio_bytes or b"")),
        content_type=content_type[:80],
        transcript_hint=(transcript_hint or "")[:220],
        magic=magic_hex[:32],
    )

    if not audio_bytes:
        return _voice_response({"error": _voice_text("No audio received", "Sin audio"), "should_listen": True}, status=400)

    owner_session_active = (
        client_profile_id_norm == _brain.DEFAULT_PROFILE_ID
        and _verificar_autorizacion(_brain.DEFAULT_PROFILE_ID)
    )
    print(
        f"[VOICE ID] Hint: '{transcript_hint[:60]}' | Profile: '{client_profile_id}' | {len(audio_bytes)} bytes | CT: {content_type[:40]} | Magic: {magic_hex[:32]}"
    )
    _emit_voice_event(
        "voice_request_context",
        owner_session_active=bool(owner_session_active),
        waiting_stage=pending_stage.value,
    )
    _emit_voice_event(
        "voice_route_selected",
        route_mode=voice_debug.get("route_mode") or "",
        route_reason=voice_debug.get("route_reason") or "",
        transcript_confidence=voice_debug.get("transcript_confidence"),
    )
    skip_biometric_lookup = (
        route_hint.get("mode") == "fast_info"
        and not esperando_nombre
        and not esperando_confirmacion
    )

    wav_purificado, wav_ok = _norm_a_wav(audio_bytes)
    wav_valid = bool(_bytes_es_wav_valido(wav_purificado))
    conversion_exitosa = bool(wav_ok and wav_valid)
    voice_debug["wav_ok"] = bool(wav_ok)
    voice_debug["wav_valid"] = bool(wav_valid)
    voice_debug["conversion_ok"] = bool(conversion_exitosa)
    _emit_voice_event(
        "voice_audio_normalized",
        wav_ok=bool(wav_ok),
        wav_valid=bool(wav_valid),
        conversion_ok=bool(conversion_exitosa),
        normalized_bytes=int(len(wav_purificado or b"")),
    )

    if esperando_sample:
        _set_voice_identity("registration_sample_captured", similarity_value=0.0)
        _pending_voice_registration[ip] = {
            "audio": wav_purificado,
            "stage": VoiceStage.AWAITING_NAME.value,
            "pending_question": (transcript_hint or "").strip()[:400],
            "created_at": _time_mod.time(),
        }
        return _voice_response(
            {
                "response": _voice_text(
                    "Voice sample captured. Now tell me your name to register your profile.",
                    "Muestra de voz capturada. Ahora dÃ­game su nombre para registrar su perfil.",
                ),
                "nueva_voz": True,
                "profile_id": None,
                "should_listen": True,
            }
        )

    identity_source = "unknown"
    profile_id, nombre, similitud = None, None, 0.0
    if skip_biometric_lookup:
        if owner_session_active:
            profile_id, nombre = _brain.DEFAULT_PROFILE_ID, "Administrador"
            identity_source = "session_owner_fast_path"
        else:
            identity_source = "fast_info_hint"
    elif (not esperando_nombre) and _biometria_activa and hasattr(_voice_id_motor, "identificar"):
        try:
            profile_id, nombre, similitud = _voice_id_motor.identificar(wav_purificado)
        except Exception as e:
            log_error("voice_biometric_identify_failed", error=str(e))

    identify_debug = {}
    if (not skip_biometric_lookup) and _biometria_activa and hasattr(_voice_id_motor, "get_ultimo_debug"):
        try:
            identify_debug = _voice_id_motor.get_ultimo_debug() or {}
        except Exception as e:
            log_warning("identify_debug_read_failed", error=str(e))
            identify_debug = {}
    voice_debug["identify_decision"] = str(identify_debug.get("decision") or "")
    voice_debug["top_profile_id"] = str(identify_debug.get("top_profile_id") or "")
    voice_debug["top_nombre"] = str(identify_debug.get("top_nombre") or "")
    voice_debug["top_similarity"] = _to_float_safe(identify_debug.get("top_sim"), 0.0)
    voice_debug["top2_gap"] = _to_float_safe(identify_debug.get("top2_gap"), 0.0)

    soft_pid, soft_nombre, soft_sim = None, None, 0.0
    if (not skip_biometric_lookup) and _biometria_activa and hasattr(_voice_id_motor, "get_ultimo_candidato"):
        soft_pid, soft_nombre, soft_sim = _voice_id_motor.get_ultimo_candidato()
    voice_debug["soft_profile_id"] = str(soft_pid or "")
    voice_debug["soft_nombre"] = str(soft_nombre or "")
    voice_debug["soft_similarity"] = _to_float_safe(soft_sim, 0.0)
    _emit_voice_event(
        "voice_identification_result",
        profile_id=str(profile_id or ""),
        nombre=str(nombre or ""),
        similarity=round(_to_float_safe(similitud), 4),
        identify_decision=voice_debug.get("identify_decision") or "",
        top_profile_id=voice_debug.get("top_profile_id") or "",
        top_nombre=voice_debug.get("top_nombre") or "",
        top_similarity=round(_to_float_safe(voice_debug.get("top_similarity")), 4),
        top2_gap=round(_to_float_safe(voice_debug.get("top2_gap")), 4),
        soft_profile_id=str(soft_pid or ""),
        soft_similarity=round(_to_float_safe(soft_sim), 4),
    )

    if not conversion_exitosa:
        profile_id = None
        nombre = None
        similitud = 0.0
        identity_source = "conversion_failed"
        print("[AUTH] Audio conversion failed. Biometrics won't be used.")
    elif owner_session_active and not esperando_nombre:
        _autorizar_por_biometria(_brain.DEFAULT_PROFILE_ID, "Administrador")
        profile_id = _brain.DEFAULT_PROFILE_ID
        nombre = "Administrador"
        identity_source = "session_owner_hint"
    elif not esperando_nombre:
        if profile_id == _brain.DEFAULT_PROFILE_ID:
            _autorizar_por_biometria(profile_id, nombre or "Administrador")
            identity_source = "biometric_match"
            print("[AUTH] Administrator identified by voice. Authorization active.")
        elif profile_id and profile_id != _brain.DEFAULT_PROFILE_ID:
            _revocar_autorizacion(_brain.DEFAULT_PROFILE_ID)
            _activar_perfil_invitado(profile_id, nombre or "Invitado")
            identity_source = "biometric_match"
            print(f"[AUTH] Guest '{nombre}' speaking. Administrator authorization inactive.")

    _set_voice_identity(source=identity_source, profile=profile_id, display_name=nombre, similarity_value=similitud)

    # Transcribir (with Dudoso shim)
    texto = _capture_transcribir_dudoso(
        wav_purificado,
        transcript_hint=transcript_hint,
        whisper_model=_whisper_model,
        transcript_confidence=transcript_confidence,
        route_mode=route_hint.get("mode") or "secure",
    )
    voice_debug["transcript"] = str(texto or "")
    if not texto:
        _set_voice_identity("transcription_empty", similarity_value=0.0)
        return _voice_response({"response": "I couldn't hear you clearly.", "should_listen": True})
    route_actual = _intent_clasificar_peticion_voz(texto)
    voice_debug["route_mode"] = route_actual.get("mode") or voice_debug.get("route_mode") or ""
    voice_debug["route_reason"] = route_actual.get("reason") or voice_debug.get("route_reason") or ""

    # Detectar cancelaciÃ³n
    texto_lower = texto.strip().lower()
    CANCELAR_REGISTRO_KEYWORDS = [
        "cancelar registro", "cancela registro", "cancelar el registro", "no registrar",
        "no quiero registrarme", "no me registres", "no quiero", "olvÃ­dalo", "olvidalo",
        "olvÃ­dalo ya", "no es necesario", "dÃ©jalo", "dejalo", "basta", "no soy",
        "no es mi nombre", "ninguno", "ninguna",
    ]
    if pending and any(kw in texto_lower for kw in CANCELAR_REGISTRO_KEYWORDS):
        _cancel_pending(ip)
        _revocar_autorizacion(_brain.DEFAULT_PROFILE_ID)
        print(f"[AUTH] Registration cancelled by voice command: '{texto[:60]}'")
        _set_voice_identity("registration_cancelled", similarity_value=0.0)
        return _voice_response(
            {
                "response": _voice_text(
                    "Registration cancelled. How else can I help?",
                    "Registro cancelado. Â¿En quÃ© mÃ¡s puedo ayudarle?",
                ),
                "should_listen": True,
                "identity_source": "registration_cancelled",
            }
        )

    if (
        not pending
        and not esperando_nombre
        and not esperando_confirmacion
        and conversion_exitosa
        and not owner_session_active
        and not profile_id
        and _intent_es_presentacion_nombre_voz(texto)
    ):
        return _registrar_guest_desde_nombre(
            texto,
            [wav_purificado],
            "guest_self_introduction",
        )

    # BiometrÃ­a es el Ãºnico mecanismo de autorizaciÃ³n
    pass

    # ConfirmaciÃ³n soft match
    if esperando_confirmacion:
        soft_match_pid = pending.get("soft_match_pid")
        soft_match_nombre = pending.get("soft_match_nombre")
        pending_question = _reparar_unicode(str(pending.get("pending_question") or "")).strip()
        texto_lower = texto.strip().lower()
        confirmado = any(
            w in texto_lower
            for w in ["sÃ­", "si", "yes", "soy yo", "claro", "exacto", "correcto", "afirmativo", "ese soy", "esa soy"]
        )
        if confirmado and soft_match_pid:
            _pop_pending(ip)
            if _biometria_activa and hasattr(_voice_id_motor, "registrar_voz") and wav_purificado:
                try:
                    _voice_id_motor.registrar_voz(wav_purificado, soft_match_pid, soft_match_nombre)
                except Exception as e:
                    log_error("voice_profile_reinforce_failed", error=str(e))
            if soft_match_pid == _brain.DEFAULT_PROFILE_ID:
                _autorizar_por_biometria(soft_match_pid, soft_match_nombre or "Administrador")
            else:
                _activar_perfil_invitado(soft_match_pid, soft_match_nombre or "Invitado")
            if pending_question:
                reply, should_listen = _brain.procesar_mensaje(pending_question, profile_id=soft_match_pid)
                reply = _voice_text(f"Perfect. {reply}", f"Perfecto. {reply}")
            else:
                should_listen = False
                reply = _voice_text(f"Confirmed, {soft_match_nombre}.", f"Confirmado, {soft_match_nombre}.")
            _set_voice_identity(source="soft_match_confirmed", profile=soft_match_pid, display_name=soft_match_nombre, similarity_value=soft_sim)
            return _voice_response(
                {"response": reply, "profile_id": soft_match_pid, "nombre": soft_match_nombre, "identity_source": "soft_match_confirmed", "should_listen": bool(should_listen)}
            )
        else:
            _pending_voice_registration[ip] = {
                "audio": pending.get("audio") or wav_purificado,
                "stage": VoiceStage.AWAITING_NAME.value,
                "pending_question": pending_question or texto[:400],
                "created_at": _time_mod.time(),
            }
            _set_voice_identity("soft_match_rejected", similarity_value=soft_sim)
            return _voice_response(
                {
                    "response": _voice_text("Understood. What is your name?", "Entendido. Â¿CÃ³mo te llamas?"),
                    "nueva_voz": True,
                    "profile_id": None,
                    "identity_source": "soft_match_rejected",
                    "should_listen": True,
                }
            )

    # Registro pendiente â€” guardar nombre
    if esperando_nombre:
        pending_audio = pending.get("audio")
        if not pending_audio:
            _pop_pending(ip)
            _set_voice_identity("pending_audio_missing", similarity_value=0.0)
            return _voice_response({"response": "I couldn't save the voice sample. Please start the registration again.", "should_listen": False}, status=409)
        if not wav_ok and not _bytes_es_wav_valido(pending_audio):
            _pop_pending(ip)
            _set_voice_identity("invalid_audio", similarity_value=0.0)
            return _voice_response(
                {
                    "response": _voice_text(
                        "The audio arrived incomplete. Repeat the question to start registration again.",
                        "El audio llegÃ³ incompleto. Repite la pregunta para iniciar registro de nuevo.",
                    ),
                    "should_listen": False,
                    "nueva_voz": False,
                    "identity_source": "invalid_audio",
                },
                status=400,
            )
        texto_nombre = _reparar_unicode(texto).strip()
        if not texto_nombre:
            _set_voice_identity("name_not_understood", similarity_value=0.0)
            return _voice_response({"response": "I couldn't understand your name. Please repeat it.", "should_listen": True, "nueva_voz": True})
        pregunta_keywords = ["cÃ³mo", "como", "quÃ©", "que", "quiÃ©n", "quien", "cuÃ¡l", "cual", "dÃ³nde", "donde", "cuÃ¡ndo", "cuando", "por quÃ©", "porque", "puedes", "puede", "sabes", "sabe", "tienes", "tiene", "hay", "es", "estÃ¡", "esta", "son", "what", "what's", "whats", "how", "who", "where", "when", "weather", "temperature", "forecast"]
        tl = texto_nombre.lower()
        es_pregunta = any(kw in tl for kw in pregunta_keywords) or "?" in texto_nombre or tl.endswith("?")
        if es_pregunta:
            print(f"[AUTH] Transcript looks like a question ('{tl}'), skipping voice registration.")
            _pop_pending(ip)
            pending_question = _reparar_unicode(str(pending.get("pending_question") or "").strip())
            pid_resp = _brain.DEFAULT_PROFILE_ID if owner_session_active else _UNVERIFIED_GUEST_PID
            nombre_resp = _voice_display_name(pid_resp)
            respuesta, sl = _brain.procesar_mensaje(texto_nombre, profile_id=pid_resp)
            _set_voice_identity(source="question_during_registration", profile=pid_resp, display_name=nombre_resp, similarity_value=similitud)
            return _voice_response(
                {"response": respuesta, "profile_id": pid_resp, "nombre": nombre_resp, "identity_source": "question_during_registration", "should_listen": bool(sl)}
            )
        nombre_nuevo = _norm_nombre_invitado(texto_nombre)
        wants_owner_alias = _es_alias_owner(nombre_nuevo)
        if wants_owner_alias:
            nombre_nuevo = "Invitado"
        pid_slug = _slugify_guest_name(nombre_nuevo)
        pid_nuevo = f"guest_{pid_slug}"
        pending_question = _reparar_unicode(str(pending.get("pending_question") or "").strip())
        registro_ok = False
        ultimo_error = None
        if _biometria_activa and hasattr(_voice_id_motor, "registrar_voz"):
            candidatos_audio = []
            if pending_audio:
                candidatos_audio.append(pending_audio)
            if wav_purificado and wav_purificado != pending_audio:
                candidatos_audio.append(wav_purificado)
            for idx, audio_candidate in enumerate(candidatos_audio, start=1):
                try:
                    ok = bool(_voice_id_motor.registrar_voz(audio_candidate, pid_nuevo, nombre_nuevo))
                    if ok:
                        registro_ok = True
                        print(f"[VOICE ID] New profile registered: {pid_nuevo} ({nombre_nuevo}) [sample {idx}]")
                        break
                    ultimo_error = "voice_register_false"
                except Exception as e:
                    ultimo_error = str(e)
                    log_error("voice_sample_registration_failed", sample_idx=idx, error=str(e))
        if _biometria_activa and not registro_ok:
            if owner_session_active:
                _pop_pending(ip)
                reply, sl = _brain.procesar_mensaje(texto, profile_id=_brain.DEFAULT_PROFILE_ID)
                _set_voice_identity(source="session_owner_fallback_registration", profile=_brain.DEFAULT_PROFILE_ID, display_name="Administrador", similarity_value=similitud)
                return _voice_response(
                    {"response": reply, "profile_id": _brain.DEFAULT_PROFILE_ID, "nombre": "Administrador", "identity_source": "session_owner_fallback_registration", "should_listen": bool(sl)}
                )
            _pending_voice_registration[ip] = {
                "audio": wav_purificado or pending_audio,
                "stage": VoiceStage.AWAITING_NAME.value,
                "pending_question": pending_question,
                "created_at": _time_mod.time(),
            }
            log_error("voice_registration_failed", profile_id=pid_nuevo, last_error=ultimo_error)
            _set_voice_identity("retry", profile=pid_nuevo, display_name=nombre_nuevo, similarity_value=0.0)
            return _voice_response(
                {"response": "I couldn't register the voiceprint with sufficient quality. Please repeat your name clearly.", "should_listen": True, "nueva_voz": True, "identity_source": "retry"},
                status=409,
            )
        _pop_pending(ip)
        _revocar_autorizacion(_brain.DEFAULT_PROFILE_ID)
        _activar_perfil_invitado(pid_nuevo, nombre_nuevo)
        identity_source = "guest_registration"
        if pending_question:
            respuesta_pregunta, should_listen = _brain.procesar_mensaje(pending_question, profile_id=pid_nuevo)
            reply = _voice_text(
                f"Done. I registered your voice as guest, {nombre_nuevo}. About what you asked: {respuesta_pregunta}",
                f"Perfecto. He registrado tu voz como invitado, {nombre_nuevo}. Sobre lo que preguntaste: {respuesta_pregunta}",
            )
        else:
            should_listen = False
            reply = _voice_text(
                f"Done. I registered your voice as guest, {nombre_nuevo}.",
                f"Perfecto. He registrado tu voz como invitado, {nombre_nuevo}.",
            )
        _guest_persist_profile_registration(pid_nuevo, nombre_nuevo, texto_nombre, reply)
        nombre_final = nombre_nuevo if pid_nuevo != _brain.DEFAULT_PROFILE_ID else _voice_display_name(_brain.DEFAULT_PROFILE_ID)
        _set_voice_identity(source=identity_source, profile=pid_nuevo, display_name=nombre_final, similarity_value=similitud)
        return _voice_response(
            {"response": reply, "profile_id": pid_nuevo, "nombre": nombre_final, "identity_source": identity_source, "should_listen": bool(should_listen)}
        )

    # Fallback de sesiÃ³n
    if not esperando_nombre and not conversion_exitosa and owner_session_active:
        reply, sl = _brain.procesar_mensaje(texto, profile_id=_brain.DEFAULT_PROFILE_ID)
        _set_voice_identity(source="session_owner_fallback_audio_error", profile=_brain.DEFAULT_PROFILE_ID, display_name="Administrador", similarity_value=similitud)
        return _voice_response(
            {"response": reply, "profile_id": _brain.DEFAULT_PROFILE_ID, "nombre": "Administrador", "identity_source": "session_owner_fallback_audio_error", "should_listen": sl}
        )

    # Session continuity
    if not esperando_nombre and not esperando_confirmacion and not profile_id and conversion_exitosa and owner_session_active and soft_pid == _brain.DEFAULT_PROFILE_ID and soft_sim >= 0.12:
        print(f"[AUTH] Session continuity: Administrador below threshold (sim={soft_sim:.4f}) but session active. Maintaining identity.")
        _autorizar_por_biometria(_brain.DEFAULT_PROFILE_ID, "Administrador")
        profile_id = _brain.DEFAULT_PROFILE_ID
        nombre = "Administrador"
        identity_source = "session_continuity"
        _set_voice_identity(source=identity_source, profile=profile_id, display_name=nombre, similarity_value=soft_sim)
        if _biometria_activa and hasattr(_voice_id_motor, "registrar_voz") and wav_purificado:
            try:
                _voice_id_motor.registrar_voz(wav_purificado, _OWNER_PID, "Administrador")
                print("[AUTH] Owner profile reinforced with session sample.")
            except Exception as e:
                log_warning("voice_registration_reinforce_failed", error=str(e))

    # Voz desconocida
    if not esperando_confirmacion and (
        (not conversion_exitosa)
        or (not profile_id and _biometria_activa and _voice_id_motor and hasattr(_voice_id_motor, "encoder") and _voice_id_motor.encoder is not None)
    ):
        if owner_session_active:
            _autorizar_por_biometria(_brain.DEFAULT_PROFILE_ID, "Administrador")
            reply, sl = _brain.procesar_mensaje(texto, profile_id=_brain.DEFAULT_PROFILE_ID)
            _set_voice_identity(source="session_owner_fallback", profile=_brain.DEFAULT_PROFILE_ID, display_name="Administrador", similarity_value=similitud)
            return _voice_response(
                {"response": reply, "profile_id": _brain.DEFAULT_PROFILE_ID, "nombre": "Administrador", "identity_source": "session_owner_fallback", "should_listen": bool(sl)}
            )
        if route_actual.get("mode") == "identity_query":
            if conversion_exitosa:
                _pending_voice_registration[ip] = {
                    "audio": wav_purificado,
                    "stage": VoiceStage.AWAITING_NAME.value,
                    "pending_question": "",
                    "created_at": _time_mod.time(),
                }
            _set_voice_identity("identity_query_unverified", similarity_value=similitud)
            return _voice_response(
                {
                    "response": _voice_text(
                        "I cannot confirm who you are with enough confidence yet. Tell me your name and I will register this voice as a guest profile.",
                        "TodavÃ­a no puedo confirmar quiÃ©n eres con suficiente seguridad. Dime tu nombre y registrarÃ© esta voz como perfil invitado.",
                    ),
                    "profile_id": None,
                    "nueva_voz": bool(conversion_exitosa),
                    "identity_source": "identity_query_unverified",
                    "should_listen": True,
                }
            )
        if route_actual.get("mode") == "fast_info":
            pid_fast, nombre_fast = _resolver_perfil_rapido(client_profile_id_norm, owner_session_active)
            reply, sl = _brain.procesar_mensaje(texto, profile_id=pid_fast)
            _set_voice_identity(
                source="fast_info_direct",
                profile=pid_fast,
                display_name=nombre_fast,
                similarity_value=similitud,
            )
            return _voice_response(
                {
                    "response": reply,
                    "profile_id": pid_fast,
                    "nombre": nombre_fast,
                    "identity_source": "fast_info_direct",
                    "should_listen": bool(sl),
                }
            )
        if conversion_exitosa and soft_pid and soft_sim >= 0.15 and soft_nombre:
            _pending_voice_registration[ip] = {
                "audio": wav_purificado,
                "stage": VoiceStage.AWAITING_CONFIRMATION.value,
                "soft_match_pid": soft_pid,
                "soft_match_nombre": soft_nombre,
                "pending_question": texto[:400],
                "created_at": _time_mod.time(),
            }
            _revocar_autorizacion(_brain.DEFAULT_PROFILE_ID)
            if soft_pid == _brain.DEFAULT_PROFILE_ID:
                reply = "One moment. Is that you, Administrator?"
            else:
                reply = f"One moment. Are you {soft_nombre}?"
            _set_voice_identity("soft_match_pending", similarity_value=soft_sim)
            return _voice_response(
                {"response": reply, "profile_id": None, "nueva_voz": True, "identity_source": "soft_match_pending", "should_listen": True}
            )
        tl = texto.lower()
        es_pregunta_simple = _intent_es_pregunta_simple_voz(texto)
        if es_pregunta_simple:
            print(f"[AUTH] Unknown voice but simple question ('{tl}'), responding without registration.")
            reply, sl = _brain.procesar_mensaje(texto, profile_id=_UNVERIFIED_GUEST_PID)
            _set_voice_identity(source="unknown_direct_response", profile=_UNVERIFIED_GUEST_PID, display_name=_voice_display_name(_UNVERIFIED_GUEST_PID), similarity_value=similitud)
            return _voice_response(
                {"response": reply, "profile_id": _UNVERIFIED_GUEST_PID, "nombre": _voice_display_name(_UNVERIFIED_GUEST_PID), "identity_source": "unknown_direct_response", "should_listen": bool(sl)}
            )
        _pending_voice_registration[ip] = {
            "audio": wav_purificado,
            "stage": VoiceStage.AWAITING_NAME.value,
            "pending_question": texto[:400],
            "created_at": _time_mod.time(),
        }
        _revocar_autorizacion(_brain.DEFAULT_PROFILE_ID)
        if not conversion_exitosa:
            reply = "I couldn't process your audio. Who are you? I will answer your question afterwards."
        else:
            reply = "One moment. I don't recognize your voice. What is your name? I will answer your question immediately after."
        _set_voice_identity(source="unknown" if conversion_exitosa else "conversion_failed", similarity_value=similitud)
        return _voice_response(
            {"response": reply, "profile_id": None, "nueva_voz": True, "identity_source": "unknown" if conversion_exitosa else "conversion_failed", "should_listen": True}
        )

    # Procesar mensaje
    pid_final = profile_id or _UNVERIFIED_GUEST_PID
    nombre_final = nombre or ("Administrador" if pid_final == _brain.DEFAULT_PROFILE_ID else "Invitado")
    reply, sl = _brain.procesar_mensaje(texto, profile_id=pid_final)
    _set_voice_identity(source=identity_source, profile=pid_final, display_name=nombre_final, similarity_value=similitud)
    return _voice_response(
        {"response": reply, "profile_id": pid_final, "nombre": nombre_final, "identity_source": identity_source, "should_listen": sl}
    )


