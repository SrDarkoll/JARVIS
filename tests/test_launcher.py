from __future__ import annotations

import ast
from pathlib import Path

import pytest

import start_app


ROOT = Path(__file__).resolve().parent.parent


def test_project_python_uses_windows_venv_layout(tmp_path):
    candidate = tmp_path / "venv" / "Scripts" / "python.exe"
    candidate.parent.mkdir(parents=True)
    candidate.touch()

    assert start_app._project_venv_python(tmp_path, platform="win32") == candidate


def test_project_python_uses_posix_venv_layout(tmp_path):
    candidate = tmp_path / "venv" / "bin" / "python"
    candidate.parent.mkdir(parents=True)
    candidate.touch()

    assert start_app._project_venv_python(tmp_path, platform="linux") == candidate


def test_relaunch_skips_current_project_interpreter(monkeypatch, tmp_path):
    candidate = tmp_path / "venv" / "Scripts" / "python.exe"
    candidate.parent.mkdir(parents=True)
    candidate.touch()
    monkeypatch.delenv("JARVIS_SKIP_VENV_REEXEC", raising=False)
    monkeypatch.setattr(start_app.sys, "executable", str(candidate))
    monkeypatch.setattr(start_app.sys, "platform", "win32")
    monkeypatch.setattr(
        start_app.os,
        "execv",
        lambda *_args: pytest.fail("execv must not run for the active interpreter"),
    )

    assert start_app._relaunch_in_project_venv(tmp_path) is False


def test_relaunch_preserves_script_arguments(monkeypatch, tmp_path):
    candidate = tmp_path / "venv" / "bin" / "python"
    candidate.parent.mkdir(parents=True)
    candidate.touch()
    observed = {}
    monkeypatch.delenv("JARVIS_SKIP_VENV_REEXEC", raising=False)
    monkeypatch.setattr(start_app.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(start_app.sys, "platform", "linux")
    monkeypatch.setattr(start_app.sys, "argv", ["start_app.py", "--demo", "value"])

    def record_execv(executable, arguments):
        observed["executable"] = executable
        observed["arguments"] = arguments

    monkeypatch.setattr(start_app.os, "execv", record_execv)

    assert start_app._relaunch_in_project_venv(tmp_path) is True
    assert observed == {
        "executable": str(candidate),
        "arguments": [
            str(candidate),
            str(tmp_path / "start_app.py"),
            "--demo",
            "value",
        ],
    }


def test_relaunch_honors_explicit_skip_override(monkeypatch, tmp_path):
    candidate = tmp_path / "venv" / "Scripts" / "python.exe"
    candidate.parent.mkdir(parents=True)
    candidate.touch()
    monkeypatch.setenv("JARVIS_SKIP_VENV_REEXEC", "true")
    monkeypatch.setattr(start_app.sys, "platform", "win32")
    monkeypatch.setattr(
        start_app.os,
        "execv",
        lambda *_args: pytest.fail("execv must not run when explicitly disabled"),
    )

    assert start_app._relaunch_in_project_venv(tmp_path) is False
    assert start_app._project_environment_error(tmp_path) is None


def test_global_python_without_project_venv_gets_setup_guidance(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("JARVIS_SKIP_VENV_REEXEC", raising=False)
    monkeypatch.setattr(start_app.sys, "prefix", "C:/Python312")
    monkeypatch.setattr(start_app.sys, "base_prefix", "C:/Python312")

    message = start_app._project_environment_error(tmp_path)

    assert message is not None
    assert "setup.ps1" in message
    assert "setup.sh" in message


def test_active_virtual_environment_does_not_require_project_venv(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("JARVIS_SKIP_VENV_REEXEC", raising=False)
    monkeypatch.setattr(start_app.sys, "prefix", str(tmp_path / "active"))
    monkeypatch.setattr(start_app.sys, "base_prefix", "C:/Python312")

    assert start_app._project_environment_error(tmp_path) is None


def test_script_bootstrap_precedes_third_party_imports():
    tree = ast.parse((ROOT / "start_app.py").read_text(encoding="utf-8"))
    bootstrap_line = min(
        node.lineno
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    )
    third_party_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and any(
            alias.name.split(".", 1)[0] in {"requests", "webview"}
            for alias in node.names
        )
    ]

    assert third_party_lines
    assert bootstrap_line < min(third_party_lines)
