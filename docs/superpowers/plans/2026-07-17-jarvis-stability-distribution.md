# JARVIS Stability and Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make JARVIS core mode deterministic, resilient to missing configuration and unwritable paths, independent from the user's global Python installation, and reproducibly installable from a clean clone.

**Architecture:** Keep `RuntimeFeatures` as the single source of truth for optional-service lifecycle. Replace duplicated or deprecated behavior with small standard-library/FFmpeg adapters, preserve public behavior with focused tests, and verify the shipped core dependency set in a dedicated virtual environment rather than the user's global Python.

**Tech Stack:** Python 3.11/3.12, Quart/Hypercorn, pytest, Ruff, pip-audit, vanilla JavaScript, FFmpeg, Groq OpenAI-compatible API, Spotify/Spotipy OAuth.

---

## File Map

- `src/backend/core/jarvis_config.py`: runtime feature and provider defaults.
- `src/backend/services/monitoring_service.py`: optional APScheduler lifecycle.
- `src/backend/api/status_routes.py`: externally visible runtime feature status.
- `tests/conftest.py`: isolated test runtime and temporary directory policy.
- `src/backend/core/desktop_session.py`: persistent desktop session with temporary fallback.
- `start_app.py`: project-venv selection and desktop/backend startup.
- `setup.ps1`, `setup.sh`: deterministic virtual-environment installation.
- `src/backend/core/errors.py`: stable exceptions shared between brain and API layers.
- `src/backend/core/brain/processor.py`: explicit unavailable-LLM behavior.
- `src/backend/api/chat_routes.py`: controlled classic and streaming error contracts.
- `src/backend/utils/audio_conversion.py`: shared FFmpeg conversions replacing pydub.
- `src/backend/voice/pipeline.py`: browser audio normalization consumer.
- `src/backend/voice/identifier.py`: voice-biometric preprocessing consumer.
- `src/backend/services/telegram_manager.py`: WAV-to-OGG/Opus consumer.
- `src/backend/tools/spotify.py`: supported Spotipy token/cache APIs and OAuth validation.
- `jarvis_settings.py`: Groq-only prompt configuration.
- `requirements.txt`, `requirements-dev.txt`: supported runtime and release-check dependencies.
- `.env.example`, `README.md`, `AGENTS.md`: distribution contract and verification commands.

## Verified External Decisions

- Groq currently documents `qwen/qwen3.6-27b` as a tool-capable text/vision model, so the existing model default remains.
- Spotify no longer accepts `localhost` redirect aliases. The supported default becomes `http://127.0.0.1:8888/callback`; port 8888 avoids collision with the JARVIS backend on 5002.
- Spotipy 2.26 marks `get_access_token(as_dict=...)` and direct `SpotifyOAuth.get_cached_token()` use as deprecated. Callers will consume the current return type and its configured cache handler.
- Python removed `audioop` in 3.13, while pydub 0.25.1 has not released since 2021. Project-owned pydub calls will be replaced by FFmpeg, which is already a documented prerequisite.
- Existing memory compatibility shims still have active consumers and will remain.

Primary references:

- https://console.groq.com/docs/model/qwen/qwen3.6-27b
- https://developer.spotify.com/documentation/web-api/concepts/redirect_uri
- https://developer.spotify.com/documentation/web-api/tutorials/migration-insecure-redirect-uri
- https://docs.python.org/3.13/library/audioop.html
- https://pypi.org/project/pydub/

### Task 1: Make monitoring an explicit runtime feature

**Files:**
- Modify: `src/backend/core/jarvis_config.py:20-93`
- Modify: `src/backend/services/monitoring_service.py:11-35`
- Modify: `src/backend/api/status_routes.py:76-87`
- Modify: `tests/test_core_mode.py:12-55`
- Create: `tests/test_monitoring_service.py`
- Modify: `.env.example:13-23`

- [ ] **Step 1: Write failing feature-resolution tests**

Add `monitoring_enabled` assertions to the three runtime-feature tests:

```python
def test_core_mode_is_the_safe_default():
    from core.jarvis_config import resolve_runtime_features

    flags = resolve_runtime_features({})

    assert flags.core_mode is True
    assert flags.voice_id_enabled is False
    assert flags.rag_enabled is False
    assert flags.vision_enabled is False
    assert flags.plugins_enabled is False
    assert flags.briefing_enabled is False
    assert flags.telegram_enabled is False
    assert flags.monitoring_enabled is False


def test_full_mode_enables_optional_features():
    from core.jarvis_config import resolve_runtime_features

    flags = resolve_runtime_features({"JARVIS_CORE_MODE": "false"})

    assert flags.core_mode is False
    assert flags.voice_id_enabled is True
    assert flags.rag_enabled is True
    assert flags.vision_enabled is True
    assert flags.plugins_enabled is True
    assert flags.briefing_enabled is True
    assert flags.telegram_enabled is True
    assert flags.monitoring_enabled is True


def test_core_mode_allows_explicit_monitoring_override():
    from core.jarvis_config import resolve_runtime_features

    flags = resolve_runtime_features(
        {"JARVIS_CORE_MODE": "true", "JARVIS_MONITORING_ENABLED": "yes"}
    )

    assert flags.core_mode is True
    assert flags.monitoring_enabled is True
```

- [ ] **Step 2: Write failing scheduler-construction tests**

Create `tests/test_monitoring_service.py`:

```python
from services import monitoring_service as monitoring_module


def test_disabled_monitoring_never_constructs_scheduler(monkeypatch):
    created = []

    class FakeScheduler:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(monitoring_module, "SCHEDULER_AVAILABLE", True)
    monkeypatch.setattr(monitoring_module, "BackgroundScheduler", FakeScheduler)

    service = monitoring_module.MonitoringService(enabled=False)

    assert service._scheduler is None
    assert created == []
    assert service.start_heartbeat() is False


def test_enabled_monitoring_constructs_available_scheduler(monkeypatch):
    created = []

    class FakeScheduler:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(monitoring_module, "SCHEDULER_AVAILABLE", True)
    monkeypatch.setattr(monitoring_module, "BackgroundScheduler", FakeScheduler)

    service = monitoring_module.MonitoringService(enabled=True)

    assert isinstance(service._scheduler, FakeScheduler)
    assert created == [{"daemon": True}]
```

- [ ] **Step 3: Run the focused tests and confirm the regression**

Run:

```powershell
python -m pytest tests\test_core_mode.py tests\test_monitoring_service.py tests\test_smoke.py::test_monitoring_service_scheduler -q
```

Expected: failures because `RuntimeFeatures` has no `monitoring_enabled` field and `MonitoringService` has no `enabled` argument.

