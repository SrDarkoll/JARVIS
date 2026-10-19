"""
Herramientas de control del sistema: PC, aplicaciones, volumen, procesos.
Optimizado para Windows con hooks de preparación para Linux/OS-Agnostic.
"""

import math
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

import psutil
from core.command_pipeline.models import ReceiptStatus, ToolExecutionResult
from core.service_container import services
from langchain_core.tools import tool
from services import security_manager
from services.notification_policy import notification_policy
from utils.jarvis_auth import verificar_autorizacion
from utils.jarvis_i18n import get_current_language

from tools._common import (
    MEMORIA_FILE,
    MEMORIA_PROFILES_FILE,
    _normalizar_profile_id,
    _perfiles_memoria,
    jarvis_state,
    memoria_lock,
)
from tools.desktop_app_resolver import (
    ApplicationResolutionError,
    resolve_local_application,
    resolve_registered_application,
    validate_application_name,
)

IS_WINDOWS = sys.platform == "win32"
_AUDIO_CONTROL_LOCK = RLock()
AUDIO_VERIFY_SECONDS = 0.3


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

        from comtypes import CLSCTX_ALL, CoInitialize, CoUninitialize
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        _VOL_AVAILABLE = True
except ImportError:
    _VOL_AVAILABLE = False


# ─────────────────────────────────────────
# Volume Control
# ─────────────────────────────────────────
@contextmanager
def _audio_com_scope():
    initialized = False
    try:
        try:
            CoInitialize()
        except Exception as exc:
            hresult = getattr(exc, "winerror", None)
            if hresult is None:
                hresult = getattr(exc, "hresult", None)
            if hresult not in (-2147417850, 0x80010106):  # RPC_E_CHANGED_MODE
                raise
        else:
            initialized = True
        yield
    finally:
        if initialized:
            CoUninitialize()


def _run_audio_in_com_scope(operation):
    """Discard the operation's COM pointers and error frames before leaving COM."""
    with _AUDIO_CONTROL_LOCK, _audio_com_scope():
        try:
            return operation()
        except Exception:
            pass
    raise RuntimeError("audio_operation_failed")


def _obtener_control_volumen():
    """Acquire a controller on the current thread inside an active audio COM scope."""
    if not IS_WINDOWS:
        raise RuntimeError("Control de volumen nativo solo available en Windows via PyCaw.")
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

    if delta == 0:
        return "Sin cambios de volumen solicitados."
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


def _audio_result(code: str, es: str, en: str, *, status=ReceiptStatus.SUCCEEDED,
                  verified=False, accepted=False, result=None) -> ToolExecutionResult:
    message = en if get_current_language().startswith("en") else es
    return ToolExecutionResult(
        result=message if result is None else result, user_message=message, status=status,
        verified=verified, accepted=accepted, diagnostic_code=code,
    )


def _audio_unavailable() -> ToolExecutionResult:
    return _audio_result(
        "audio_controller_unavailable", "No puedo consultar de forma fiable el audio del sistema. "
        "No cambié su nivel ni su estado de silencio.",
        "System audio could not be read reliably. Its level and mute state were not changed.",
        status=ReceiptStatus.UNAVAILABLE,
    )


def _read_audio_state(volume) -> tuple[float, bool]:
    level = float(volume.GetMasterVolumeLevelScalar())
    muted = int(volume.GetMute())
    if not math.isfinite(level) or not 0 <= level <= 1 or muted not in (0, 1):
        raise RuntimeError("audio_state_invalid")
    return level * 100, bool(muted)


def _leer_volumen_actual() -> float:
    if not _VOL_AVAILABLE:
        raise RuntimeError("audio_controller_unavailable")
    try:
        return _run_audio_in_com_scope(lambda: _read_audio_state(_obtener_control_volumen())[0])
    except Exception:
        raise RuntimeError("audio_state_unavailable") from None


def _confirm_audio_state(volume, *, level: float | None = None, muted: bool) -> bool:
    deadline = time.monotonic() + AUDIO_VERIFY_SECONDS
    while True:
        try:
            actual_level, actual_mute = _read_audio_state(volume)
        except Exception:
            return False
        if actual_mute == muted and (level is None or abs(actual_level - level) <= .5):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(.03)


