"""
Fachada de compatibilidad para el Cerebro de J.A.R.V.I.S.
Delega toda la lógica a los módulos en core.brain.*
"""

from core.brain.processor import (
    procesar_mensaje,
    stream_procesar_mensaje_events,
    necesita_tools,
    _invocar_tool_wrapper,
)
from core.brain.llm_engine import init_brain
from core.brain.brain_utils import (
    _limpiar_thinking,
    _limpiar_metadatos_voz,
    _formatear_reply_por_perfil,
    _normalizar_ascii,
    parsear_recordatorio,
    parsear_comando_volumen,
)
from core.brain.history_manager import (
    _registrar_accion_pendiente_auth,
    _extraer_accion_pendiente_auth,
    _get_history_for_profile,
    _append_to_profile_history,
)
from core.brain.security_engine import (
    _es_bloqueo_autorizacion,
    _mensaje_solicitar_autorizacion,
    _tool_requiere_autorizacion,
)
from core.brain.tool_manager import (
    _resultado_parece_error,
    _intentar_autocuracion,
    _recargar_plugins_runtime,
    _cargar_plugins_dinamicos,
    _invocar_tool_entry,
    _invocar_tool,
)
from core.brain.llm_engine import _rebuild_tooling
from core.brain.router import (
    _router_hibrido,
    _ROUTER_WEB_DIRECTO,
    _ROUTER_APP_CANDIDATOS,
    _extraer_objetivo_apertura,
)
from core.brain.prompts import get_system_msg
from core.brain import brain_state as _brain_state
from core.brain.social_engine import (
    _respuesta_rapida_social,
    _respuesta_seguimiento_contextual,
    _debe_buscar_en_web,
    KEYWORDS_WEB_DINAMICAS,
)
from core.brain.music_engine import (
    _es_comando_repetir_musica,
    _es_peticion_musica_generica,
    _es_posible_titulo_cancion,
    _contexto_musica_activo,
)
from core import jarvis_state


def __getattr__(name: str):
    if name in {
        "llm",
        "llm_with_tools",
        "tools_list",
        "tool_map",
        "PLUGIN_STATE",
        "PLUGIN_LOCK",
        "memoria_lock",
    }:
        return getattr(_brain_state, name)
    raise AttributeError(f"module 'core.jarvis_brain' has no attribute '{name}'")

# Alias para retrocompatibilidad total
DEFAULT_PROFILE_ID = jarvis_state.DEFAULT_PROFILE_ID

# Re-export de funciones originales del monolítico que puedan estar en uso
from core.brain.router import _router_hibrido as _router_hibrido_orig
from core.brain.tool_manager import _invocar_tool as _invocar_tool_orig
