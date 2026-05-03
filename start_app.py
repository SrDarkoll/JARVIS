import webview
import subprocess
import time
import sys
import os
import requests
import threading

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def is_backend_running(url):
    try:
        response = requests.get(f"{url}/api/status", timeout=1)
        return response.status_code == 200
    except:
        return False

def start_app():
    # 1. Route configuration
    root_dir = os.path.dirname(os.path.abspath(__file__))
    backend_script = os.path.join(root_dir, "src", "backend", "jarvis_backend.py")
    try:
        from core.desktop_session import load_desktop_session

        desktop_session = load_desktop_session(port=5002)
        url = desktop_session.origin
        webview_storage_dir = desktop_session.webview_storage_dir
    except Exception as e:
        print(f"[SYSTEM] Desktop session fallback: {e}")
        url = "http://localhost:5002"
        webview_storage_dir = os.path.join(
            os.environ.get("LOCALAPPDATA") or root_dir,
            "JARVIS",
            "WebView2",
        )
        os.makedirs(webview_storage_dir, exist_ok=True)

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
        intentos = 0
        while intentos < 90:
            if is_backend_running(url):
                print("[SYSTEM] Server ready.")
                break
            time.sleep(1)
            intentos += 1
            if intentos % 10 == 0:
                print(f"[SYSTEM] Waiting for core ({intentos}s)...")
        else:
            print("[ERROR] Core did not respond after 90s. Check terminal.")
            if backend_proc: backend_proc.terminate()
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
        os._exit(0)

    window.events.closed += on_closed

    # 4. Start desktop app with persistent Edge Chromium storage.
    print("[SYSTEM] Opening user interface...")
    webview.start(
        gui='edgechromium', 
        debug=False,
        private_mode=False,
        storage_path=webview_storage_dir,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/DesktopJarvis/1.0"
    )

if __name__ == "__main__":
    try:
        start_app()
    except KeyboardInterrupt:
        sys.exit(0)
