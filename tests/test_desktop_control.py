from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "src", "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def test_tool_policy_covers_desktop_control_and_action_plans():
    from core.security.tool_policy import get_tool_policy

    expected = {
        "listar_ventanas": ("elevated", False),
        "enfocar_ventana": ("elevated", False),
        "controlar_ventana": ("elevated", False),
        "crear_plan_acciones": ("elevated", False),
        "ver_plan_acciones": ("elevated", False),
        "ejecutar_plan_acciones": ("critical", True),
    }

    for tool_name, (risk_level, requires_confirmation) in expected.items():
        policy = get_tool_policy(tool_name)
        assert policy.risk_level == risk_level
        assert policy.requires_confirmation is requires_confirmation
        assert policy.allowed_profiles == ("admin",)
        assert policy.audit_log is True


def test_action_plan_rejects_unknown_tools():
    from core.action_plans import create_action_plan

    with pytest.raises(ValueError, match="herramienta no permitida"):
        create_action_plan(
            "hacer algo inseguro",
            [{"tool": "ejecutar_powershell_libre", "args": {}, "description": "shell"}],
            created_by="test",
        )


def test_create_action_plan_tool_returns_widget_and_stores_plan():
    from core.action_plans import get_action_plan
    from tools.action_plan import crear_plan_acciones

    steps = [
        {
            "tool": "abrir_aplicacion",
            "args": {"nombre_app": "vscode"},
            "description": "Abrir VS Code",
        },
        {
            "tool": "listar_ventanas",
            "args": {"maximo": 5},
            "description": "Verificar ventanas abiertas",
        },
    ]

    out = crear_plan_acciones.invoke(
        {"objetivo": "preparar entorno de trabajo", "pasos_json": json.dumps(steps)}
    )

    assert "Plan de acciones preparado" in out
    assert "<WIDGET>" in out
    plan_id = out.split("ID: ", 1)[1].split(".", 1)[0].strip()
    plan = get_action_plan(plan_id)
    assert plan["status"] == "pending"
    assert plan["goal"] == "preparar entorno de trabajo"
    assert [step["tool"] for step in plan["steps"]] == ["abrir_aplicacion", "listar_ventanas"]


def test_execute_action_plan_requires_explicit_confirmar(monkeypatch):
    from core.action_plans import create_action_plan
    from core.service_container import services
    from tools.action_plan import ejecutar_plan_acciones

    plan = create_action_plan(
        "subir volumen",
        [{"tool": "ajustar_volumen", "args": {"nivel": 20}, "description": "Volumen 20"}],
        created_by="test",
    )
    monkeypatch.setattr(services, "invocar_tool", lambda *_args, **_kwargs: "ok")

    out = ejecutar_plan_acciones.invoke({"plan_id": plan["id"], "confirmar": False})

    assert "ACCESO_DENEGADO" in out
    assert "confirmacion explicita" in out.lower()


def test_execute_action_plan_invokes_steps_when_confirmed(monkeypatch):
    from core.action_plans import create_action_plan, get_action_plan
    from core.service_container import services
    from tools.action_plan import ejecutar_plan_acciones

    calls = []

    def fake_invoker(tool_name, args, user_input, source="unknown", profile_id=None):
        calls.append((tool_name, args, user_input, source, profile_id))
        return f"ok:{tool_name}"

    monkeypatch.setattr(services, "invocar_tool", fake_invoker)
    plan = create_action_plan(
        "modo trabajo",
        [
            {
                "tool": "ajustar_volumen",
                "args": {"nivel": 30},
                "description": "Volumen bajo",
            },
            {
                "tool": "listar_ventanas",
                "args": {"maximo": 3},
                "description": "Revisar ventanas",
            },
        ],
        created_by="test",
    )

    out = ejecutar_plan_acciones.invoke({"plan_id": plan["id"], "confirmar": True})

    assert "Plan ejecutado" in out
    assert [call[0] for call in calls] == ["ajustar_volumen", "listar_ventanas"]
    assert all(call[3] == "action_plan" for call in calls)
    stored = get_action_plan(plan["id"])
    assert stored["status"] == "completed"
    assert [step["status"] for step in stored["steps"]] == ["completed", "completed"]


def test_listar_ventanas_formats_snapshot(monkeypatch):
    from tools import desktop_control

    monkeypatch.setattr(
        desktop_control,
        "_window_snapshot",
        lambda maximo=10: [
            {"handle": 1001, "title": "Visual Studio Code", "pid": 42},
            {"handle": 1002, "title": "J.A.R.V.I.S.", "pid": 43},
        ][:maximo],
    )

    out = desktop_control.listar_ventanas.invoke({"maximo": 2})

    assert "Ventanas detectadas" in out
    assert "[1001] Visual Studio Code (PID 42)" in out
    assert "[1002] J.A.R.V.I.S. (PID 43)" in out


