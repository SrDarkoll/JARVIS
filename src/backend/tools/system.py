"""
Herramientas de control del sistema: PC, aplicaciones, volumen, procesos.
Optimizado para Windows con hooks de preparación para Linux/OS-Agnostic.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import psutil
from core.service_container import services
from langchain_core.tools import tool
from services import security_manager
from utils.jarvis_auth import verificar_autorizacion

from tools._common import (
    MEMORIA_FILE,
    MEMORIA_PROFILES_FILE,
    _normalizar_profile_id,
    _perfiles_memoria,
    jarvis_state,
    memoria_lock,
)

IS_WINDOWS = sys.platform == "win32"


def _system_tools_enabled() -> bool:
    return str(os.getenv("JARVIS_SYSTEM_TOOLS_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}


def _allowed_file_write_roots() -> tuple[Path, ...]:
    raw = str(os.getenv("JARVIS_FILE_WRITE_ROOTS") or "").strip()
    if raw:
        roots = [Path(item).expanduser().resolve(strict=False) for item in raw.split(os.pathsep) if item.strip()]
        if roots:
            return tuple(roots)

    configured_desktop = str(os.getenv("JARVIS_DESKTOP_HOME") or "").strip()
    if configured_desktop:
        return (Path(configured_desktop).expanduser().resolve(strict=False),)

    home = Path.home()
    desktop = home / "Desktop"
    if not desktop.exists():
        desktop = home / "Escritorio"
    return (desktop.resolve(strict=False),)


def _resolve_allowed_write_path(raw_path: str) -> Path | None:
    roots = _allowed_file_write_roots()
    requested = str(raw_path or "").strip() or "nota_jarvis.txt"
    requested = re.sub(
        r"^(?:escritorio|desktop)[/\\]",
        "",
        requested,
        flags=re.IGNORECASE,
    )
    candidate = Path(requested).expanduser()
    if not candidate.is_absolute():
        candidate = roots[0] / candidate
    candidate = candidate.resolve(strict=False)
    if any(candidate == root or root in candidate.parents for root in roots):
        return candidate
    return None


# ─────────────────────────────────────────
# Zona de Carga Condicional de Drivers SO
# ─────────────────────────────────────────
_VOL_AVAILABLE = False
try:
    if IS_WINDOWS:
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL, CoInitialize
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

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
    if endpoint is not None:
        return endpoint
    raise RuntimeError("I could not get the volume controller del sistema.")


def _windows_key_volume(delta: float) -> str:
    """Send Windows virtual media key events (works out-of-the-box on all Windows systems)."""
    import ctypes

    vk = 0xAF if delta > 0 else 0xAE
    steps = max(1, min(50, int(round(abs(delta) / 2.0))))
    for _ in range(steps):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
    action = "aumentado" if delta > 0 else "disminuido"
    return f"Volumen {action}."


def _windows_key_mute() -> str:
    """Toggle mute via Windows virtual media key."""
    import ctypes

    ctypes.windll.user32.keybd_event(0xAD, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0xAD, 0, 2, 0)
    return "Silencio alternado."


def _leer_volumen_actual() -> float:
    try:
        if not _VOL_AVAILABLE:
            return 50.0
        volume = _obtener_control_volumen()
        return max(0.0, min(100.0, volume.GetMasterVolumeLevelScalar() * 100.0))
    except Exception:
        return 50.0


def _ajustar_volumen_absoluto(objetivo: float) -> str:
    try:
        objetivo = max(0.0, min(100.0, float(objetivo)))
        if _VOL_AVAILABLE:
            volume = _obtener_control_volumen()
            volume.SetMasterVolumeLevelScalar(objetivo / 100.0, None)
            return f"Volumen al {int(round(objetivo))}%."
        if IS_WINDOWS:
            # Fallback to virtual key events when pycaw is not installed
            if objetivo == 0:
                return _windows_key_mute()
            actual = _leer_volumen_actual()
            delta = objetivo - actual
            return _windows_key_volume(delta if delta != 0 else 10.0)
        return "Control de volumen no available en este SO."
    except Exception as e:
        if IS_WINDOWS:
            try:
                return _windows_key_volume(10.0 if objetivo > 50 else -10.0)
            except Exception:
                pass
        return f"Error ajustando volumen: {e}"


def _ajustar_volumen_relativo(delta: float) -> str:
    actual = _leer_volumen_actual()
    return _ajustar_volumen_absoluto(actual + float(delta))


@tool
def ajustar_volumen(nivel: int | float | str) -> str:
    """Ajusta el volumen maestro del sistema (0-100), o sube/baja relativamente ('subir', 'bajar', '+10', '-10', '50%', 'bajalo al 50', 'subelo al 100')."""
    try:
        raw = str(nivel).strip().lower()

        if any(w in raw for w in ("mute", "mutear", "silenciar", "silencio", "callate", "cállate")):
            return modo_no_molestar.invoke({"activar": True})

        if any(w in raw for w in ("maximo", "máximo", "max", "todo", "a tope")):
            return _ajustar_volumen_absoluto(100.0)

        if any(w in raw for w in ("minimo", "mínimo", "min", "cero")):
            return _ajustar_volumen_absoluto(0.0)

        # 1. Check for absolute target markers: 'al 50', 'a 80', 'en 40', 'hasta 70', '50%'
        target_match = re.search(r"\b(?:al|a|en|en\s+el|hasta)\s+(\d+(?:[.,]\d+)?)\s*%?", raw)
        if target_match:
            return _ajustar_volumen_absoluto(float(target_match.group(1).replace(",", ".")))

        percent_match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", raw)
        if percent_match:
            return _ajustar_volumen_absoluto(float(percent_match.group(1).replace(",", ".")))

        # 2. Check for relative plus/minus explicitly (+10, -20)
        if raw.startswith("+") or raw.startswith("-"):
            num = re.search(r"-?\d+(?:[.,]\d+)?", raw)
            if num:
                return _ajustar_volumen_relativo(float(num.group(0).replace(",", ".")))

        # 3. Check for relative phrases: 'sube 20', 'baja 15', 'subele un 10'
        rel_up = re.search(r"\b(?:subir|sube|subele|súbele|aumentar|aumenta|mas|más|up)\s+(?:un\s+)?(\d+(?:[.,]\d+)?)", raw)
        if rel_up:
            return _ajustar_volumen_relativo(float(rel_up.group(1).replace(",", ".")))

        rel_down = re.search(r"\b(?:bajar|baja|bajale|bájale|disminuir|disminuye|menos|down)\s+(?:un\s+)?(\d+(?:[.,]\d+)?)", raw)
        if rel_down:
            return _ajustar_volumen_relativo(-float(rel_down.group(1).replace(",", ".")))

        # 4. Pure directional words without numbers
        if any(w in raw for w in ("subir", "sube", "subelo", "súbelo", "subele", "súbele", "aumentar", "aumenta", "up", "mas", "más", "louder", "higher")):
            return _ajustar_volumen_relativo(10.0)

        if any(w in raw for w in ("bajar", "baja", "bajalo", "bájalo", "bajale", "bájale", "disminuir", "disminuye", "down", "menos", "softer", "lower")):
            return _ajustar_volumen_relativo(-10.0)

        # 5. Pure numeric fallback: '50', '80.0', 50
        m = re.search(r"-?\d+(?:[.,]\d+)?", raw)
        if not m:
            return "Indique un nivel numérico."

        valor = float(m.group(0).replace(",", "."))
        if 0.0 < valor < 1.0:
            valor *= 100.0
        return _ajustar_volumen_absoluto(valor)
    except Exception as e:
        return f"Error: {e}"


@tool
def modo_no_molestar(activar: bool) -> str:
    """Silencia o activa el sonido del sistema."""
    try:
        if _VOL_AVAILABLE:
            volume = _obtener_control_volumen()
            volume.SetMute(1 if activar else 0, None)
            return "Sistema silenciado." if activar else "Sonido activado."
        if IS_WINDOWS:
            _windows_key_mute()
            return "Silencio alternado."
        return "Control de audio no available."
    except Exception as e:
        return f"Error: {e}"


# ─────────────────────────────────────────
# PC Control (OS-Agnostic Basic)
# ─────────────────────────────────────────
@tool
def controlar_pc(accion: str, el_usuario_ya_confirmo: bool = False) -> str:
    """Controlar hardware del PC: apagar, reiniciar, hibernar, bloquear, cancelar.
    ADVERTENCIA: Usar UNICAMENTE cuando el usuario solicite de forma EXPLICITA apagar, reiniciar, hibernar o bloquear la computadora (ej. 'apaga la pc', 'reinicia el equipo', 'bloquea la computadora').
    REGLA: Para apagar, reiniciar o bloquear la PC, DEBES primero preguntarle al usuario si esta seguro.
    No llames esta tool con el_usuario_ya_confirmo=True a menos que el usuario haya confirmado afirmativamente de forma explicita.
    """
    accion = accion.lower().strip()
    if accion in ["apagar", "reiniciar", "hibernar", "bloquear", "lock", "shutdown", "reboot"] and not el_usuario_ya_confirmo:
        return "ACCION_DENEGADA: Antes de apagar, reiniciar o bloquear la PC, preguntale al usuario si esta seguro y espera su confirmacion explicita."

    pid = _normalizar_profile_id(jarvis_state.get_active_profile_id())
    if accion in ["apagar", "reiniciar", "hibernar"] and not verificar_autorizacion(pid):
        return "ACCESO_DENEGADO: Requiere autorización del Administrador."

    try:
        if IS_WINDOWS:
            if accion in {"apagar", "apaga", "shutdown"}:
                subprocess.Popen(["shutdown", "/s", "/t", "10"])
                return "Apagando (10s)."
            if accion in {"reiniciar", "reinicia", "reboot", "restart"}:
                subprocess.Popen(["shutdown", "/r", "/t", "10"])
                return "Reiniciando (10s)."
            if accion in {"hibernar", "hiberna", "hibernate"}:
                subprocess.Popen(["shutdown", "/h"])
                return "Hibernando."
            if accion in {"bloquear", "bloquea", "bloquear_pc", "lock", "bloquear_equipo"}:
                subprocess.Popen(["rundll32", "user32.dll,LockWorkStation"])
                return "Bloqueado."
            if accion in {"cancelar", "cancela", "cancel", "abortar"}:
                subprocess.Popen(["shutdown", "/a"])
                return "Abortado."
        else:  # Linux/Mac hooks
            if accion in {"apagar", "apaga", "shutdown"}:
                subprocess.Popen(["sudo", "shutdown", "-h", "now"])
                return "Apagando (Linux)."
            if accion in {"reiniciar", "reinicia", "reboot", "restart"}:
                subprocess.Popen(["sudo", "reboot"])
                return "Reiniciando (Linux)."
            if accion in {"bloquear", "bloquea", "bloquear_pc", "lock"}:
                subprocess.Popen(["gnome-screensaver-command", "-l"])
                return "Bloqueado (Linux)."
        return f"Acción '{accion}' no soportada en este entorno."
    except Exception as e:
        return f"Error: {e}"


# ─────────────────────────────────────────
# Applications
# ─────────────────────────────────────────
@tool
def abrir_aplicacion(nombre_app: str) -> str:
    """Abre aplicaciones locales y juegos en Windows/Linux/macOS."""
    pid = _normalizar_profile_id(jarvis_state.get_active_profile_id())
    if not verificar_autorizacion(pid):
        return "ACCESO_DENEGADO: Autorización requerida."

    app_raw = str(nombre_app or "").strip()
    if not app_raw:
        return "Nombre de aplicación no especificado."
    app = app_raw.lower()

    if IS_WINDOWS:
        import webbrowser

        # 1. Juegos populares de Steam (App IDs)
        steam_games = {
            "counter-strike 2": "730",
            "counter strike 2": "730",
            "counter-strike": "730",
            "counter strike": "730",
            "cs2": "730",
            "cs:go": "730",
            "csgo": "730",
            "dota 2": "570",
            "dota": "570",
            "team fortress 2": "440",
            "tf2": "440",
            "apex legends": "1172470",
            "apex": "1172470",
            "pubg": "578080",
            "gta v": "271590",
            "gta 5": "271590",
            "grand theft auto v": "271590",
            "rust": "252490",
            "cyberpunk 2077": "1091500",
            "cyberpunk": "1091500",
            "terraria": "105600",
            "left 4 dead 2": "550",
            "l4d2": "550",
            "garrys mod": "4000",
            "gmod": "4000",
        }
        for alias, game_id in steam_games.items():
            if alias in app or app in alias:
                try:
                    webbrowser.open(f"steam://rungameid/{game_id}")
                    return f"Iniciando {app_raw} a través de Steam..."
                except Exception:
                    pass

        # 2. Esquemas URI y ejecutables conocidos
        uri_map = {
            "chrome": "chrome.exe",
            "firefox": "firefox.exe",
            "edge": "msedge.exe",
            "vscode": "code.exe",
            "visual studio code": "code.exe",
            "cursor": "cursor.exe",
            "spotify": "spotify:",
            "discord": "discord:",
            "steam": "steam://open/main",
            "roblox": "roblox-player:",
            "minecraft": "minecraft:",
            "epic": "com.epicgames.launcher:",
            "epic games": "com.epicgames.launcher:",
            "whatsapp": "whatsapp:",
            "telegram": "tg:",
            "calculadora": "calc.exe",
            "calc": "calc.exe",
            "bloc de notas": "notepad.exe",
            "notepad": "notepad.exe",
            "explorador": "explorer.exe",
            "cmd": "cmd.exe",
            "terminal": "wt.exe",
            "powershell": "powershell.exe",
            "word": "winword.exe",
            "excel": "excel.exe",
            "powerpoint": "powerpnt.exe",
        }
        if app in uri_map:
            target = uri_map[app]
            if target.endswith(":") or "://" in target:
                webbrowser.open(target)
                return f"Iniciando {app_raw}..."
            try:
                os.startfile(target)
                return f"Iniciando {app_raw}..."
            except Exception:
                pass

        # 3. Intento directo por os.startfile
        try:
            os.startfile(app_raw)
            return f"Iniciando {app_raw}..."
        except Exception:
            pass

        # 4. Búsqueda en accesos directos de Menú Inicio y Escritorio
        roots = [
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
            r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
            os.path.expandvars(r"%USERPROFILE%\Desktop"),
            r"C:\Users\Public\Desktop",
        ]
        all_lnks = []
        for r in roots:
            if os.path.exists(r):
                for dirpath, _, filenames in os.walk(r):
                    for f in filenames:
                        if f.lower().endswith(".lnk") or f.lower().endswith(".url"):
                            all_lnks.append(os.path.join(dirpath, f))

        words = [w for w in app.split() if len(w) > 1]
        for lnk in all_lnks:
            base = os.path.splitext(os.path.basename(lnk))[0].lower()
            if app in base or (words and all(w in base for w in words)):
                try:
                    os.startfile(lnk)
                    return f"Iniciando {app_raw}..."
                except Exception:
                    pass

        services.log_event("open_app_failed", app=app_raw, error="not_found")
        return f"No se encontró el ejecutable o acceso directo para '{app_raw}'."
    else:
        try:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", app_raw])
            return f"Iniciando {app_raw}..."
        except Exception as e:
            services.log_event("open_app_failed", app=app_raw, error=str(e))
            return f"No se pudo abrir '{app_raw}'. Error: {e}"


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
        for p in top:
            res += f"- {p['name']} (PID {p['pid']}): CPU {p['cpu_percent']:.1f}% | RAM {p['memory_percent']:.1f}%\n"
        return res.strip()
    except Exception as e:
        return f"Sensores offline: {e}"


@tool
def matar_proceso(nombre: str) -> str:
    """Detiene un proceso por nombre. Requiere autorización."""
    pid = _normalizar_profile_id(jarvis_state.get_active_profile_id())
    if not verificar_autorizacion(pid):
        return "ACCESO_DENEGADO."
    try:
        killed = 0
        for p in psutil.process_iter(["name"]):
            if nombre.lower() in p.info["name"].lower():
                p.terminate()
                killed += 1
        return f"Interrumpidos {killed} hilos de '{nombre}'." if killed else "No se detectó el proceso."
    except Exception as e:
        return f"Fallo en terminación: {e}"


@tool
def borrar_memoria() -> str:
    """Reseteo total de memoria. Autorización de nivel 5 obligatoria."""
    pid = _normalizar_profile_id(jarvis_state.get_active_profile_id())
    if not verificar_autorizacion(pid):
        return "ACCESO_DENEGADO."

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
    with memoria_lock:
        _perfiles_memoria.clear()
        jarvis_state._msg_counter_by_profile.clear()
    for f in [MEMORIA_FILE, MEMORIA_PROFILES_FILE]:
        if os.path.exists(f):
            os.remove(f)
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


@tool
def crear_archivo_texto(
    nombre_o_ruta: str,
    contenido: str,
    sobrescribir: bool = False,
) -> str:
    """Crea un archivo de texto dentro de las raíces permitidas."""
    if not _system_tools_enabled():
        return "Herramienta deshabilitada. Configure JARVIS_SYSTEM_TOOLS_ENABLED=true para habilitarla."
    pid = _normalizar_profile_id(jarvis_state.get_active_profile_id())
    if not verificar_autorizacion(pid):
        return "ACCESO_DENEGADO: Requiere autorización del Administrador."

    try:
        full_path = _resolve_allowed_write_path(nombre_o_ruta)
        if full_path is None:
            return "Ruta no permitida para escritura."
        if full_path.exists() and not sobrescribir:
            return "El archivo ya existe. Confirme explícitamente que desea sobrescribirlo."

        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(str(contenido or ""), encoding="utf-8")
        return f"Archivo creado exitosamente en '{full_path}'."
    except Exception as exc:
        services.log_event(
            "text_file_write_error",
            error=type(exc).__name__,
        )
        return "No fue posible crear el archivo."


@tool
def ejecutar_comando_terminal(comando: str) -> str:
    """Ejecuta un comando en la consola/terminal o PowerShell del sistema operativo (por ejemplo dir, mkdir, echo, scripts, etc.)."""
    if not _system_tools_enabled():
        return "Herramienta deshabilitada. Configure JARVIS_SYSTEM_TOOLS_ENABLED=true para habilitarla."
    pid = _normalizar_profile_id(jarvis_state.get_active_profile_id())
    if not verificar_autorizacion(pid):
        return "ACCESO_DENEGADO: Requiere autorización del Administrador."

    try:
        cmd_str = str(comando or "").strip()
        if not cmd_str:
            return "Comando vacío."

        if IS_WINDOWS:
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    cmd_str,
                ],
                capture_output=True,
                text=True,
                timeout=20,
                shell=False,
                check=False,
            )
        else:
            proc = subprocess.run(
                cmd_str,
                capture_output=True,
                text=True,
                timeout=20,
                shell=True,
                check=False,
            )

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()

        if proc.returncode == 0:
            res = stdout if stdout else "Comando ejecutado con éxito."
            return f"Salida:\n{res[:1000]}"
        else:
            err = stderr if stderr else stdout
            return f"Comando finalizó con código {proc.returncode}:\n{err[:1000]}"
    except subprocess.TimeoutExpired:
        return "El comando superó el tiempo límite de ejecución (20s)."
    except Exception as exc:
        services.log_event(
            "terminal_command_error",
            error=type(exc).__name__,
        )
        return "No fue posible ejecutar el comando."
