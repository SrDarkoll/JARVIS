"""
Herramientas de control del sistema: PC, aplicaciones, volumen, procesos.
Optimizado para Windows con hooks de preparación para Linux/OS-Agnostic.
"""

import os, re, subprocess, sys
import psutil
from langchain_core.tools import tool
from utils.jarvis_auth import verificar_autorizacion
from tools._common import _normalizar_profile_id, jarvis_state, memoria_lock, _perfiles_memoria, MEMORIA_FILE, MEMORIA_PROFILES_FILE
from core.service_container import services
from services import security_manager

IS_WINDOWS = sys.platform == "win32"

# ─────────────────────────────────────────
# Zona de Carga Condicional de Drivers SO
# ─────────────────────────────────────────
_VOL_AVAILABLE = False
try:
    if IS_WINDOWS:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL, CoInitialize
        from ctypes import POINTER, cast
        _VOL_AVAILABLE = True
except ImportError:
    _VOL_AVAILABLE = False

# ─────────────────────────────────────────
# Volume Control
# ─────────────────────────────────────────
def _obtener_control_volumen():
    if not IS_WINDOWS:
        raise RuntimeError("Control de volumen nativo solo available en Windows via PyCaw.")
    CoInitialize()
    devices = AudioUtilities.GetSpeakers()
    if hasattr(devices, "Activate"):
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))
    endpoint = getattr(devices, "EndpointVolume", None)
    if endpoint is not None: return endpoint
    raise RuntimeError("I could not get the volume controller del sistema.")

def _leer_volumen_actual() -> float:
    try:
        if not _VOL_AVAILABLE: return 0.0
        volume = _obtener_control_volumen()
        return max(0.0, min(100.0, volume.GetMasterVolumeLevelScalar() * 100.0))
    except Exception: return 0.0

def _ajustar_volumen_absoluto(objetivo: float) -> str:
    try:
        objetivo = max(0.0, min(100.0, float(objetivo)))
        if not _VOL_AVAILABLE: return "Control de volumen no available en este SO."
        volume = _obtener_control_volumen()
        volume.SetMasterVolumeLevelScalar(objetivo / 100.0, None)
        return f"Volumen al {int(round(objetivo))}%."
    except Exception as e: return f"Error ajustando volumen: {e}"

def _ajustar_volumen_relativo(delta: float) -> str:
    actual = _leer_volumen_actual()
    return _ajustar_volumen_absoluto(actual + float(delta))

@tool
def ajustar_volumen(nivel: int | float | str) -> str:
    """Ajusta el volumen maestro del sistema (0-100)."""
    try:
        raw = str(nivel).strip().lower()
        m = re.search(r"-?\d+(?:[.,]\d+)?", raw)
        if not m: return "Indique un nivel numérico."
        valor = float(m.group(0).replace(",", "."))
        if raw.startswith("+") or raw.startswith("-"): return _ajustar_volumen_relativo(valor)
        return _ajustar_volumen_absoluto(valor)
    except Exception as e: return f"Error: {e}"

@tool
def modo_no_molestar(activar: bool) -> str:
    """Silencia o activa el sonido del sistema."""
    try:
        if not _VOL_AVAILABLE: return "Control de audio no available."
        volume = _obtener_control_volumen()
        volume.SetMute(1 if activar else 0, None)
        return "Sistema silenciado." if activar else "Sonido activado."
    except Exception as e: return f"Error: {e}"

# ─────────────────────────────────────────
# PC Control (OS-Agnostic Basic)
# ─────────────────────────────────────────
@tool
def controlar_pc(accion: str, el_usuario_ya_confirmo: bool = False) -> str:
    """Controlar hardware: apagar, reiniciar, hibernar, bloquear, cancelar.
    REGLA: Para apagar o reiniciar, DEBES primero preguntarle al usuario si esta seguro.
    No llames esta tool con el_usuario_ya_confirmo=True hasta que el usuario te diga que SI.
    """
    accion = accion.lower().strip()
    if accion in ['apagar', 'reiniciar', 'hibernar'] and not el_usuario_ya_confirmo:
        return 'ACCION_DENEGADA: Antes de apagar o reiniciar la PC, preguntale al usuario si esta seguro y espera su respuesta afirmativa.'

    pid = _normalizar_profile_id(jarvis_state.get_active_profile_id())
    if accion in ["apagar", "reiniciar", "hibernar"] and not verificar_autorizacion(pid):
        return "ACCESO_DENEGADO: Requiere autorización del Administrador."

    try:
        if IS_WINDOWS:
            if "apag" in accion: subprocess.Popen(["shutdown", "/s", "/t", "10"]); return "Apagando (10s)."
            if "rein" in accion: subprocess.Popen(["shutdown", "/r", "/t", "10"]); return "Reiniciando (10s)."
            if "hiber" in accion: subprocess.Popen(["shutdown", "/h"]); return "Hibernando."
            if "bloq" in accion: subprocess.Popen(["rundll32", "user32.dll,LockWorkStation"]); return "Bloqueado."
            if "canc" in accion: subprocess.Popen(["shutdown", "/a"]); return "Abortado."
        else: # Linux/Mac hooks
            if "apag" in accion: subprocess.Popen(["sudo", "shutdown", "-h", "now"]); return "Apagando (Linux)."
            if "rein" in accion: subprocess.Popen(["sudo", "reboot"]); return "Reiniciando (Linux)."
            if "bloq" in accion: subprocess.Popen(["gnome-screensaver-command", "-l"]); return "Bloqueado (Linux)."
        return f"Acción '{accion}' no soportada en este entorno."
    except Exception as e: return f"Error: {e}"

