import os
import sys

os.environ.setdefault("WANDB_MODE", "disabled")
os.environ.setdefault("WANDB_DISABLED", "true")
os.environ.setdefault("WANDB_DISABLE_SERVICE", "true")
os.environ.setdefault("WANDB_SILENT", "true")
if os.getenv("JARVIS_TEST_MODE") == "1" and os.name == "nt":
    import tempfile as _safe_tempfile

    _OriginalTemporaryDirectory = _safe_tempfile.TemporaryDirectory

    class _WindowsTestTemporaryDirectory(_OriginalTemporaryDirectory):
        def __init__(self, *args, **kwargs):
            kwargs["ignore_cleanup_errors"] = True
            super().__init__(*args, **kwargs)
            self._ignore_cleanup_errors = True

        def cleanup(self):
            try:
                return super().cleanup()
            except PermissionError:
                return None

    _safe_tempfile.TemporaryDirectory = _WindowsTestTemporaryDirectory

sys.stdout.reconfigure(encoding="utf-8")

if os.getenv("JARVIS_TEST_MODE") != "1":
    from core.unified_log import install_console_capture

    install_console_capture()

import asyncio
import hmac
import tempfile
import threading
import time as _time
import traceback
from urllib.parse import urlparse

from api.api_routes import api_bp, init_api_routes
from api.chat_routes import ChatRoutesConfig, chat_bp, init_chat_routes
from api.language_routes import init_language_routes, language_bp
from api.security_routes import SecurityRoutesConfig, init_security_routes, security_bp
from api.status_routes import StatusRoutesConfig, init_status_routes, status_bp
from api.tts_routes import TTSRoutesConfig, init_tts_routes, tts_bp
from api.voice_routes import VoiceRoutesConfig, init_voice_routes, voice_bp
from core import core_tools, jarvis_brain, jarvis_config
from core.app_config import get_app_config, init_app_config
from core.jarvis_config import (
    MODEL_PATH,
    OBS_DIR,
    PLUGINS_DIR,
    ROOT_DIR,
    SECURITY_AUDIT_FILE,
    SECURITY_POLICY_FILE,
    SRC_DIR,
    TTS_PRONUN_FILE,
)
from core.jarvis_observability import obs_event, obs_inc, obs_snapshot
from core.jarvis_state import recordatorios_lock as reminders_lock
from core.runtime_logger import log_error, log_warning
from core.service_container import services
from engines.tts_engine import TTSEngine
from quart import (
    Quart,
    jsonify,
    request,
)  # pyright: ignore[reportMissingImports]
from services import security_manager
from services.monitoring_service import monitoring_service
from utils.jarvis_auth import (
    activar_perfil_invitado as _activate_guest_profile,
)
from utils.jarvis_auth import (
    autorizar_por_biometria as _authorize_by_biometrics,
)
from utils.jarvis_auth import (
    revocar_autorizacion as _revoke_authorization,
)
from utils.jarvis_auth import (
    verificar_autorizacion as _verify_authorization,
)
from utils.jarvis_i18n import get_current_language
from utils.jarvis_text import normalizar_tratamiento_admin, reparar_unicode
from utils.jarvis_tts_lexicon import TTS_PRONUN_DEFAULT
from voice.transcription import build_transcription_coordinator