def _set_audio_mute(muted: bool) -> ToolExecutionResult:
    def set_mute() -> ToolExecutionResult:
        try:
            volume = _obtener_control_volumen()
            _read_audio_state(volume)
        except Exception:
            return _audio_unavailable()
        try:
            volume.SetMute(int(muted), None)
        except Exception:
            return _audio_result("audio_mute_failed", "No pude confirmar el cambio de silencio del audio.",
                                 "The audio mute change could not be confirmed.", status=ReceiptStatus.FAILED)
        if not _confirm_audio_state(volume, muted=muted):
            return _audio_result("audio_mute_unconfirmed", "Se solicitó el cambio de silencio, pero no pude comprobarlo.",
                                 "The mute change was requested, but it could not be verified.", accepted=True)
        return _audio_result("audio_mute_verified", "Audio del sistema silenciado." if muted else
                             "Silencio del audio desactivado; el nivel de volumen no cambió.",
                             "System audio muted." if muted else "Audio unmuted; its volume level was unchanged.",
                             verified=True, result={"muted": muted})

    if not _VOL_AVAILABLE:
        return _audio_unavailable()
    try:
        return _run_audio_in_com_scope(set_mute)
    except Exception:
        return _audio_unavailable()


def _ajustar_volumen_absoluto(objetivo: float) -> ToolExecutionResult:
    try:
        target = float(objetivo)
        if not math.isfinite(target):
            raise ValueError
        target = max(0.0, min(100.0, target))
    except (ValueError, TypeError):
        return _audio_result("audio_level_invalid", "Indica un nivel numérico válido.",
                             "Provide a valid numeric audio level.", status=ReceiptStatus.FAILED)
    def set_level() -> ToolExecutionResult:
        try:
            volume = _obtener_control_volumen()
            _read_audio_state(volume)
        except Exception:
            return _audio_unavailable()
        try:
            volume.SetMasterVolumeLevelScalar(target / 100, None)
            volume.SetMute(int(target == 0), None)
        except Exception:
            return _audio_result("audio_write_failed", "No pude confirmar el ajuste del audio. No envié un segundo cambio.",
                                 "The audio adjustment could not be confirmed. No second change was sent.",
                                 status=ReceiptStatus.FAILED)
        if not _confirm_audio_state(volume, level=target, muted=target == 0):
            return _audio_result("audio_level_unconfirmed", "Se solicitó ajustar el volumen, pero no pude comprobar el resultado.",
                                 "The volume change was requested, but its result could not be verified.", accepted=True)
        return _audio_result("audio_level_verified", f"Volumen comprobado al {target:g}%.",
                             f"Volume verified at {target:g}%.", verified=True,
                             result={"level": target, "muted": target == 0})

    if not _VOL_AVAILABLE:
        return _audio_unavailable()
    try:
        return _run_audio_in_com_scope(set_level)
    except Exception:
        return _audio_unavailable()


def _ajustar_volumen_relativo(delta: float) -> ToolExecutionResult:
    try:
        change = float(delta)
        if not math.isfinite(change):
            raise ValueError
    except (TypeError, ValueError):
        return _audio_result("audio_delta_invalid", "Indica un cambio de volumen válido.",
                             "Provide a valid volume change.", status=ReceiptStatus.FAILED)
    if change == 0:
        return _audio_result("audio_no_change_requested", "No se solicitó ningún cambio de volumen.",
                             "No volume change was requested.", verified=True)
    with _AUDIO_CONTROL_LOCK:
        try:
            actual = _leer_volumen_actual()
        except RuntimeError:
            if not IS_WINDOWS:
                return _audio_unavailable()
            try:
                _windows_key_volume(change)
            except Exception:
                return _audio_result("audio_key_failed", "No pude enviar el ajuste relativo del audio.",
                                     "The relative audio adjustment could not be sent.", status=ReceiptStatus.FAILED)
            return _audio_result("audio_relative_requested", "Se enviaron teclas de volumen; el cambio exacto no está comprobado.",
                                 "Volume key requests were sent; the exact change is not verified.", accepted=True)
        return _ajustar_volumen_absoluto(actual + change)


