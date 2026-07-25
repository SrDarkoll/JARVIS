from __future__ import annotations

from pathlib import Path

from core.jarvis_config import resolve_runtime_features
from core.security.tool_policy import get_tool_policy
from tools import _get_base_tools_impl, system


def _tool_names() -> set[str]:
    return {str(getattr(tool, "name", "")) for tool in _get_base_tools_impl()}


def test_full_mode_keeps_voice_biometrics_disabled_without_explicit_opt_in():
    flags = resolve_runtime_features({"JARVIS_CORE_MODE": "false"})

    assert flags.voice_id_enabled is False


def test_voice_biometrics_can_still_be_enabled_explicitly():
    flags = resolve_runtime_features(
        {
            "JARVIS_CORE_MODE": "false",
            "JARVIS_VOICE_ID_ENABLED": "true",
        }
    )

    assert flags.voice_id_enabled is True


def test_dangerous_system_tools_are_not_registered_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_SYSTEM_TOOLS_ENABLED", raising=False)

    names = _tool_names()

    assert "crear_archivo_texto" not in names
    assert "ejecutar_comando_terminal" not in names


def test_dangerous_system_tools_require_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("JARVIS_SYSTEM_TOOLS_ENABLED", "true")

    names = _tool_names()

    assert "crear_archivo_texto" in names
    assert "ejecutar_comando_terminal" in names


def test_dangerous_system_tools_are_critical_and_require_confirmation():
    for tool_name in ("crear_archivo_texto", "ejecutar_comando_terminal"):
        policy = get_tool_policy(tool_name)
        assert policy.risk_level == "critical"
        assert policy.allowed_profiles == ("admin",)
        assert policy.requires_confirmation is True
        assert policy.audit_log is True


def test_text_file_tool_rejects_paths_outside_allowed_roots(
    monkeypatch,
    tmp_path: Path,
):
    allowed_root = tmp_path / "allowed"
    outside_path = tmp_path / "outside" / "blocked.txt"
    monkeypatch.setenv("JARVIS_SYSTEM_TOOLS_ENABLED", "true")
    monkeypatch.setenv("JARVIS_FILE_WRITE_ROOTS", str(allowed_root))
    monkeypatch.setattr(system, "verificar_autorizacion", lambda _pid: True)

    result = system.crear_archivo_texto.invoke(
        {
            "nombre_o_ruta": str(outside_path),
            "contenido": "must not be written",
        }
    )

    assert "ruta no permitida" in result.lower()
    assert not outside_path.exists()


def test_text_file_tool_writes_inside_allowed_root(monkeypatch, tmp_path: Path):
    allowed_root = tmp_path / "allowed"
    target = allowed_root / "notes" / "jarvis.txt"
    monkeypatch.setenv("JARVIS_SYSTEM_TOOLS_ENABLED", "true")
    monkeypatch.setenv("JARVIS_FILE_WRITE_ROOTS", str(allowed_root))
    monkeypatch.setattr(system, "verificar_autorizacion", lambda _pid: True)

    result = system.crear_archivo_texto.invoke(
        {
            "nombre_o_ruta": str(target),
            "contenido": "safe content",
        }
    )

    assert "exitosamente" in result.lower()
    assert target.read_text(encoding="utf-8") == "safe content"


def test_terminal_tool_is_disabled_even_when_invoked_directly(monkeypatch):
    monkeypatch.delenv("JARVIS_SYSTEM_TOOLS_ENABLED", raising=False)
    monkeypatch.setattr(system, "verificar_autorizacion", lambda _pid: True)

    result = system.ejecutar_comando_terminal.invoke({"comando": "Write-Output should-not-run"})

    assert "deshabilitada" in result.lower()