- [ ] **Step 4: Implement the feature flag and gate scheduler construction**

Add the field and resolver entry in `jarvis_config.py`:

```python
@dataclass(frozen=True)
class RuntimeFeatures:
    core_mode: bool
    voice_id_enabled: bool
    rag_enabled: bool
    vision_enabled: bool
    plugins_enabled: bool
    briefing_enabled: bool
    telegram_enabled: bool
    monitoring_enabled: bool


def resolve_runtime_features(env: Mapping[str, str] | None = None) -> RuntimeFeatures:
    source = os.environ if env is None else env
    core_mode = _read_bool(source, "JARVIS_CORE_MODE", True)
    optional_default = not core_mode
    return RuntimeFeatures(
        core_mode=core_mode,
        voice_id_enabled=_read_bool(source, "JARVIS_VOICE_ID_ENABLED", optional_default),
        rag_enabled=_read_bool(source, "JARVIS_RAG_ENABLED", optional_default),
        vision_enabled=_read_bool(source, "JARVIS_VISION_ENABLED", optional_default),
        plugins_enabled=_read_bool(source, "JARVIS_PLUGINS_ENABLED", optional_default),
        briefing_enabled=_read_bool(source, "JARVIS_BRIEFING_ENABLED", optional_default),
        telegram_enabled=_read_bool(source, "JARVIS_TELEGRAM_ENABLED", optional_default),
        monitoring_enabled=_read_bool(source, "JARVIS_MONITORING_ENABLED", optional_default),
    )
```

Export `MONITORING_ENABLED = RUNTIME_FEATURES.monitoring_enabled`. Update the monitoring service constructor and start guard:

```python
from core.jarvis_config import BRIEFING_HORA, HEARTBEAT_INTERVALO, MONITORING_ENABLED


class MonitoringService:
    def __init__(self, *, enabled: bool = MONITORING_ENABLED):
        self._enabled = bool(enabled)
        self._telegram_manager = None
        self._brain_state = None
        self._security_manager = None
        self._ejecutar_briefing_func = None
        self._check_briefing_sent_func = None
        self._scheduler = (
            BackgroundScheduler(daemon=True)
            if self._enabled and SCHEDULER_AVAILABLE
            else None
        )

    def start_heartbeat(self, ip_cleanup_func=None):
        if not self._enabled:
            print("[SCHEDULER] Background monitoring is disabled by runtime configuration.")
            return False
        if not SCHEDULER_AVAILABLE or self._scheduler is None:
            print("[SCHEDULER] APScheduler is not installed; background monitoring is disabled.")
            return False
```

Keep the existing job-registration body after those guards. Add `"monitoring": RUNTIME_FEATURES.monitoring_enabled` to `/api/status` and `JARVIS_MONITORING_ENABLED=""` to `.env.example`.

- [ ] **Step 5: Run focused tests**

Run the Step 3 command again.

Expected: all selected tests pass; the global service has `_scheduler is None` in default core mode even when APScheduler is installed.

- [ ] **Step 6: Commit the monitoring fix**

```powershell
git add src/backend/core/jarvis_config.py src/backend/services/monitoring_service.py src/backend/api/status_routes.py tests/test_core_mode.py tests/test_monitoring_service.py .env.example
git commit -m "fix: gate monitoring by runtime configuration"
```

### Task 2: Isolate every pytest temporary path

**Files:**
- Modify: `tests/conftest.py:12-45`
- Modify: `tests/test_smoke.py:1665-1676`
- Create: `tests/test_test_runtime.py`

- [ ] **Step 1: Add a test that requires repo-local temporary paths**

Create `tests/test_test_runtime.py`:

```python
from pathlib import Path

from conftest import ROOT, TEST_TMP_DIR


def test_pytest_tmp_path_uses_isolated_repo_runtime(tmp_path):
    resolved_tmp = Path(tmp_path).resolve()
    resolved_runtime = TEST_TMP_DIR.resolve()

    assert resolved_runtime == resolved_tmp or resolved_runtime in resolved_tmp.parents
    assert (ROOT / "scratch").resolve() in resolved_tmp.parents
```

Update the desktop-session smoke test to stop reusing `scratch/desktop_session_test`:

```python
def test_desktop_session_uses_stable_localhost_origin(monkeypatch, tmp_path):
    from core.desktop_session import load_desktop_session

    desktop_home = tmp_path / "desktop-home"
    monkeypatch.setenv("JARVIS_DESKTOP_HOME", str(desktop_home))
    session = load_desktop_session(port=5002)

    assert session.origin == "http://localhost:5002"
    assert str(desktop_home) in session.webview_storage_dir
    assert session.persist_permissions is True
```

- [ ] **Step 2: Reproduce the current Windows temp failure**

Run:

```powershell
python -m pytest tests\test_test_runtime.py tests\test_smoke.py::test_desktop_session_uses_stable_localhost_origin tests\test_briefing_resilience.py -q
```

Expected before the fix: `tmp_path` may attempt to create under the global `%TEMP%`, and the new locality assertion fails.

- [ ] **Step 3: Bind Python and subprocess temp APIs to the test runtime**

Immediately after `TEST_TMP_DIR.mkdir(...)` in `tests/conftest.py`, add:

```python
for temp_name in ("TEMP", "TMP", "TMPDIR"):
    os.environ[temp_name] = str(TEST_TMP_DIR)
tempfile.tempdir = str(TEST_TMP_DIR)
```

This must execute before fixtures request `tmp_path`. Keep the per-process `jarvis_tests_{os.getpid()}` directory and existing `atexit` cleanup.

- [ ] **Step 4: Verify repeatability**

Run the Step 2 command twice.

Expected: both runs pass and create no reusable `scratch/desktop_session_test` directory.

- [ ] **Step 5: Commit test isolation**

```powershell
git add tests/conftest.py tests/test_smoke.py tests/test_test_runtime.py
git commit -m "test: isolate runtime and temporary paths"
```

### Task 3: Make desktop session persistence non-fatal

**Files:**
- Modify: `src/backend/core/desktop_session.py:5-81`
- Create: `tests/test_desktop_session.py`

- [ ] **Step 1: Write failure and atomic-write tests**

Create `tests/test_desktop_session.py`:

