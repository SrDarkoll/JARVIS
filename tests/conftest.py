from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "src" / "backend"
TEST_RUNTIME_PARENT = ROOT / "scratch" / "pytest_runtime"
TEST_RUNTIME_PARENT.mkdir(parents=True, exist_ok=True)
TEST_RUNTIME_DIR = Path(
    tempfile.mkdtemp(prefix="jarvis_tests_", dir=TEST_RUNTIME_PARENT)
)
TEST_TMP_DIR = TEST_RUNTIME_DIR / "tmp"
TEST_TMP_DIR.mkdir(parents=True, exist_ok=True)
for temp_name in ("TEMP", "TMP", "TMPDIR"):
    os.environ[temp_name] = str(TEST_TMP_DIR)
tempfile.tempdir = str(TEST_TMP_DIR)

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("GROQ_API_KEY", "test-key-smoke")
os.environ["JARVIS_TEST_MODE"] = "1"
os.environ["JARVIS_AUTOCURACION"] = "false"
os.environ["JARVIS_RUNTIME_DIR"] = str(TEST_RUNTIME_DIR)
os.environ["JARVIS_DB_PATH"] = str(TEST_RUNTIME_DIR / "memoria_jarvis_test.db")
os.environ["JARVIS_CACHE_DIR"] = str(TEST_RUNTIME_DIR / ".cache")
os.environ["JARVIS_FAISS_DIR"] = str(TEST_RUNTIME_DIR / "faiss_index")
os.environ["JARVIS_HF_CACHE"] = str(TEST_RUNTIME_DIR / ".cache" / "huggingface")
os.environ.setdefault("WANDB_MODE", "disabled")
os.environ.setdefault("WANDB_DISABLED", "true")
os.environ["WANDB_DIR"] = str(TEST_RUNTIME_DIR / "wandb")
os.environ["WANDB_CACHE_DIR"] = str(TEST_RUNTIME_DIR / "wandb" / "cache")
os.environ["WANDB_CONFIG_DIR"] = str(TEST_RUNTIME_DIR / "wandb" / "config")
os.environ["WANDB_ARTIFACT_DIR"] = str(TEST_RUNTIME_DIR / "wandb" / "artifacts")


def _cleanup_runtime_dir() -> None:
    shutil.rmtree(TEST_RUNTIME_DIR, ignore_errors=True)


atexit.register(_cleanup_runtime_dir)


@pytest.fixture(autouse=True)
def _no_browser_tabs(monkeypatch):
    monkeypatch.setattr("webbrowser.open", lambda *a, **kw: False)


@pytest.fixture(autouse=True)
def _restore_mutable_runtime_state():
    try:
        from core import jarvis_state
        from utils import jarvis_auth

        reminders = list(jarvis_state._recordatorios)
        profiles = {
            pid: {
                "history": list((pdata or {}).get("history", [])),
                "facts": (pdata or {}).get("facts", ""),
            }
            for pid, pdata in jarvis_state._perfiles_memoria.items()
        }
        counters = dict(jarvis_state._msg_counter_by_profile)
        active_profile = jarvis_state.get_active_profile_id()
        auth_state = dict(jarvis_auth._auth_state)
    except Exception:
        yield
        return

    yield

    jarvis_state._recordatorios[:] = reminders
    jarvis_state._perfiles_memoria.clear()
    jarvis_state._perfiles_memoria.update(profiles)
    jarvis_state._msg_counter_by_profile.clear()
    jarvis_state._msg_counter_by_profile.update(counters)
    jarvis_state.set_active_profile_id(active_profile)
    with jarvis_auth._auth_lock:
        jarvis_auth._auth_state.clear()
        jarvis_auth._auth_state.update(auth_state)
