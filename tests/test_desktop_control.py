from __future__ import annotations

import json
import os
import sys

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
        "controlar_ventana": ("critical", True),
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
