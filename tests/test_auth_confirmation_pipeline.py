from __future__ import annotations

from core.brain import history_manager, processor, security_engine, tool_manager
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


def test_pending_auth_confirmation_treats_mixed_negation_as_cancel():
    history_manager._registrar_accion_pendiente_auth(
        "admin",
        "borrar_memoria",
        {"confirmar": True},
        "borra la memoria",
    )

    reply, should_listen = processor._resolve_pending_auth_confirmation("sí, pero no", "admin")

    assert "cancelada" in reply.lower() or "cancelled" in reply.lower()
    assert should_listen is False
    assert history_manager._extraer_accion_pendiente_auth("admin") is None


def test_unconfirmed_critical_tool_registers_pending_action(monkeypatch):
    history_manager._extraer_accion_pendiente_auth("admin", pop=True)
    monkeypatch.setattr(tool_manager, "_tool_permitida_por_contexto", lambda *_args: True)
    monkeypatch.setattr(
        tool_manager.security_manager,
        "_security_guard",
        lambda *_args, **_kwargs: (
            False,
            "La herramienta requiere confirmacion explicita.",
        ),
    )

    try:
        result = tool_manager._invocar_tool_entry(
            "ejecutar_procedimiento",
            {"nombre": "rutina segura"},
            "ejecuta el procedimiento rutina segura",
            "llm_loop",
            "admin",
        )
        pending = history_manager._extraer_accion_pendiente_auth("admin")

        assert str(result).startswith("ACCESS_DENIED:")
        assert "ejecutar_procedimiento" in str(result)
        assert "rutina segura" in str(result)
        assert pending is not None
        assert pending["tool"] == "ejecutar_procedimiento"
        assert pending["args"] == {"nombre": "rutina segura"}
        assert pending["user_input"] == "ejecuta el procedimiento rutina segura"
    finally:
        history_manager._extraer_accion_pendiente_auth("admin", pop=True)


def test_default_command_pipeline_resumes_a_pending_confirmation(monkeypatch):
    history_manager._extraer_accion_pendiente_auth("admin", pop=True)
    executed = {}

    def fake_invocar(tool_name, args, user_input, source, profile_id):
        executed.update(tool=tool_name, args=args, source=source, profile_id=profile_id)
        return "acción ejecutada"

    monkeypatch.setattr(tool_manager, "_invocar_tool_entry", fake_invocar)
    monkeypatch.setattr(processor, "_record_pipeline_response", lambda *_args: None)
    monkeypatch.setattr(
        processor,
        "_get_command_orchestrator",
        lambda: (_ for _ in ()).throw(AssertionError("orchestrator must not run")),
    )
    history_manager._registrar_accion_pendiente_auth(
        "admin",
        "matar_proceso",
        {"nombre_proceso": "notepad.exe"},
        "mata notepad",
    )

    try:
        reply, should_listen = processor.procesar_mensaje(
            "si, hazlo",
            profile_id="admin",
            count_inbound=False,
        )
        assert reply == "acción ejecutada"
        assert should_listen is False
        assert executed["tool"] == "matar_proceso"
        assert executed["source"] == "auth_resume"
        assert history_manager._extraer_accion_pendiente_auth("admin") is None
    finally:
        history_manager._extraer_accion_pendiente_auth("admin", pop=True)


def test_confirmation_markers_are_not_forwarded_to_tool_schema(monkeypatch):
    captured = {}

    class FakeTool:
        name = "declared_args_only"

        @staticmethod
        def invoke(args):
            captured.update(args)
            return "ok"

    monkeypatch.setattr(tool_manager, "_tool_permitida_por_contexto", lambda *_args: True)
    monkeypatch.setattr(
        tool_manager.security_manager,
        "_security_guard",
        lambda *_args, **_kwargs: (True, ""),
    )
    monkeypatch.setattr(security_engine, "_tool_requiere_autorizacion", lambda _name: False)

    result = tool_manager._invocar_tool(
        {
            "id": "confirmed-step",
            "name": "declared_args_only",
            "args": {
                "value": 7,
                "_confirmed": True,
                "confirmed": True,
                "el_usuario_ya_confirmo": True,
            },
        },
        {"declared_args_only": FakeTool()},
        {
            "user_input": "confirmo",
            "source": "auth_resume",
            "profile_id": "admin",
            "request_id": "confirmation-strip-test",
        },
    )

    assert result == "ok"
    assert captured == {"value": 7}