```python
import json
from pathlib import Path

from core import desktop_session


def test_session_metadata_is_written_as_valid_json(monkeypatch, tmp_path):
    home = tmp_path / "persistent"
    monkeypatch.setenv("JARVIS_DESKTOP_HOME", str(home))

    session = desktop_session.load_desktop_session(port=5002)
    payload = json.loads(Path(session.session_file).read_text(encoding="utf-8"))

    assert payload["origin"] == "http://localhost:5002"
    assert payload["persist_permissions"] is True
    session_directory = Path(session.session_file).parent
    assert list(session_directory.glob(".desktop-session-*.tmp")) == []


def test_unwritable_preferred_storage_uses_temporary_fallback(monkeypatch, tmp_path):
    blocked_home = tmp_path / "blocked"
    real_ensure = desktop_session._ensure_directory

    def selective_ensure(path: str) -> bool:
        if str(blocked_home) in str(path):
            return False
        return real_ensure(path)

    monkeypatch.setenv("JARVIS_DESKTOP_HOME", str(blocked_home))
    monkeypatch.setattr(desktop_session, "_ensure_directory", selective_ensure)
    monkeypatch.setattr(desktop_session.tempfile, "gettempdir", lambda: str(tmp_path))

    session = desktop_session.load_desktop_session(port=5002)

    assert str(blocked_home) not in session.webview_storage_dir
    assert session.persist_permissions is False
    assert Path(session.webview_storage_dir).is_dir()


def test_session_write_failure_disables_permission_persistence(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DESKTOP_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(desktop_session, "_write_session", lambda *_args: False)

    session = desktop_session.load_desktop_session(port=5002)

    assert session.persist_permissions is False
```

- [ ] **Step 2: Run tests to confirm missing helpers and uncaught failures**

```powershell
python -m pytest tests\test_desktop_session.py tests\test_smoke.py::test_desktop_session_uses_stable_localhost_origin -q
```

Expected: failure because `_ensure_directory` is absent and `_write_session` does not report persistence failure.

- [ ] **Step 3: Add atomic writes and fallback selection**

Replace the persistence helpers and `load_desktop_session` with this contract:

```python
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime

logger = logging.getLogger(__name__)


def _ensure_directory(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError:
        return False


def _fallback_home() -> str:
    return os.path.join(tempfile.gettempdir(), "JARVIS", "desktop-fallback")


def _write_session(path: str, payload: dict) -> bool:
    directory = os.path.dirname(path)
    if not _ensure_directory(directory):
        return False
    file_descriptor = -1
    temp_path = ""
    try:
        file_descriptor, temp_path = tempfile.mkstemp(
            prefix=".desktop-session-", suffix=".tmp", dir=directory
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            file_descriptor = -1
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = ""
        return True
    except OSError:
        return False
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def load_desktop_session(port: int = 5002, *, persist: bool = True) -> DesktopSession:
    preferred_home = _desktop_home()
    path = _session_file(preferred_home)
    previous = _read_session(path)
    origin = (
        os.environ.get("JARVIS_DESKTOP_ORIGIN")
        or previous.get("origin")
        or f"http://localhost:{int(port)}"
    )
    if origin.startswith("http://127.0.0.1:"):
        origin = origin.replace("http://127.0.0.1:", "http://localhost:", 1)

    storage_dir = (
        os.environ.get("JARVIS_WEBVIEW_STORAGE")
        or previous.get("webview_storage_dir")
        or os.path.join(preferred_home, "WebView2")
    )
    persistent_storage = _ensure_directory(storage_dir)
    if not persistent_storage:
        fallback_home = _fallback_home()
        storage_dir = os.path.join(fallback_home, "WebView2")
        if not _ensure_directory(storage_dir):
            raise OSError("JARVIS could not create a writable desktop storage directory")
        path = _session_file(fallback_home)
        logger.warning("Desktop persistence unavailable; using temporary WebView storage.")

    session = DesktopSession(
        origin=origin,
        webview_storage_dir=storage_dir,
        session_file=path,
        persist_permissions=bool(persist and persistent_storage),
    )
    if persist:
        data = asdict(session)
        data.update(
            {
                "schema_version": 1,
                "last_launch": datetime.now().isoformat(timespec="seconds"),
            }
        )
        if not _write_session(path, data):
            logger.warning("Desktop session metadata could not be persisted.")
            session = replace(session, persist_permissions=False)
    return session
```

Keep `_desktop_home`, `_session_file`, `_read_session`, and `DesktopSession`; narrow `_read_session` to `except (OSError, json.JSONDecodeError, TypeError)`.

- [ ] **Step 4: Run desktop tests**

Run the Step 2 command.

Expected: all selected tests pass; warnings contain no user path.

- [ ] **Step 5: Commit desktop-session resilience**

```powershell
git add src/backend/core/desktop_session.py tests/test_desktop_session.py
git commit -m "fix: make desktop session persistence resilient"
```

### Task 4: Always launch through the project virtual environment

**Files:**
- Modify: `start_app.py:1-14`
- Modify: `setup.ps1:55-68`
- Modify: `setup.sh:41-55`
- Create: `tests/test_launcher.py`
- Modify: `tests/test_installation_contract.py:60-68`

- [ ] **Step 1: Write launcher and setup-contract tests**

Create `tests/test_launcher.py`:

```python
from pathlib import Path

import start_app


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


def test_relaunch_skips_current_interpreter(monkeypatch, tmp_path):
    candidate = tmp_path / "venv" / "Scripts" / "python.exe"
    candidate.parent.mkdir(parents=True)
    candidate.touch()
    monkeypatch.setattr(start_app.sys, "executable", str(candidate))
    monkeypatch.setattr(start_app.sys, "platform", "win32")
    monkeypatch.setattr(start_app.os, "execv", lambda *_args: (_ for _ in ()).throw(AssertionError()))

    assert start_app._relaunch_in_project_venv(tmp_path) is False


def test_global_python_without_project_venv_gets_setup_guidance(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_SKIP_VENV_REEXEC", raising=False)
    monkeypatch.setattr(start_app.sys, "prefix", "C:/Python312")
    monkeypatch.setattr(start_app.sys, "base_prefix", "C:/Python312")

    message = start_app._project_environment_error(tmp_path)

    assert message is not None
    assert "setup.ps1" in message
    assert "setup.sh" in message
```

Extend `test_setup_scripts_require_explicit_full_mode_for_optional_dependencies`:

```python
    assert '$venvPython = Join-Path' in powershell
    assert '& $venvPython -m pip install -r requirements.txt' in powershell
    assert 'VENV_PYTHON="venv/bin/python"' in shell
    assert '"$VENV_PYTHON" -m pip install -r requirements.txt' in shell
```

- [ ] **Step 2: Run tests and confirm helpers are missing**

```powershell
python -m pytest tests\test_launcher.py tests\test_installation_contract.py -q
```

Expected: launcher helper tests fail and setup contract assertions fail.

- [ ] **Step 3: Add stdlib-only bootstrap before third-party imports**

At the top of `start_app.py`, keep standard-library imports first and add:

