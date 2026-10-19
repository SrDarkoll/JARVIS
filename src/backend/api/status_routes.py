import asyncio
import math
import os
import sqlite3
import sys
import threading
from pathlib import Path
from time import monotonic

import psutil
from core import jarvis_brain, jarvis_state
from core.action_plans import list_action_plans
from core.api_contracts import validate_status_full_response
from core.app_config import get_default_location
from core.brain import llm_engine
from core.capabilities import (
    CapabilityRegistry,
    CapabilityReport,
    CapabilityState,
)
from core.jarvis_config import ROOT_DIR, RUNTIME_FEATURES
from core.jarvis_observability import obs_event, obs_snapshot, obs_tail
from core.llm_providers import resolve_llm_provider_config
from core.operator_status import build_operator_status
from core.runtime_logger import log_warning
from core.security.browser_profile import (
    can_access_browser_profile,
    can_manage_browser_profiles,
    get_browser_profile_id,
)
from core.setup_configuration import (
    SetupAttemptLimiter,
    SetupConfigurationError,
    apply_provider_configuration,
)
from core.setup_wizard import build_setup_status
from quart import Blueprint, jsonify, request
from services import security_manager
from services.notification_policy import notification_policy

status_bp = Blueprint("status", __name__)
_SETUP_PROVIDER_LIMITER = SetupAttemptLimiter(limit=5, window_seconds=600)
_wmi_temperature_lock = threading.Lock()
_wmi_temperature_retry_at = 0.0
_WMI_TEMPERATURE_RETRY_SECONDS = 300.0
_SETUP_PROVIDER_ERROR_STATUS = {
    "invalid_provider": 400,
    "invalid_request": 400,
    "invalid_key_format": 400,
    "provider_validation_failed": 422,
    "configuration_save_failed": 500,
    "configuration_rollback_failed": 500,
    "runtime_reconfigure_failed": 503,
}

# Injected dependencies
_services = None
reminders_lock = None
SECURITY_POLICY = None
PROACTIVE_STATE = None
PROACTIVE_LOCK = None
PLUGINS_DIR = None
DEFAULT_PROFILE_ID = None
memory_lock = None
_profile_memory = None
_proactive_snapshot = None
_monitoring_snapshot = None
_transcription_snapshot = None


class StatusRoutesConfig:
    def __init__(
        self,
        services,
        reminders_lock,
        security_policy,
        proactive_state,
        proactive_lock,
        plugins_dir,
        default_profile_id,
        memory_lock,
        profile_memory,
        proactive_snapshot_fn,
        monitoring_snapshot_fn=None,
        transcription_snapshot_fn=None,
    ):
        self.services = services
        self.reminders_lock = reminders_lock
        self.security_policy = security_policy
        self.proactive_state = proactive_state
        self.proactive_lock = proactive_lock
        self.plugins_dir = plugins_dir
        self.default_profile_id = default_profile_id
        self.memory_lock = memory_lock
        self.profile_memory = profile_memory
        self.proactive_snapshot_fn = proactive_snapshot_fn
        self.monitoring_snapshot_fn = monitoring_snapshot_fn
        self.transcription_snapshot_fn = transcription_snapshot_fn


def init_status_routes(config: StatusRoutesConfig):
    global _services, reminders_lock, SECURITY_POLICY
    global PROACTIVE_STATE, PROACTIVE_LOCK, PLUGINS_DIR, DEFAULT_PROFILE_ID, memory_lock
    global _profile_memory, _proactive_snapshot, _monitoring_snapshot
    global _transcription_snapshot
    _services = config.services
    reminders_lock = config.reminders_lock
    SECURITY_POLICY = config.security_policy
    PROACTIVE_STATE = config.proactive_state
    PROACTIVE_LOCK = config.proactive_lock
    PLUGINS_DIR = config.plugins_dir
    DEFAULT_PROFILE_ID = config.default_profile_id
    memory_lock = config.memory_lock
    _profile_memory = config.profile_memory
    _proactive_snapshot = config.proactive_snapshot_fn
    _monitoring_snapshot = config.monitoring_snapshot_fn
    _transcription_snapshot = config.transcription_snapshot_fn


