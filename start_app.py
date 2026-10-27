from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _desktop_bind() -> tuple[str, int]:
    """Resolve the desktop URL from the same .env the backend uses."""
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT_DIR / ".env")
    except (ImportError, OSError):
        pass
    raw_host = str(os.getenv("JARVIS_HOST", "localhost") or "localhost").strip() or "localhost"
    try:
        port = int(str(os.getenv("JARVIS_PORT", "5002") or "5002").strip())
    except (TypeError, ValueError):
        port = 5002
    ui_host = "localhost" if raw_host in {"0.0.0.0", "::", "127.0.0.1", "localhost"} or ":" in raw_host else raw_host
    return ui_host, port


def _skip_venv_reexec() -> bool:
    return os.getenv("JARVIS_SKIP_VENV_REEXEC", "").strip().lower() in _TRUE_VALUES


def _project_venv_python(
    root: Path = ROOT_DIR, *, platform: str | None = None
) -> Path | None:
    active_platform = platform or sys.platform
    relative_path = (
        Path("venv/Scripts/python.exe")
        if active_platform == "win32"
        else Path("venv/bin/python")
    )
    candidate = Path(root) / relative_path
    return candidate if candidate.is_file() else None


def _relaunch_in_project_venv(root: Path = ROOT_DIR) -> bool:
    if _skip_venv_reexec():
        return False

    candidate = _project_venv_python(root)
    if candidate is None:
        return False

    current_python = os.path.normcase(os.path.realpath(sys.executable))
    project_python = os.path.normcase(os.path.realpath(candidate))
    if current_python == project_python:
        return False

    script_path = Path(root) / "start_app.py"
    os.execv(
        str(candidate),
        [str(candidate), str(script_path), *sys.argv[1:]],
    )
    return True


def _project_environment_error(root: Path = ROOT_DIR) -> str | None:
    if _skip_venv_reexec():
        return None
    if _project_venv_python(root) is not None:
        return None
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return None
    return (
        "Project virtual environment not found. Run .\\setup.ps1 on Windows "
        "or ./setup.sh on macOS/Linux before starting JARVIS."
    )


if __name__ == "__main__":
    environment_error = _project_environment_error()
    if environment_error:
        print(environment_error, file=sys.stderr)
        raise SystemExit(1)
    _relaunch_in_project_venv()


try:
    import requests
    import webview
except ModuleNotFoundError as exc:
    dependency = exc.name or "unknown"
    raise SystemExit(
        f"Missing runtime dependency '{dependency}'. Run .\\setup.ps1 on Windows "
        "or ./setup.sh on macOS/Linux."
    ) from exc


BACKEND_DIR = str(ROOT_DIR / "src" / "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def is_backend_running(url, *, expected_nonce: str | None = None):
    try:
        response = requests.get(f"{url}/api/health", timeout=1)
        if response.status_code != 200:
            return False
        payload = response.json()
        if not isinstance(payload, dict):
            return False
        if (
            payload.get("status") != "online"
            or payload.get("service") != "jarvis"
            or payload.get("api_version") != 1
            or not str(payload.get("instance_id") or "").strip()
        ):
            return False
        if expected_nonce:
            expected_hash = hashlib.sha256(
                expected_nonce.encode("utf-8")
            ).hexdigest()
            supplied_hash = str(payload.get("startup_nonce_sha256") or "")
            return hmac.compare_digest(supplied_hash, expected_hash)
        return True
    except (requests.RequestException, TypeError, ValueError):
        return False


def start_app():
    # 1. Route configuration
    root_dir = os.path.dirname(os.path.abspath(__file__))
    backend_script = os.path.join(root_dir, "src", "backend", "jarvis_backend.py")
    cleanup_dirs: set[str] = set()

    def register_temporary_storage(path: str | None) -> None:
        if path:
            cleanup_dirs.add(os.path.abspath(path))

    def cleanup_temporary_storage():
        while cleanup_dirs:
            temporary_dir = cleanup_dirs.pop()
            shutil.rmtree(temporary_dir, ignore_errors=True)

    try:
        try:
            from core.desktop_session import load_desktop_session

            ui_host, ui_port = _desktop_bind()
            desktop_session = load_desktop_session(port=ui_port)
            url = desktop_session.origin
            webview_storage_dir = desktop_session.webview_storage_dir
            register_temporary_storage(
                getattr(desktop_session, "cleanup_dir", None)
            )
        except Exception:
            print(
                "[SYSTEM] Persistent desktop session unavailable; "
                "using temporary storage."
            )
            ui_host, ui_port = _desktop_bind()
            url = f"http://{ui_host}:{ui_port}"
            cleanup_dir = tempfile.mkdtemp(prefix="jarvis-desktop-emergency-")
            register_temporary_storage(cleanup_dir)
            try:
                os.chmod(cleanup_dir, 0o700)
            except OSError:
                pass
            webview_storage_dir = cleanup_dir

        print("--- Starting J.A.R.V.I.S. Desktop Engine ---")

        # 2. Start backend if needed
        backend_proc = None
        if not is_backend_running(url):
            print("[SYSTEM] Starting backend server...")
            startup_nonce = secrets.token_urlsafe(32)
            backend_env = os.environ.copy()
            backend_env["JARVIS_STARTUP_NONCE"] = startup_nonce
            # Inherit stdout/stderr so startup diagnostics remain visible.
            backend_proc = subprocess.Popen(
                [sys.executable, backend_script],
                cwd=root_dir,
                env=backend_env,
            )

            # Wait up to 90s because local ML models can be heavy.
            attempts = 0
            while attempts < 90:
                if is_backend_running(url, expected_nonce=startup_nonce):
                    print("[SYSTEM] Server ready.")
                    break
                time.sleep(1)
                attempts += 1
                if attempts % 10 == 0:
                    print(f"[SYSTEM] Waiting for core ({attempts}s)...")
            else:
                print("[ERROR] Core did not respond after 90s. Check terminal.")
                if backend_proc:
                    backend_proc.terminate()
                return
        else:
            print("[SYSTEM] Server is already running.")

        # 3. Create desktop window
        window = webview.create_window(
            'J.A.R.V.I.S. - Just A Rather Very Intelligent System',
            url,
            width=1366,
            height=768,
            min_size=(1024, 768),
            background_color='#000000',
            text_select=False,
            confirm_close=True
        )

        def on_closed():
            print("[SYSTEM] Closing application...")
            if backend_proc:
                backend_proc.terminate()
            cleanup_temporary_storage()
            os._exit(0)

        window.events.closed += on_closed

        # 4. Start desktop app with persistent Edge Chromium storage.
        print("[SYSTEM] Opening user interface...")
        gui_engine = 'edgechromium' if sys.platform == 'win32' else None

        try:
            webview.start(
                gui=gui_engine,
                debug=False,
                private_mode=False,
                storage_path=webview_storage_dir,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/DesktopJarvis/1.0"
            )
        except Exception as exc:
            if "0x800700AA" in str(exc) or "recurso solicitado" in str(exc) or "EBWebView" in str(exc):
                print("[SYSTEM] Default webview storage locked; using fresh isolated storage...")
                fallback_storage = tempfile.mkdtemp(prefix="jarvis-webview-isolated-")
                register_temporary_storage(fallback_storage)
                webview.start(
                    gui=gui_engine,
                    debug=False,
                    private_mode=False,
                    storage_path=fallback_storage,
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/DesktopJarvis/1.0"
                )
            else:
                raise
    finally:
        cleanup_temporary_storage()


if __name__ == "__main__":
    try:
        start_app()
    except KeyboardInterrupt:
        sys.exit(0)
