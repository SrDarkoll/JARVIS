"""
core_tools.py — Shim de compatibilidad.

Este archivo re-exporta todo desde el paquete `tools/` para mantener
compatibilidad con el backend y los tests existentes.
TODO el código real vive ahora en el paquete `tools/`.
"""

from tools._common import (  # noqa: F401
    jarvis_state, BASE_DIR, ROOT_DIR, DEFAULT_PROFILE_ID, SHARED_PROFILE_ID,
    memoria_lock, _normalizar_ascii, _normalizar_profile_id, _normalizar_destino_web,
    _limpiar_respuesta
)

from tools.search import (  # noqa: F401
    _buscar_multi_fuente, buscar_en_internet
)

from tools.browser import (  # noqa: F401
    BrowserWorker, cerrar_navegador_playwright, abrir_youtube, _abrir_en_navegador_sistema
)

from tools.spotify import (  # noqa: F401
    sp, _ULTIMA_CANCION_SOLICITADA, reproducir_en_spotify, controlar_reproduccion
)

from tools.system import (  # noqa: F401
    _ajustar_volumen_absoluto, _ajustar_volumen_relativo, ajustar_volumen,
    controlar_pc, abrir_aplicacion, matar_proceso, borrar_memoria
)

from tools.desktop_control import (  # noqa: F401
    listar_ventanas, enfocar_ventana, controlar_ventana
)

from tools.action_plan import (  # noqa: F401
    crear_plan_acciones, ver_plan_acciones, ejecutar_plan_acciones
)

from tools.memory import (  # noqa: F401
    init_sqlite_db, cargar_memoria_perfiles, guardar_memoria_perfiles,
    cargar_memoria, guardar_memoria, guardar_memoria_async,
    _obtener_contexto_perfil, _obtener_contexto_memoria_entrelazada,
    _sincronizar_memoria_entrelazada, extraer_datos_criticos
)

from tools.utilities import (  # noqa: F401
    obtener_clima, obtener_partidos_nba, agregar_recordatorio,
    leer_archivo, frase_motivacional, analizar_pantalla,
    ejecutar_rutina, generar_resumen_noticias, _obtener_clima_logic
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
