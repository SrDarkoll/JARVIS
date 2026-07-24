"""Formal tool permission policy.

Policy fields map directly to the audit requirement:
tool_name, risk_level, allowed_profiles, requires_confirmation, audit_log.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ToolPolicy:
    tool_name: str
    risk_level: str
    allowed_profiles: tuple[str, ...]
    requires_confirmation: bool
    audit_log: bool


@dataclass(frozen=True)
class ToolPolicyDecision:
    allowed: bool
    reason: str
    policy: ToolPolicy


PUBLIC_POLICY = ToolPolicy(
    tool_name="*",
    risk_level="public",
    allowed_profiles=("admin", "guest", "authorized"),
    requires_confirmation=False,
    audit_log=False,
)

DEFAULT_TOOL_POLICIES: dict[str, ToolPolicy] = {
    "leer_archivo": ToolPolicy("leer_archivo", "critical", ("admin",), True, True),
    "analizar_pantalla": ToolPolicy("analizar_pantalla", "critical", ("admin",), True, True),
    "controlar_pc": ToolPolicy("controlar_pc", "critical", ("admin",), True, True),
    "controlar_ventana": ToolPolicy("controlar_ventana", "critical", ("admin",), True, True),
    "matar_proceso": ToolPolicy("matar_proceso", "critical", ("admin",), True, True),
    "borrar_memoria": ToolPolicy("borrar_memoria", "critical", ("admin",), True, True),
    "abrir_aplicacion": ToolPolicy("abrir_aplicacion", "elevated", ("admin",), False, True),
    "ejecutar_rutina": ToolPolicy("ejecutar_rutina", "elevated", ("admin",), False, True),
    "recargar_plugins": ToolPolicy("recargar_plugins", "elevated", ("admin",), False, True),
    "listar_ventanas": ToolPolicy("listar_ventanas", "elevated", ("admin",), False, True),
    "enfocar_ventana": ToolPolicy("enfocar_ventana", "elevated", ("admin",), False, True),
    "crear_plan_acciones": ToolPolicy("crear_plan_acciones", "elevated", ("admin",), False, True),
    "ver_plan_acciones": ToolPolicy("ver_plan_acciones", "elevated", ("admin",), False, True),
    "ejecutar_plan_acciones": ToolPolicy("ejecutar_plan_acciones", "critical", ("admin",), True, True),
    "crear_archivo_texto": ToolPolicy("crear_archivo_texto", "elevated", ("admin",), False, True),
    "ejecutar_comando_terminal": ToolPolicy("ejecutar_comando_terminal", "elevated", ("admin",), False, True),
    "buscar_en_wikipedia": ToolPolicy("buscar_en_wikipedia", "elevated", ("admin",), False, True),
    "reproducir_en_youtube": ToolPolicy("reproducir_en_youtube", "elevated", ("admin",), False, True),
}


def _profile_role(profile_id: str | None, authorized: bool) -> str:
    pid = str(profile_id or "").strip().lower()
    from core.jarvis_config import resolve_runtime_features
    if not resolve_runtime_features().allow_guest_mode and not pid.startswith("guest_") and pid != "guest":
        return "admin"
    if authorized:
        return "admin"
    pid = str(profile_id or "").strip().lower()
    if pid == "admin":
        return "admin" if authorized else "guest"
    if pid.startswith("guest_") or pid in {"guest", "guest_unverified", ""}:
        return "guest"
    return "guest"


def _policy_from_override(tool_name: str, raw: dict[str, Any]) -> ToolPolicy | None:
    entry = (raw or {}).get(tool_name)
    if not isinstance(entry, dict):
        return None
    allowed = entry.get("allowed_profiles", PUBLIC_POLICY.allowed_profiles)
    if isinstance(allowed, str):
        allowed = (allowed,)
    return ToolPolicy(
        tool_name=tool_name,
        risk_level=str(entry.get("risk_level") or "public"),
        allowed_profiles=tuple(str(x) for x in allowed),
        requires_confirmation=bool(entry.get("requires_confirmation", False)),
        audit_log=bool(entry.get("audit_log", False)),
    )


def get_tool_policy(tool_name: str, overrides: dict[str, Any] | None = None) -> ToolPolicy:
    name = str(tool_name or "").strip()
    override = _policy_from_override(name, overrides or {})
    if override:
        return override
    return DEFAULT_TOOL_POLICIES.get(name, PUBLIC_POLICY)


def tool_requires_authorization(tool_name: str, overrides: dict[str, Any] | None = None) -> bool:
    policy = get_tool_policy(tool_name, overrides=overrides)
    return policy.risk_level in {"critical", "elevated"} or policy.allowed_profiles == ("admin",)


def evaluate_tool_policy(
    tool_name: str,
    *,
    profile_id: str | None,
    authorized: bool,
    confirmed: bool,
    overrides: dict[str, Any] | None = None,
) -> ToolPolicyDecision:
    policy = get_tool_policy(tool_name, overrides=overrides)
    role = _profile_role(profile_id, authorized)

    role_allowed = role in policy.allowed_profiles
    authorized_allowed = "authorized" in policy.allowed_profiles and authorized
    if not role_allowed and not authorized_allowed:
        return ToolPolicyDecision(
            allowed=False,
            reason="La herramienta requiere autorizacion del Administrador.",
            policy=policy,
        )
    if policy.requires_confirmation and not confirmed:
        return ToolPolicyDecision(
            allowed=False,
            reason="La herramienta requiere confirmacion explicita.",
            policy=policy,
        )
    return ToolPolicyDecision(allowed=True, reason="", policy=policy)


def export_tool_policy_table(overrides: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    names = set(DEFAULT_TOOL_POLICIES)
    names.update((overrides or {}).keys())
    return {name: asdict(get_tool_policy(name, overrides=overrides)) for name in sorted(names)}