@tool
def ajustar_volumen(nivel: int | float | str) -> ToolExecutionResult | str:
    """Ajusta el volumen maestro del sistema (0-100), o sube/baja relativamente ('subir', 'bajar', '+10', '-10', '50%', 'bajalo al 50', 'subelo al 100')."""
    try:
        raw = str(nivel).strip().lower()

        if any(w in raw for w in ("unmute", "desilenciar", "desmutear", "quitar silencio", "activar sonido")) or re.search(
            r"\b(?:desactiva(?:r)?|quita(?:r)?|disable|remove|turn off)\b.*\b(?:silencio|mute)\b", raw,
        ):
            return _set_audio_mute(False)
        if any(w in raw for w in ("mute", "mutear", "silenciar", "silencio", "callate", "cállate")):
            return _set_audio_mute(True)

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
            return _audio_result("audio_level_invalid", "Indique un nivel numérico válido.",
                                 "Provide a valid numeric level.", status=ReceiptStatus.FAILED)

        valor = float(m.group(0).replace(",", "."))
        if 0.0 < valor < 1.0:
            valor *= 100.0
        return _ajustar_volumen_absoluto(valor)
    except Exception:
        return _audio_result("audio_adjustment_failed", "No pude completar el ajuste del audio.",
                             "The audio adjustment could not be completed.", status=ReceiptStatus.FAILED)


@tool
def modo_no_molestar(activar: bool) -> ToolExecutionResult:
    """Pausa o reanuda sólo avisos automáticos no urgentes de JARVIS. No silencia el sistema,
    la conversación, los errores importantes ni los recordatorios solicitados."""
    try:
        state = notification_policy.set_quiet(jarvis_state.get_active_profile_id(), activar)
    except Exception:
        return _audio_result("notification_policy_save_failed", "No pude guardar el modo de avisos. El audio del sistema no cambió.",
                             "The notification mode could not be saved. System audio was unchanged.",
                             status=ReceiptStatus.FAILED)
    if state["state"] != "available" or state["quiet"] is not activar:
        return _audio_result("notification_policy_unconfirmed", "Se solicitó el modo de avisos, pero no pude comprobarlo.",
                             "The notification mode was requested, but it could not be verified.", accepted=True)
    return _audio_result("notifications_quiet_enabled" if activar else "notifications_quiet_disabled",
                         "No molestar de JARVIS activado: los avisos no urgentes esperan. La conversación y los recordatorios siguen activos." if activar else
                         "No molestar de JARVIS desactivado: los avisos automáticos pueden reanudarse.",
                         "JARVIS quiet mode enabled: non-urgent notices wait. Conversation and reminders remain active." if activar else
                         "JARVIS quiet mode disabled: automatic notices can resume.", verified=True, result=state)


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
def _application_result(
    message: str, status: ReceiptStatus, code: str,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        result=message, user_message=message, status=status,
        accepted=status is ReceiptStatus.ACCEPTED, verified=False, diagnostic_code=code,
    )


