import json
import os
import threading
import time as _time
from datetime import datetime
from urllib.parse import urlparse

from core.jarvis_context import context
from core import jarvis_config
from core.security.tool_policy import evaluate_tool_policy, export_tool_policy_table

# DEPENDENCY INJECTION
_invocar_tool = None
_recargar_plugins_runtime = None
enviar_telegram_sync = None
_normalizar_destino_web = None
verificar_autorizacion = None
normalizar_tratamiento_admin = None
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
SECURITY_POLICY_FILE = ""
SECURITY_AUDIT_FILE = ""
SECURITY_POLICY = {}
SECURITY_STATE = {}
SECURITY_LOCK = threading.RLock()
PROACTIVE_STATE = {
    "enabled": bool(jarvis_config.PROACTIVE_ACTIVO),
    "cooldown_seconds": int(jarvis_config.PROACTIVE_COOLDOWN),
    "alerts": [],
    "last_health_check": "",
    "tool_errors_window": [],
    "last_alert_by_key": {},
}
PROACTIVE_LOCK = threading.RLock()
_obs_inc = None
_obs_event = None


def inject_dependencies(deps):
    deps = deps or {}
    context.update(deps)

    # Claves que se pueden inyectar de forma segura en este modulo.
    injectable = {
        "_invocar_tool",
        "_recargar_plugins_runtime",
        "enviar_telegram_sync",
        "_normalizar_destino_web",
        "verificar_autorizacion",
        "normalizar_tratamiento_admin",
        "SECURITY_POLICY_DEFAULT",
        "SECURITY_POLICY_FILE",
        "SECURITY_AUDIT_FILE",
        "SECURITY_POLICY",
        "SECURITY_STATE",
        "SECURITY_LOCK",
        "PROACTIVE_STATE",
        "PROACTIVE_LOCK",
        "_obs_inc",
        "_obs_event",
    }

    for k, v in deps.items():
        if k in injectable:
            globals()[k] = v


def _warn(msg: str):
    try:
        if callable(_obs_event):
            _obs_event("security_warning", message=str(msg)[:280])
        else:
            print(f"[WARN SECURITY] {msg}")
    except Exception as e:
        print(f"[WARN security] _obs_event failed: {e}")


def _security_copy_default() -> dict:
    return json.loads(json.dumps(SECURITY_POLICY_DEFAULT, ensure_ascii=False))


def _security_normalizar_policy(raw: dict | None) -> dict:
    policy = _security_copy_default()
    raw = raw or {}

    if "strict_mode" in raw:
        policy["strict_mode"] = bool(raw.get("strict_mode"))
    if "allow_system_browser_fallback" in raw:
        policy["allow_system_browser_fallback"] = bool(
            raw.get("allow_system_browser_fallback")
        )
    if "max_tool_errors_5m" in raw:
        try:
            val = int(raw.get("max_tool_errors_5m"))
            policy["max_tool_errors_5m"] = max(3, min(val, 100))
        except Exception as e:
            _warn(f"max_tool_errors_5m invalid in policy: {e}")

    if isinstance(raw.get("blocked_tools"), list):
        blocked = []
        for item in raw.get("blocked_tools", []):
            t = str(item or "").strip()
            if t and t not in blocked:
                blocked.append(t)
        policy["blocked_tools"] = blocked

    if isinstance(raw.get("allowed_web_domains"), list):
        dominios = []
        for d in raw.get("allowed_web_domains", []):
            host = str(d or "").strip().lower()
            if host.startswith("http://") or host.startswith("https://"):
                try:
                    host = (urlparse(host).hostname or "").lower().strip()
                except Exception as e:
                    print(f"[WARN security] urlparse error: {e}")
                    host = ""
            host = host.lstrip(".")
            if host.startswith("www."):
                host = host[4:]
            if host and host not in dominios:
                dominios.append(host)
        if dominios:
            policy["allowed_web_domains"] = dominios

    if isinstance(raw.get("safe_apps"), list):
        apps = []
        for a in raw.get("safe_apps", []):
            name = str(a or "").strip().lower()
            if name and name not in apps:
                apps.append(name)
        if apps:
            policy["safe_apps"] = apps

    if isinstance(raw.get("tool_policies"), dict):
        policy["tool_policies"] = raw.get("tool_policies") or {}

    return policy


