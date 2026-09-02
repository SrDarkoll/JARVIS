"""
Voice Routes: voice enrollment, registration, and main voice processing.
The full implementation extracted from jarvis_backend.py.
"""

import asyncio
import importlib.util
import os
import sys
import urllib.parse
from dataclasses import dataclass

from core.jarvis_config import resolve_live_voice_config
from core.runtime_logger import log_warning
from quart import Blueprint, jsonify, request
from voice.live_session import (
    DEFAULT_MAX_AUDIO_CHUNK_BYTES,
    DEFAULT_MAX_CONTROL_MESSAGE_BYTES,
    DEFAULT_MAX_USER_TEXT_CHARS,
)
from voice.pipeline import normalizar_confianza_transcript as _norm_conf
from voice.service import VoiceService

voice_bp = Blueprint("voice", __name__)
voice_service = VoiceService(lambda: sys.modules[__name__])

MAX_AUDIO_BYTES = int((os.getenv("JARVIS_MAX_AUDIO_BYTES") or str(12 * 1024 * 1024)).strip())
MIN_AUDIO_BYTES = 1000
MAX_LIVE_WS_MESSAGE_BYTES = DEFAULT_MAX_CONTROL_MESSAGE_BYTES
MAX_LIVE_AUDIO_CHUNK_BYTES = DEFAULT_MAX_AUDIO_CHUNK_BYTES
MAX_LIVE_USER_TEXT_CHARS = DEFAULT_MAX_USER_TEXT_CHARS


@dataclass(frozen=True, slots=True)
class LiveModeSelection:
    mode: str
    code: str = ""


def _gemini_live_dependency_available() -> bool:
    return importlib.util.find_spec("websockets") is not None


def select_live_mode(
    *,
    requested_mode: str,
    gemini_configured: bool,
    gemini_dependency_available: bool,
    hybrid_available: bool,
) -> LiveModeSelection:
    mode = str(requested_mode or "auto").strip().lower()
    if mode == "hybrid_stream":
        mode = "hybrid"
    if mode not in {"auto", "gemini_live", "hybrid"}:
        mode = "auto"

    if mode == "hybrid":
        return LiveModeSelection(
            "hybrid" if hybrid_available else "unavailable",
            "" if hybrid_available else "live_voice_unavailable",
        )
    if gemini_configured and gemini_dependency_available:
        return LiveModeSelection("gemini_live")

    gemini_code = (
        "gemini_live_unconfigured"
        if not gemini_configured
        else "gemini_live_dependency_missing"
    )
    if mode == "auto" and hybrid_available:
        return LiveModeSelection("hybrid", gemini_code)
    if mode == "gemini_live":
        return LiveModeSelection("unavailable", gemini_code)
    return LiveModeSelection("unavailable", "live_voice_unavailable")


def build_live_voice_status(
    *,
    gemini_configured: bool | None = None,
    dependency_available: bool | None = None,
    hybrid_available: bool = True,
    requested_mode: str | None = None,
    active_mode: str = "",
    handshake_state: str = "idle",
    last_failure_code: str = "",
) -> dict[str, object]:
    from core.llm_providers import resolve_gemini_api_key

    config = resolve_live_voice_config()
    configured = (
        bool(resolve_gemini_api_key())
        if gemini_configured is None
        else bool(gemini_configured)
    )
    dependency = (
        _gemini_live_dependency_available()
        if dependency_available is None
        else bool(dependency_available)
    )
    requested = str(
        requested_mode
        or os.getenv("JARVIS_VOICE_PROVIDER")
        or "auto"
    ).strip().lower()
    active = str(active_mode or "").strip().lower()
    handshake = str(handshake_state or "idle").strip().lower()
    failure_code = str(last_failure_code or "").strip().lower()
    gemini_startable = configured and dependency
    runtime_ready = handshake == "ready" and bool(active)

    if active == "hybrid_stream":
        state = "degraded"
        code = failure_code or "live_fallback_active"
    elif gemini_startable:
        state = "available"
        code = "gemini_live_ready" if runtime_ready else "gemini_live_configured"
    elif hybrid_available:
        state = "degraded"
        code = (
            "gemini_live_unconfigured"
            if not configured
            else "gemini_live_dependency_missing"
        )
    else:
        state = "unavailable"
        code = "live_voice_unavailable"

    return {
        "ok": True,
        "state": state,
        "code": code,
        "configured": configured,
        "dependency_available": dependency,
        "live_supported": gemini_startable or hybrid_available,
        "gemini_live_available": gemini_startable,
        "hybrid_available": bool(hybrid_available),
        "fallback_available": bool(hybrid_available),
        "preferred_mode": "gemini_live" if gemini_startable else "hybrid_stream",
        "requested_mode": requested,
        "active_mode": active,
        "handshake_state": handshake,
        "runtime_ready": runtime_ready,
        "last_failure_code": failure_code,
        "model": config.model,
        "voice_name": config.voice_name,
        "provider_preview": True,
        "session_resumption_enabled": False,
    }


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
    return jsonify(build_live_voice_status())


