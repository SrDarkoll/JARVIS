"""
core_tools.py — Shim de compatibilidad.

Este archivo re-exporta todo desde el paquete `tools/` para mantener
compatibilidad con el backend y los tests existentes.
TODO el código real vive ahora en el paquete `tools/`.
"""

from tools._common import (  # noqa: F401
    BASE_DIR,
    DEFAULT_PROFILE_ID,
    ROOT_DIR,
    SHARED_PROFILE_ID,
    _limpiar_respuesta,
    _normalizar_ascii,
    _normalizar_destino_web,
    _normalizar_profile_id,
    jarvis_state,
    memoria_lock,
)
from tools.action_plan import crear_plan_acciones, ejecutar_plan_acciones, ver_plan_acciones  # noqa: F401
from tools.browser import (  # noqa: F401
    BrowserWorker,
    _abrir_en_navegador_sistema,
    abrir_youtube,
    cerrar_navegador_playwright,
)
from tools.desktop_control import controlar_ventana, enfocar_ventana, listar_ventanas  # noqa: F401
from tools.memory import (  # noqa: F401
    _obtener_contexto_memoria_entrelazada,
    _obtener_contexto_perfil,
    _sincronizar_memoria_entrelazada,
    cargar_memoria,
    cargar_memoria_perfiles,
    extraer_datos_criticos,
    guardar_memoria,
    guardar_memoria_async,
    guardar_memoria_perfiles,
    init_sqlite_db,
)
from tools.search import _buscar_multi_fuente, buscar_en_internet  # noqa: F401
from modules.spotify.tools import (  # noqa: F401
    controlar_reproduccion,
    reproducir_en_spotify,
    reproducir_mix_spotify,
)
from tools.system import (  # noqa: F401
    _ajustar_volumen_absoluto,
    _ajustar_volumen_relativo,
    abrir_aplicacion,
    ajustar_volumen,
    borrar_memoria,
    controlar_pc,
    matar_proceso,
)
from tools.utilities import (  # noqa: F401
    _obtener_clima_logic,
    agregar_recordatorio,
    analizar_pantalla,
    ejecutar_rutina,
    frase_motivacional,
    generar_resumen_noticias,
    leer_archivo,
    obtener_clima,
    obtener_deportes_espn,
)


def get_base_tools():
    from tools import _get_base_tools_impl
    return _get_base_tools_impl()

def inject_dependencies(deps: dict):
    from tools import _inject_dependencies_impl
    return _inject_dependencies_impl(deps)
from core.service_container import services

# Legacy compatibility shims
noticias_cache = services.noticias_cache
weather_cache = services.weather_cache
recordatorios = services.get_reminders()
recordatorios_lock = jarvis_state.recordatorios_lock

chat_history = jarvis_state.chat_history
DATOS_CURIOSOS = jarvis_state.DATOS_CURIOSOS

# El diccionario 'context' global ha sido ELIMINADO en favor de ServiceContainer.
# Si algún plugin lo usa, debe migrarse a 'services'.
