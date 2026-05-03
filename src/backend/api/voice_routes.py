"""
Voice Routes: voice enrollment, registration, and main voice processing.
The full implementation extracted from jarvis_backend.py.
"""

from quart import Blueprint, request, jsonify
import asyncio
import sys
import urllib.parse

from voice.service import VoiceService
from voice.pipeline import normalizar_confianza_transcript as _norm_conf
import os

voice_bp = Blueprint("voice", __name__)
voice_service = VoiceService(lambda: sys.modules[__name__])

MAX_AUDIO_BYTES = int((os.getenv("JARVIS_MAX_AUDIO_BYTES") or str(12 * 1024 * 1024)).strip())
MIN_AUDIO_BYTES = 1000


async def _read_audio_payload(min_bytes: int = 1):
    content_length = request.content_length
    if content_length is not None and int(content_length) > MAX_AUDIO_BYTES:
        return None, (
            jsonify(
                {
                    "ok": False,
                    "error": f"Audio too large. Maximum allowed: {MAX_AUDIO_BYTES} bytes.",
                }
            ),
            413,
        )

    audio_bytes = await request.get_data()
    if len(audio_bytes or b"") > MAX_AUDIO_BYTES:
        return None, (
            jsonify(
                {
                    "ok": False,
                    "error": f"Audio too large. Maximum allowed: {MAX_AUDIO_BYTES} bytes.",
                }
            ),
            413,
        )
    if min_bytes and len(audio_bytes or b"") < min_bytes:
        return None, (
            jsonify({"ok": False, "error": "Insufficient audio. Please speak more clearly."}),
            400,
        )
    return audio_bytes, None


def _service_response(result):
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], dict):
        return jsonify(result[0]), int(result[1])
    if isinstance(result, dict):
        return jsonify(result)
    return result


# Injected dependencies (set via init_voice_routes)
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


class VoiceRoutesConfig:
    def __init__(
        self,
        voice_id_motor,
        biometria_activa,
        pending_voice_registration,
        norm_a_wav,
        bytes_es_wav_valido,
        cleanup_pending,
        cancel_pending,
        get_pending,
        pop_pending,
        norm_nombre_invitado,
        slugify_guest_name,
        es_alias_owner,
        reserved_owner_aliases,
        owner_similarity_override,
        verificar_autorizacion,
        autorizar_por_biometria,
        revocar_autorizacion,
        activar_perfil_invitado,
        whisper_model,
        brain,
        obs_event,
        obs_snapshot,
        reparar_unicode,
        normalizar_tratamiento_admin,
        time_module,
    ):
        self.voice_id_motor = voice_id_motor
        self.biometria_activa = biometria_activa
        self.pending_voice_registration = pending_voice_registration
        self.norm_a_wav = norm_a_wav
        self.bytes_es_wav_valido = bytes_es_wav_valido
        self.cleanup_pending = cleanup_pending
        self.cancel_pending = cancel_pending
        self.get_pending = get_pending
        self.pop_pending = pop_pending
        self.norm_nombre_invitado = norm_nombre_invitado
        self.slugify_guest_name = slugify_guest_name
        self.es_alias_owner = es_alias_owner
        self.reserved_owner_aliases = reserved_owner_aliases
        self.owner_similarity_override = owner_similarity_override
        self.verificar_autorizacion = verificar_autorizacion
        self.autorizar_por_biometria = autorizar_por_biometria
        self.revocar_autorizacion = revocar_autorizacion
        self.activar_perfil_invitado = activar_perfil_invitado
        self.whisper_model = whisper_model
        self.brain = brain
        self.obs_event = obs_event
        self.obs_snapshot = obs_snapshot
        self.reparar_unicode = reparar_unicode
        self.normalizar_tratamiento_admin = normalizar_tratamiento_admin
        self.time_module = time_module


