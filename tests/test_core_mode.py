from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_core_mode_is_the_safe_default():
    from core.jarvis_config import resolve_runtime_features  # pyright: ignore[reportMissingImports]

    flags = resolve_runtime_features({})

    assert flags.core_mode is True
    assert flags.voice_id_enabled is False
    assert flags.rag_enabled is False
    assert flags.vision_enabled is False
    assert flags.plugins_enabled is False
    assert flags.briefing_enabled is False
    assert flags.telegram_enabled is False
    assert flags.monitoring_enabled is False
    assert flags.computer_use_enabled is False
    assert flags.proactive_tools_enabled is False
    assert flags.cognitive_memory_enabled is False
    assert flags.action_plans_enabled is False


def test_full_mode_enables_optional_features_except_voice_biometrics():
    from core.jarvis_config import resolve_runtime_features  # pyright: ignore[reportMissingImports]

    flags = resolve_runtime_features({"JARVIS_CORE_MODE": "false"})

    assert flags.core_mode is False
    assert flags.voice_id_enabled is False
    assert flags.rag_enabled is True
    assert flags.vision_enabled is True
    assert flags.plugins_enabled is True
    assert flags.briefing_enabled is True
    assert flags.telegram_enabled is True
    assert flags.monitoring_enabled is True
    assert flags.computer_use_enabled is True
    assert flags.proactive_tools_enabled is True
    assert flags.cognitive_memory_enabled is True
    assert flags.action_plans_enabled is True


def test_core_mode_allows_explicit_feature_override():
    from core.jarvis_config import resolve_runtime_features  # pyright: ignore[reportMissingImports]

    flags = resolve_runtime_features(
        {
            "JARVIS_CORE_MODE": "true",
            "JARVIS_VOICE_ID_ENABLED": "yes",
            "JARVIS_RAG_ENABLED": "1",
        }
    )

    assert flags.core_mode is True
    assert flags.voice_id_enabled is True
    assert flags.rag_enabled is True
    assert flags.vision_enabled is False


def test_core_mode_allows_explicit_monitoring_override():
    from core.jarvis_config import resolve_runtime_features  # pyright: ignore[reportMissingImports]

    flags = resolve_runtime_features({"JARVIS_CORE_MODE": "true", "JARVIS_MONITORING_ENABLED": "yes"})

    assert flags.core_mode is True
    assert flags.monitoring_enabled is True
    assert flags.computer_use_enabled is False
    assert flags.proactive_tools_enabled is False
    assert flags.cognitive_memory_enabled is False
    assert flags.action_plans_enabled is False


def test_core_tool_catalog_excludes_experimental_surfaces_by_default(monkeypatch):
    from tools import _get_base_tools_impl

    monkeypatch.setenv("JARVIS_CORE_MODE", "true")
    for flag in (
        "JARVIS_COMPUTER_USE_ENABLED",
        "JARVIS_PROACTIVE_TOOLS_ENABLED",
        "JARVIS_COGNITIVE_MEMORY_ENABLED",
        "JARVIS_ACTION_PLANS_ENABLED",
        "JARVIS_VISION_ENABLED",
        "JARVIS_PLUGINS_ENABLED",
    ):
        monkeypatch.delenv(flag, raising=False)

    names = {tool.name for tool in _get_base_tools_impl()}

    assert "reproducir_en_spotify" in names
    assert "poner_recordatorio" in names
    assert "computer_click" not in names
    assert "consultar_estado_proactivo" not in names
    assert "aprender_procedimiento" not in names
    assert "crear_plan_acciones" not in names
    assert "analizar_pantalla" not in names
    assert "recargar_plugins" not in names


