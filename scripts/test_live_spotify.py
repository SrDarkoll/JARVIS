"""Live desktop integration test for Spotify automation.

Runs directly against the real running Spotify application on Windows without mocks.
Usage:
    python scripts/test_live_spotify.py                  # Runs interactive test suite
    python scripts/test_live_spotify.py --queue "song"   # Tests adding a specific song to queue
    python scripts/test_live_spotify.py --play "song"    # Tests playing a specific song
    python scripts/test_live_spotify.py --controls       # Tests playback controls (pause/resume/next)
    python scripts/test_live_spotify.py --info           # Inspects current track
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

# Auto re-exec into project venv if running with global Python
def _ensure_venv():
    venv_py = ROOT_DIR / "venv" / "Scripts" / "python.exe" if sys.platform == "win32" else ROOT_DIR / "venv" / "bin" / "python"
    if venv_py.is_file():
        current_py = os.path.normcase(os.path.realpath(sys.executable))
        target_py = os.path.normcase(os.path.realpath(str(venv_py)))
        if current_py != target_py:
            command = [str(venv_py), *sys.argv]
            result = subprocess.run(command, cwd=str(ROOT_DIR))
            sys.exit(result.returncode)

_ensure_venv()

# Ensure backend modules are in path
BACKEND_DIR = os.path.abspath(os.path.join(str(ROOT_DIR), "src", "backend"))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from modules.spotify import service
from modules.spotify.desktop.windows import (
    WindowsSpotifyWindowAdapter,
    SpotifyUIAutomationAdapter,
    _control_name,
    _control_type,
)


def log(step: str, detail: str = ""):
    timestamp = time.strftime("%H:%M:%S")
    arrow = ">>" if detail else "=="
    print(f"[{timestamp}] {arrow} {step}")
    if detail:
        print(f"         {detail}")


def test_window_detection():
    log("PASO 1: Detectando ventana real de Spotify...")
    adapter = WindowsSpotifyWindowAdapter()
    window = adapter.discover_window()
    if not window:
        log("No se detectó ventana abierta. Intentando iniciar Spotify...")
        try:
            window = adapter.ensure_window(timeout=10.0)
        except Exception as error:
            log(f"FALLO: No se pudo abrir Spotify: {error}")
            return None

    log(f"Ventana detectada: '{window.title}' | Handle: {window.handle} | PID: {window.pid}")
    
    focused = adapter.focus(window)
    is_fg = adapter.is_foreground(window)
    log(f"Foco en ventana: {'EXITOSO' if focused and is_fg else 'PENDIENTE'}")
    return window


def test_inspect_controls(window):
    log("PASO 2: Inspeccionando elementos UIA de Spotify...")
    uia = SpotifyUIAutomationAdapter()
    controls = uia._controls(window.handle)
    log(f"Controles detectados en Spotify: {len(controls)}")

    buttons = [c for c in controls if _control_type(c).lower() == "button"]
    log(f"Botones interactivos encontrados: {len(buttons)}")
    for b in buttons[:8]:
        log(f"  Botón: '{_control_name(b)}'")

    now_playing = uia.now_playing(window.handle)
    state = uia.playback_state(window.handle)
    log(f"Canción actual en UI: {now_playing}")
    log(f"Estado de reproducción: {state}")
    return uia


def test_queue_flow(song: str):
    log(f"PASO 3: Probando AÑADIR A LA COLA con '{song}'...")
    start = time.monotonic()
    result = service.add_to_queue(song)
    elapsed = round(time.monotonic() - start, 2)
    log(f"Resultado del servicio ({elapsed}s):", result)
    return result


def test_play_flow(song: str):
    log(f"Probando REPRODUCIR con '{song}'...")
    start = time.monotonic()
    result = service.play(song)
    elapsed = round(time.monotonic() - start, 2)
    log(f"Resultado del servicio ({elapsed}s):", result)
    return result


def test_controls_flow():
    log("Probando CONTROLES DE REPRODUCCIÓN...")
    for action in ["pause", "resume", "like", "info"]:
        log(f"Ejecutando control: '{action}'...")
        start = time.monotonic()
        result = service.control(action)
        elapsed = round(time.monotonic() - start, 2)
        log(f"Respuesta ({elapsed}s):", result)
        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description="Test en vivo de Spotify Desktop")
    parser.add_argument("--queue", type=str, help="Canción para añadir a la cola")
    parser.add_argument("--play", type=str, help="Canción para reproducir")
    parser.add_argument("--controls", action="store_true", help="Probar controles")
    parser.add_argument("--info", action="store_true", help="Inspeccionar canción actual")
    args = parser.parse_args()

    print("=" * 60)
    print(" J.A.R.V.I.S. — SPOTIFY LIVE DESKTOP TEST ")
    print("=" * 60)

    window = test_window_detection()
    if not window:
        sys.exit(1)

    if args.info:
        test_inspect_controls(window)
        sys.exit(0)

    if args.queue:
        test_queue_flow(args.queue)
        sys.exit(0)

    if args.play:
        test_play_flow(args.play)
        sys.exit(0)

    if args.controls:
        test_controls_flow()
        sys.exit(0)

    # Si no se pasó argumento, ejecuta diagnóstico completo interactivo
    test_inspect_controls(window)
    print()
    test_queue_flow("Blinding Lights")


if __name__ == "__main__":
    main()
