import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_pytest_tmp_path_uses_isolated_repo_runtime(tmp_path):
    resolved_tmp = Path(tmp_path).resolve()
    runtime_tmp = (Path(os.environ["JARVIS_RUNTIME_DIR"]) / "tmp").resolve()

    assert runtime_tmp == resolved_tmp or runtime_tmp in resolved_tmp.parents
    assert (ROOT / "scratch").resolve() in resolved_tmp.parents


def test_unified_runtime_log_defaults_off_during_tests():
    from core import jarvis_config

    assert os.environ.get("JARVIS_TEST_MODE") == "1"
    assert jarvis_config.UNIFIED_LOG_ENABLED is False


def test_spotify_oauth_credentials_are_isolated_from_developer_env():
    from core import jarvis_config

    assert os.environ["SPOTIPY_CLIENT_ID"] == ""
    assert os.environ["SPOTIPY_CLIENT_SECRET"] == ""
    assert jarvis_config.SPOTIPY_CLIENT_ID == ""
    assert jarvis_config.SPOTIPY_CLIENT_SECRET == ""


def test_backend_import_accepts_captured_stdout(tmp_path):
    runtime = tmp_path / "captured-stdout-runtime"
    environment = {**os.environ, "JARVIS_TEST_MODE": "1", "JARVIS_CORE_MODE": "true",
                   "JARVIS_DATA_DIR": str(runtime), "JARVIS_RUNTIME_DIR": str(runtime),
                   "JARVIS_DB_PATH": str(runtime / "memory" / "memoria_jarvis.db"),
                   "JARVIS_MONITORING_ENABLED": "false", "GEMINI_API_KEY": "", "GOOGLE_API_KEY": "",
                   "GROQ_API_KEY": "", "JARVIS_API_TOKEN": ""}
    code = (
        "import io, sys; sys.path.insert(0, 'src/backend'); sys.stdout = io.StringIO(); "
        "import jarvis_backend; sys.__stdout__.write('import_ok')"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=environment,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "import_ok"
