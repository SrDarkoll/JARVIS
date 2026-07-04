"""
Core package - fachada unificada para JARVIS.
Se mantiene mínimo para evitar dependencias circulares durante el booteo.
"""

from core import core_tools, jarvis_brain, jarvis_config, jarvis_observability, jarvis_state, service_container

# No importar core.brain aquí para evitar recursión con processor.py
__all__ = [
    "jarvis_state",
    "core_tools",
    "jarvis_brain",
    "jarvis_config",
    "jarvis_observability",
    "service_container"
]