@voice_bp.websocket("/api/voice/stream")
async def live_voice_stream():
    import json
    import uuid

    from quart import websocket
    from voice.live_gemini import GeminiLiveStreamer
    from voice.live_hybrid import HybridLiveStreamer
    from voice.live_session import live_session_manager
    from voice.live_transport import LiveTransportError

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
    live_config = resolve_live_voice_config()
    voice_provider = os.getenv("JARVIS_VOICE_PROVIDER", "").strip().lower()
    effective_requested_mode = requested_mode
    if requested_mode == "auto" and voice_provider == "hybrid":
        effective_requested_mode = "hybrid"
    selection = select_live_mode(
        requested_mode=effective_requested_mode,
        gemini_configured=bool(gemini_key),
        gemini_dependency_available=_gemini_live_dependency_available(),
        hybrid_available=True,
    )

    streamer_task: asyncio.Task | None = None
    hybrid_streamer: HybridLiveStreamer | None = None

    async def _activate_hybrid(reason_code: str = "") -> None:
        nonlocal hybrid_streamer
        import jarvis_backend

        hybrid_streamer = HybridLiveStreamer(
            session=session,
            tts_engine=getattr(jarvis_backend, "tts_engine", None),
            command_orchestrator=getattr(
                jarvis_backend,
                "command_orchestrator",
                None,
            ),
        )
        await hybrid_streamer.start()
        if reason_code:
            await session.emit_json(
                {
                    "type": "live_fallback_active",
                    "code": reason_code,
                    "mode": "hybrid_stream",
                }
            )

    if selection.mode == "gemini_live":
        gemini_streamer = GeminiLiveStreamer(session=session, api_key=gemini_key)
        streamer_task = asyncio.create_task(gemini_streamer.start())
    elif selection.mode == "hybrid":
        await _activate_hybrid(selection.code)
    else:
        await session.emit_json(
            {
                "type": "error",
                "code": selection.code,
                "message": "The requested live voice mode is unavailable.",
            }
        )
        await live_session_manager.remove_session(session_id)
        return

    try:
        while True:
            if streamer_task is not None:
                receive_task = asyncio.create_task(websocket.receive())
                done, _pending = await asyncio.wait(
                    {receive_task, streamer_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if streamer_task in done:
                    if not receive_task.done():
                        receive_task.cancel()
                        await asyncio.gather(
                            receive_task,
                            return_exceptions=True,
                        )
                    provider_error = streamer_task.exception()
                    code = (
                        provider_error.code
                        if isinstance(provider_error, LiveTransportError)
                        else "gemini_live_connect_failed"
                    )
                    streamer_task = None
                    requested_normalized = str(requested_mode or "auto").lower()
                    if (
                        requested_normalized == "auto"
                        and live_config.auto_fallback_enabled
                    ):
                        await _activate_hybrid(code)
                        continue
                    await session.emit_json(
                        {
                            "type": "error",
                            "code": code,
                            "message": "Gemini Live is unavailable.",
                        }
                    )
                    break
                msg = receive_task.result()
            else:
                msg = await websocket.receive()
            if msg is None:
                break
            if isinstance(msg, bytes):
                if len(msg) > session.max_audio_chunk_bytes:
                    await session.emit_json(
                        {
                            "type": "error",
                            "code": "audio_frame_too_large",
                            "message": "Audio frame exceeds the configured limit.",
                        }
                    )
                    continue
                if not await session.handle_audio_chunk(msg):
                    await session.emit_json(
                        {
                            "type": "error",
                            "code": "audio_frame_rejected",
                            "message": "Audio frame was rejected by the session budget.",
                        }
                    )
            elif isinstance(msg, str):
                if len(msg.encode("utf-8")) > session.max_control_message_bytes:
                    await session.emit_json(
                        {
                            "type": "error",
                            "code": "control_message_too_large",
                            "message": "Control message exceeds the configured limit.",
                        }
                    )
                    break
                try:
                    event = json.loads(msg)
                except Exception:
                    await session.emit_json(
                        {
                            "type": "error",
                            "code": "invalid_json_event",
                            "message": "Control message must be valid JSON.",
                        }
                    )
                    continue
                if not isinstance(event, dict):
                    await session.emit_json(
                        {
                            "type": "error",
                            "code": "invalid_event_shape",
                            "message": "Control message must be a JSON object.",
                        }
                    )
                    continue

                event_type = event.get("type", "")
                if event_type == "interrupt":
                    await session.interrupt()
                elif event_type in ("user_text", "user_transcript"):
                    text = str(event.get("text", "")).strip()
                    if len(text) > session.max_user_text_chars:
                        await session.emit_json(
                            {
                                "type": "error",
                                "code": "user_text_too_large",
                                "message": "User text exceeds the configured limit.",
                            }
                        )
                        continue
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
                elif event_type in ("screen_frame", "image_frame"):
                    img_data = event.get("data", "")
                    mime_type = event.get("mime_type", "image/jpeg")
                    if img_data:
                        if not await session.handle_screen_frame(str(img_data), str(mime_type)):
                            await session.emit_json(
                                {
                                    "type": "error",
                                    "code": "screen_frame_rejected",
                                    "message": "Screen frame exceeds the configured session budget.",
                                }
                            )
                elif event_type == "ping":
                    await session.emit_json({"type": "pong"})
                elif event_type == "stop":
                    break
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        log_warning("live_voice_stream_failed", error=type(exc).__name__)
        await session.emit_json(
            {
                "type": "error",
                "code": "live_stream_failed",
                "message": "The live stream ended unexpectedly.",
            }
        )
    finally:
        if streamer_task and not streamer_task.done():
            streamer_task.cancel()
        if streamer_task:
            await asyncio.gather(streamer_task, return_exceptions=True)
        await live_session_manager.remove_session(session_id)
