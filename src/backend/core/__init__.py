# pyright: reportUnsupportedDunderAll=false
"""Lazy facade for JARVIS core modules."""

from importlib import import_module

__all__ = [
    "jarvis_state",
    "core_tools",
    "jarvis_brain",
    "jarvis_config",
    "jarvis_observability",
    "service_container",
    "unified_log",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module 'core' has no attribute {name!r}")
    module = import_module(f"core.{name}")
    globals()[name] = module
    return module