def _load_security_policy():
    global SECURITY_POLICY
    loaded = {}
    if os.path.exists(SECURITY_POLICY_FILE):
        try:
            with open(SECURITY_POLICY_FILE, encoding="utf-8") as f:
                loaded = json.load(f) or {}
        except Exception as e:
            print(f"[SECURITY] Could not read policy: {e}")
    with SECURITY_LOCK:
        normalized = _security_normalizar_policy(loaded)
        if not isinstance(SECURITY_POLICY, dict):
            SECURITY_POLICY = {}
        else:
            SECURITY_POLICY.clear()
        SECURITY_POLICY.update(normalized)
        SECURITY_STATE["last_update"] = datetime.now().isoformat(timespec="seconds")


def _save_security_policy():
    try:
        with SECURITY_LOCK:
            snapshot = json.loads(
                json.dumps(SECURITY_POLICY, ensure_ascii=False, default=str)
            )
        with open(SECURITY_POLICY_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[SECURITY] Could not save policy: {e}")


def _security_audit(
    action: str,
    level: str = "info",
    tool: str = "",
    reason: str = "",
    source: str = "",
    metadata: dict | None = None,
):
    evt = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action": str(action or "event"),
        "level": str(level or "info"),
        "tool": str(tool or ""),
        "source": str(source or ""),
        "reason": str(reason or "")[:300],
        "metadata": metadata or {},
    }
    try:
        line = json.dumps(evt, ensure_ascii=False, default=str)
        # Usamos el LogWorker asíncrono de observabilidad para evitar latencia de disco
        from core.jarvis_observability import _log_worker

        _log_worker.log(SECURITY_AUDIT_FILE, line)
    except Exception as e:
        _warn(f"Could not enqueue security audit: {e}")

    if callable(_obs_event):
        _obs_event(
            "security_audit",
            action=evt["action"],
            level=evt["level"],
            tool=evt["tool"],
            source=evt["source"],
            reason=evt["reason"],
        )


def _security_tail(limit: int = 60) -> list[dict]:
    if not os.path.exists(SECURITY_AUDIT_FILE):
        return []
    try:
        with open(SECURITY_AUDIT_FILE, encoding="utf-8") as f:
            lines = f.readlines()[-max(1, int(limit)) :]
        out = []
        for line in lines:
            line = (line or "").strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                out.append({"raw": line})
    except Exception as e:
        print(f"[WARN _security_tail] error: {e}")
        return []
    return out


def _security_host_for_destino(destino: str) -> str:
    raw = str(destino or "").strip()
    if not raw:
        return ""
    try:
        url = _normalizar_destino_web(raw)
        host = (urlparse(url).hostname or "").strip().lower()
    except Exception as e:
        print(f"[WARN security] urlparse error: {e}")
        host = ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _security_domain_allowed(host: str, allowed_domains: list[str]) -> bool:
    h = (host or "").strip().lower()
    if not h:
        return False
    for allowed in allowed_domains or []:
        d = str(allowed or "").strip().lower().lstrip(".")
        if not d:
            continue
        if h == d or h.endswith("." + d):
            return True
    return False


def _security_allow_system_browser_fallback() -> bool:
    with SECURITY_LOCK:
        return bool(SECURITY_POLICY.get("allow_system_browser_fallback", False))


