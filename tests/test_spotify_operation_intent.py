from types import SimpleNamespace

import pytest
from core.brain import tool_manager
from core.command_pipeline.execution import ToolExecutionService
from core.command_pipeline.models import ActionStep, CommandRequest, ReceiptStatus


@pytest.mark.parametrize("tool,text,expected", [
    ("agregar_a_cola_spotify", "Ahora Please Please Please", False),
    ("agregar_a_cola_spotify", "Reproduce Good 4 U", False),
    ("agregar_a_cola_spotify", "Añade Please Please Please a la cola", True),
    ("agregar_a_cola_spotify", "Pon esta canción en la cola", True),
    ("agregar_a_cola_spotify", "No agregues esta a la cola", False),
    ("agregar_a_cola_spotify", "El mensaje dice añade esta a la cola", False),
    ("agregar_a_cola_spotify", "Can you add Good 4 U to the queue?", True),
    ("dar_like_spotify", "Me gusta esta canción", False),
    ("dar_like_spotify", "Dale like a esta canción", True),
    ("dar_like_spotify", "Guarda esta en mis favoritos", True),
    ("dar_like_spotify", "No guardes esta en favoritos", False),
    ("dar_like_spotify", "Please save this song to my liked songs", True),
    ("quitar_like_spotify", "Dale like a esta canción", False),
    ("quitar_like_spotify", "Quita esta canción de mis Me Gusta", True),
    ("quitar_like_spotify", "Unlike this song", True),
    ("quitar_like_spotify", "Do not unlike this song", False),
])
@pytest.mark.parametrize("channel", ["gemini_live", "live_voice", "planner", "router", "stream"])
def test_spotify_mutations_require_the_requested_operation(tool, text, expected, channel):
    assert tool_manager._tool_permitida_por_contexto(tool, text, channel) is expected


def test_wrong_operation_is_explained_without_claiming_a_security_permission_problem(monkeypatch):
    calls = []
    fake = SimpleNamespace(invoke=lambda args: calls.append(args))
    monkeypatch.setattr(tool_manager.security_manager, "_security_guard", lambda *_args, **_kwargs: (True, ""))
    request = CommandRequest.create(text="Reproduce Good 4 U", profile_id="admin", channel="gemini_live", language="es",
                                    metadata={"_tool_map": {"agregar_a_cola_spotify": fake}})
    receipt = ToolExecutionService(tool_manager._invoke_tool_once).execute(
        request, ActionStep("wrong-operation", "agregar_a_cola_spotify", {"cancion": "Good 4 U"}),
    )
    assert receipt.status is ReceiptStatus.BLOCKED
    assert receipt.diagnostic_code == "spotify_operation_intent_required"
    assert "operación" in receipt.user_message
    assert "seguridad" not in receipt.user_message
    assert calls == []


@pytest.mark.parametrize("action", ["like", "guardar", "unlike", "quitar de favoritos", "guardar   canción", "quitar\tlike"])
def test_control_aliases_cannot_bypass_library_intent_checks(monkeypatch, action):
    calls = []
    fake = SimpleNamespace(invoke=lambda args: calls.append(args))
    monkeypatch.setattr(tool_manager.security_manager, "_security_guard", lambda *_args, **_kwargs: (True, ""))
    request = CommandRequest.create(text="Reproduce Good 4 U", profile_id="admin", channel="gemini_live",
                                    metadata={"_tool_map": {"controlar_reproduccion": fake}})
    receipt = ToolExecutionService(tool_manager._invoke_tool_once).execute(
        request, ActionStep("alias", "controlar_reproduccion", {"accion": action}),
    )
    assert receipt.status is ReceiptStatus.BLOCKED
    assert calls == []


def test_control_like_alias_uses_the_elevated_library_policy(monkeypatch):
    guards = []
    fake = SimpleNamespace(invoke=lambda args: "ok")
    def guard(name, *_args, **_kwargs):
        guards.append(name)
        return True, ""
    monkeypatch.setattr(tool_manager.security_manager, "_security_guard", guard)
    monkeypatch.setattr(tool_manager, "AUTOCURACION_ACTIVA", False)
    request = CommandRequest.create(text="Dale like a esta canción", profile_id="admin", channel="gemini_live",
                                    metadata={"_tool_map": {"controlar_reproduccion": fake}})
    ToolExecutionService(tool_manager._invoke_tool_once).execute(
        request, ActionStep("like-alias", "controlar_reproduccion", {"accion": "like"}),
    )
    assert guards == ["dar_like_spotify"]