# SAFE IMPORT OF BIOMETRICS
try:
    from voice import VOICE_ID_DISPONIBLE as VOICE_ID_AVAILABLE
    from voice import voice_id_motor
    from voice.pipeline import (
        _PENDING_VOICE_REGISTRATION,
        OWNER_SIMILARITY_OVERRIDE,
        RESERVED_OWNER_ALIASES,
    )
    from voice.pipeline import (
        bytes_es_wav_valido as _bytes_are_valid_wav,
    )
    from voice.pipeline import (
        cancel_pending_voice_registration as _cancel_pending_voice_registration,
    )
    from voice.pipeline import (
        cleanup_pending_voice_registration as _cleanup_pending_voice_registration,
    )
    from voice.pipeline import (
        es_alias_owner as _is_owner_alias,
    )
    from voice.pipeline import (
        get_pending as _get_pending,
    )
    from voice.pipeline import (
        hint_necesita_reintento_whisper as _hint_needs_whisper,
    )
    from voice.pipeline import (
        normalizar_a_wav as _normalize_to_wav,
    )
    from voice.pipeline import (
        normalizar_confianza_transcript as _normalize_transcript_confidence,
    )
    from voice.pipeline import (
        normalizar_nombre_invitado as _normalize_guest_name,
    )
    from voice.pipeline import (
        normalizar_transcript_hint as _normalize_transcript_hint,
    )
    from voice.pipeline import (
        pop_pending as _pop_pending,
    )
    from voice.pipeline import (
        slugify_guest_name as _slugify_guest_name,
    )
    from voice.pipeline import (
        transcribir_audio as transcribe_audio,
    )

    BIOMETRICS_ENABLED = bool(VOICE_ID_AVAILABLE)
except ImportError as _bio_err:
    print(f"[VOICE] Voice package not found: {_bio_err}. Biometrics disabled.")
    voice_id_motor = None
    BIOMETRICS_ENABLED = False

    def transcribe_audio(audio, hint="", whisper_model=None):
        return hint


# --- COMPATIBILITY SHIMS ---
DEFAULT_PROFILE_ID = jarvis_brain.DEFAULT_PROFILE_ID
SHARED_PROFILE_ID = "shared"

# Import jarvis_settings for language hot-swap
import sys as _sys

if jarvis_config.ROOT_DIR not in _sys.path:
    _sys.path.insert(0, jarvis_config.ROOT_DIR)
try:
    import jarvis_settings
except ImportError:

    class jarvis_settings:
        ASSISTANT_NAME = "J.A.R.V.I.S."
        ASSISTANT_FULLNAME = "Just A Rather Very Intelligent System"
        OWNER_TITLE = "Administrator"
        COMPANY_NAME = "YOUR_COMPANY"
        LOCATION = "YOUR_CITY, YOUR_COUNTRY"
        LANGUAGE = "en"
        LOCALE = "en-US"


APP_CONFIG = init_app_config(jarvis_settings)
for _cfg_warn in APP_CONFIG.validation_warnings:
    log_warning("App config validation warning", detail=_cfg_warn)

_real_transcribe_audio = transcribe_audio


def transcribe_audio(
    audio_bytes,
    transcript_hint="",
    whisper_model=None,
    transcript_confidence=None,
):
    wm = whisper_model or globals().get("whisper_model")
    hint = _normalize_transcript_hint(transcript_hint)
    hint_conf = _normalize_transcript_confidence(transcript_confidence)
    # "Doubtful" logic for test compatibility (Fix B)
    is_doubtful = "?" in hint or "¿" in hint or len(hint.split()) < 3
    is_doubtful = _hint_needs_whisper(hint, hint_conf)
    if is_doubtful and wm:
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".wav")
            os.write(fd, audio_bytes)
            os.close(fd)
            try:
                transcriber = globals().get(
                    "_transcribir_con_whisper_archivo",
                    _transcribe_with_whisper_file,
                )
                res = transcriber(tmp_path)
                if res:
                    return res
            finally:
                try:
                    if tmp_path and os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except OSError:
                    pass
        except Exception:
            pass
    return _real_transcribe_audio(
        audio_bytes,
        transcript_hint,
        whisper_model=wm,
        transcript_confidence=hint_conf,
    )


def _transcribe_with_whisper_file(wav_path):
    wm = globals().get("whisper_model")
    if not wm:
        return ""
    try:
        segments, _ = wm.transcribe(
            wav_path,
            language=get_current_language(),
            vad_filter=True,
            beam_size=1,
            condition_on_previous_text=False,
        )
        return " ".join([s.text for s in segments]).strip()
    except Exception:
        return ""


# -------------------------------


try:
    from faster_whisper import WhisperModel  # pyright: ignore[reportMissingImports]
except ImportError:
    WhisperModel = None