def test_experimental_tool_surfaces_require_explicit_core_opt_in(monkeypatch):
    from tools import _get_base_tools_impl

    monkeypatch.setenv("JARVIS_CORE_MODE", "true")
    monkeypatch.setenv("JARVIS_COMPUTER_USE_ENABLED", "true")
    monkeypatch.setenv("JARVIS_PROACTIVE_TOOLS_ENABLED", "true")
    monkeypatch.setenv("JARVIS_COGNITIVE_MEMORY_ENABLED", "true")
    monkeypatch.setenv("JARVIS_ACTION_PLANS_ENABLED", "true")
    monkeypatch.setenv("JARVIS_VISION_ENABLED", "true")
    monkeypatch.setenv("JARVIS_PLUGINS_ENABLED", "true")

    names = {tool.name for tool in _get_base_tools_impl()}

    assert "computer_click" in names
    assert "consultar_estado_proactivo" in names
    assert "aprender_procedimiento" in names
    assert "crear_plan_acciones" in names
    assert "analizar_pantalla" in names
    assert "recargar_plugins" in names


def test_core_mode_does_not_import_speechbrain():
    env = os.environ.copy()
    env.pop("JARVIS_TEST_MODE", None)
    env["JARVIS_CORE_MODE"] = "true"
    env["PYTHONPATH"] = str(ROOT / "src" / "backend")
    script = """
import builtins
original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name.startswith("speechbrain"):
        raise AssertionError("speechbrain import attempted in core mode")
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
from voice.identifier import VOICE_ID_DISPONIBLE
assert VOICE_ID_DISPONIBLE is False
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_core_mode_does_not_start_rag_background_thread(monkeypatch):
    from engines import memory_rag  # pyright: ignore[reportMissingImports]

    def fail_thread(*_args, **_kwargs):
        raise AssertionError("RAG background thread started in core mode")

    monkeypatch.delenv("JARVIS_TEST_MODE", raising=False)
    monkeypatch.setattr(memory_rag, "RAG_ENABLED", False, raising=False)
    monkeypatch.setattr(memory_rag, "FAISS_DISPONIBLE", True)
    monkeypatch.setattr(memory_rag.threading, "Thread", fail_thread)

    motor = memory_rag.MemoryRAG()

    assert motor.lista is False


def test_core_mode_does_not_start_telegram_thread(monkeypatch):
    from services import telegram_manager as telegram_module  # pyright: ignore[reportMissingImports]

    def fail_thread(*_args, **_kwargs):
        raise AssertionError("Telegram thread started in core mode")

    monkeypatch.setattr(telegram_module, "TELEGRAM_ENABLED", False, raising=False)
    monkeypatch.setattr(telegram_module, "TELEGRAM_TOKEN", "test-token")
    monkeypatch.setattr(telegram_module, "TELEGRAM_CHAT_ID", 12345)
    monkeypatch.setattr(telegram_module.threading, "Thread", fail_thread)

    manager = telegram_module.TelegramManager()
    manager.start()

    assert manager.thread is None


def test_core_mode_does_not_import_telegram_package():
    env = os.environ.copy()
    env.pop("JARVIS_TEST_MODE", None)
    env["JARVIS_CORE_MODE"] = "true"
    env["PYTHONPATH"] = str(ROOT / "src" / "backend")
    script = """
import builtins
original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == "telegram" or name.startswith("telegram."):
        raise AssertionError("telegram package import attempted in core mode")
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
from services.telegram_manager import TELEGRAM_AVAILABLE, TelegramManager
assert TELEGRAM_AVAILABLE is False
manager = TelegramManager()
manager.start()
assert manager.thread is None
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_core_mode_monitoring_does_not_import_apscheduler():
    env = os.environ.copy()
    env.pop("JARVIS_TEST_MODE", None)
    env["JARVIS_CORE_MODE"] = "true"
    env["PYTHONPATH"] = str(ROOT / "src" / "backend")
    script = """
import builtins
original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == "apscheduler" or name.startswith("apscheduler."):
        raise AssertionError("apscheduler import attempted with monitoring disabled")
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
from services.monitoring_service import monitoring_service
assert monitoring_service.configured is False
assert monitoring_service._scheduler is None
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