# ─────────────────────────────────────────
# Applications
# ─────────────────────────────────────────
@tool
def abrir_aplicacion(nombre_app: str) -> str:
    """Abre aplicaciones locales. Requiere autorización."""
    pid = _normalizar_profile_id(jarvis_state.get_active_profile_id())
    if not verificar_autorizacion(pid): return "ACCESO_DENEGADO: Autorización requerida."

    app = str(nombre_app or "").strip().lower()
    app_map = {
        "chrome": "chrome.exe", "firefox": "firefox.exe", "edge": "msedge.exe",
        "vscode": "code.exe", "spotify": "spotify:", "discord": "discord:",
        "notepad": "notepad.exe", "calc": "calc.exe"
    }
    exe = app_map.get(app, app)

    try:
        if IS_WINDOWS:
            import webbrowser
            if exe.endswith(":"): webbrowser.open(exe)
            else: os.startfile(exe)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", exe])
        return f"Iniciando {app}..."
    except Exception as e:
        services.log_event("open_app_failed", app=app, error=str(e))
        return f"No se pudo abrir '{app}'. Error: {e}"

# ─────────────────────────────────────────
# Monitoring
# ─────────────────────────────────────────
@tool
def ver_procesos_pesados() -> str:
    """Muestra procesos con mayor carga en CPU/RAM."""
    try:
        procs = [p.info for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"])]
        top = sorted(procs, key=lambda x: x["cpu_percent"] or 0, reverse=True)[:5]
        res = "Sensores de sistema activados:\n"
        for p in top: res += f"- {p['name']} (PID {p['pid']}): CPU {p['cpu_percent']:.1f}% | RAM {p['memory_percent']:.1f}%\n"
        return res.strip()
    except Exception as e: return f"Sensores offline: {e}"

@tool
def matar_proceso(nombre: str) -> str:
    """Detiene un proceso por nombre. Requiere autorización."""
    pid = _normalizar_profile_id(jarvis_state.get_active_profile_id())
    if not verificar_autorizacion(pid): return "ACCESO_DENEGADO."
    try:
        killed = 0
        for p in psutil.process_iter(["name"]):
            if nombre.lower() in p.info["name"].lower(): p.terminate(); killed += 1
        return f"Interrumpidos {killed} hilos de '{nombre}'." if killed else "No se detectó el proceso."
    except Exception as e: return f"Fallo en terminación: {e}"

@tool
def borrar_memoria() -> str:
    """Reseteo total de memoria. Autorización de nivel 5 obligatoria."""
    pid = _normalizar_profile_id(jarvis_state.get_active_profile_id())
    if not verificar_autorizacion(pid): return "ACCESO_DENEGADO."

    security_manager._security_audit(
        "memory_wipe",
        level="critical",
        tool="borrar_memoria",
        reason="Wipe requested by authorized user",
        source="tool_execution",
        metadata={"profile_id": pid},
    )

    jarvis_state.chat_history.clear()
    jarvis_state.DATOS_CURIOSOS = ""
    with memoria_lock: _perfiles_memoria.clear(); jarvis_state._msg_counter_by_profile.clear()
    for f in [MEMORIA_FILE, MEMORIA_PROFILES_FILE]:
        if os.path.exists(f): os.remove(f)
    try:
        import sqlite3

        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "memoria_jarvis.db")
        db_path = os.path.normpath(db_path)
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("DELETE FROM profiles")
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        services.log_event("memory_clear_db_error", error=str(e)[:200])
    return "Borrado total de bancos de memoria completado, Administrador."
