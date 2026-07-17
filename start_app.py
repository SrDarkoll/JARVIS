import webview
import subprocess
import time
import sys
import os
import shutil
import tempfile
import requests
import threading

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def is_backend_running(url):
    try:
        response = requests.get(f"{url}/api/status", timeout=1)
        return response.status_code == 200
    except requests.RequestException:
        return False

def start_app():
    # 1. Route configuration
    root_dir = os.path.dirname(os.path.abspath(__file__))
    backend_script = os.path.join(root_dir, "src", "backend", "jarvis_backend.py")
    cleanup_dir = None

    def cleanup_temporary_storage():
        nonlocal cleanup_dir
        if not cleanup_dir:
            return
        temporary_dir = cleanup_dir
        cleanup_dir = None
        shutil.rmtree(temporary_dir, ignore_errors=True)

    try:
        try:
            from core.desktop_session import load_desktop_session

            desktop_session = load_desktop_session(port=5002)
            url = desktop_session.origin
            webview_storage_dir = desktop_session.webview_storage_dir
            cleanup_dir = getattr(desktop_session, "cleanup_dir", None)
        except Exception:
            print(
                "[SYSTEM] Persistent desktop session unavailable; "
                "using temporary storage."
            )
            url = "http://localhost:5002"
            cleanup_dir = tempfile.mkdtemp(prefix="jarvis-desktop-emergency-")
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
            # Inherit stdout/stderr so startup diagnostics remain visible.
            backend_proc = subprocess.Popen(
                [sys.executable, backend_script],
                cwd=root_dir
            )

            # Wait up to 90s because local ML models can be heavy.
            attempts = 0
            while attempts < 90:
                if is_backend_running(url):
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

        webview.start(
            gui=gui_engine,
            debug=False,
            private_mode=False,
            storage_path=webview_storage_dir,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/DesktopJarvis/1.0"
        )
    finally:
        cleanup_temporary_storage()

if __name__ == "__main__":
    try:
        start_app()
    except KeyboardInterrupt:
        sys.exit(0)