```python
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent


def _project_venv_python(root: Path = ROOT_DIR, *, platform: str | None = None) -> Path | None:
    active_platform = platform or sys.platform
    relative = Path("venv/Scripts/python.exe") if active_platform == "win32" else Path("venv/bin/python")
    candidate = root / relative
    return candidate if candidate.is_file() else None


def _relaunch_in_project_venv(root: Path = ROOT_DIR) -> bool:
    if os.getenv("JARVIS_SKIP_VENV_REEXEC", "").strip().lower() in {"1", "true", "yes"}:
        return False
    candidate = _project_venv_python(root)
    if candidate is None:
        return False
    current = os.path.normcase(os.path.abspath(sys.executable))
    target = os.path.normcase(os.path.abspath(str(candidate)))
    if current == target:
        return False
    os.execv(str(candidate), [str(candidate), str(Path(__file__).resolve()), *sys.argv[1:]])
    return True


def _project_environment_error(root: Path = ROOT_DIR) -> str | None:
    skip = os.getenv("JARVIS_SKIP_VENV_REEXEC", "").strip().lower()
    if skip in {"1", "true", "yes"}:
        return None
    if _project_venv_python(root) is not None or sys.prefix != sys.base_prefix:
        return None
    return (
        "Project virtual environment not found. Run .\\setup.ps1 on Windows "
        "or ./setup.sh on macOS/Linux before starting JARVIS."
    )


if __name__ == "__main__":
    environment_error = _project_environment_error()
    if environment_error:
        raise SystemExit(environment_error)
    _relaunch_in_project_venv()

try:
    import requests
    import webview
except ModuleNotFoundError as exc:
    raise SystemExit(
        f"Missing runtime dependency '{exc.name}'. Run .\\setup.ps1 on Windows "
        "or ./setup.sh on macOS/Linux."
    ) from exc
```

Remove duplicate standard-library imports and derive `BACKEND_DIR` from `ROOT_DIR`.

- [ ] **Step 4: Make setup scripts call the venv interpreter explicitly**

In `setup.ps1`, replace activation-dependent install calls with:

```powershell
$venvPython = Join-Path (Get-Location) "venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt
if ($Full) {
    & $venvPython -m pip install -r requirements-optional.txt
}
if ($Dev) {
    & $venvPython -m pip install -r requirements-dev.txt
}
```

Use `& $venvPython -m playwright install chromium` in full mode. In `setup.sh`, define and use:

```bash
VENV_PYTHON="venv/bin/python"
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -r requirements.txt
if [ "$FULL_MODE" -eq 1 ]; then
  "$VENV_PYTHON" -m pip install -r requirements-optional.txt
fi
if [ "$DEV_MODE" -eq 1 ]; then
  "$VENV_PYTHON" -m pip install -r requirements-dev.txt
fi
```

- [ ] **Step 5: Run launcher and installation tests**

Run the Step 2 command.

Expected: all selected tests pass.

- [ ] **Step 6: Commit launcher determinism**

```powershell
git add start_app.py setup.ps1 setup.sh tests/test_launcher.py tests/test_installation_contract.py
git commit -m "fix: launch JARVIS through its project environment"
```

### Task 5: Return controlled LLM-unavailable errors

**Files:**
- Create: `src/backend/core/errors.py`
- Modify: `src/backend/core/brain/processor.py:316-327,382-455`
- Modify: `src/backend/api/chat_routes.py:1-180`
- Modify: `tests/test_smoke.py:702-766`

- [ ] **Step 1: Write classic and streaming error-contract tests**

Add to `tests/test_smoke.py`:

```python
def test_chat_returns_503_when_llm_is_unconfigured(monkeypatch):
    import jarvis_backend
    from api import chat_routes
    from core.errors import LLMUnavailableError

    def unavailable(*_args, **_kwargs):
        raise LLMUnavailableError

    monkeypatch.setattr(chat_routes.jarvis_brain, "procesar_mensaje", unavailable)
    client = _test_client(jarvis_backend.app)
    response = client.post(
        "/api/chat",
        json={"message": "explain quantum computing"},
        environ_base={"REMOTE_ADDR": "127.0.0.177"},
    )

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "llm_unconfigured",
        "message": "Configure GROQ_API_KEY to enable AI responses.",
    }


def test_chat_stream_hides_internal_exception_text(monkeypatch):
    import jarvis_backend
    from api import chat_routes

    def broken_stream(*_args, **_kwargs):
        raise RuntimeError("proxy-token-private-detail")

    monkeypatch.setattr(chat_routes.jarvis_brain, "stream_procesar_mensaje_events", broken_stream)
    client = _test_client(jarvis_backend.app)
    response = client.post(
        "/api/chat/stream",
        json={"message": "hello"},
        environ_base={"REMOTE_ADDR": "127.0.0.178"},
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "chat_unavailable" in body
    assert "proxy-token-private-detail" not in body
```

- [ ] **Step 2: Run tests and confirm current 200/raw-error behavior**

```powershell
python -m pytest tests\test_smoke.py::test_chat_returns_503_when_llm_is_unconfigured tests\test_smoke.py::test_chat_stream_hides_internal_exception_text -q
```

Expected: failure because `LLMUnavailableError` is absent and the stream emits raw exception text.

- [ ] **Step 3: Add the shared exception and raise it only after local preflight**

Create `src/backend/core/errors.py`:

```python
class LLMUnavailableError(RuntimeError):
    """Raised when a request needs an LLM but no provider is configured."""
```

In `_ejecutar_cerebro_llm` and the direct streaming branch, preserve `_llm_calls_disabled_for_tests()` behavior but raise when the real LLM is absent:

```python
from core.errors import LLMUnavailableError

if brain_state.llm is None:
    raise LLMUnavailableError
if _llm_calls_disabled_for_tests():
    return "Brain not initialized.", False
```

For the streaming direct-response branch, emit the test-only response only when `_llm_calls_disabled_for_tests()` is true; otherwise raise `LLMUnavailableError`.

- [ ] **Step 4: Replace duplicated fallback logic with controlled API responses**

In `chat_routes.py`, remove `traceback`, `HumanMessage`, and the second direct `llm.invoke` attempt. Use:

```python
from core.errors import LLMUnavailableError


def _llm_unconfigured_response():
    return jsonify(
        {
            "error": "llm_unconfigured",
            "message": "Configure GROQ_API_KEY to enable AI responses.",
        }
    ), 503
```

At the end of `api_chat`:

```python
    except LLMUnavailableError:
        obs_event("api_chat_unconfigured", ip=request.remote_addr or "unknown")
        return _llm_unconfigured_response()
    except Exception as exc:
        obs_event("api_chat_failed", error=type(exc).__name__)
        return jsonify(
            {
                "error": "chat_unavailable",
                "message": "The AI service is temporarily unavailable.",
            }
        ), 503
```