@tool
def abrir_aplicacion(nombre_app: str) -> ToolExecutionResult:
    """Solicita abrir aplicaciones, carpetas del escritorio y juegos en Windows/Linux/macOS."""
    pid = _normalizar_profile_id(jarvis_state.get_active_profile_id())
    if not verificar_autorizacion(pid):
        return _application_result(
            "ACCESO_DENEGADO: Autorización requerida.", ReceiptStatus.BLOCKED, "app_authorization_required",
        )

    app_raw = str(nombre_app or "").strip()
    if not app_raw:
        return _application_result(
            "Nombre de aplicación no especificado.", ReceiptStatus.FAILED, "app_name_required",
        )
    app_lookup = app_raw if re.search(r"[/\\]", app_raw) else re.sub(
        r"\bcs:go\b", "csgo", app_raw, flags=re.IGNORECASE,
    )
    try:
        is_uri = validate_application_name(app_lookup, allow_posix_absolute=not IS_WINDOWS)
    except ApplicationResolutionError as exc:
        return _application_result(
            "Indica un nombre de aplicación o una ruta corta del escritorio válida.", ReceiptStatus.FAILED, str(exc),
        )
    app = app_lookup.lower()
    app_normalized = re.sub(r"^(?:el|la|los|las|un|una|the)\s+", "", app).strip()
    accepted = _application_result(f"Iniciando {app_raw}...", ReceiptStatus.ACCEPTED, "app_launch_accepted")

    if IS_WINDOWS:
        import webbrowser

        def launch_uri(uri: str, message: str = "") -> ToolExecutionResult:
            try:
                if webbrowser.open(uri):
                    return _application_result(
                        message or accepted.user_message, ReceiptStatus.ACCEPTED, "app_launch_accepted",
                    )
            except Exception:
                pass
            return _application_result(f"No se pudo abrir '{app_raw}'.", ReceiptStatus.FAILED, "app_launch_failed")

        if is_uri:
            return launch_uri(app_raw)
        try:
            local_target = resolve_local_application(app_lookup)
            if local_target is None and app_normalized != app:
                local_target = resolve_local_application(app_normalized)
        except ApplicationResolutionError as exc:
            code = str(exc)
            message = (
                f"Hay varias coincidencias para '{app_raw}'. Indica el nombre exacto."
                if code == "app_ambiguous"
                else "No pude completar la búsqueda local de forma segura. Indica un nombre más preciso."
            )
            return _application_result(message, ReceiptStatus.FAILED, code)
        if local_target is not None:
            try:
                os.startfile(str(local_target))
                return accepted
            except Exception:
                return _application_result(f"No se pudo abrir '{app_raw}'.", ReceiptStatus.FAILED, "app_launch_failed")
        if re.search(r"[/\\]", app_raw):
            return _application_result(
                f"No se encontró el ejecutable o acceso directo para '{app_raw}'.", ReceiptStatus.FAILED, "app_not_found",
            )

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
            if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", app_normalized):
                return launch_uri(f"steam://rungameid/{game_id}", f"Iniciando {app_raw} a través de Steam...")

        # 2. Esquemas URI y ejecutables conocidos
        uri_map = {
            "chrome": "chrome.exe",
            "firefox": "firefox.exe",
            "edge": "msedge.exe",
            "navegador": "msedge.exe",
            "browser": "msedge.exe",
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
            "notas": "notepad.exe",
            "explorador": "explorer.exe",
            "archivos": "explorer.exe",
            "cmd": "cmd.exe",
            "terminal": "wt.exe",
            "powershell": "powershell.exe",
            "word": "winword.exe",
            "excel": "excel.exe",
            "powerpoint": "powerpnt.exe",
            "paint": "mspaint.exe",
            "administrador de tareas": "taskmgr.exe",
            "task manager": "taskmgr.exe",
        }
        target = uri_map.get(app) or uri_map.get(app_normalized)
        if not target:
            for k, v in uri_map.items():
                if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", app_normalized):
                    target = v
                    break
        if target:
            if target.endswith(":") or "://" in target:
                return launch_uri(target)
            try:
                os.startfile(target)
                return accepted
            except Exception:
                pass

        # Only literal bare names may fall back to registered applications or PATH.
        direct_names = dict.fromkeys((app_raw, app_normalized)) if not re.search(r"[/\\]", app_raw) else ()
        for direct_name in direct_names:
            try:
                os.startfile(direct_name)
                return accepted
            except Exception:
                pass

        try:
            registered_target = resolve_registered_application(app_normalized)
        except ApplicationResolutionError as exc:
            return _application_result(
                f"No pude resolver de forma única la aplicación '{app_raw}'.", ReceiptStatus.FAILED, str(exc),
            )
        if registered_target is not None:
            try:
                os.startfile(registered_target)
                return accepted
            except Exception:
                return _application_result(f"No se pudo abrir '{app_raw}'.", ReceiptStatus.FAILED, "app_launch_failed")

        services.log_event("open_app_failed", app=app_raw, error="not_found")
        return _application_result(
            f"No se encontró el ejecutable o acceso directo para '{app_raw}'.", ReceiptStatus.FAILED, "app_not_found",
        )
    else:
        try:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", app_raw])
            return accepted
        except Exception as e:
            services.log_event("open_app_failed", app=app_raw, error=type(e).__name__)
            return _application_result(f"No se pudo abrir '{app_raw}'.", ReceiptStatus.FAILED, "app_launch_failed")


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

        from core import jarvis_config
        db_path = os.getenv("JARVIS_DB_PATH") or os.path.join(jarvis_config.MEMORY_DIR, "memoria_jarvis.db")
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
