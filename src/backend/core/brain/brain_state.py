"""Estado mutable compartido del cerebro de JARVIS.

Este módulo es la fuente canónica de estado para `jarvis_brain.py` y
los módulos modulares en `core/brain/`. Contiene los LLMs, tools,
plugins y locks que se comparten entre subsistemas.
"""

import threading
from typing import Any

from core import jarvis_state
from core.command_pipeline.tool_registry import (
    ToolRegistryService,
    ToolRegistrySnapshot,
)

# Global Locks
memoria_lock = jarvis_state.memoria_lock
PLUGIN_LOCK = threading.RLock()

# LLM Engines
llm: Any = None
llm_vision: Any = None
llm_fallback: Any = None
llm_fallbacks: tuple[Any, ...] = ()
llm_with_tools: Any = None
llm_primary_provider = ""
llm_fallback_provider = ""
llm_chain_providers: tuple[str, ...] = ()


def fallback_models() -> tuple[Any, ...]:
    """Fallback clients in chain order, bridging the legacy single-fallback slot."""
    if llm_fallbacks:
        return llm_fallbacks
    if llm_fallback is not None:
        return (llm_fallback,)
    return ()

# Tool Registry
tools_list: list = []
tool_map: dict = {}
_BASE_TOOLS: list = []
tool_registry = ToolRegistryService()


def get_tooling_snapshot() -> tuple[Any, Any, ToolRegistrySnapshot]:
    """Return a coherent model and tool registry view."""
    with PLUGIN_LOCK:
        return llm_with_tools, llm, tool_registry.snapshot()


# Plugin Management
PLUGIN_STATE: dict[str, Any] = {
    "last_reload": "",
    "loaded": {},
    "errors": {},
    "tools": [],
}

# App reference for plugin context
_app_ref: Any = None
