from __future__ import annotations

from core.brain import history_manager, processor, tool_manager
from core.security.tool_policy import get_tool_policy


def test_analizar_pantalla_policy_is_elevated_and_unblocked():
    policy = get_tool_policy("analizar_pantalla")
    assert policy.risk_level == "elevated"
    assert policy.requires_confirmation is False
    assert policy.allowed_profiles == ("admin",)
    assert policy.audit_log is True


def test_pending_auth_confirmation_executes_on_affirmative_response(monkeypatch):
    executed = {}

    def mock_invocar_tool(tool_name, args, user_input, source, profile_id):
        executed["tool"] = tool_name
        executed["args"] = args
        executed["source"] = source
        executed["profile_id"] = profile_id
        return "Acción ejecutada correctamente."

    monkeypatch.setattr(tool_manager, "_invocar_tool_entry", mock_invocar_tool)

    history_manager._registrar_accion_pendiente_auth(
        "admin",
        "matar_proceso",
        {"nombre_proceso": "notepad.exe"},
        "mata el notepad",
    )

    reply, should_listen = processor._resolve_pending_auth_confirmation("Sí, hazlo", "admin")

    assert reply == "Acción ejecutada correctamente."
    assert should_listen is False
    assert executed["tool"] == "matar_proceso"
    assert executed["args"]["nombre_proceso"] == "notepad.exe"
    assert executed["args"]["_confirmed"] is True
    assert executed["source"] == "auth_resume"
    assert executed["profile_id"] == "admin"
    assert history_manager._extraer_accion_pendiente_auth("admin") is None


def test_pending_auth_confirmation_cancels_on_negative_response():
    history_manager._registrar_accion_pendiente_auth(
        "admin",
        "borrar_memoria",
        {"confirmar": True},
        "borra la memoria",
    )

    reply, should_listen = processor._resolve_pending_auth_confirmation("no, cancela eso", "admin")

    assert "cancelada" in reply.lower() or "cancelled" in reply.lower()
    assert should_listen is False
    assert history_manager._extraer_accion_pendiente_auth("admin") is None