Move iterator creation inside the stream `try` block and sanitize stream failures:

```python
    async def generate():
        try:
            iterator = jarvis_brain.stream_procesar_mensaje_events(
                user_input, profile_id=profile_id
            )
            while True:
                event, done = await asyncio.to_thread(_next_stream_event, iterator)
                if done:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except LLMUnavailableError:
            event = {
                "type": "error",
                "code": "llm_unconfigured",
                "message": "Configure GROQ_API_KEY to enable AI responses.",
            }
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            obs_event("api_chat_stream_failed", error=type(exc).__name__)
            event = {
                "type": "error",
                "code": "chat_unavailable",
                "message": "The AI service is temporarily unavailable.",
            }
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
```

Apply the same two exception branches inside `stream_procesar_mensaje_events` so direct callers do not receive `str(e)`.

- [ ] **Step 5: Run chat regressions**

```powershell
python -m pytest tests\test_smoke.py::test_chat_returns_503_when_llm_is_unconfigured tests\test_smoke.py::test_chat_stream_hides_internal_exception_text tests\test_smoke.py::test_chat_accepts_profile_id_payload tests\test_smoke.py::test_chat_stream_endpoint_exists -q
```

Expected: all selected tests pass; local/preflight behavior remains covered by existing router tests.

- [ ] **Step 6: Commit API error contracts**

```powershell
git add src/backend/core/errors.py src/backend/core/brain/processor.py src/backend/api/chat_routes.py tests/test_smoke.py
git commit -m "fix: expose controlled LLM availability errors"
```

### Task 6: Replace pydub with one FFmpeg conversion utility

**Files:**
- Create: `src/backend/utils/audio_conversion.py`
- Create: `tests/test_audio_conversion.py`
- Modify: `src/backend/voice/pipeline.py:97-166`
- Modify: `src/backend/voice/identifier.py:596-707`
- Modify: `src/backend/services/telegram_manager.py:179-203`
- Modify: `requirements.txt:14-19`

- [ ] **Step 1: Write conversion command and failure tests**

Create `tests/test_audio_conversion.py`:

```python
from pathlib import Path
from types import SimpleNamespace

import pytest
from utils import audio_conversion


def test_normalize_audio_uses_pcm_16khz_mono(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"RIFF" + b"\x00" * 48)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(audio_conversion.subprocess, "run", fake_run)

    result = audio_conversion.normalize_audio_bytes_to_wav(
        b"OggS" + b"\x00" * 100, runtime_dir=str(tmp_path)
    )

    assert result.startswith(b"RIFF")
    assert "pcm_s16le" in commands[0]
    assert commands[0][commands[0].index("-ar") + 1] == "16000"
    assert commands[0][commands[0].index("-ac") + 1] == "1"


def test_wav_to_ogg_uses_opus(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"OggS" + b"\x00" * 48)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(audio_conversion.subprocess, "run", fake_run)

    result = audio_conversion.wav_bytes_to_ogg_opus(
        b"RIFF" + b"\x00" * 100, runtime_dir=str(tmp_path)
    )

    assert result.startswith(b"OggS")
    assert "libopus" in commands[0]


def test_normalize_audio_retries_with_matroska_hint(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        if len(commands) == 2:
            Path(command[-1]).write_bytes(b"RIFF" + b"\x00" * 48)
            return SimpleNamespace(returncode=0, stderr=b"")
        return SimpleNamespace(returncode=1, stderr=b"decode failed")

    monkeypatch.setattr(audio_conversion.subprocess, "run", fake_run)

    result = audio_conversion.normalize_audio_bytes_to_wav(
        b"\x1a\x45\xdf\xa3" + b"\x00" * 100, runtime_dir=str(tmp_path)
    )

    assert result.startswith(b"RIFF")
    assert commands[1][commands[1].index("-f") + 1] == "matroska"


def test_missing_ffmpeg_returns_actionable_error(monkeypatch, tmp_path):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(audio_conversion.subprocess, "run", missing)

    with pytest.raises(audio_conversion.AudioConversionError, match="FFmpeg"):
        audio_conversion.normalize_audio_bytes_to_wav(
            b"OggS" + b"\x00" * 100, runtime_dir=str(tmp_path)
        )
```

- [ ] **Step 2: Run tests and confirm the utility is absent**

```powershell
python -m pytest tests\test_audio_conversion.py -q
```

Expected: collection fails because `utils.audio_conversion` does not exist.

- [ ] **Step 3: Implement the shared converter**

Create `src/backend/utils/audio_conversion.py`:

```python
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


class AudioConversionError(RuntimeError):
    """Raised when FFmpeg cannot produce the requested audio format."""


def _input_suffix(audio_bytes: bytes) -> str:
    if audio_bytes.startswith(b"RIFF"):
        return ".wav"
    if audio_bytes.startswith(b"OggS"):
        return ".ogg"
    if audio_bytes.startswith(b"\x1a\x45\xdf\xa3"):
        return ".webm"
    if audio_bytes.startswith(b"ID3") or audio_bytes.startswith(b"\xff\xfb"):
        return ".mp3"
    return ".bin"


def _convert(
    audio_bytes: bytes,
    *,
    output_suffix: str,
    output_args: list[str],
    runtime_dir: str | None,
) -> bytes:
    if not isinstance(audio_bytes, (bytes, bytearray)) or not audio_bytes:
        raise AudioConversionError("Audio input is empty or invalid.")
    with tempfile.TemporaryDirectory(dir=runtime_dir) as temp_dir:
        input_path = Path(temp_dir) / f"input{_input_suffix(bytes(audio_bytes))}"
        output_path = Path(temp_dir) / f"output{output_suffix}"
        input_path.write_bytes(bytes(audio_bytes))
        input_variants = [[]]
        if output_suffix == ".wav":
            input_variants.extend(
                [
                    ["-f", "matroska"],
                    ["-err_detect", "ignore_err", "-fflags", "+genpts+discardcorrupt"],
                ]
            )
        timed_out = False
        for input_args in input_variants:
            if output_path.exists():
                output_path.unlink()
            command = [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                *input_args,
                "-i",
                str(input_path),
                *output_args,
                str(output_path),
            ]
            try:
                result = subprocess.run(command, capture_output=True, timeout=30, check=False)
            except FileNotFoundError as exc:
                raise AudioConversionError("FFmpeg is not installed or not available on PATH.") from exc
            except subprocess.TimeoutExpired:
                timed_out = True
                continue
            if result.returncode == 0 and output_path.is_file():
                converted = output_path.read_bytes()
                if converted:
                    return converted
        if timed_out:
            raise AudioConversionError("FFmpeg audio conversion timed out.")
        raise AudioConversionError("FFmpeg could not decode the supplied audio.")


def normalize_audio_bytes_to_wav(audio_bytes: bytes, *, runtime_dir: str | None = None) -> bytes:
    return _convert(
        audio_bytes,
        output_suffix=".wav",
        output_args=["-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1"],
        runtime_dir=runtime_dir,
    )


def wav_bytes_to_ogg_opus(audio_bytes: bytes, *, runtime_dir: str | None = None) -> bytes:
    return _convert(
        audio_bytes,
        output_suffix=".ogg",
        output_args=["-c:a", "libopus", "-b:a", "48k", "-application", "voip"],
        runtime_dir=runtime_dir,
    )
```

