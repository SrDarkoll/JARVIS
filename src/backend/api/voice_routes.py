"""
Voice Routes: voice enrollment, registration, and main voice processing.
The full implementation extracted from jarvis_backend.py.
"""

import asyncio
import os
import sys
import urllib.parse

from quart import Blueprint, jsonify, request
from voice.pipeline import normalizar_confianza_transcript as _norm_conf
from voice.service import VoiceService

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
_biometrics_enabled = False
_biometria_activa = _biometrics_enabled
_pending_voice_registration = None
_norm_a_wav = None
_bytes_are_valid_wav = None
_bytes_es_wav_valido = _bytes_are_valid_wav
_cleanup_pending = None
_cancel_pending = None
_get_pending = None
_pop_pending = None
_normalize_guest_name = None
_norm_nombre_invitado = _normalize_guest_name
_slugify_guest_name = None
_is_owner_alias = None
_es_alias_owner = _is_owner_alias
_reserved_owner_aliases = None
_owner_similarity_override = None
_verify_authorization = None
_authorize_by_biometrics = None
_revoke_authorization = None
_activate_guest_profile = None
_verificar_autorizacion = _verify_authorization
_autorizar_por_biometria = _authorize_by_biometrics
_revocar_autorizacion = _revoke_authorization
_activar_perfil_invitado = _activate_guest_profile
_whisper_model = None
_transcription_service = None
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
        biometrics_enabled,
        pending_voice_registration,
        norm_a_wav,
        bytes_are_valid_wav,
        cleanup_pending,
        cancel_pending,
        get_pending,
        pop_pending,
        normalize_guest_name,
        slugify_guest_name,
        is_owner_alias,
        reserved_owner_aliases,
        owner_similarity_override,
        verify_authorization,
        authorize_by_biometrics,
        revoke_authorization,
        activate_guest_profile,
        whisper_model,
        transcription_service,
        brain,
        obs_event,
        obs_snapshot,
        reparar_unicode,
        normalizar_tratamiento_admin,
        time_module,
    ):
        self.voice_id_motor = voice_id_motor
        self.biometrics_enabled = biometrics_enabled
        self.pending_voice_registration = pending_voice_registration
        self.norm_a_wav = norm_a_wav
        self.bytes_are_valid_wav = bytes_are_valid_wav
        self.cleanup_pending = cleanup_pending
        self.cancel_pending = cancel_pending
        self.get_pending = get_pending
        self.pop_pending = pop_pending
        self.normalize_guest_name = normalize_guest_name
        self.slugify_guest_name = slugify_guest_name
        self.is_owner_alias = is_owner_alias
        self.reserved_owner_aliases = reserved_owner_aliases
        self.owner_similarity_override = owner_similarity_override
        self.verify_authorization = verify_authorization
        self.authorize_by_biometrics = authorize_by_biometrics
        self.revoke_authorization = revoke_authorization
        self.activate_guest_profile = activate_guest_profile
        self.whisper_model = whisper_model
        self.transcription_service = transcription_service
        self.brain = brain
        self.obs_event = obs_event
        self.obs_snapshot = obs_snapshot
        self.reparar_unicode = reparar_unicode
        self.normalizar_tratamiento_admin = normalizar_tratamiento_admin
        self.time_module = time_module


