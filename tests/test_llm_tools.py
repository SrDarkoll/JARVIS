"""Pruebas unitarias ligeras para la fachada LLM/tools del cerebro."""

from core import jarvis_brain
from core.brain import prompts


def test_jarvis_brain_facade_exports_expected_symbols():
    assert callable(jarvis_brain.procesar_mensaje)
    assert callable(jarvis_brain.stream_procesar_mensaje_events)
    assert callable(jarvis_brain.necesita_tools)
    assert callable(jarvis_brain.get_system_msg)
    assert isinstance(jarvis_brain.DEFAULT_PROFILE_ID, str)


def test_necesita_tools_router_heuristics():
    assert jarvis_brain.necesita_tools("reproduce musica de coldplay") is True
    assert jarvis_brain.necesita_tools("hola jarvis") is False


def test_get_system_msg_returns_system_message_instance():
    msg = prompts.get_system_msg("Dime la estatura de Messi")
    assert msg is not None
    assert isinstance(getattr(msg, "content", ""), str)
    assert "J.A.R.V.I.S" in msg.content