- [ ] **Step 4: Replace all three pydub consumers**

In `voice/pipeline.py`, use `normalize_audio_bytes_to_wav`; preserve the optimized-WAV fast path and return `(audio_bytes, False)` on `AudioConversionError`. In `voice/identifier.py`, use the same helper before `soundfile.read`. In `telegram_manager.py`, convert TTS bytes using `wav_bytes_to_ogg_opus` and pass an `io.BytesIO` containing the result to `requests.post`.

The pipeline replacement body must be:

```python
def normalizar_a_wav(audio_bytes: bytes) -> tuple[bytes, bool]:
    if wav_ya_optimizado(audio_bytes):
        return audio_bytes, True
    try:
        converted = normalize_audio_bytes_to_wav(audio_bytes, runtime_dir=BASE_DIR)
        return (converted, True) if bytes_es_wav_valido(converted) else (audio_bytes, False)
    except AudioConversionError as exc:
        print(f"[VOICE PIPELINE] Audio conversion unavailable: {exc}")
        return audio_bytes, False
```

Replace the non-WAV temporary-file block in `VoiceIdentifier._preprocess_audio_bytes` with:

```python
            if not es_wav:
                try:
                    audio_a_procesar = normalize_audio_bytes_to_wav(
                        audio_bytes, runtime_dir=RUNTIME_DIR
                    )
                except AudioConversionError as exc:
                    print(f"[VOICE_ID] Audio conversion unavailable: {exc}")
                    return None
```

Replace the Telegram audio body with:

```python
            if audio and self._tts_engine:
                audio_bytes = self._tts_engine.sintetizar(texto_para_leer[:600])
                runtime_dir = os.getenv("JARVIS_RUNTIME_DIR") or tempfile.gettempdir()
                ogg_audio = wav_bytes_to_ogg_opus(audio_bytes, runtime_dir=runtime_dir)
                with io.BytesIO(ogg_audio) as ogg_buffer:
                    url_voice = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVoice"
                    response = http_requests.post(
                        url_voice,
                        data={"chat_id": TELEGRAM_CHAT_ID},
                        files={"voice": ("jarvis.ogg", ogg_buffer, "audio/ogg")},
                        timeout=15,
                    )
                    response.raise_for_status()
                    return True
```

Remove project imports of pydub and remove `pydub>=0.25` from `requirements.txt`.

- [ ] **Step 5: Run audio and voice regressions**

```powershell
python -m pytest tests\test_audio_conversion.py tests\test_smoke.py::test_normalizar_a_wav_rejects_garbage tests\test_smoke.py::test_normalizar_a_wav_accepts_wav tests\test_smoke.py::test_normalizar_a_wav_accepts_ogg tests\test_smoke.py::test_voice_id_preprocess_helper_from_bytes -q
rg -n "pydub|AudioSegment" requirements.txt src\backend tests
```

Expected: tests pass and `rg` returns no project-owned pydub usage.

- [ ] **Step 6: Commit the audio replacement**

```powershell
git add src/backend/utils/audio_conversion.py src/backend/voice/pipeline.py src/backend/voice/identifier.py src/backend/services/telegram_manager.py tests/test_audio_conversion.py requirements.txt
git commit -m "refactor: replace pydub conversions with ffmpeg"
```

### Task 7: Modernize Spotify OAuth and finish Groq-only configuration

**Files:**
- Modify: `src/backend/core/jarvis_config.py:76-121`
- Modify: `src/backend/tools/spotify.py:11-55,251-281`
- Modify: `tests/test_spotify_recs.py`
- Modify: `tests/test_installation_contract.py`
- Modify: `jarvis_settings.py:131-167`
- Modify: `.env.example:7-51`
- Modify: `requirements.txt:7-14`

- [ ] **Step 1: Write configuration and token-access tests**

Add to `tests/test_spotify_recs.py`:

```python
from types import SimpleNamespace


def test_spotify_access_token_uses_supported_cache_handler(monkeypatch):
    from tools import spotify

    cache_handler = SimpleNamespace(
        get_cached_token=lambda: {"access_token": "cached-token"}
    )
    auth_manager = SimpleNamespace(
        get_access_token=lambda: None,
        cache_handler=cache_handler,
    )
    monkeypatch.setattr(spotify, "sp", SimpleNamespace(auth_manager=auth_manager))
    monkeypatch.setattr(spotify, "SPOTIFY_ENABLED", True)

    assert spotify._spotify_access_token() == "cached-token"


def test_spotify_access_token_accepts_current_string_return(monkeypatch):
    from tools import spotify

    auth_manager = SimpleNamespace(
        get_access_token=lambda: "live-token",
        cache_handler=SimpleNamespace(get_cached_token=lambda: None),
    )
    monkeypatch.setattr(spotify, "sp", SimpleNamespace(auth_manager=auth_manager))
    monkeypatch.setattr(spotify, "SPOTIFY_ENABLED", True)

    assert spotify._spotify_access_token() == "live-token"


def test_spotify_rejects_obsolete_localhost_redirect():
    from tools import spotify

    issue = spotify._spotify_redirect_error("http://localhost:8888/callback")

    assert issue == "Spotify no longer accepts localhost redirect aliases; use 127.0.0.1."


def test_spotify_rejects_backend_port_for_oauth_listener():
    from tools import spotify

    issue = spotify._spotify_redirect_error("http://127.0.0.1:5002/callback")

    assert issue == "Spotify OAuth cannot share the JARVIS backend port 5002."
```

Add to `tests/test_installation_contract.py`:

```python
def test_public_configuration_is_groq_only_and_uses_supported_spotify_redirect():
    settings = (ROOT / "jarvis_settings.py").read_text(encoding="utf-8").lower()
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    config = (ROOT / "src/backend/core/jarvis_config.py").read_text(encoding="utf-8")

    assert "minimax" not in settings
    assert "MINIMAX" not in env_example
    assert 'SPOTIPY_REDIRECT_URI="http://127.0.0.1:8888/callback"' in env_example
    assert '"http://127.0.0.1:8888/callback"' in config
    assert 'SPOTIFY_AUTO_SHUFFLE="false"' in env_example
```