def init_voice_routes(config: VoiceRoutesConfig):
    global _voice_id_motor, _biometrics_enabled, _pending_voice_registration
    global _norm_a_wav, _bytes_are_valid_wav
    global _cleanup_pending, _cancel_pending, _get_pending, _pop_pending
    global _normalize_guest_name, _slugify_guest_name, _is_owner_alias
    global _reserved_owner_aliases, _owner_similarity_override
    global _verify_authorization, _authorize_by_biometrics
    global _revoke_authorization, _activate_guest_profile
    global _whisper_model, _transcription_service, _brain, _obs_event, _obs_snapshot
    global _reparar_unicode, _normalizar_tratamiento_admin, _time_mod
    _voice_id_motor = config.voice_id_motor
    _biometrics_enabled = config.biometrics_enabled
    globals()["_biometria_activa"] = _biometrics_enabled
    _pending_voice_registration = config.pending_voice_registration
    _norm_a_wav = config.norm_a_wav
    _bytes_are_valid_wav = config.bytes_are_valid_wav
    globals()["_bytes_es_wav_valido"] = _bytes_are_valid_wav
    _cleanup_pending = config.cleanup_pending
    _cancel_pending = config.cancel_pending
    _get_pending = config.get_pending
    _pop_pending = config.pop_pending
    _normalize_guest_name = config.normalize_guest_name
    globals()["_norm_nombre_invitado"] = _normalize_guest_name
    _slugify_guest_name = config.slugify_guest_name
    _is_owner_alias = config.is_owner_alias
    globals()["_es_alias_owner"] = _is_owner_alias
    _reserved_owner_aliases = config.reserved_owner_aliases
    _owner_similarity_override = config.owner_similarity_override
    _verify_authorization = config.verify_authorization
    _authorize_by_biometrics = config.authorize_by_biometrics
    _revoke_authorization = config.revoke_authorization
    _activate_guest_profile = config.activate_guest_profile
    globals()["_verificar_autorizacion"] = _verify_authorization
    globals()["_autorizar_por_biometria"] = _authorize_by_biometrics
    globals()["_revocar_autorizacion"] = _revoke_authorization
    globals()["_activar_perfil_invitado"] = _activate_guest_profile
    _whisper_model = config.whisper_model
    _transcription_service = config.transcription_service
    _brain = config.brain
    _obs_event = config.obs_event
    _obs_snapshot = config.obs_snapshot
    _reparar_unicode = config.reparar_unicode
    _normalizar_tratamiento_admin = config.normalizar_tratamiento_admin
    _time_mod = config.time_module


@voice_bp.route("/api/voice/cancel", methods=["POST"])
@voice_bp.route("/api/voice/cancelar", methods=["POST"])
def cancel_voice_registration():
    ip = request.remote_addr or "127.0.0.1"
    return _service_response(voice_service.cancel_registration(ip))


@voice_bp.route("/api/voice/registration/init", methods=["POST"])
@voice_bp.route("/api/voice/registro/iniciar", methods=["POST"])
def start_voice_registration():
    ip = request.remote_addr or "unknown"
    return _service_response(voice_service.start_voice_registration(ip))


@voice_bp.route("/api/voice/registration/guest/init", methods=["POST"])
@voice_bp.route("/api/voice/registro/invitado/iniciar", methods=["POST"])
async def start_guest_registration():
    data = (await request.get_json(silent=True)) or {}
    ip = request.remote_addr or "unknown"
    return _service_response(voice_service.start_guest_enrollment(ip, data.get("nombre", "")))


@voice_bp.route("/api/voice/registration/admin/init", methods=["POST"])
@voice_bp.route("/api/voice/registro/admin/iniciar", methods=["POST"])
def start_admin_registration():
    client_profile_id = (request.headers.get("X-Profile-Id", "") or "").strip().lower()
    ip = request.remote_addr or "unknown"
    return _service_response(voice_service.start_admin_enrollment(ip, client_profile_id))


@voice_bp.route("/api/voice/registration/admin/capture", methods=["POST"])
@voice_bp.route("/api/voice/registro/admin/capturar", methods=["POST"])
async def capture_admin_registration():
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


@voice_bp.route("/api/voice/registration/guest/capture", methods=["POST"])
@voice_bp.route("/api/voice/registro/invitado/capturar", methods=["POST"])
async def capture_guest_registration():
    ip = request.remote_addr or "unknown"
    audio_bytes, error_response = await _read_audio_payload(min_bytes=MIN_AUDIO_BYTES)
    if error_response:
        return error_response
    return _service_response(await asyncio.to_thread(voice_service.capture_guest_sample, ip, audio_bytes))


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
    return _service_response(await asyncio.to_thread(voice_service.process_voice, audio_bytes, voice_request))


