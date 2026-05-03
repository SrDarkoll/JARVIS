"""
Security Routes: security policy, proactive alerts, quick control.
"""

from quart import Blueprint, request, jsonify
from services import security_manager
from core.jarvis_observability import obs_event, obs_snapshot

security_bp = Blueprint("security", __name__)

# Injected dependencies
_security_snapshot = None
_proactive_snapshot = None
_actualizar_security_policy = None
_ejecutar_accion_control = None
SECURITY_POLICY = None
PROACTIVE_STATE = None
PROACTIVE_LOCK = None
jarvis_brain = None


class SecurityRoutesConfig:
    def __init__(
        self,
        security_snapshot_fn,
        proactive_snapshot_fn,
        actualizar_security_policy_fn,
        ejecutar_accion_control_fn,
        security_policy,
        proactive_state,
        proactive_lock,
        brain,
    ):
        self.security_snapshot_fn = security_snapshot_fn
        self.proactive_snapshot_fn = proactive_snapshot_fn
        self.actualizar_security_policy_fn = actualizar_security_policy_fn
        self.ejecutar_accion_control_fn = ejecutar_accion_control_fn
        self.security_policy = security_policy
        self.proactive_state = proactive_state
        self.proactive_lock = proactive_lock
        self.brain = brain


def init_security_routes(config: SecurityRoutesConfig):
    global _security_snapshot, _proactive_snapshot, _actualizar_security_policy
    global _ejecutar_accion_control, SECURITY_POLICY, PROACTIVE_STATE, PROACTIVE_LOCK, jarvis_brain
    _security_snapshot = config.security_snapshot_fn
    _proactive_snapshot = config.proactive_snapshot_fn
    _actualizar_security_policy = config.actualizar_security_policy_fn
    _ejecutar_accion_control = config.ejecutar_accion_control_fn
    SECURITY_POLICY = config.security_policy
    PROACTIVE_STATE = config.proactive_state
    PROACTIVE_LOCK = config.proactive_lock
    jarvis_brain = config.brain


@security_bp.route("/api/security", methods=["GET"])
async def get_security():
    try:
        limit = int(request.args.get("limit", "60"))
    except Exception as e:
        print(f"[WARN get_security] limit parse error: {e}")
        limit = 60
    limit = max(1, min(limit, 200))
    obs = obs_snapshot()
    from services.security_manager import _security_tail
    return jsonify(
        {
            **_security_snapshot(),
            "metrics": {
                "blocked_total": int(obs.get("security_blocked_total", 0)),
                "warning_total": int(obs.get("security_warning_total", 0)),
            },
            "audit": _security_tail(limit=limit),
        }
    )


@security_bp.route("/api/security/policy", methods=["POST"])
async def update_security_policy():
    data = (await request.get_json(silent=True)) or {}
    return jsonify(
        {
            "message": "Security policy updated.",
            **_actualizar_security_policy(data),
        }
    )


@security_bp.route("/api/proactive", methods=["GET"])
async def get_proactive():
    try:
        limit = int(request.args.get("limit", "40"))
    except Exception as e:
        print(f"[WARN get_proactive] limit parse error: {e}")
        limit = 40
    return jsonify(_proactive_snapshot(limit=max(1, min(limit, 200))))


@security_bp.route("/api/proactive/clear", methods=["POST"])
async def clear_proactive_alerts():
    with PROACTIVE_LOCK:
        PROACTIVE_STATE["alerts"] = []
    return jsonify({"message": "Proactive alerts cleared."})


@security_bp.route("/api/control/quick", methods=["POST"])
async def control_quick_action():
    data = (await request.get_json(silent=True)) or {}
    action = str(data.get("action", "")).strip()
    print(f"\n[PANEL] Executing action: {action}")
    result = _ejecutar_accion_control(action)
    print(f"[PANEL] Result ({action}): {result}")
    return jsonify(
        {
            "action": action,
            "result": result,
            "security": _security_snapshot(),
            "proactive": _proactive_snapshot(limit=20),
            "plugins": {
                "last_reload": jarvis_brain.PLUGIN_STATE.get("last_reload", ""),
                "loaded": jarvis_brain.PLUGIN_STATE.get("loaded", {}),
                "errors": jarvis_brain.PLUGIN_STATE.get("errors", {}),
            },
        }
    )