- [ ] **Step 2: Run tests and confirm deprecated/default mismatches**

```powershell
python -m pytest tests\test_spotify_recs.py tests\test_installation_contract.py -q
```

Expected: the configuration contract fails on MiniMax and localhost; token tests fail against deprecated access paths.

- [ ] **Step 3: Align cache and OAuth defaults**

In `jarvis_config.py`, set:

```python
SPOTIFY_CACHE = os.path.join(BASE_DIR, ".cache-jarvis")
SPOTIPY_REDIRECT_URI = os.getenv(
    "SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"
)
SPOTIFY_AUTO_SHUFFLE = _read_bool(os.environ, "SPOTIFY_AUTO_SHUFFLE", False)
```

In `.env.example`, use the same redirect and `SPOTIFY_AUTO_SHUFFLE="false"`. This preserves the existing cache location while eliminating duplicate path construction.

- [ ] **Step 4: Use current Spotipy APIs**

Require `spotipy>=2.26,<3` in `requirements.txt`. In `spotify.py`, import `CacheFileHandler` directly, use `jarvis_config.SPOTIFY_CACHE`, and build `SpotifyOAuth` with the cache handler. Replace `_spotify_access_token` with:

```python
from urllib.parse import urlparse


def _spotify_redirect_error(redirect_uri: str) -> str:
    parsed = urlparse(redirect_uri)
    if parsed.hostname == "localhost":
        return "Spotify no longer accepts localhost redirect aliases; use 127.0.0.1."
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "::1"}:
        return "Spotify HTTP redirects must use an explicit loopback IP address."
    if parsed.hostname in {"127.0.0.1", "::1"} and parsed.port == 5002:
        return "Spotify OAuth cannot share the JARVIS backend port 5002."
    return ""


SPOTIFY_CONFIG_ERROR = _spotify_redirect_error(SPOTIFY_REDIRECT_URI)
SPOTIFY_ENABLED = bool(
    SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET and not SPOTIFY_CONFIG_ERROR
)
if SPOTIFY_CONFIG_ERROR:
    print(f"  [SPOTIFY] Configuration disabled: {SPOTIFY_CONFIG_ERROR}")
```

Then replace `_spotify_access_token` with:

```python
def _spotify_access_token() -> str | None:
    ready, _ = _spotify_ready()
    if not ready:
        return None
    try:
        token = sp.auth_manager.get_access_token()
        if isinstance(token, str) and token.strip():
            return token.strip()
        if isinstance(token, dict):
            access_token = token.get("access_token")
            if isinstance(access_token, str) and access_token.strip():
                return access_token.strip()
    except Exception as exc:
        print(f"  [SPOTIFY] Access token refresh failed: {type(exc).__name__}")

    try:
        cache_handler = getattr(sp.auth_manager, "cache_handler", None)
        cached = cache_handler.get_cached_token() if cache_handler is not None else None
        access_token = (cached or {}).get("access_token")
        return access_token.strip() if isinstance(access_token, str) and access_token.strip() else None
    except Exception as exc:
        print(f"  [SPOTIFY] Cached token read failed: {type(exc).__name__}")
        return None
```

Do not print token values or raw provider responses.

- [ ] **Step 5: Remove active MiniMax prompt references**

In `jarvis_settings.py`, restore the technical-search example to `Groq model updates` and remove `MINIMAX` from the provider list. Preserve all unrelated prompt customization.

- [ ] **Step 6: Run Spotify/provider regressions**

```powershell
python -m pytest tests\test_spotify_recs.py tests\test_installation_contract.py tests\test_llm_engine_fallback.py -q
rg -n "get_access_token\(as_dict|auth_manager\.get_cached_token" src\backend\tools\spotify.py
rg -n "MiniMax|MINIMAX|minimax|http://localhost:5002/callback" jarvis_settings.py .env.example requirements.txt src\backend README.md
```

Expected: tests pass and the search finds none of the obsolete active patterns.

- [ ] **Step 7: Commit provider modernization**

```powershell
git add src/backend/core/jarvis_config.py src/backend/tools/spotify.py tests/test_spotify_recs.py tests/test_installation_contract.py jarvis_settings.py .env.example requirements.txt
git commit -m "fix: modernize Spotify and Groq configuration"
```

### Task 8: Make release checks reproducible and update documentation

**Files:**
- Modify: `requirements-dev.txt`
- Modify: `README.md:36-205,225-287`
- Modify: `AGENTS.md:32-75,96-125,217-228`

- [ ] **Step 1: Add release tools to the dev environment**

Replace `requirements-dev.txt` with:

```text
# Development, test, lint, and release-audit tools.
-r requirements.txt
pytest>=8.0,<9
pytest-cov>=7.0,<8
ruff==0.15.20
pip-audit==2.10.1
```

These are dev-only dependencies and do not increase the runtime installation.

- [ ] **Step 2: Document the corrected runtime contract**

Update README and AGENTS with these exact facts:

```markdown
- Run `setup.ps1` or `setup.sh` before the launcher. `start_app.py` automatically
  re-executes through the project `venv` when it exists, preventing conflicts
  with packages installed in the user's global Python.
- FFmpeg is the audio conversion backend for browser voice, voice identity, and
  Telegram OGG/Opus output. `pydub` is no longer a direct dependency.
- Spotify OAuth must register exactly
  `http://127.0.0.1:8888/callback` in the Spotify developer dashboard. Spotify
  no longer accepts `localhost` aliases. Delete `src/backend/.cache-jarvis` and
  authenticate again after changing the redirect URI or scopes.
- `JARVIS_MONITORING_ENABLED` defaults to false in core mode and true in full
  mode. Installing APScheduler alone does not enable background jobs.
- Without `GROQ_API_KEY`, status, setup diagnostics, TTS, and local preflight
  tools remain available; requests that require AI return `llm_unconfigured`
  with HTTP 503.
