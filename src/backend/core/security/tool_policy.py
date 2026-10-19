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

UNCLASSIFIED_POLICY = ToolPolicy(
    tool_name="*",
    risk_level="unclassified",
    allowed_profiles=(),
    requires_confirmation=True,
    audit_log=True,
)


def _public(tool_name: str, *, audit_log: bool = False) -> ToolPolicy:
    return ToolPolicy(
        tool_name,
        "public",
        ("admin", "guest", "authorized"),
        False,
        audit_log,
    )


def _elevated(tool_name: str) -> ToolPolicy:
    return ToolPolicy(tool_name, "elevated", ("admin",), False, True)


def _critical(tool_name: str) -> ToolPolicy:
    return ToolPolicy(tool_name, "critical", ("admin",), True, True)


DEFAULT_TOOL_POLICIES: dict[str, ToolPolicy] = {
    "buscar_en_internet": _public("buscar_en_internet"),
    "obtener_deportes_espn": _public("obtener_deportes_espn"),
    "obtener_clima": _public("obtener_clima"),
    "abrir_navegador": _elevated("abrir_navegador"),
    "navegar_en_navegador": _elevated("navegar_en_navegador"),
    "click_en_navegador": _critical("click_en_navegador"),
    "escribir_en_navegador": _critical("escribir_en_navegador"),
    "leer_pagina_navegador": _elevated("leer_pagina_navegador"),
    "cerrar_navegador_playwright": _elevated("cerrar_navegador_playwright"),
    "abrir_youtube": _elevated("abrir_youtube"),
    "leer_archivo": _critical("leer_archivo"),
    "poner_recordatorio": _public("poner_recordatorio", audit_log=True),
    "reproducir_en_spotify": _public("reproducir_en_spotify", audit_log=True),
    "reproducir_mix_spotify": _public("reproducir_mix_spotify", audit_log=True),
    "controlar_reproduccion": _public("controlar_reproduccion", audit_log=True),
    "agregar_a_cola_spotify": _elevated("agregar_a_cola_spotify"),
    "dar_like_spotify": _elevated("dar_like_spotify"),
    "quitar_like_spotify": _elevated("quitar_like_spotify"),
    "cancion_actual_spotify": _public("cancion_actual_spotify"),
    "ajustar_volumen": _public("ajustar_volumen", audit_log=True),
    "modo_no_molestar": _public("modo_no_molestar", audit_log=True),
    "controlar_pc": _critical("controlar_pc"),
    "abrir_aplicacion": _elevated("abrir_aplicacion"),
    "abrir_documento": _elevated("abrir_documento"),
    "ver_procesos_pesados": _elevated("ver_procesos_pesados"),
    "matar_proceso": _critical("matar_proceso"),
    "borrar_memoria": _critical("borrar_memoria"),
    "guardar_dato_memoria": _public("guardar_dato_memoria", audit_log=True),
    "listar_ventanas": _elevated("listar_ventanas"),
    "enfocar_ventana": _elevated("enfocar_ventana"),
    "controlar_ventana": _elevated("controlar_ventana"),
    "crear_plan_acciones": _elevated("crear_plan_acciones"),
    "ver_plan_acciones": _elevated("ver_plan_acciones"),
    "ejecutar_plan_acciones": _critical("ejecutar_plan_acciones"),
    "frase_motivacional": _public("frase_motivacional"),
    "analizar_pantalla": _elevated("analizar_pantalla"),
    "recargar_plugins": _elevated("recargar_plugins"),
    "ejecutar_rutina": _elevated("ejecutar_rutina"),
    "evaluar_expresion_matematica": _public("evaluar_expresion_matematica"),
    "crear_archivo_texto": _critical("crear_archivo_texto"),
    "ejecutar_comando_terminal": _critical("ejecutar_comando_terminal"),
    "buscar_en_wikipedia": _elevated("buscar_en_wikipedia"),
    "reproducir_en_youtube": _elevated("reproducir_en_youtube"),
    "computer_click": _critical("computer_click"),
    "computer_type": _critical("computer_type"),
    "computer_hotkey": _critical("computer_hotkey"),
    "computer_scroll": _elevated("computer_scroll"),
    "computer_inspect_screen": _elevated("computer_inspect_screen"),
    "computer_drag_and_drop": _critical("computer_drag_and_drop"),
    "consultar_estado_proactivo": _elevated("consultar_estado_proactivo"),
    "ejecutar_accion_proactiva": _critical("ejecutar_accion_proactiva"),
    "predecir_siguiente_accion": _elevated("predecir_siguiente_accion"),
    "aprender_patron_rutina": _elevated("aprender_patron_rutina"),
    "consultar_rutinas_predictivas": _elevated("consultar_rutinas_predictivas"),
    "aprender_procedimiento": _elevated("aprender_procedimiento"),
    "consultar_procedimientos": _elevated("consultar_procedimientos"),
    "ejecutar_procedimiento": _critical("ejecutar_procedimiento"),
    "recordar_episodio": _elevated("recordar_episodio"),
    "guardar_memoria_entrelazada": _elevated("guardar_memoria_entrelazada"),
    "consultar_memoria_unificada": _elevated("consultar_memoria_unificada"),
    "enlazar_conceptos": _elevated("enlazar_conceptos"),
    "sincronizar_boveda_conocimiento": _elevated("sincronizar_boveda_conocimiento"),
    "ejecutar_consolidacion_memoria": _elevated("ejecutar_consolidacion_memoria"),
    "crear_nota_boveda": _elevated("crear_nota_boveda"),
    "buscar_en_boveda": _elevated("buscar_en_boveda"),
    "busqueda_semantica_hibrida": _elevated("busqueda_semantica_hibrida"),
    "consultar_memoria_jit": _elevated("consultar_memoria_jit"),
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
    policy = DEFAULT_TOOL_POLICIES.get(name)
    if policy is not None:
        return policy
    return ToolPolicy(
        tool_name=name,
        risk_level=UNCLASSIFIED_POLICY.risk_level,
        allowed_profiles=UNCLASSIFIED_POLICY.allowed_profiles,
        requires_confirmation=UNCLASSIFIED_POLICY.requires_confirmation,
        audit_log=UNCLASSIFIED_POLICY.audit_log,
    )


def tool_requires_authorization(tool_name: str, overrides: dict[str, Any] | None = None) -> bool:
    policy = get_tool_policy(tool_name, overrides=overrides)
    return (
        policy.risk_level in {"critical", "elevated", "unclassified"}
        or not policy.allowed_profiles
        or policy.allowed_profiles == ("admin",)
    )


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
