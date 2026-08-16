"""Live test script for Voice Biometrics & Speaker Identification in J.A.R.V.I.S.

Tests speaker embeddings, cosine similarity, profile enrollment and live identification.
Usage:
    python scripts/test_live_voice_id.py --list                 # List registered voice profiles in SQLite
    python scripts/test_live_voice_id.py --status               # Check model status and dependencies
    python scripts/test_live_voice_id.py --register "Nombre"    # Record 3s sample to enroll/update a profile
    python scripts/test_live_voice_id.py --identify             # Record 3s sample and identify the speaker
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def _ensure_venv():
    venv_py = (
        ROOT_DIR / "venv" / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else ROOT_DIR / "venv" / "bin" / "python"
    )
    if venv_py.is_file():
        current_py = os.path.normcase(os.path.realpath(sys.executable))
        target_py = os.path.normcase(os.path.realpath(str(venv_py)))
        if current_py != target_py:
            command = [str(venv_py), *sys.argv]
            result = subprocess.run(command, cwd=str(ROOT_DIR))
            sys.exit(result.returncode)


_ensure_venv()

BACKEND_DIR = os.path.abspath(os.path.join(str(ROOT_DIR), "src", "backend"))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from core.jarvis_config import RUNTIME_DIR

DB_PATH = os.getenv("JARVIS_DB_PATH") or os.path.join(RUNTIME_DIR, "memoria_jarvis.db")


def log(step: str, detail: str = ""):
    timestamp = time.strftime("%H:%M:%S")
    arrow = ">>" if detail else "=="
    print(f"[{timestamp}] {arrow} {step}")
    if detail:
        print(f"         {detail}")


def check_dependencies() -> bool:
    log("Verificando dependencias de biometría de voz...")
    missing = []
    for pkg in ["torch", "torchaudio", "speechbrain", "sounddevice", "soundfile"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    if missing:
        log(f"Faltan paquetes opcionales: {', '.join(missing)}")
        log("Para instalarlos, ejecuta:")
        log(f"  .\\venv\\Scripts\\pip install {' '.join(missing)}")
        return False
    log("Todas las dependencias de biometría están instaladas correctamente.")
    return True


def list_profiles():
    log("Consultando perfiles de voz registrados en SQLite...")
    if not os.path.isfile(DB_PATH):
        log("Base de datos no encontrada aún. No hay perfiles creados.")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT profile_id, nombre, created_at FROM voice_profiles"
        ).fetchall()
        if not rows:
            log("No hay perfiles de voz guardados aún en la base de datos.")
            return

        print("\n" + "=" * 60)
        print(f" PERFILES REGISTRADOS ({len(rows)} en total)")
        print("=" * 60)
        for pid, nombre, created in rows:
            n_samples = conn.execute(
                "SELECT COUNT(*) FROM voice_embeddings WHERE profile_id=?", (pid,)
            ).fetchone()[0]
            role = "👑 Administrador" if pid in ("default", "admin") else "👤 Invitado"
            print(f" • [{role}] ID: '{pid}' | Nombre: '{nombre}' | Muestras: {n_samples} | Creado: {created}")
        print("=" * 60 + "\n")
    finally:
        conn.close()


def record_sample(seconds: int = 3) -> bytes | None:
    try:
        import io
        import sounddevice as sd
        import soundfile as sf
    except ImportError:
        log("Error: 'sounddevice' no está instalado para grabar audio del micrófono.")
        log("Instálalo con: .\\venv\\Scripts\\pip install sounddevice soundfile")
        return None

    sample_rate = 16000
    log(f"🎤 Habla ahora por tu micrófono ({seconds} segundos)...")
    for s in range(seconds, 0, -1):
        print(f"   [{s}s restantes]...", end="\r", flush=True)
        time.sleep(1)
    print("   [Procesando audio...]                 ")

    recording = sd.rec(int(seconds * sample_rate), samplerate=sample_rate, channels=1, dtype="float32")
    sd.wait()

    buf = io.BytesIO()
    sf.write(buf, recording, sample_rate, format="WAV")
    return buf.getvalue()


def run_identification():
    if not check_dependencies():
        return

    # Enable voice biometrics for this process
    os.environ["JARVIS_VOICE_ID_ENABLED"] = "true"
    os.environ["JARVIS_CORE_MODE"] = "false"

    from voice.identifier import VoiceIdentifier

    identifier = VoiceIdentifier()
    log("Cargando modelo neuronal SpeechBrain ECAPA-VoxCeleb...")
    if not identifier._wait_encoder_ready(timeout=15.0):
        log("Error: El modelo de voz no pudo inicializarse.")
        return

    log("Modelo listo.")
    wav_bytes = record_sample(seconds=3)
    if not wav_bytes:
        return

    log("Extrayendo huella acústica y comparando con perfiles...")
    pid, nombre, sim = identifier.identificar(wav_bytes)

    debug = identifier.get_ultimo_debug()
    print("\n" + "=" * 60)
    print(" RESULTADO DE IDENTIFICACIÓN BIOMÉTRICA")
    print("=" * 60)
    print(f" • Decisión del motor: {debug.get('decision', 'N/A')}")
    print(f" • Mejor coincidencia: {debug.get('top_nombre', 'Ninguno')} ({debug.get('top_profile_id', 'N/A')})")
    print(f" • Similitud Coseno:   {debug.get('top_sim', 0.0):.4f}")
    print(f" • Diferencia Top2:    {debug.get('top2_gap', 0.0):.4f}")
    print("-" * 60)
    if pid:
        print(f" ✅ VOZ RECONOCIDA: ¡Hola, {nombre}! (ID: {pid})")
    else:
        print(" ❓ VOZ NO RECONOCIDA: 'No te conozco, ¿quién eres?'")
    print("=" * 60 + "\n")


def run_registration(nombre: str):
    if not check_dependencies():
        return

    os.environ["JARVIS_VOICE_ID_ENABLED"] = "true"
    os.environ["JARVIS_CORE_MODE"] = "false"

    from voice.identifier import VoiceIdentifier

    identifier = VoiceIdentifier()
    log(f"Iniciando registro de voz para: '{nombre}'...")
    if not identifier._wait_encoder_ready(timeout=15.0):
        log("Error: El modelo de voz no pudo inicializarse.")
        return

    wav_bytes = record_sample(seconds=4)
    if not wav_bytes:
        return

    pid = "default" if nombre.lower() in ("admin", "administrador", "owner") else f"guest_{nombre.lower().replace(' ', '_')}"
    ok = identifier.registrar_voz(wav_bytes, pid, nombre)
    if ok:
        log(f"✅ ¡Voz de '{nombre}' registrada exitosamente con ID '{pid}'!")
        list_profiles()
    else:
        log("❌ No se pudo registrar la huella de voz.")


def main():
    parser = argparse.ArgumentParser(description="Test en vivo de Biometría de Voz de J.A.R.V.I.S.")
    parser.add_argument("--list", action="store_true", help="Listar perfiles registrados")
    parser.add_argument("--status", action="store_true", help="Verificar estado y dependencias")
    parser.add_argument("--register", type=str, help="Registrar o actualizar la voz de una persona")
    parser.add_argument("--identify", action="store_true", help="Identificar quién está hablando por el micrófono")
    args = parser.parse_args()

    print("=" * 60)
    print(" J.A.R.V.I.S. — TEST EN VIVO DE BIOMETRÍA DE VOZ ")
    print("=" * 60)

    if args.list:
        list_profiles()
        return

    if args.status:
        check_dependencies()
        list_profiles()
        return

    if args.register:
        run_registration(args.register)
        return

    if args.identify:
        run_identification()
        return

    # Si no se pasó argumento, muestra el estado y lista perfiles
    check_dependencies()
    list_profiles()


if __name__ == "__main__":
    main()