def _security_guard(
    tool_name: str,
    args: dict,
    user_input: str,
    source: str,
    profile_id: str | None = None,
) -> tuple[bool, str]:
    with SECURITY_LOCK:
        policy = json.loads(
            json.dumps(SECURITY_POLICY, ensure_ascii=False, default=str)
        )
    blocked = set(policy.get("blocked_tools") or [])

    if tool_name in blocked:
        msg = f"Tool '{tool_name}' blocked by security policy."
        with SECURITY_LOCK:
            SECURITY_STATE["last_block_reason"] = msg
            SECURITY_STATE["last_block_ts"] = datetime.now().isoformat(
                timespec="seconds"
            )
        if callable(_obs_inc):
            _obs_inc("security_blocked_total", 1)
        _security_audit(
            "tool_blocked",
            level="warning",
            tool=tool_name,
            reason=msg,
            source=source,
            metadata={"args": args},
        )
        return False, msg

    if bool(policy.get("strict_mode")):
        if tool_name in {"abrir_navegador", "navegar_en_navegador"}:
            host = _security_host_for_destino((args or {}).get("destino", ""))
            allowed_domains = policy.get("allowed_web_domains") or []
            if host and not _security_domain_allowed(host, allowed_domains):
                msg = f"Domain '{host}' outside of allowlist in strict mode."
                with SECURITY_LOCK:
                    SECURITY_STATE["last_block_reason"] = msg
                    SECURITY_STATE["last_block_ts"] = datetime.now().isoformat(
                        timespec="seconds"
                    )
                if callable(_obs_inc):
                    _obs_inc("security_blocked_total", 1)
                _security_audit(
                    "domain_blocked",
                    level="warning",
                    tool=tool_name,
                    reason=msg,
                    source=source,
                    metadata={"host": host},
                )
                return False, msg

        if tool_name == "abrir_aplicacion":
            app_name = str((args or {}).get("nombre_app", "")).strip().lower()
            safe_apps = set(a.lower().strip() for a in (policy.get("safe_apps") or []))
            if app_name and app_name not in safe_apps:
                msg = f"Application '{app_name}' not permitted in strict mode."
                with SECURITY_LOCK:
                    SECURITY_STATE["last_block_reason"] = msg
                    SECURITY_STATE["last_block_ts"] = datetime.now().isoformat(
                        timespec="seconds"
                    )
                if callable(_obs_inc):
                    _obs_inc("security_blocked_total", 1)
                _security_audit(
                    "app_blocked",
                    level="warning",
                    tool=tool_name,
                    reason=msg,
                    source=source,
                )
                return False, msg

    if callable(verificar_autorizacion):
        authorized = bool(verificar_autorizacion(profile_id))
    else:
        authorized = False
    confirmed = bool(
        (args or {}).get("_confirmed")
        or (args or {}).get("confirmed")
        or (args or {}).get("confirmar")
        or (args or {}).get("el_usuario_ya_confirmo")
        or str(source or "").lower() in {"control_panel", "auth_resume"}
    )
    decision = evaluate_tool_policy(
        tool_name,
        profile_id=profile_id,
        authorized=authorized,
        confirmed=confirmed,
        overrides=policy.get("tool_policies") or {},
    )
    if not decision.allowed:
        if callable(_obs_inc):
            _obs_inc("security_warning_total", 1)
        _security_audit(
            "critical_without_auth",
            level="warning",
            tool=tool_name,
            reason=decision.reason,
            source=source,
            metadata={
                "user_input": (user_input or "")[:120],
                "profile_id": str(profile_id or "")[:64],
                "risk_level": decision.policy.risk_level,
                "requires_confirmation": decision.policy.requires_confirmation,
            },
        )
        return False, decision.reason

    return True, ""


def _security_snapshot() -> dict:
    with SECURITY_LOCK:
        policy = json.loads(
            json.dumps(SECURITY_POLICY, ensure_ascii=False, default=str)
        )
        state = json.loads(json.dumps(SECURITY_STATE, ensure_ascii=False, default=str))
    return {
        "policy": policy,
        "tool_policies": export_tool_policy_table(policy.get("tool_policies") or {}),
        "state": state,
    }


def _proactive_push_alert(
    kind: str,
    message: str,
    severity: str = "info",
    key: str = "",
    send_telegram: bool = False,
    cooldown: int | None = None,
) -> bool:
    now_ts = _time.time()
    if not key:
        key = f"{kind}:{(message or '')[:60]}"
    with PROACTIVE_LOCK:
        enabled = bool(PROACTIVE_STATE.get("enabled", True))
        if not enabled:
            return False
        cooldown_s = int(cooldown or PROACTIVE_STATE.get("cooldown_seconds", 600))
        last_sent = float(
            PROACTIVE_STATE.get("last_alert_by_key", {}).get(key, 0.0) or 0.0
        )
        if last_sent and (now_ts - last_sent) < cooldown_s:
            return False
        PROACTIVE_STATE.setdefault("last_alert_by_key", {})[key] = now_ts
        alert = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "kind": str(kind or "generic"),
            "severity": str(severity or "info"),
            "message": str(message or "").strip(),
            "key": key,
        }
        PROACTIVE_STATE.setdefault("alerts", []).append(alert)
        PROACTIVE_STATE["alerts"] = PROACTIVE_STATE["alerts"][-200:]

    if callable(_obs_inc):
        _obs_inc("proactive_alerts_total", 1)
    if callable(_obs_event):
        _obs_event(
            "proactive_alert",
            kind=kind,
            severity=severity,
            message=(message or "")[:220],
            key=key,
        )

    if (
        send_telegram
        and "enviar_telegram_sync" in globals()
        and callable(enviar_telegram_sync)
    ):
        try:
            msg_to_send = (
                normalizar_tratamiento_admin(message)
                if callable(normalizar_tratamiento_admin)
                else message
            )
            threading.Thread(
                target=enviar_telegram_sync,
                args=(msg_to_send,),
                daemon=True,
            ).start()
        except Exception as e:
            _warn(f"Could not send proactive alert to Telegram: {e}")
    return True


