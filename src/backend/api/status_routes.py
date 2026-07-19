import os

import psutil
from core import jarvis_brain, jarvis_state
from core.action_plans import list_action_plans
from core.api_contracts import validate_status_full_response
from core.app_config import get_default_location
from core.jarvis_config import RUNTIME_FEATURES
from core.jarvis_observability import obs_event, obs_snapshot, obs_tail
from core.operator_status import build_operator_status
from core.runtime_logger import log_warning
from core.setup_wizard import build_setup_status
from quart import Blueprint, jsonify, request
from services import security_manager

status_bp = Blueprint("status", __name__)

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


def init_status_routes(config: StatusRoutesConfig):
    global _services, reminders_lock, SECURITY_POLICY
    global PROACTIVE_STATE, PROACTIVE_LOCK, PLUGINS_DIR, DEFAULT_PROFILE_ID, memory_lock
    global _profile_memory, _proactive_snapshot, _monitoring_snapshot
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
        for key in status:
            status[key] = bool(snapshot.get(key, status[key]))
    except Exception as exc:
        log_warning("monitoring_status_failed", error=type(exc).__name__)
    return status


@status_bp.route("/api/status", methods=["GET"])
def api_status():
    monitoring = _monitoring_status()
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
            },
            "profile": jarvis_state.get_active_profile_id(),
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
    try:
        from api import voice_routes

        motor = getattr(voice_routes, "_voice_id_motor", None)
        profiles = getattr(motor, "voice_profiles", {}) or {}
        admin_voice_profiles = 1 if DEFAULT_PROFILE_ID in profiles else 0
    except Exception:
        admin_voice_profiles = 0
    from utils.jarvis_i18n import get_current_language

    return jsonify(
        build_setup_status(
            env=os.environ,
            language=get_current_language(),
            admin_voice_profiles=admin_voice_profiles,
            weather_location=get_default_location(),
        )
    )


@status_bp.route("/api/reminders", methods=["GET"])
def get_reminders():
    reminders = _services.get_reminders()
    with reminders_lock:
        return jsonify(
            [
                {
                    "text": r.get("text", r.get("texto", "")),
                    "when": r.get("when", r.get("cuando")).strftime("%H:%M"),
                    "texto": r.get("text", r.get("texto", "")),
                    "cuando": r.get("when", r.get("cuando")).strftime("%H:%M"),
                }
                for r in reminders
                if r.get("when", r.get("cuando")) is not None
            ]
        )


@status_bp.route("/api/status/full", methods=["GET"])
def get_status_full():
    cpu_usage, ram = psutil.cpu_percent(interval=None), psutil.virtual_memory().percent
    temp = 45.0 + (cpu_usage * 0.35)
    try:
        import wmi
        from pythoncom import CoInitialize, CoUninitialize

        CoInitialize()
        try:
            items = wmi.WMI(namespace="root/wmi").MSAcpi_ThermalZoneTemperature()
            if items:
                temp = (items[0].CurrentTemperature / 10.0) - 273.15
        finally:
            CoUninitialize()
    except Exception:
        pass

    llm_ok, llm_latency = False, -1
    try:
        if jarvis_brain.llm:
            llm_ok = True
            llm_latency = 0
    except Exception:
        pass

    payload = {
        "status": "online",
        "llm_ok": llm_ok,
        "llm_latency_ms": llm_latency,
        "cpu": cpu_usage,
        "ram": ram,
        "temp": round(temp, 1),
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
    payload, status = _profile_detail_payload(pid)
    return jsonify(payload), status


@status_bp.route("/api/profiles/<profile_id>", methods=["PATCH"])
@status_bp.route("/api/perfiles/<profile_id>", methods=["PATCH"])
async def update_profile_detail(profile_id):
    data = (await request.get_json(silent=True)) or {}
    pid = jarvis_state.normalize_profile_id(profile_id)
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