```

Add release commands:

```powershell
python -m pip check
python -m pip_audit -r requirements.txt
python -m ruff check src/backend tests --select F
```

Keep Python support at 3.11/3.12 and explain that Python 3.13 remains outside the supported window even after removing pydub.

- [ ] **Step 3: Validate documentation and installation contracts**

```powershell
python -m pytest tests\test_installation_contract.py -q
rg -n "localhost:5002/callback|MINIMAX|Minimax" README.md AGENTS.md .env.example requirements.txt requirements-dev.txt requirements-optional.txt
rg -n "pydub(>=|==|~=)" requirements.txt requirements-dev.txt requirements-optional.txt
```

Expected: installation tests pass and the search returns no obsolete active instructions.

- [ ] **Step 4: Commit release tooling and docs**

```powershell
git add requirements-dev.txt README.md AGENTS.md
git commit -m "docs: document reproducible JARVIS setup"
```

### Task 9: Run full regression, dependency audit, and clean-clone proof

**Files:**
- Verify only: entire repository
- Update only if measured output changes: `AGENTS.md`

- [ ] **Step 1: Install the updated core and dev sets in the isolated core venv**

```powershell
& .\scratch\core-install-venv\Scripts\python.exe -m pip install --upgrade pip
& .\scratch\core-install-venv\Scripts\python.exe -m pip install -r requirements-dev.txt
& .\scratch\core-install-venv\Scripts\python.exe -m pip check
```

Expected: `No broken requirements found.`

- [ ] **Step 2: Run the full local verification matrix**

```powershell
& .\scratch\core-install-venv\Scripts\python.exe -m pytest -q
& .\scratch\core-install-venv\Scripts\python.exe -m compileall -q start_app.py src\backend
& .\scratch\core-install-venv\Scripts\python.exe -m ruff check src\backend tests --select F
& .\scratch\core-install-venv\Scripts\python.exe -m pip_audit -r requirements.txt
node --check src\frontend\static\js\main.js
node --check src\frontend\static\js\modules\api.js
git diff --check
git lfs ls-files
```

Expected: pytest has zero failures/errors, compileall and JavaScript checks are silent, Ruff reports success, pip-audit reports no known vulnerabilities, and both ONNX models appear in Git LFS.

- [ ] **Step 3: Verify no tracked runtime data or embedded secret**

```powershell
git ls-files -- .env src/backend/logs src/backend/.cache-jarvis voice_profiles desktop_session.json
git grep -n -E "gsk_[A-Za-z0-9_-]{20,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"
git status --short
```

Expected: the first two commands print nothing. `git status` contains no generated runtime artifacts.

- [ ] **Step 4: Create the clean local clone**

The target was confirmed absent during planning.

```powershell
git clone --branch codex/jarvis-core-rescue --single-branch . C:\tmp\jarvis-stability-clean-20260717
Set-Location C:\tmp\jarvis-stability-clean-20260717
git lfs pull
.\setup.ps1 -Dev
```

Expected: setup creates `venv` and `.env`, downloads real ONNX files, and completes without installing optional full-mode packages.

- [ ] **Step 5: Run clean-clone tests and no-key backend smoke**

```powershell
& .\venv\Scripts\python.exe -m pip check
& .\venv\Scripts\python.exe -m pytest -q
$backendProcess = Start-Process -FilePath ".\venv\Scripts\python.exe" -ArgumentList "src\backend\jarvis_backend.py" -PassThru -WindowStyle Hidden
try {
    $status = $null
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $status = Invoke-RestMethod -Uri "http://127.0.0.1:5002/api/status" -TimeoutSec 1
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $status) { throw "Backend did not become ready" }
    if ($status.mode -ne "core") { throw "Backend did not start in core mode" }
    if ($status.features.monitoring) { throw "Monitoring unexpectedly enabled in core mode" }
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:5002/api/chat" -Method Post -ContentType "application/json" -Body '{"message":"Explain unit testing"}'
        throw "Chat unexpectedly succeeded without GROQ_API_KEY"
    } catch {
        $response = $_.Exception.Response
        if (-not $response -or [int]$response.StatusCode -ne 503) { throw }
    }
} finally {
    if ($backendProcess -and -not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id -Force
    }
}
```

Expected: clean tests pass, status reports `mode: core`, monitoring is false, and AI chat returns controlled HTTP 503 without a key.

- [ ] **Step 6: Validate TTS and live Groq separately**

Start the clean-clone backend again and validate TTS:

```powershell
Set-Location C:\tmp\jarvis-stability-clean-20260717
$backendProcess = Start-Process -FilePath ".\venv\Scripts\python.exe" -ArgumentList "src\backend\jarvis_backend.py" -PassThru -WindowStyle Hidden
try {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:5002/api/status" -TimeoutSec 1 | Out-Null
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    $ttsFile = Join-Path (Get-Location) "scratch\clean-tts.wav"
    $ttsResponse = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:5002/api/tts" -Method Post -ContentType "application/json" -Body '{"text":"Systems online."}' -OutFile $ttsFile -PassThru
    if ($ttsResponse.StatusCode -ne 200) { throw "TTS did not return HTTP 200" }
    if ((Get-Item $ttsFile).Length -le 44) { throw "TTS returned an invalid WAV body" }
    $wavHeader = [System.IO.File]::ReadAllBytes($ttsFile)[0..3]
    if ([System.Text.Encoding]::ASCII.GetString($wavHeader) -ne "RIFF") { throw "TTS body is not WAV" }
} finally {
    if ($backendProcess -and -not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id -Force
    }
}
```

Run live Groq chat from the original workspace, whose untracked `.env` is loaded by the backend without exposing the key:

```powershell
Set-Location C:\Users\ramir\Desktop\JARVIS
$backendProcess = Start-Process -FilePath ".\scratch\core-install-venv\Scripts\python.exe" -ArgumentList "src\backend\jarvis_backend.py" -PassThru -WindowStyle Hidden
try {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:5002/api/status" -TimeoutSec 1 | Out-Null
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    $chatResponse = Invoke-RestMethod -Uri "http://127.0.0.1:5002/api/chat" -Method Post -ContentType "application/json" -Body '{"message":"Reply with exactly CORE_OK","profile_id":"admin"}'
    if ($chatResponse.response.Trim() -ne "CORE_OK") { throw "Groq live smoke returned an unexpected response" }
} finally {
    if ($backendProcess -and -not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id -Force
    }
}
```

Do not print, copy, or persist the Groq key in the clean clone.

- [ ] **Step 7: Confirm clone and source hygiene**

```powershell
Set-Location C:\tmp\jarvis-stability-clean-20260717
git status --short
git ls-files -- .env
Set-Location C:\Users\ramir\Desktop\JARVIS
git status --short
```

Expected: clone output is empty because generated files are ignored. Source output is empty after all planned commits.

- [ ] **Step 8: Record the final measured baseline if it changed**

If pytest's count differs from the documented AGENTS baseline, replace only the numeric baseline with the exact Step 2 output, run `git diff --check`, and commit:

```powershell
git add AGENTS.md
git commit -m "docs: record JARVIS stability baseline"
```

Do not create this commit when the documented count remains exact.