SECURITY_POLICY_DEFAULT = {
    "strict_mode": False,
    "blocked_tools": [],
    "allowed_web_domains": [
        "google.com",
        "youtube.com",
        "facebook.com",
        "instagram.com",
        "x.com",
        "mail.google.com",
        "spotify.com",
        "search.brave.com",
    ],
    "allow_system_browser_fallback": False,
    "safe_apps": [
        "chrome",
        "google chrome",
        "firefox",
        "edge",
        "microsoft edge",
        "word",
        "excel",
        "powerpoint",
        "paint",
        "cmd",
        "terminal",
        "browser",
        "task manager",
        "discord",
        "vscode",
        "visual studio code",
        "spotify",
        "steam",
        "obs",
        "opera",
        "brave",
    ],
    "max_tool_errors_5m": 12,
    "tool_policies": {},
}

SECURITY_POLICY: dict = {}
SECURITY_STATE = {
    "last_update": "",
    "last_block_reason": "",
    "last_block_ts": "",
}

PROACTIVE_STATE = {
    "enabled": True,
    "cooldown_seconds": 600,
    "alerts": [],
    "last_alert_by_key": {},
    "tool_errors_window": [],
    "last_health_check": "",
}

SECURITY_LOCK = threading.RLock()
PROACTIVE_LOCK = threading.RLock()
TTS_LOCK = threading.RLock()
TTS_API_LOCK = threading.RLock()
BRIEFING_TELEGRAM_LOCK = threading.RLock()
WARN_ONCE_LOCK = threading.RLock()

WEATHER_UPDATE_LOCK = threading.RLock()

_WARN_ONCE_ERRORS: dict[str, str] = {}
try:
    TTS_MAX_CHARS = int((os.getenv("JARVIS_TTS_MAX_CHARS") or "420").strip() or "420")
except Exception as e:
    print(f"[WARN] TTS_MAX_CHARS parse error: {e}")
    TTS_MAX_CHARS = 420


class UnavailableTTSEngine:
    """Lightweight TTS adapter used when Piper cannot be loaded."""

    def __init__(self, model_path: str, pronun_file: str, repair_unicode_func):
        self.model_path = model_path
        self.pronun_file = pronun_file
        self.reparar_unicode = repair_unicode_func
        self.voice = None
        self.tts_lock = TTS_LOCK
        self.tts_pronun_map = dict(TTS_PRONUN_DEFAULT)

    def reload_model(self, new_model_path: str) -> bool:
        self.model_path = new_model_path
        return False

    def update_reglas(self, reglas_norm: dict, replace: bool = False) -> dict:
        with self.tts_lock:
            if replace:
                self.tts_pronun_map.clear()
            self.tts_pronun_map.update(reglas_norm or {})
            return dict(self.tts_pronun_map)

    def reset_reglas(self) -> dict:
        with self.tts_lock:
            self.tts_pronun_map.clear()
            self.tts_pronun_map.update(dict(TTS_PRONUN_DEFAULT))
            return dict(self.tts_pronun_map)

    def aplicar_pronunciacion(self, texto: str) -> str:
        engine = TTSEngine.__new__(TTSEngine)
        engine.tts_lock = self.tts_lock
        engine.tts_pronun_map = dict(self.tts_pronun_map)
        engine.reparar_unicode = self.reparar_unicode
        return engine.aplicar_pronunciacion(texto)

    def sintetizar(self, texto: str) -> bytes:
        raise RuntimeError("The Piper engine is not loaded.")


def _build_tts_engine():
    if os.getenv("JARVIS_TEST_MODE") == "1":
        return UnavailableTTSEngine(MODEL_PATH, TTS_PRONUN_FILE, reparar_unicode)
    try:
        return TTSEngine(MODEL_PATH, TTS_PRONUN_FILE, reparar_unicode)
    except Exception as e:
        print(f"[WARN] TTS init failed: {e}")
        return UnavailableTTSEngine(MODEL_PATH, TTS_PRONUN_FILE, reparar_unicode)