def test_find_window_matches_by_handle_or_title():
    from tools.desktop_control import _find_window

    windows = [
        {"handle": 1001, "title": "Visual Studio Code", "pid": 42},
        {"handle": 1002, "title": "J.A.R.V.I.S.", "pid": 43},
    ]

    assert _find_window("1002", windows)["title"] == "J.A.R.V.I.S."
    assert _find_window("studio", windows)["handle"] == 1001
    assert _find_window("missing", windows) is None


@pytest.fixture
def window_api(monkeypatch):
    import ctypes

    from tools import desktop_control

    api = MagicMock()
    api.IsWindow.return_value = True
    api.IsWindowVisible.return_value = True
    api.IsIconic.return_value = False
    api.IsZoomed.return_value = False
    api.GetForegroundWindow.return_value = 9999
    api.PostMessageW.return_value = True
    api.ShowWindow.return_value = 0  # Previous visibility, NOT an operation failure.

    def get_pid(_hwnd, pointer):
        pointer._obj.value = 42
        return 1

    api.GetWindowThreadProcessId.side_effect = get_pid
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=api), raising=False)
    monkeypatch.setattr(desktop_control, "IS_WINDOWS", True)
    monkeypatch.setattr(desktop_control, "WINDOW_VERIFY_SECONDS", 0, raising=False)
    monkeypatch.setattr(desktop_control, "_window_snapshot", lambda *_args, **_kwargs: [
        {"handle": 1001, "title": "JARVIS test window", "pid": 42},
    ])
    return api


def test_focus_does_not_claim_verified_success_when_windows_refuses(window_api):
    from tools import desktop_control

    result = desktop_control.enfocar_ventana.invoke({"titulo_o_id": "1001"})
    assert result.verified is False
    assert result.accepted is True
    assert "no pude confirmar" in result.user_message.lower()


def test_minimize_checks_postcondition_not_showwindow_return_value(window_api):
    from tools import desktop_control

    window_api.IsIconic.return_value = True
    result = desktop_control.controlar_ventana.invoke({"titulo_o_id": "1001", "accion": "minimizar"})
    assert result.verified is True
    assert result.accepted is False
    assert window_api.IsIconic.called


def test_close_only_confirms_request_acceptance_not_window_closure(window_api):
    from tools import desktop_control

    result = desktop_control.controlar_ventana.invoke({"titulo_o_id": "1001", "accion": "cerrar"})
    assert result.accepted is True
    assert result.verified is False
    assert "solicitud" in result.user_message.lower()


def test_close_rejection_is_failed(window_api):
    from tools import desktop_control

    window_api.PostMessageW.return_value = False
    result = desktop_control.controlar_ventana.invoke({"titulo_o_id": "1001", "accion": "cerrar"})
    assert result.status.value == "failed"
    assert result.accepted is False


def test_numeric_window_id_never_falls_back_to_title():
    from tools.desktop_control import _find_window

    assert _find_window("1002", [{"handle": 1001, "title": "report 1002"}]) is None


def test_ambiguous_window_title_does_not_send_close(monkeypatch, window_api):
    from tools import desktop_control

    monkeypatch.setattr(desktop_control, "_window_snapshot", lambda *_args, **_kwargs: [
        {"handle": 1001, "title": "Editor - first", "pid": 42},
        {"handle": 1002, "title": "Editor - second", "pid": 43},
    ])
    result = desktop_control.controlar_ventana.invoke({"titulo_o_id": "Editor", "accion": "cerrar"})
    assert result.status.value == "blocked"
    assert "identificador" in result.user_message.lower()
    window_api.PostMessageW.assert_not_called()


def test_reused_window_handle_does_not_act_on_another_process(window_api):
    from tools import desktop_control

    def different_pid(_hwnd, pointer):
        pointer._obj.value = 99
        return 1

    window_api.GetWindowThreadProcessId.side_effect = different_pid
    result = desktop_control.controlar_ventana.invoke({"titulo_o_id": "1001", "accion": "minimizar"})
    assert result.status.value == "unavailable"
    window_api.ShowWindow.assert_not_called()


def test_window_identity_is_rechecked_before_confirming_postcondition(window_api):
    from tools import desktop_control

    def reused_during_action(_hwnd, pointer):
        pointer._obj.value = 42 if window_api.GetWindowThreadProcessId.call_count == 1 else 99
        return 1

    window_api.GetWindowThreadProcessId.side_effect = reused_during_action
    window_api.IsIconic.return_value = True
    result = desktop_control.controlar_ventana.invoke({"titulo_o_id": "1001", "accion": "minimizar"})
    assert result.verified is False
    assert result.diagnostic_code == "window_target_changed"
