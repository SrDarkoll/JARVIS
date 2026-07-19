"""
tools package — Aggregador de todos los sub-módulos de herramientas.
Consolidado para usar ServiceContainer. inject_dependencies ahora mapea a 'services'.
"""

from core.service_container import services

from tools.action_plan import (
    crear_plan_acciones,
    ejecutar_plan_acciones,
    ver_plan_acciones,
)
from tools.browser import (
    abrir_navegador,
    abrir_youtube,
    cerrar_navegador_playwright,
    click_en_navegador,
    escribir_en_navegador,
    leer_pagina_navegador,
    navegar_en_navegador,
)
from tools.desktop_control import (
    controlar_ventana,
    enfocar_ventana,
    listar_ventanas,
)
from tools.search import buscar_en_internet
from tools.spotify import controlar_reproduccion, reproducir_en_spotify, reproducir_mix_spotify
from tools.system import (
    abrir_aplicacion,
    ajustar_volumen,
    borrar_memoria,
    controlar_pc,
    matar_proceso,
    modo_no_molestar,
    ver_procesos_pesados,
)
from tools.utilities import (
    analizar_pantalla,
    ejecutar_rutina,
    frase_motivacional,
    leer_archivo,
    obtener_clima,
    obtener_deportes_espn,
    poner_recordatorio,
    recargar_plugins,
)


def _get_base_tools_impl():
    return [
        buscar_en_internet, obtener_deportes_espn, obtener_clima,
        abrir_navegador, navegar_en_navegador, click_en_navegador,
        escribir_en_navegador, leer_pagina_navegador, cerrar_navegador_playwright,
        abrir_youtube, leer_archivo, poner_recordatorio,
        reproducir_en_spotify, reproducir_mix_spotify, controlar_reproduccion, ajustar_volumen,
        modo_no_molestar, controlar_pc, abrir_aplicacion,
        ver_procesos_pesados, matar_proceso, borrar_memoria,
        listar_ventanas, enfocar_ventana, controlar_ventana,
        crear_plan_acciones, ver_plan_acciones, ejecutar_plan_acciones,
        frase_motivacional, analizar_pantalla, recargar_plugins, ejecutar_rutina,
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