tts_engine = _build_tts_engine()
whisper_model = None
transcription_service = build_transcription_coordinator(
    jarvis_config.SPEECH_TO_TEXT,
    jarvis_config.GROQ_API_KEY,
    jarvis_config.RUNTIME_DIR,
)


def _warn_once(key: str, err: Exception | str) -> None:
    msg = str(err or "").strip() or "unknown error"
    with WARN_ONCE_LOCK:
        prev = _WARN_ONCE_ERRORS.get(key)
        if prev == msg:
            return
        _WARN_ONCE_ERRORS[key] = msg
    log_warning("Runtime warning", key=key, error=msg)
    try:
        obs_event("internal_warning", key=key, error=msg[:300])
    except Exception as e:
        log_warning("obs_event failed in _warn_once", error=str(e))


def _install_runtime_error_hooks() -> None:
    def _thread_hook(args):
        try:
            name = getattr(args.thread, "name", "thread")
            exc = getattr(args, "exc_value", None)
            tb = getattr(args, "exc_traceback", None)
            log_error("Unhandled thread exception", thread=name, error=str(exc))
            if tb:
                traceback.print_exception(args.exc_type, exc, tb)
            obs_event("thread_unhandled_exception", thread=name, error=str(exc)[:300])
        except Exception as e:
            log_warning("obs_event failed in thread hook", error=str(e))

    def _sys_hook(exc_type, exc_value, exc_tb):
        try:
            log_error("Unhandled process exception", error=str(exc_value))
            traceback.print_exception(exc_type, exc_value, exc_tb)
            obs_event("process_unhandled_exception", error=str(exc_value)[:300])
        except Exception as e:
            log_warning("obs_event failed in sys hook", error=str(e))

    threading.excepthook = _thread_hook
    sys.excepthook = _sys_hook


_install_runtime_error_hooks()

from core.jarvis_context import context

security_manager.inject_dependencies(
    {
        "_obs_inc": obs_inc,
        "_obs_event": obs_event,
        "_reparar_unicode": reparar_unicode,
        "_invocar_tool": None,
        "_recargar_plugins_runtime": None,
        "enviar_telegram_sync": None,
        "_normalizar_destino_web": core_tools._normalizar_destino_web,
        "verificar_autorizacion": _verify_authorization,
        "normalizar_tratamiento_admin": normalizar_tratamiento_admin,
        "SECURITY_POLICY_DEFAULT": SECURITY_POLICY_DEFAULT,
        "SECURITY_POLICY_FILE": SECURITY_POLICY_FILE,
        "SECURITY_AUDIT_FILE": SECURITY_AUDIT_FILE,
        "SECURITY_POLICY": SECURITY_POLICY,
        "SECURITY_STATE": SECURITY_STATE,
        "SECURITY_LOCK": SECURITY_LOCK,
        "PROACTIVE_STATE": PROACTIVE_STATE,
        "PROACTIVE_LOCK": PROACTIVE_LOCK,
    }
)

_load_security_policy = security_manager._load_security_policy
_security_snapshot = security_manager._security_snapshot
_security_tail = security_manager._security_tail
_proactive_snapshot = security_manager._proactive_snapshot
_actualizar_security_policy = security_manager._actualizar_security_policy
_ejecutar_accion_control = security_manager._ejecutar_accion_control
_proactive_push_alert = security_manager._proactive_push_alert
_load_security_policy()

with PROACTIVE_LOCK:
    _app_cfg = get_app_config()
    PROACTIVE_STATE["enabled"] = _app_cfg.toggles.proactive_activo
    PROACTIVE_STATE["cooldown_seconds"] = _app_cfg.toggles.proactive_cooldown

_cors_origins = list(get_app_config().cors_origins)

from quart_cors import cors

