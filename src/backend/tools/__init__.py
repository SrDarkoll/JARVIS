# pyright: reportUnsupportedDunderAll=false
"""Lazy aggregator for the base JARVIS tools."""

import os
from importlib import import_module

_LAZY_EXPORTS = {
    "buscar_en_internet": ("tools.search", "buscar_en_internet"),
    "obtener_deportes_espn": ("tools.utilities", "obtener_deportes_espn"),
    "obtener_clima": ("tools.utilities", "obtener_clima"),
    "abrir_navegador": ("tools.browser", "abrir_navegador"),
    "navegar_en_navegador": ("tools.browser", "navegar_en_navegador"),
    "click_en_navegador": ("tools.browser", "click_en_navegador"),
    "escribir_en_navegador": ("tools.browser", "escribir_en_navegador"),
    "leer_pagina_navegador": ("tools.browser", "leer_pagina_navegador"),
    "cerrar_navegador_playwright": (
        "tools.browser",
        "cerrar_navegador_playwright",
    ),
    "abrir_youtube": ("tools.browser", "abrir_youtube"),
    "reproducir_en_youtube": (
        "modules.youtube.tools",
        "reproducir_en_youtube",
    ),
    "buscar_en_wikipedia": ("tools.browser", "buscar_en_wikipedia"),
    "leer_archivo": ("tools.utilities", "leer_archivo"),
    "poner_recordatorio": ("tools.utilities", "poner_recordatorio"),
    "reproducir_en_spotify": (
        "modules.spotify.tools",
        "reproducir_en_spotify",
    ),
    "reproducir_mix_spotify": (
        "modules.spotify.tools",
        "reproducir_mix_spotify",
    ),
    "controlar_reproduccion": (
        "modules.spotify.tools",
        "controlar_reproduccion",
    ),
    "agregar_a_cola_spotify": (
        "modules.spotify.tools",
        "agregar_a_cola_spotify",
    ),
    "dar_like_spotify": (
        "modules.spotify.tools",
        "dar_like_spotify",
    ),
    "quitar_like_spotify": (
        "modules.spotify.tools",
        "quitar_like_spotify",
    ),
    "cancion_actual_spotify": (
        "modules.spotify.tools",
        "cancion_actual_spotify",
    ),
    "ajustar_volumen": ("tools.system", "ajustar_volumen"),
    "modo_no_molestar": ("tools.system", "modo_no_molestar"),
    "controlar_pc": ("tools.system", "controlar_pc"),
    "abrir_aplicacion": ("tools.system", "abrir_aplicacion"),
    "abrir_documento": ("tools.documents", "abrir_documento"),
    "ver_procesos_pesados": ("tools.system", "ver_procesos_pesados"),
    "matar_proceso": ("tools.system", "matar_proceso"),
    "borrar_memoria": ("tools.system", "borrar_memoria"),
    "guardar_dato_memoria": ("tools.memory", "guardar_dato_memoria"),
    "listar_ventanas": ("tools.desktop_control", "listar_ventanas"),
    "enfocar_ventana": ("tools.desktop_control", "enfocar_ventana"),
    "controlar_ventana": ("tools.desktop_control", "controlar_ventana"),
    "crear_plan_acciones": ("tools.action_plan", "crear_plan_acciones"),
    "ver_plan_acciones": ("tools.action_plan", "ver_plan_acciones"),
    "ejecutar_plan_acciones": ("tools.action_plan", "ejecutar_plan_acciones"),
    "frase_motivacional": ("tools.utilities", "frase_motivacional"),
    "analizar_pantalla": ("tools.utilities", "analizar_pantalla"),
    "recargar_plugins": ("tools.utilities", "recargar_plugins"),
    "ejecutar_rutina": ("tools.utilities", "ejecutar_rutina"),
    "evaluar_expresion_matematica": (
        "tools.utilities",
        "evaluar_expresion_matematica",
    ),
    "crear_archivo_texto": ("tools.system", "crear_archivo_texto"),
    "ejecutar_comando_terminal": ("tools.system", "ejecutar_comando_terminal"),
    "computer_click": ("tools.computer_use", "computer_click"),
    "computer_type": ("tools.computer_use", "computer_type"),
    "computer_hotkey": ("tools.computer_use", "computer_hotkey"),
    "computer_scroll": ("tools.computer_use", "computer_scroll"),
    "computer_inspect_screen": ("tools.computer_use", "computer_inspect_screen"),
    "computer_drag_and_drop": ("tools.computer_use", "computer_drag_and_drop"),
    "consultar_estado_proactivo": ("tools.proactive", "consultar_estado_proactivo"),
    "ejecutar_accion_proactiva": ("tools.proactive", "ejecutar_accion_proactiva"),
    "predecir_siguiente_accion": ("tools.proactive", "predecir_siguiente_accion"),
    "aprender_patron_rutina": ("tools.proactive", "aprender_patron_rutina"),
    "consultar_rutinas_predictivas": ("tools.proactive", "consultar_rutinas_predictivas"),
    "aprender_procedimiento": ("tools.procedural_memory", "aprender_procedimiento"),
    "consultar_procedimientos": ("tools.procedural_memory", "consultar_procedimientos"),
    "ejecutar_procedimiento": ("tools.procedural_memory", "ejecutar_procedimiento"),
    "recordar_episodio": ("tools.procedural_memory", "recordar_episodio"),
    "guardar_memoria_entrelazada": ("tools.procedural_memory", "guardar_memoria_entrelazada"),
    "consultar_memoria_unificada": ("tools.procedural_memory", "consultar_memoria_unificada"),
    "enlazar_conceptos": ("tools.procedural_memory", "enlazar_conceptos"),
    "sincronizar_boveda_conocimiento": ("tools.procedural_memory", "sincronizar_boveda_conocimiento"),
    "ejecutar_consolidacion_memoria": ("tools.procedural_memory", "ejecutar_consolidacion_memoria"),
    "crear_nota_boveda": ("tools.procedural_memory", "crear_nota_boveda"),
    "buscar_en_boveda": ("tools.procedural_memory", "buscar_en_boveda"),
    "busqueda_semantica_hibrida": ("tools.procedural_memory", "busqueda_semantica_hibrida"),
    "consultar_memoria_jit": ("tools.procedural_memory", "consultar_memoria_jit"),
}
_BASE_TOOL_NAMES = tuple(_LAZY_EXPORTS)
_OPT_IN_SYSTEM_TOOLS = frozenset(
    {
        "crear_archivo_texto",
        "ejecutar_comando_terminal",
    }
)
_FEATURE_TOOL_GROUPS = {
    "JARVIS_COMPUTER_USE_ENABLED": frozenset(
        {
            "computer_click",
            "computer_type",
            "computer_hotkey",
            "computer_scroll",
            "computer_inspect_screen",
            "computer_drag_and_drop",
        }
    ),
    "JARVIS_PROACTIVE_TOOLS_ENABLED": frozenset(
        {
            "consultar_estado_proactivo",
            "ejecutar_accion_proactiva",
            "predecir_siguiente_accion",
            "aprender_patron_rutina",
            "consultar_rutinas_predictivas",
        }
    ),
    "JARVIS_COGNITIVE_MEMORY_ENABLED": frozenset(
        {
            "aprender_procedimiento",
            "consultar_procedimientos",
            "ejecutar_procedimiento",
            "recordar_episodio",
            "guardar_memoria_entrelazada",
            "consultar_memoria_unificada",
            "enlazar_conceptos",
            "sincronizar_boveda_conocimiento",
            "ejecutar_consolidacion_memoria",
            "crear_nota_boveda",
            "buscar_en_boveda",
            "busqueda_semantica_hibrida",
            "consultar_memoria_jit",
        }
    ),
    "JARVIS_ACTION_PLANS_ENABLED": frozenset(
        {
            "crear_plan_acciones",
            "ver_plan_acciones",
            "ejecutar_plan_acciones",
        }
    ),
    "JARVIS_VISION_ENABLED": frozenset({"analizar_pantalla"}),
    "JARVIS_PLUGINS_ENABLED": frozenset({"recargar_plugins"}),
}
__all__ = ["get_base_tools"]
__all__.extend(_BASE_TOOL_NAMES)


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'tools' has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _get_base_tools_impl():
    core_mode = _env_bool("JARVIS_CORE_MODE", True)
    optional_default = not core_mode
    system_tools_enabled = _env_bool("JARVIS_SYSTEM_TOOLS_ENABLED", False)
    disabled_tools = set()
    for flag, tool_names in _FEATURE_TOOL_GROUPS.items():
        if not _env_bool(flag, optional_default):
            disabled_tools.update(tool_names)

    return [
        __getattr__(name)
        for name in _BASE_TOOL_NAMES
        if name not in disabled_tools
        and (system_tools_enabled or name not in _OPT_IN_SYSTEM_TOOLS)
    ]


def get_base_tools():
    """Retorna la lista completa de herramientas base para LangChain."""
    return _get_base_tools_impl()


def _inject_dependencies_impl(deps: dict):
    """
    Inyecta dependencias mapeándolas internamente al ServiceContainer.
    Esto permite que el backend siga llamando a core_tools.inject_dependencies
    pero el estado se guarde en el contenedor de servicios moderno.
    """
    from core.service_container import services

    for key, val in deps.items():
        if key == "_obs_event":
            services.obs_event = val
        elif key == "_obs_inc":
            services.obs_inc = val
        elif key == "llm":
            services.llm = val
        elif key == "llm_vision":
            services.llm_vision = val
        elif key == "_reparar_unicode":
            services.reparar_unicode = val
        elif key == "_invocar_tool":
            services.invocar_tool = val
        elif key == "_recargar_plugins_runtime":
            services.recargar_plugins = val

    # Cargar briefing si noticias_cache fue inyectado
    if "noticias_cache" in deps:
        from tools.utilities import _cargar_briefing_persistido

        _cargar_briefing_persistido()
