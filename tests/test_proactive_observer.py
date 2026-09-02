"""Tests for Proactive Desktop Intelligence module."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from core import jarvis_state
from modules.proactive.models import (
    AlertSeverity,
    DesktopContext,
    ProactiveAction,
    ProactiveAlert,
)
from modules.proactive.reasoner import ProactiveReasoner
from modules.proactive.service import ProactiveObserverService
from modules.proactive.tools import (
    consultar_estado_proactivo,
    ejecutar_accion_proactiva,
)
from modules.proactive.watcher import DesktopContextWatcher


def test_proactive_models():
    action = ProactiveAction(
        action_id="act123",
        title="Instalar paquete",
        command_or_tool="ejecutar_comando_terminal",
        arguments={"comando": "pip install requests"},
    )
    alert = ProactiveAlert(
        alert_id="alert123",
        key="py_missing",
        message="Falta requests",
        severity=AlertSeverity.WARNING,
        suggested_action=action,
    )
    data = alert.to_dict()
    assert data["alert_id"] == "alert123"
    assert data["key"] == "py_missing"
    assert data["severity"] == "warning"
    assert data["action"]["id"] == "act123"


def test_watcher_error_detection():
    watcher = DesktopContextWatcher()
    with patch.object(watcher, "get_foreground_window_info", return_value={"title": "cmd.exe", "process_name": "cmd.exe", "pid": 1234}):
        # Mocking psutil metrics
        with patch("psutil.cpu_percent", return_value=15.0), patch("psutil.virtual_memory") as mock_vm:
            mock_vm.return_value.percent = 40.0
            ctx = watcher.capture_context("ModuleNotFoundError: No module named 'fastapi'")
            assert ctx.is_terminal is True
            assert "modulenotfound" in ctx.detected_errors
            assert ctx.cpu_percent == 15.0


def test_reasoner_evaluates_and_respects_cooldown():
    reasoner = ProactiveReasoner(default_cooldown_seconds=100)

    ctx_err = DesktopContext(
        active_app="powershell.exe",
        window_title="PowerShell - ModuleNotFoundError: No module named 'uvicorn'",
        pid=123,
        detected_errors=("modulenotfound",),
    )

    alerts1 = reasoner.evaluate_context(ctx_err)
    assert len(alerts1) >= 1
    err_alert = next((a for a in alerts1 if a.key == "py_module_missing"), None)
    assert err_alert is not None
    assert "uvicorn" in err_alert.message
    assert err_alert.suggested_action is not None
    assert err_alert.suggested_action.arguments["comando"] == "pip install uvicorn"

    # Second evaluation within cooldown should produce 0 duplicate alerts
    alerts2 = reasoner.evaluate_context(ctx_err)
    assert len(alerts2) == 0


def test_service_scan_and_execute_action():
    mock_watcher = MagicMock(spec=DesktopContextWatcher)
    mock_watcher.capture_context.return_value = DesktopContext(
        active_app="code.exe",
        window_title="VS Code - npm ERR! code ELIFECYCLE",
        pid=456,
        detected_errors=("npmerror",),
    )

    invocations = []

    def invoke_action(tool_name, args, user_input, source, profile_id):
        invocations.append((tool_name, args, user_input, source, profile_id))
        return "npm dependencies reinstalled successfully"

    service = ProactiveObserverService(
        watcher=mock_watcher,
        action_invoker=invoke_action,
    )
    new_alerts = service.scan_cycle()
    assert len(new_alerts) >= 1
    active = service.get_active_alerts()
    assert len(active) >= 1

    action_id = active[0]["action"]["id"]

    result = service.execute_action(action_id, profile_id="profile_a")
    assert "ejecutada" in result
    assert "npm dependencies" in result
    assert len(invocations) == 1
    assert invocations[0][0] == "ejecutar_comando_terminal"
    assert invocations[0][3] == "proactive_action"
    assert invocations[0][4] == "profile_a"


def test_proactive_service_has_no_direct_tool_invocation():
    source = inspect.getsource(ProactiveObserverService.execute_action)
    assert ".invoke(" not in source


def test_proactive_tool_passes_active_profile(monkeypatch):
    captured = {}

    def execute_action(action_id, profile_id=None):
        captured["action_id"] = action_id
        captured["profile_id"] = profile_id
        return "accion simulada"

    monkeypatch.setattr(
        "modules.proactive.tools.proactive_service.execute_action",
        execute_action,
    )
    with jarvis_state.active_profile("profile_a"):
        result = ejecutar_accion_proactiva.invoke({"action_id": "action-1"})

    assert result == "accion simulada"
    assert captured == {"action_id": "action-1", "profile_id": "profile_a"}


def test_proactive_langchain_tools():
    out = consultar_estado_proactivo.invoke({})
    assert "Contexto del escritorio" in out

    out_exec = ejecutar_accion_proactiva.invoke({"action_id": "non_existent"})
    assert "No se encontro" in out_exec


def test_proactive_tools_in_base_catalog():
    from core import core_tools

    tools = core_tools.get_base_tools()
    tool_names = {t.name for t in tools}

    assert "consultar_estado_proactivo" in tool_names
    assert "ejecutar_accion_proactiva" in tool_names