# Initialize the Quart application
app = Quart(
    __name__,
    template_folder=os.path.join(SRC_DIR, "frontend", "templates"),
    static_folder=os.path.join(SRC_DIR, "frontend", "static"),
    static_url_path="/static",
)
app.config["MAX_CONTENT_LENGTH"] = int((os.getenv("JARVIS_MAX_REQUEST_BYTES") or str(12 * 1024 * 1024)).strip())
app = cors(app, allow_origin=_cors_origins)
app.register_blueprint(tts_bp)
app.register_blueprint(api_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(security_bp)
app.register_blueprint(voice_bp)
app.register_blueprint(status_bp)
app.register_blueprint(language_bp)

_LOOPBACK_ADDRS = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost", ""}
_CRITICAL_API_PATHS = {
    "/api/auth_status",
    "/api/control/quick",
    "/api/language",
    "/api/observability",
    "/api/observabilidad",
    "/api/operator/status",
    "/api/profiles",
    "/api/perfiles",
    "/api/plugins",
    "/api/plugins/reload",
    "/api/proactive",
    "/api/proactive/clear",
    "/api/security",
    "/api/security/policy",
    "/api/setup/status",
    "/api/tts/pronunciation",
    "/api/tts/pronunciation/reset",
    "/api/tts/pronunciacion",
    "/api/tts/pronunciacion/reset",
    "/api/voice/registration/admin/capture",
    "/api/voice/registration/admin/init",
    "/api/voice/registro/admin/capturar",
    "/api/voice/registro/admin/iniciar",
}
_ORIGIN_PROTECTED_API_PATHS = {
    "/api/chat",
    "/api/chat/stream",
    "/api/tts",
    "/api/voice",
    "/api/voice/live/status",
}


def _is_loopback(addr: str | None) -> bool:
    return str(addr or "").strip().lower() in _LOOPBACK_ADDRS


def _normalize_origin(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.hostname:
        return ""
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port else ""
    return f"{scheme}://{host}{port}"


def _is_trusted_browser_origin() -> bool:
    allowed = {origin for origin in (_normalize_origin(item) for item in jarvis_config.get_cors_origins()) if origin}
    origin = _normalize_origin(request.headers.get("Origin"))
    if origin:
        return origin in allowed
    referer = _normalize_origin(request.headers.get("Referer"))
    if referer:
        return referer in allowed
    return True


def _is_critical_api_path(path: str) -> bool:
    normalized = (path or "").rstrip("/") or "/"
    if normalized in _CRITICAL_API_PATHS:
        return True
    return any(normalized.startswith(prefix + "/") for prefix in _CRITICAL_API_PATHS)


def _is_origin_protected_api_path(path: str) -> bool:
    normalized = (path or "").rstrip("/") or "/"
    if normalized in _ORIGIN_PROTECTED_API_PATHS:
        return True
    return any(normalized.startswith(prefix + "/") for prefix in _ORIGIN_PROTECTED_API_PATHS)


def _has_valid_api_token() -> bool:
    configured_token = (os.getenv("JARVIS_API_TOKEN") or "").strip()
    if not configured_token:
        return False
    supplied = (request.headers.get("X-JARVIS-API-TOKEN") or "").strip()
    return hmac.compare_digest(supplied, configured_token)


@app.before_request
async def _require_token_for_critical_routes():
    if request.method == "OPTIONS":
        return None
    is_critical = _is_critical_api_path(request.path)
    if _is_origin_protected_api_path(request.path) and not _is_trusted_browser_origin() and not _has_valid_api_token():
        obs_event("api_origin_denied", path=request.path, ip=request.remote_addr)
        message = "Untrusted origin for critical route." if is_critical else "Untrusted origin for API route."
        return jsonify({"error": message}), 403
    if not is_critical:
        return None

    configured_token = (os.getenv("JARVIS_API_TOKEN") or "").strip()
    if configured_token:
        if _has_valid_api_token():
            return None
        obs_event("api_token_denied", path=request.path, ip=request.remote_addr)
        return jsonify({"error": "Invalid or missing token."}), 401

    if not _is_trusted_browser_origin():
        obs_event("api_origin_denied", path=request.path, ip=request.remote_addr)
        return jsonify({"error": "Untrusted origin for critical route."}), 403

    if _is_loopback(request.remote_addr):
        return None

    obs_event("api_token_required", path=request.path, ip=request.remote_addr)
    return jsonify({"error": "Token required for critical routes."}), 401


@app.after_request
async def _set_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), geolocation=(), microphone=(self)",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' https: data: blob:; "
        "media-src 'self' blob:; "
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self'",
    )
    return response


