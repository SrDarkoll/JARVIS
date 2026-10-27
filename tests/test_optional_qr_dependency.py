from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_backend_import_does_not_require_the_optional_qr_dependency(tmp_path):
    runtime = tmp_path / "runtime"
    environment = {
        **os.environ,
        "JARVIS_TEST_MODE": "1",
        "JARVIS_CORE_MODE": "true",
        "JARVIS_DATA_DIR": str(runtime),
        "JARVIS_RUNTIME_DIR": str(runtime),
        "JARVIS_DB_PATH": str(runtime / "memory" / "memoria_jarvis.db"),
        "JARVIS_LOG_DIR": str(runtime / "logs"),
        "JARVIS_CACHE_DIR": str(runtime / "cache"),
        "JARVIS_MONITORING_ENABLED": "false",
        "GEMINI_API_KEY": "",
        "GOOGLE_API_KEY": "",
        "GROQ_API_KEY": "",
        "JARVIS_API_TOKEN": "",
    }
    code = (
        "import sys; sys.modules['qrcode'] = None; "
        "sys.path.insert(0, 'src/backend'); "
        "import jarvis_backend; sys.__stdout__.write('import_ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "import_ok" in result.stdout