def _monitoring_status() -> dict[str, bool]:
    status = {
        "configured": RUNTIME_FEATURES.monitoring_enabled,
        "available": False,
        "running": False,
    }
    if not callable(_monitoring_snapshot):
        return status
    try:
        snapshot = _monitoring_snapshot() or {}
        status = {key: bool(snapshot.get(key, value)) for key, value in status.items()}
    except Exception as exc:
        log_warning("monitoring_status_failed", error=type(exc).__name__)
    return status


def _speech_to_text_status() -> dict:
    fallback = {
        "provider": "auto",
        "groq_configured": False,
        "local_enabled": False,
        "local_state": "unavailable",
    }
    if not callable(_transcription_snapshot):
        return fallback
    try:
        return {**fallback, **(_transcription_snapshot() or {})}
    except Exception as exc:
        log_warning("transcription_status_failed", error=type(exc).__name__)
        return fallback


def _valid_cpu_temperature(value) -> float | None:
    try:
        temperature = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(temperature) or not -30.0 <= temperature <= 180.0:
        return None
    return round(temperature, 1)


def _read_cpu_temperature() -> tuple[float | None, str]:
    global _wmi_temperature_retry_at
    sensor_reader = getattr(psutil, "sensors_temperatures", None)
    if callable(sensor_reader):
        try:
            sensor_groups = sensor_reader() or {}
            preferred = ("coretemp", "k10temp", "cpu_thermal", "acpitz")
            names = [*preferred, *(name for name in sensor_groups if name not in preferred)]
            for name in names:
                for sensor in sensor_groups.get(name, ()):
                    temperature = _valid_cpu_temperature(getattr(sensor, "current", None))
                    if temperature is not None:
                        return temperature, f"psutil:{name}"
        except Exception as exc:
            log_warning("cpu_temperature_psutil_failed", error_type=type(exc).__name__)

    if sys.platform != "win32":
        return None, "unavailable"

    with _wmi_temperature_lock:
        if monotonic() < _wmi_temperature_retry_at:
            return None, "unavailable"
        initialized = False
        try:
            import wmi  # pyright: ignore[reportMissingImports]
            from pythoncom import CoInitialize, CoUninitialize  # pyright: ignore[reportMissingImports]

            CoInitialize()
            initialized = True
            items = wmi.WMI(namespace="root/wmi").MSAcpi_ThermalZoneTemperature()
            for item in items or ():
                raw_kelvin_tenths = getattr(item, "CurrentTemperature", None)
                if raw_kelvin_tenths is None:
                    continue
                temperature = _valid_cpu_temperature((float(raw_kelvin_tenths) / 10.0) - 273.15)
                if temperature is not None:
                    _wmi_temperature_retry_at = 0.0
                    return temperature, "wmi:msacpi"
        except Exception as exc:
            log_warning("cpu_temperature_wmi_unavailable", error_type=type(exc).__name__)
        finally:
            if initialized:
                CoUninitialize()
        _wmi_temperature_retry_at = monotonic() + _WMI_TEMPERATURE_RETRY_SECONDS
    return None, "unavailable"


def _admin_voice_profile_count() -> int:
    try:
        from api import voice_routes

        motor = getattr(voice_routes, "_voice_id_motor", None)
        profiles = getattr(motor, "voice_profiles", None) or getattr(motor, "perfiles_voz", None) or {}
        return 1 if DEFAULT_PROFILE_ID in profiles else 0
    except Exception:
        return 0


