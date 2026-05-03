"""Estado mutable compartido del cerebro de JARVIS.

Este módulo es la fuente canónica de estado para `jarvis_brain.py` y
los módulos modulares en `core/brain/`. Contiene los LLMs, tools,
plugins y locks que se comparten entre subsistemas.
"""

import threading
from typing import Any
from core import jarvis_state

# Global Locks
memoria_lock = jarvis_state.memoria_lock
PLUGIN_LOCK = threading.Lock()

# LLM Engines
llm: Any = None
llm_vision: Any = None
llm_fallback: Any = None
llm_with_tools: Any = None

# Tool Registry
tools_list: list = []
tool_map: dict = {}
_BASE_TOOLS: list = []

# Plugin Management
PLUGIN_STATE: dict[str, Any] = {
    "last_reload": "",
    "loaded": {},
    "errors": {},
    "tools": [],
}

# App reference for plugin context
_app_ref: Any = None