@voice_bp.route("/api/voice/live/status", methods=["GET"])
async def live_voice_status():
    from core.llm_providers import resolve_gemini_api_key
    gemini_key = resolve_gemini_api_key()
    gemini_available = bool(gemini_key)
    voice_provider = os.getenv("JARVIS_VOICE_PROVIDER", "").strip().lower()
    preferred_mode = "gemini_live" if (gemini_available and voice_provider != "hybrid") else "hybrid_stream"
    return jsonify({
        "ok": True,
        "live_supported": True,
        "gemini_live_available": gemini_available,
        "hybrid_available": True,
        "preferred_mode": preferred_mode,
    })


@voice_bp.websocket("/api/voice/stream")
async def live_voice_stream():
    import json
    import uuid

    from quart import websocket
    from voice.live_gemini import GeminiLiveStreamer
    from voice.live_hybrid import HybridLiveStreamer
    from voice.live_session import live_session_manager

    session_id = str(uuid.uuid4())
    language = websocket.args.get("lang", "es")
    profile_id = websocket.args.get("profile_id", "default")
    requested_mode = websocket.args.get("mode", "auto")

    async def _send_text(text: str) -> None:
        await websocket.send(text)

    async def _send_bytes(data: bytes) -> None:
        await websocket.send(data)

    session = await live_session_manager.create_session(
        session_id=session_id,
        send_text=_send_text,
        send_bytes=_send_bytes,
        profile_id=profile_id,
        language=language,
        mode=requested_mode,
    )

    from core.llm_providers import resolve_gemini_api_key
    gemini_key = resolve_gemini_api_key()
    voice_provider = os.getenv("JARVIS_VOICE_PROVIDER", "").strip().lower()
    use_gemini_live = (
        (
            requested_mode == "gemini_live"
            or (
                requested_mode == "auto"
                and (voice_provider == "gemini_live" or (voice_provider != "hybrid" and bool(gemini_key)))
            )
        )
        and bool(gemini_key)
    )

    streamer_task: asyncio.Task | None = None
    hybrid_streamer: HybridLiveStreamer | None = None
    if use_gemini_live:
        gemini_streamer = GeminiLiveStreamer(session=session, api_key=gemini_key)
        streamer_task = asyncio.create_task(gemini_streamer.start())
    else:
        import jarvis_backend
        hybrid_streamer = HybridLiveStreamer(
            session=session,
            tts_engine=getattr(jarvis_backend, "tts_engine", None),
            command_orchestrator=getattr(jarvis_backend, "command_orchestrator", None),
        )
        streamer_task = asyncio.create_task(hybrid_streamer.start())

    try:
        while True:
            msg = await websocket.receive()
            if isinstance(msg, bytes):
                await session.handle_audio_chunk(msg)
            elif isinstance(msg, str):
                try:
                    event = json.loads(msg)
                except Exception:
                    continue

                event_type = event.get("type", "")
                if event_type == "interrupt":
                    await session.interrupt()
                elif event_type in ("user_text", "user_transcript"):
                    text = str(event.get("text", "")).strip()
                    if text:
                        print(f"\n[USUARIO] >> {text}\n", flush=True)
                        try:
                            from core import core_tools
                            from core.unified_log import write_conversation
                            from langchain_core.messages import HumanMessage
                            from services.memory_manager import memory_manager

                            write_conversation("USUARIO", text, profile_id=profile_id, channel="gemini_live")
                            memory_manager.append_history(profile_id, [HumanMessage(content=text)])
                            core_tools.guardar_memoria_async(profile_id)
                        except Exception:
                            pass

                    if hybrid_streamer and text and event_type == "user_text":
                        await hybrid_streamer.process_user_text(text)
                elif event_type == "ping":
                    await session.emit_json({"type": "pong"})
                elif event_type == "stop":
                    break
    except asyncio.CancelledError:
        pass
    except Exception as e:
        await session.emit_json({"type": "error", "message": str(e)})
    finally:
        if streamer_task and not streamer_task.done():
            streamer_task.cancel()
        await live_session_manager.remove_session(session_id)