def _runtime_capabilities(
    *,
    monitoring: dict[str, bool],
    speech_to_text: dict,
    admin_voice_profiles: int | None = None,
) -> dict[str, dict[str, str]]:
    registry = CapabilityRegistry()

    def publish(
        name: str,
        state: CapabilityState,
        code: str,
        action: str = "",
        detail: str = "",
    ) -> None:
        registry.set(
            CapabilityReport(
                name=name,
                state=state,
                code=code,
                action=action,
                detail=detail,
            )
        )

    llm_config = resolve_llm_provider_config()
    if jarvis_brain.llm is not None:
        detail = f"Fallback provider: {llm_config.fallback_provider}" if llm_config.fallback_provider else ""
        publish(
            "llm",
            CapabilityState.AVAILABLE,
            f"{llm_config.primary_provider or 'llm'}_ready",
            detail=detail,
        )
    elif llm_config.configured:
        publish(
            "llm",
            CapabilityState.DEGRADED,
            f"{llm_config.primary_provider}_initializing",
            "Review backend logs if initialization does not complete",
        )
    else:
        publish(
            "llm",
            CapabilityState.UNCONFIGURED,
            "llm_key_missing",
            "Configure GEMINI_API_KEY or GROQ_API_KEY",
        )

    from api.voice_routes import build_live_voice_status

    live_voice = build_live_voice_status()
    live_state = {
        "available": CapabilityState.AVAILABLE,
        "degraded": CapabilityState.DEGRADED,
        "unconfigured": CapabilityState.UNCONFIGURED,
        "disabled": CapabilityState.DISABLED,
        "unavailable": CapabilityState.FAILED,
    }.get(str(live_voice.get("state") or ""), CapabilityState.FAILED)
    publish(
        "live_voice",
        live_state,
        str(live_voice.get("code") or "live_voice_unavailable"),
        (
            "Configure Gemini or use the hybrid voice fallback"
            if live_state is not CapabilityState.AVAILABLE
            else ""
        ),
        detail=(
            f"preferred={live_voice.get('preferred_mode')}; "
            f"provider_api={'preview' if live_voice.get('provider_preview') else 'stable'}; "
            "session_resumption=disabled"
        ),
    )

    voice_profiles = _admin_voice_profile_count() if admin_voice_profiles is None else int(admin_voice_profiles)
    if not RUNTIME_FEATURES.voice_id_enabled:
        publish(
            "voice_id",
            CapabilityState.DISABLED,
            "core_mode",
            "Enable JARVIS_VOICE_ID_ENABLED",
        )
    elif voice_profiles > 0:
        publish("voice_id", CapabilityState.AVAILABLE, "voice_id_ready")
    else:
        publish(
            "voice_id",
            CapabilityState.UNCONFIGURED,
            "admin_voice_missing",
            "Register the administrator voice",
        )

    for name, enabled, action in (
        (
            "rag",
            RUNTIME_FEATURES.rag_enabled,
            "Enable JARVIS_RAG_ENABLED",
        ),
        (
            "vision",
            RUNTIME_FEATURES.vision_enabled,
            "Enable JARVIS_VISION_ENABLED",
        ),
        (
            "plugins",
            RUNTIME_FEATURES.plugins_enabled,
            "Enable JARVIS_PLUGINS_ENABLED",
        ),
        (
            "briefing",
            RUNTIME_FEATURES.briefing_enabled,
            "Enable JARVIS_BRIEFING_ENABLED",
        ),
        (
            "computer_use",
            RUNTIME_FEATURES.computer_use_enabled,
            "Enable JARVIS_COMPUTER_USE_ENABLED",
        ),
        (
            "proactive_tools",
            RUNTIME_FEATURES.proactive_tools_enabled,
            "Enable JARVIS_PROACTIVE_TOOLS_ENABLED",
        ),
        (
            "cognitive_memory",
            RUNTIME_FEATURES.cognitive_memory_enabled,
            "Enable JARVIS_COGNITIVE_MEMORY_ENABLED",
        ),
        (
            "action_plans",
            RUNTIME_FEATURES.action_plans_enabled,
            "Enable JARVIS_ACTION_PLANS_ENABLED",
        ),
    ):
        publish(
            name,
            (CapabilityState.AVAILABLE if enabled else CapabilityState.DISABLED),
            "enabled" if enabled else "core_mode",
            "" if enabled else action,
        )

    spotify_mode = (os.getenv("SPOTIFY_PLAYBACK_MODE") or "auto").strip().lower()
    spotify_api = bool(
        (os.getenv("SPOTIPY_CLIENT_ID") or os.getenv("SPOTIFY_CLIENT_ID") or "").strip()
        and (os.getenv("SPOTIPY_CLIENT_SECRET") or os.getenv("SPOTIFY_CLIENT_SECRET") or "").strip()
    )
    spotify_desktop = sys.platform == "win32"
    spotify_available = (
        (spotify_mode == "api" and spotify_api)
        or (spotify_mode == "desktop" and spotify_desktop)
        or (spotify_mode not in {"api", "desktop"} and (spotify_api or spotify_desktop))
    )
    publish(
        "spotify",
        (CapabilityState.AVAILABLE if spotify_available else CapabilityState.UNCONFIGURED),
        "spotify_ready" if spotify_available else "spotify_unconfigured",
        ("" if spotify_available else "Configure Spotify credentials or use Windows desktop mode"),
    )

    telegram_configured = bool(
        (os.getenv("TELEGRAM_TOKEN") or "").strip() and (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    )
    if not RUNTIME_FEATURES.telegram_enabled:
        publish(
            "telegram",
            CapabilityState.DISABLED,
            "core_mode",
            "Enable JARVIS_TELEGRAM_ENABLED",
        )
    elif telegram_configured:
        publish("telegram", CapabilityState.AVAILABLE, "telegram_ready")
    else:
        publish(
            "telegram",
            CapabilityState.UNCONFIGURED,
            "telegram_credentials_missing",
            "Configure TELEGRAM_TOKEN and TELEGRAM_CHAT_ID",
        )

    if not monitoring["configured"]:
        publish(
            "monitoring",
            CapabilityState.DISABLED,
            "monitoring_disabled",
            "Enable JARVIS_MONITORING_ENABLED",
        )
    elif monitoring["available"] and monitoring["running"]:
        publish(
            "monitoring",
            CapabilityState.AVAILABLE,
            "monitoring_running",
        )
    else:
        publish(
            "monitoring",
            CapabilityState.DEGRADED,
            "monitoring_unavailable",
            "Install optional monitoring dependencies",
        )

    local_state = str(speech_to_text.get("local_state") or "")
    if speech_to_text.get("groq_configured") or local_state == "loaded":
        publish(
            "speech_to_text",
            CapabilityState.AVAILABLE,
            "speech_to_text_ready",
        )
    else:
        publish(
            "speech_to_text",
            CapabilityState.DEGRADED,
            "browser_transcription_only",
            "Configure Groq transcription or install local Whisper",
        )

    publish(
        "command_pipeline",
        CapabilityState.AVAILABLE,
        "single_pipeline_ready",
    )
    return registry.snapshot()


@status_bp.route("/api/status", methods=["GET"])
def api_status():
    monitoring = _monitoring_status()
    speech_to_text = _speech_to_text_status()
    return jsonify(
        {
            "status": "online",
            "mode": "core" if RUNTIME_FEATURES.core_mode else "full",
            "features": {
                "voice_id": RUNTIME_FEATURES.voice_id_enabled,
                "rag": RUNTIME_FEATURES.rag_enabled,
                "vision": RUNTIME_FEATURES.vision_enabled,
                "plugins": RUNTIME_FEATURES.plugins_enabled,
                "briefing": RUNTIME_FEATURES.briefing_enabled,
                "telegram": RUNTIME_FEATURES.telegram_enabled,
                "monitoring": monitoring["configured"],
                "monitoring_available": monitoring["available"],
                "monitoring_running": monitoring["running"],
                "computer_use": RUNTIME_FEATURES.computer_use_enabled,
                "proactive_tools": RUNTIME_FEATURES.proactive_tools_enabled,
                "cognitive_memory": RUNTIME_FEATURES.cognitive_memory_enabled,
                "action_plans": RUNTIME_FEATURES.action_plans_enabled,
            },
            "speech_to_text": speech_to_text,
            "capabilities": _runtime_capabilities(
                monitoring=monitoring,
                speech_to_text=speech_to_text,
            ),
            "profile": jarvis_state.get_active_profile_id(),
            "notifications": notification_policy.snapshot(get_browser_profile_id()),
            "heartbeat": jarvis_state.heartbeat_state.get("last_pulse", 0),
        }
    )


@status_bp.route("/api/operator/status", methods=["GET"])
def operator_status():
    from utils.jarvis_auth import verificar_autorizacion as _verificar_autorizacion

    profile_id = jarvis_state.get_active_profile_id(DEFAULT_PROFILE_ID or "admin")
    with memory_lock:
        profiles = {
            pid: {
                "facts": str((pdata or {}).get("facts") or ""),
                "history": list((pdata or {}).get("history") or []),
            }
            for pid, pdata in (_profile_memory or {}).items()
        }

    try:
        plan_limit = max(1, min(int(request.args.get("plans", "8")), 30))
    except Exception:
        plan_limit = 8
    try:
        audit_limit = max(1, min(int(request.args.get("audit", "40")), 120))
    except Exception:
        audit_limit = 40

    policy_overrides = {}
    if isinstance(SECURITY_POLICY, dict):
        policy_overrides = SECURITY_POLICY.get("tool_policies") or {}

    return jsonify(
        build_operator_status(
            active_profile_id=profile_id,
            authorized=_verificar_autorizacion(profile_id),
            profiles=profiles,
            plans=list_action_plans(limit=plan_limit),
            security_snapshot=security_manager._security_snapshot(),
            proactive_snapshot=_proactive_snapshot(limit=20),
            audit=security_manager._security_tail(limit=audit_limit),
            policy_overrides=policy_overrides,
        )
    )


@status_bp.route("/api/observability", methods=["GET"])
@status_bp.route("/api/observabilidad", methods=["GET"])
def api_observability():
    try:
        limit = max(1, min(int(request.args.get("limit", "80")), 300))
    except Exception as e:
        log_warning("observability_limit_parse_failed", error=str(e))
        limit = 80
    return jsonify(
        {
            "metrics": obs_snapshot(),
            "events": obs_tail(limit=limit),
            "security": security_manager._security_snapshot(),
            "proactive": _proactive_snapshot(limit=20),
        }
    )


@status_bp.route("/api/news", methods=["GET"])
@status_bp.route("/api/noticias", methods=["GET"])
def get_news():
    from utils.jarvis_i18n import get_current_language

    nc = getattr(_services, "news_cache", None) or getattr(_services, "noticias_cache", {})
    ready = bool(nc.get("ready", nc.get("listo", False)))
    summary = str(nc.get("summary", nc.get("resumen", "")) or "")
    language = nc.get("language") or get_current_language()
    automatic = str(request.args.get("automatic", "")).strip() == "1"
    if automatic and not notification_policy.allows_automatic(get_browser_profile_id()):
        return jsonify({"ready": False, "summary": "", "listo": False, "resumen": "",
                        "language": language, "deferred": True}), 202
    if not ready:
        return jsonify(
            {
                "ready": False,
                "summary": "",
                "listo": False,
                "resumen": "",
                "language": language,
            }
        ), 202
    return jsonify(
        {
            "ready": True,
            "summary": summary,
            "listo": True,
            "resumen": summary,
            "language": language,
        }
    )


@status_bp.route("/api/auth_status", methods=["GET"])
def auth_status():
    from utils.jarvis_auth import verificar_autorizacion as _verificar_autorizacion

    pid = jarvis_state.get_active_profile_id(DEFAULT_PROFILE_ID or "admin")
    authorized = _verificar_autorizacion(pid)
    return jsonify({"authorized": authorized, "autorizado": authorized})


@status_bp.route("/api/setup/status", methods=["GET"])
def setup_status():
    admin_voice_profiles = _admin_voice_profile_count()
    from utils.jarvis_i18n import get_current_language

    setup = build_setup_status(
        env=os.environ,
        language=get_current_language(),
        admin_voice_profiles=admin_voice_profiles,
        weather_location=get_default_location(),
    )
    setup["capabilities"] = _runtime_capabilities(
        monitoring=_monitoring_status(),
        speech_to_text=_speech_to_text_status(),
        admin_voice_profiles=admin_voice_profiles,
    )
    response = jsonify(setup)
    response.headers["Cache-Control"] = "no-store"
    return response


def _apply_setup_provider_payload(data: dict) -> dict[str, object]:
    return apply_provider_configuration(
        provider=data.get("provider"),
        api_key=data.get("api_key"),
        save_local=data.get("save_local"),
        env_path=Path(ROOT_DIR) / ".env",
        environ=os.environ,
        prepare_runtime=llm_engine.prepare_llm_runtime,
        activate_runtime=llm_engine.activate_llm_runtime,
    )


def _setup_provider_response(payload: dict, status: int = 200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response, status


@status_bp.route("/api/setup/provider", methods=["POST"])
async def setup_provider():
    retry_after = _SETUP_PROVIDER_LIMITER.check(request.remote_addr)
    if retry_after > 0:
        return _setup_provider_response(
            {
                "error": "setup_rate_limited",
                "retry_after": round(retry_after, 3),
            },
            429,
        )

    data = await request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    try:
        result = await asyncio.to_thread(_apply_setup_provider_payload, data)
        return _setup_provider_response(result)
    except (SetupConfigurationError, llm_engine.LLMReconfigurationError) as exc:
        error_code = str(exc) or "setup_failed"
        log_warning("setup_provider_failed", error=error_code)
        return _setup_provider_response(
            {"error": error_code},
            _SETUP_PROVIDER_ERROR_STATUS.get(error_code, 500),
        )
    except Exception as exc:
        log_warning("setup_provider_failed", error=type(exc).__name__)
        return _setup_provider_response({"error": "setup_failed"}, 500)


def _reminder_storage_unavailable(operation: str, error: Exception):
    log_warning("reminder_storage_unavailable", operation=operation, error=type(error).__name__)
    return jsonify({"ok": False, "error": "reminders_unavailable"}), 503, {"Retry-After": "5"}


@status_bp.route("/api/reminders", methods=["GET"])
def get_reminders():
    profile_id = get_browser_profile_id()
    try:
        records = _services.reminder_service.list_active(profile_id)
    except (OSError, sqlite3.Error) as exc:
        return _reminder_storage_unavailable("list", exc)
    return jsonify({"reminders": [record.to_dict() for record in records]})


@status_bp.route("/api/reminders/claim", methods=["POST"])
def claim_due_reminders():
    profile_id = get_browser_profile_id()
    try:
        records = _services.reminder_service.claim_due(profile_id, limit=1)
    except (OSError, sqlite3.Error) as exc:
        return _reminder_storage_unavailable("claim", exc)
    return jsonify({"reminders": [record.to_dict() for record in records]})


@status_bp.route("/api/reminders/<reminder_id>/ack", methods=["POST"])
def acknowledge_reminder(reminder_id: str):
    profile_id = get_browser_profile_id()
    try:
        acknowledged = _services.reminder_service.acknowledge(
            reminder_id,
            profile_id,
        )
    except (OSError, sqlite3.Error) as exc:
        return _reminder_storage_unavailable("acknowledge", exc)
    if not acknowledged:
        return jsonify({"ok": False, "error": "reminder_not_claimed"}), 404
    return jsonify({"ok": True})


@status_bp.route("/api/session/profile", methods=["GET"])
def get_browser_profile():
    response = jsonify({"profile_id": get_browser_profile_id()})
    response.headers["Cache-Control"] = "no-store"
    return response


@status_bp.route("/api/status/full", methods=["GET"])
def get_status_full():
    cpu_usage, ram = psutil.cpu_percent(interval=None), psutil.virtual_memory().percent
    temp, temp_source = _read_cpu_temperature()

    llm_ok, llm_latency = False, -1
    try:
        if jarvis_brain.llm:
            llm_ok = True
            llm_latency = 0
    except Exception:
        pass

    payload = {
        "status": "online",
        "notifications": notification_policy.snapshot(get_browser_profile_id()),
        "llm_ok": llm_ok,
        "llm_latency_ms": llm_latency,
        "cpu": cpu_usage,
        "ram": ram,
        "temp": temp,
        "temp_source": temp_source,
        "weather": {
            "temp": _services.weather_cache["temp"],
            "desc": _services.weather_cache["desc"],
        },
        "security": {
            "strict_mode": bool((security_manager.SECURITY_POLICY or {}).get("strict_mode", False)),
            "blocked_total": int(obs_snapshot().get("security_blocked_total", 0)),
        },
        "proactive": {"enabled": bool(PROACTIVE_STATE.get("enabled", True))},
    }

    contract = validate_status_full_response(payload)
    if not contract.ok:
        obs_event(
            "api_contract_violation",
            endpoint="/api/status/full",
            side="response",
            error=contract.error,
        )
        return jsonify({"status": "error", "error": contract.error}), 500
    return jsonify(payload)


@status_bp.route("/api/plugins", methods=["GET"])
def get_plugins_status():
    with jarvis_brain.PLUGIN_LOCK:
        return jsonify(
            {
                "last_reload": jarvis_brain.PLUGIN_STATE.get("last_reload", ""),
                "loaded": jarvis_brain.PLUGIN_STATE.get("loaded", {}),
                "errors": jarvis_brain.PLUGIN_STATE.get("errors", {}),
                "plugins_dir": PLUGINS_DIR,
            }
        )


@status_bp.route("/api/plugins/reload", methods=["POST"])
def reload_plugins_http():
    msg = jarvis_brain._recargar_plugins_runtime()
    return jsonify(
        {
            "message": msg,
            "plugins": {
                "last_reload": jarvis_brain.PLUGIN_STATE.get("last_reload", ""),
                "loaded": jarvis_brain.PLUGIN_STATE.get("loaded", {}),
                "errors": jarvis_brain.PLUGIN_STATE.get("errors", {}),
            },
        }
    )


@status_bp.route("/api/profiles", methods=["GET"])
@status_bp.route("/api/perfiles", methods=["GET"])
def get_profiles():
    own_profile = get_browser_profile_id()
    can_manage = can_manage_browser_profiles()
    with memory_lock:
        return jsonify(
            {
                "default_profile": DEFAULT_PROFILE_ID,
                "profiles": {
                    pid: {
                        "history_len": len((pdata or {}).get("history", [])),
                        "facts_len": len((pdata or {}).get("facts", "") or ""),
                    }
                    for pid, pdata in _profile_memory.items()
                    if can_manage or pid == own_profile
                },
            }
        )


def _serialize_profile_message(message) -> dict:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content", "")
    msg_type = getattr(message, "type", None)
    if not msg_type and isinstance(message, dict):
        msg_type = message.get("type") or message.get("role")
    if not msg_type:
        cls_name = message.__class__.__name__.lower()
        if "human" in cls_name:
            msg_type = "human"
        elif "ai" in cls_name:
            msg_type = "ai"
        else:
            msg_type = "message"
    return {"type": str(msg_type), "content": str(content or "")}


def _profile_detail_payload(pid: str) -> tuple[dict, int]:
    pid = jarvis_state.normalize_profile_id(pid)
    with memory_lock:
        pdata = dict((_profile_memory or {}).get(pid) or {})
        if not pdata:
            return {"error": "profile_not_found", "profile_id": pid}, 404
        history = pdata.get("history") or []
        facts = str(pdata.get("facts") or "")
        return {
            "profile_id": pid,
            "is_default": pid == DEFAULT_PROFILE_ID,
            "facts": facts,
            "facts_len": len(facts),
            "history": [_serialize_profile_message(item) for item in history],
            "history_len": len(history),
        }, 200


@status_bp.route("/api/profiles/<profile_id>", methods=["GET"])
@status_bp.route("/api/perfiles/<profile_id>", methods=["GET"])
def get_profile_detail(profile_id):
    pid = jarvis_state.normalize_profile_id(profile_id)
    if not can_access_browser_profile(pid):
        return jsonify({"error": "profile_access_denied"}), 403
    payload, status = _profile_detail_payload(pid)
    return jsonify(payload), status


@status_bp.route("/api/profiles/<profile_id>", methods=["PATCH"])
@status_bp.route("/api/perfiles/<profile_id>", methods=["PATCH"])
async def update_profile_detail(profile_id):
    data = (await request.get_json(silent=True)) or {}
    pid = jarvis_state.normalize_profile_id(profile_id)
    if not can_access_browser_profile(pid):
        return jsonify({"error": "profile_access_denied"}), 403
    facts = data.get("facts")
    try:
        from services.memory_manager import memory_manager
        from tools.memory import guardar_memoria_async

        if facts is not None:
            memory_manager.set_facts(pid, str(facts or ""))
            guardar_memoria_async(pid)
    except Exception as e:
        log_warning("profile_memory_update_failed", profile_id=pid, error=str(e))
        return jsonify({"error": "profile_memory_update_failed", "profile_id": pid}), 500

    payload, status = _profile_detail_payload(pid)
    return jsonify(payload), status


@status_bp.route("/api/profiles/<profile_id>", methods=["DELETE"])
@status_bp.route("/api/perfiles/<profile_id>", methods=["DELETE"])
def clear_profile_detail(profile_id):
    pid = jarvis_state.normalize_profile_id(profile_id)
    if not can_access_browser_profile(pid):
        return jsonify({"error": "profile_access_denied"}), 403
    try:
        from services.memory_manager import memory_manager
        from tools.memory import guardar_memoria_async

        memory_manager.set_facts(pid, "")
        memory_manager.set_profile_history(pid, [])
        guardar_memoria_async(pid)
    except Exception as e:
        log_warning("profile_memory_clear_failed", profile_id=pid, error=str(e))
        return jsonify({"error": "profile_memory_clear_failed", "profile_id": pid}), 500

    payload, status = _profile_detail_payload(pid)
    return jsonify(payload), status
