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


def test_full_mode_enables_optional_features():
    from core.jarvis_config import resolve_runtime_features  # pyright: ignore[reportMissingImports]

    flags = resolve_runtime_features({"JARVIS_CORE_MODE": "false"})

    assert flags.core_mode is False
    assert flags.voice_id_enabled is True
    assert flags.rag_enabled is True
    assert flags.vision_enabled is True
    assert flags.plugins_enabled is True
    assert flags.briefing_enabled is True
    assert flags.telegram_enabled is True


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