context.update(
    {
        "app": app,
        "obs_inc": obs_inc,
        "obs_event": obs_event,
        "noticias_cache": services.noticias_cache,
        "weather_cache": services.weather_cache,
        "reminders": services.get_reminders(),
        "recordatorios_lock": reminders_lock,
    }
)

IP_LAST_CALL = {}
CHAT_LIMIT_SECONDS = 1.0
TTS_LIMIT_SECONDS = 0.35
IP_LAST_CALL_MAX_SIZE = 10000
_IP_LAST_CALL_LOCK = threading.Lock()


def _check_rate_limit(ip, limit, endpoint):
    ip_norm = (str(ip or "unknown")).strip().lower()
    if ip_norm in {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}:
        return True, 0.0
    key = (ip_norm, endpoint)
    now = _time.time()
    with _IP_LAST_CALL_LOCK:
        last = IP_LAST_CALL.get(key, 0.0)
        elapsed = now - last
        if elapsed < limit:
            return False, max(0.0, limit - elapsed)
        if len(IP_LAST_CALL) >= IP_LAST_CALL_MAX_SIZE:
            oldest_entries = sorted(IP_LAST_CALL.items(), key=lambda x: x[1])[: IP_LAST_CALL_MAX_SIZE // 4]
            for old_key, _ in oldest_entries:
                del IP_LAST_CALL[old_key]
        IP_LAST_CALL[key] = now
        return True, 0.0


os.makedirs(OBS_DIR, exist_ok=True)
os.makedirs(PLUGINS_DIR, exist_ok=True)

from core.service_container import services

services.obs_inc = obs_inc
services.obs_event = obs_event
services.reparar_unicode = reparar_unicode
services.security_audit = security_manager._security_audit
services.security_guard = security_manager._security_guard
services.security_allow_fallback = security_manager._security_allow_system_browser_fallback
services.proactive_tool_error = security_manager._proactive_register_tool_error
services.reminders = services.get_reminders()
services.reminders_lock = reminders_lock
services.SRC_DIR = SRC_DIR
services.ROOT_DIR = ROOT_DIR

# Initialize the brain
jarvis_brain.init_brain(app)

# Inject dependencies from the brain
services.invoke_tool = jarvis_brain._invocar_tool
services.reload_plugins = jarvis_brain._recargar_plugins_runtime

# Synchronize with core_tools (shim) for legacy plugins if necessary
core_tools.inject_dependencies({"noticias_cache": services.noticias_cache, "weather_cache": services.weather_cache})
security_manager.inject_dependencies(
    {
        "_invocar_tool": jarvis_brain._invocar_tool,
        "_recargar_plugins_runtime": jarvis_brain._recargar_plugins_runtime,
    }
)

from core import jarvis_state

DEFAULT_PROFILE_ID = jarvis_state.DEFAULT_PROFILE_ID
memory_lock = jarvis_state.memoria_lock
_profiles_memory = jarvis_state._perfiles_memoria

init_api_routes(services, os.getenv("JARVIS_BROWSER_MODE", "system"), ROOT_DIR)
init_chat_routes(ChatRoutesConfig(IP_LAST_CALL, _IP_LAST_CALL_LOCK, CHAT_LIMIT_SECONDS))
init_tts_routes(
    TTSRoutesConfig(
        tts_engine,
        TTS_LOCK,
        TTS_API_LOCK,
        TTS_MAX_CHARS,
        tts_engine.sintetizar,
    )
)
init_security_routes(
    SecurityRoutesConfig(
        _security_snapshot,
        _proactive_snapshot,
        _actualizar_security_policy,
        _ejecutar_accion_control,
        SECURITY_POLICY,
        PROACTIVE_STATE,
        PROACTIVE_LOCK,
        jarvis_brain,
    )
)
init_voice_routes(
    VoiceRoutesConfig(
        voice_id_motor,
        BIOMETRICS_ENABLED,
        _PENDING_VOICE_REGISTRATION,
        _normalize_to_wav,
        _bytes_are_valid_wav,
        _cleanup_pending_voice_registration,
        _cancel_pending_voice_registration,
        _get_pending,
        _pop_pending,
        _normalize_guest_name,
        _slugify_guest_name,
        _is_owner_alias,
        RESERVED_OWNER_ALIASES,
        OWNER_SIMILARITY_OVERRIDE,
        _verify_authorization,
        _authorize_by_biometrics,
        _revoke_authorization,
        _activate_guest_profile,
        whisper_model,
        transcription_service,
        jarvis_brain,
        obs_event,
        obs_snapshot,
        reparar_unicode,
        normalizar_tratamiento_admin,
        _time,
    )
)
init_status_routes(
    StatusRoutesConfig(
        services,
        reminders_lock,
        SECURITY_POLICY,
        PROACTIVE_STATE,
        PROACTIVE_LOCK,
        PLUGINS_DIR,
        DEFAULT_PROFILE_ID,
        memory_lock,
        _profiles_memory,
        _proactive_snapshot,
        monitoring_service.snapshot,
        transcription_service.snapshot,
    )
)
init_language_routes(
    {
        "tts_engine": tts_engine,
        "jarvis_settings": jarvis_settings,
        "whisper_model_ref": globals(),
    }
)


def _configure_monitoring_service():
    telegram_service = None
    if jarvis_config.RUNTIME_FEATURES.telegram_enabled:
        try:
            from services.telegram_manager import telegram_manager as telegram_service
        except Exception as exc:
            log_warning("monitoring_telegram_unavailable", error=type(exc).__name__)

    monitoring_service.inject_dependencies(
        telegram_service,
        jarvis_brain,
        security_manager,
        (core_tools.generar_resumen_noticias if jarvis_config.RUNTIME_FEATURES.briefing_enabled else None),
        None,
    )


@app.before_serving
async def _start_monitoring_service():
    try:
        _configure_monitoring_service()
        started = monitoring_service.start_heartbeat()
        obs_event(
            "monitoring_lifecycle_start",
            configured=monitoring_service.configured,
            started=bool(started),
        )
        import threading

        from tools.utilities import _auto_init_weather

        threading.Thread(target=_auto_init_weather, daemon=True).start()
    except Exception as exc:
        log_warning("monitoring_startup_failed", error=type(exc).__name__)


@app.after_serving
async def _stop_monitoring_service():
    try:
        stopped = monitoring_service.stop()
        obs_event("monitoring_lifecycle_stop", stopped=bool(stopped))
    except Exception as exc:
        log_warning("monitoring_shutdown_failed", error=type(exc).__name__)


TTS_PRONUN_MAP: dict = {}


def _normalize_tts_map(data: dict | None) -> dict:
    source = data or {}
    out = {}
    for k, v in source.items():
        key = reparar_unicode(str(k or "")).strip().lower()
        val = reparar_unicode(str(v or "")).strip()
        if key and val:
            out[key] = val
    return out


# ---------------------------------------------------------------------------
# Compatibility aliases (original Spanish names used by test_smoke.py)
# ---------------------------------------------------------------------------
transcribir_audio = transcribe_audio
_normalizar_a_wav = _normalize_to_wav
_transcribir_con_whisper_archivo = _transcribe_with_whisper_file


def _aplicar_pronunciacion_tts(texto: str) -> str:
    """Module-level shim that applies TTS pronunciation rules."""
    engine = TTSEngine.__new__(TTSEngine)
    engine.tts_lock = TTS_LOCK
    engine.tts_pronun_map = dict(TTS_PRONUN_DEFAULT)
    engine.reparar_unicode = reparar_unicode
    return engine.aplicar_pronunciacion(texto)


if __name__ == "__main__":
    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    config = Config()
    config.bind = ["localhost:5002"]
    config.use_reloader = False

    try:
        asyncio.run(serve(app, config))
    except KeyboardInterrupt:
        pass