def init_voice_routes(config: VoiceRoutesConfig):
    global _voice_id_motor, _biometria_activa, _pending_voice_registration
    global _norm_a_wav, _bytes_es_wav_valido
    global _cleanup_pending, _cancel_pending, _get_pending, _pop_pending
    global _norm_nombre_invitado, _slugify_guest_name, _es_alias_owner
    global _reserved_owner_aliases, _owner_similarity_override
    global _verificar_autorizacion, _autorizar_por_biometria
    global _revocar_autorizacion, _activar_perfil_invitado
    global _whisper_model, _brain, _obs_event, _obs_snapshot
    global _reparar_unicode, _normalizar_tratamiento_admin, _time_mod
    _voice_id_motor = config.voice_id_motor
    _biometria_activa = config.biometria_activa
    _pending_voice_registration = config.pending_voice_registration
    _norm_a_wav = config.norm_a_wav
    _bytes_es_wav_valido = config.bytes_es_wav_valido
    _cleanup_pending = config.cleanup_pending
    _cancel_pending = config.cancel_pending
    _get_pending = config.get_pending
    _pop_pending = config.pop_pending
    _norm_nombre_invitado = config.norm_nombre_invitado
    _slugify_guest_name = config.slugify_guest_name
    _es_alias_owner = config.es_alias_owner
    _reserved_owner_aliases = config.reserved_owner_aliases
    _owner_similarity_override = config.owner_similarity_override
    _verificar_autorizacion = config.verificar_autorizacion
    _autorizar_por_biometria = config.autorizar_por_biometria
    _revocar_autorizacion = config.revocar_autorizacion
    _activar_perfil_invitado = config.activar_perfil_invitado
    _whisper_model = config.whisper_model
    _brain = config.brain
    _obs_event = config.obs_event
    _obs_snapshot = config.obs_snapshot
    _reparar_unicode = config.reparar_unicode
    _normalizar_tratamiento_admin = config.normalizar_tratamiento_admin
    _time_mod = config.time_module


@voice_bp.route("/api/voice/cancelar", methods=["POST"])
def voice_cancelar_registro():
    ip = request.remote_addr or "127.0.0.1"
    return _service_response(voice_service.cancel_registration(ip))


@voice_bp.route("/api/voice/registro/iniciar", methods=["POST"])
def iniciar_registro_voz():
    ip = request.remote_addr or "unknown"
    return _service_response(voice_service.start_voice_registration(ip))


@voice_bp.route("/api/voice/registro/invitado/iniciar", methods=["POST"])
async def iniciar_registro_invitado():
    data = (await request.get_json(silent=True)) or {}
    ip = request.remote_addr or "unknown"
    return _service_response(voice_service.start_guest_enrollment(ip, data.get("nombre", "")))


@voice_bp.route("/api/voice/registro/admin/iniciar", methods=["POST"])
def iniciar_registro_admin():
    client_profile_id = (request.headers.get("X-Profile-Id", "") or "").strip().lower()
    ip = request.remote_addr or "unknown"
    return _service_response(voice_service.start_admin_enrollment(ip, client_profile_id))


@voice_bp.route("/api/voice/registro/admin/capturar", methods=["POST"])
async def capturar_registro_admin():
    ip = request.remote_addr or "unknown"
    client_profile_id = (request.headers.get("X-Profile-Id", "") or "").strip().lower()
    audio_bytes, error_response = await _read_audio_payload(min_bytes=MIN_AUDIO_BYTES)
    if error_response:
        return error_response
    return _service_response(
        await asyncio.to_thread(
            voice_service.capture_admin_sample,
            ip,
            client_profile_id,
            audio_bytes,
        )
    )


@voice_bp.route("/api/voice/registro/invitado/capturar", methods=["POST"])
async def capturar_registro_invitado():
    ip = request.remote_addr or "unknown"
    audio_bytes, error_response = await _read_audio_payload(min_bytes=MIN_AUDIO_BYTES)
    if error_response:
        return error_response
    return _service_response(
        await asyncio.to_thread(voice_service.capture_guest_sample, ip, audio_bytes)
    )


@voice_bp.route("/api/voice", methods=["POST"])
async def process_voice():
    audio_bytes, error_response = await _read_audio_payload(min_bytes=1)
    if error_response:
        return error_response
    voice_request = {
        "transcript_hint": urllib.parse.unquote(request.headers.get("X-Transcript", "")).strip(),
        "transcript_confidence": _norm_conf(request.headers.get("X-Transcript-Confidence", "")),
        "client_profile_id": (request.headers.get("X-Profile-Id", "") or "").strip(),
        "ip": request.remote_addr or "unknown",
        "content_type": (request.headers.get("Content-Type", "") or "").strip(),
    }
    return _service_response(
        await asyncio.to_thread(voice_service.process_voice, audio_bytes, voice_request)
    )
