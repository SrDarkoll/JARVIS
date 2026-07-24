"""Lazy aggregator for the base JARVIS tools."""

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
    "ajustar_volumen": ("tools.system", "ajustar_volumen"),
    "modo_no_molestar": ("tools.system", "modo_no_molestar"),
    "controlar_pc": ("tools.system", "controlar_pc"),
    "abrir_aplicacion": ("tools.system", "abrir_aplicacion"),
    "ver_procesos_pesados": ("tools.system", "ver_procesos_pesados"),
    "matar_proceso": ("tools.system", "matar_proceso"),
    "borrar_memoria": ("tools.system", "borrar_memoria"),
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
}
_BASE_TOOL_NAMES = tuple(_LAZY_EXPORTS)
__all__ = ["get_base_tools", *_BASE_TOOL_NAMES]


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'tools' has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def _get_base_tools_impl():
    return [__getattr__(name) for name in _BASE_TOOL_NAMES]

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
        if key == "_obs_event": services.obs_event = val
        elif key == "_obs_inc": services.obs_inc = val
        elif key == "llm": services.llm = val
        elif key == "llm_vision": services.llm_vision = val
        elif key == "_reparar_unicode": services.reparar_unicode = val
        elif key == "_invocar_tool": services.invocar_tool = val
        elif key == "_recargar_plugins_runtime": services.recargar_plugins = val

    # Cargar briefing si noticias_cache fue inyectado
    if "noticias_cache" in deps:
        from tools.utilities import _cargar_briefing_persistido
        _cargar_briefing_persistido()