def _proactive_register_tool_error(tool_name: str, error_text: str):
    now_ts = _time.time()
    with PROACTIVE_LOCK:
        win = [
            x
            for x in (PROACTIVE_STATE.get("tool_errors_window") or [])
            if now_ts - float(x.get("ts", 0)) <= 300
        ]
        win.append({"ts": now_ts, "tool": tool_name, "error": (error_text or "")[:160]})
        PROACTIVE_STATE["tool_errors_window"] = win
        total_5m = len(win)
        same_tool = sum(1 for x in win if x.get("tool") == tool_name)

    with SECURITY_LOCK:
        threshold = int(SECURITY_POLICY.get("max_tool_errors_5m") or 12)
    threshold = max(3, min(threshold, 100))

    if total_5m >= threshold:
        _proactive_push_alert(
            "tool_error_rate",
            f"Administrator, detected a high rate of tool failures ({total_5m} in 5 minutes).",
            severity="warning",
            key="tool_error_rate",
            send_telegram=True,
            cooldown=900,
        )

    if same_tool >= 4:
        _proactive_push_alert(
            "tool_repeated_fail",
            f"Administrator, tool '{tool_name}' has failed {same_tool} times recently.",
            severity="warning",
            key=f"tool_repeated_fail:{tool_name}",
            send_telegram=True,
            cooldown=900,
        )


def _proactive_snapshot(limit: int = 30) -> dict:
    with PROACTIVE_LOCK:
        enabled = bool(PROACTIVE_STATE.get("enabled", True))
        cooldown = int(PROACTIVE_STATE.get("cooldown_seconds", 600))
        alerts = list(PROACTIVE_STATE.get("alerts", []))[-max(1, int(limit)) :]
        errors_5m = len(PROACTIVE_STATE.get("tool_errors_window", []))
        last_check = PROACTIVE_STATE.get("last_health_check", "")
    return {
        "enabled": enabled,
        "cooldown_seconds": cooldown,
        "errors_5m": errors_5m,
        "last_health_check": last_check,
        "alerts": alerts,
    }


def _actualizar_security_policy(payload: dict) -> dict:
    payload = payload or {}
    with SECURITY_LOCK:
        current = json.loads(
            json.dumps(SECURITY_POLICY, ensure_ascii=False, default=str)
        )
        merged = dict(current)
        for key in [
            "strict_mode",
            "blocked_tools",
            "allowed_web_domains",
            "allow_system_browser_fallback",
            "safe_apps",
            "max_tool_errors_5m",
        ]:
            if key in payload:
                merged[key] = payload.get(key)
        normalized = _security_normalizar_policy(merged)
        SECURITY_POLICY.clear()
        SECURITY_POLICY.update(normalized)
        SECURITY_STATE["last_update"] = datetime.now().isoformat(timespec="seconds")
    _save_security_policy()
    _security_audit(
        "policy_update",
        level="info",
        reason="Security policy updated via API.",
    )
    return _security_snapshot()


def _ejecutar_accion_control(action: str) -> str:
    act = (action or "").strip().lower()
    if not act:
        return "No quick action specified."

    if act == "reload_plugins":
        if callable(_recargar_plugins_runtime):
            return str(_recargar_plugins_runtime())
        return "Plugin system is not initialized."

    if act == "security_strict_toggle":
        enabled = not bool(SECURITY_POLICY.get("strict_mode", False))
        _actualizar_security_policy({"strict_mode": enabled})
        return f"Strict security mode {'enabled' if enabled else 'disabled'}."

    if act == "proactive_toggle":
        with PROACTIVE_LOCK:
            enabled = not bool(PROACTIVE_STATE.get("enabled", True))
            PROACTIVE_STATE["enabled"] = enabled
        return f"Proactive monitoring {'enabled' if enabled else 'disabled'}."

    routine_map = {
        "rutina_trabajo": "trabajo",
        "rutina_gaming": "gaming",
        "rutina_buenos_dias": "buenos dias",
    }
    if act in routine_map:
        try:
            from core.brain.tool_manager import _invocar_tool_entry

            return str(
                _invocar_tool_entry(
                    "ejecutar_rutina",
                    {"nombre": routine_map[act]},
                    f"panel {act}",
                    source="control_panel",
                )
            )
        except Exception as exc:
            return f"I could not execute routine '{routine_map[act]}'. Error: {exc}"

    if act == "analizar_pantalla":
        try:
            from core.brain.tool_manager import _invocar_tool_entry

            return str(
                _invocar_tool_entry(
                    "analizar_pantalla",
                    {},
                    "panel analizar_pantalla",
                    source="control_panel",
                )
            )
        except Exception as exc:
            return f"I could not analyze the screen. Error: {exc}"

    return f"Unknown quick action: {act}"
