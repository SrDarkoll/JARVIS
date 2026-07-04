"""
Fachada de compatibilidad para el Cerebro de J.A.R.V.I.S.
Delega toda la lógica a los módulos en core.brain.*
"""

from core import jarvis_state
from core.brain import brain_state

# Re-export variables de estado
llm = brain_state.llm
llm_with_tools = brain_state.llm_with_tools
tools_list = brain_state.tools_list
tool_map = brain_state.tool_map
PLUGIN_STATE = brain_state.PLUGIN_STATE
PLUGIN_LOCK = brain_state.PLUGIN_LOCK
memoria_lock = brain_state.memoria_lock
DEFAULT_PROFILE_ID = jarvis_state.DEFAULT_PROFILE_ID

def __getattr__(name: str):
    if name == "init_brain":
        from core.brain.llm_engine import init_brain
        return init_brain
    if name == "necesita_tools":
        from core.brain.processor import necesita_tools
        return necesita_tools
    if name == "procesar_mensaje":
        from core.brain.processor import procesar_mensaje
        return procesar_mensaje
    if name == "stream_procesar_mensaje_events":
        from core.brain.processor import stream_procesar_mensaje_events
        return stream_procesar_mensaje_events
    if name == "_debe_buscar_en_web":
        from core.brain.social_engine import _debe_buscar_en_web
        return _debe_buscar_en_web
    if name == "_invocar_tool":
        from core.brain.tool_manager import _invocar_tool
        return _invocar_tool
    if name == "_recargar_plugins_runtime":
        from core.brain.tool_manager import _recargar_plugins_runtime
        return _recargar_plugins_runtime
    if name == "get_system_msg":
        from core.brain.prompts import get_system_msg
        return get_system_msg

    # Check si está en brain_state para cubrir algo que me haya faltado
    if hasattr(brain_state, name):
        return getattr(brain_state, name)

    raise AttributeError(f"module 'core.jarvis_brain' has no attribute '{name}'")

# Alias para retrocompatibilidad total
DEFAULT_PROFILE_ID = jarvis_state.DEFAULT_PROFILE_ID

# Re-export de funciones originales del monolítico que puedan estar en uso
