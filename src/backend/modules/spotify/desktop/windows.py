from __future__ import annotations

import ctypes
import os
import re
import sys
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

import psutil

from modules.spotify.desktop.matching import normalize_text
from modules.spotify.desktop.models import SpotifyCandidate

IS_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class SpotifyWindow:
    handle: int
    pid: int
    title: str


def _spotify_process_ids() -> set[int]:
    result: set[int] = set()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if str(process.info.get("name") or "").lower() == "spotify.exe":
                pid = process.info.get("pid") or getattr(process, "pid", None)
                if pid:
                    result.add(int(pid))
        except (psutil.Error, OSError, TypeError, ValueError, KeyError):
            continue
    return result


def _visible_windows() -> list[SpotifyWindow]:
    if not IS_WINDOWS:
        return []

    user32 = ctypes.windll.user32
    result: list[SpotifyWindow] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    signatures = {
        "OpenInputDesktop": (wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
        "CloseDesktop": (wintypes.BOOL, [wintypes.HANDLE]),
        "EnumDesktopWindows": (wintypes.BOOL, [wintypes.HANDLE, callback_type, wintypes.LPARAM]),
        "EnumWindows": (wintypes.BOOL, [callback_type, wintypes.LPARAM]),
        "IsWindowVisible": (wintypes.BOOL, [wintypes.HWND]),
        "GetWindowTextLengthW": (ctypes.c_int, [wintypes.HWND]),
        "GetWindowTextW": (ctypes.c_int, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]),
        "GetWindowThreadProcessId": (wintypes.DWORD, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]),
    }
    for name, (return_type, arguments) in signatures.items():
        function = getattr(user32, name)
        function.restype = return_type
        function.argtypes = arguments

    def callback(handle, _parameter):
        try:
            if not user32.IsWindowVisible(handle):
                return True
            length = user32.GetWindowTextLengthW(handle)
            if length <= 0:
                return True
            title_buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, title_buffer, length + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            result.append(
                SpotifyWindow(
                    handle=int(handle),
                    pid=int(pid.value),
                    title=title_buffer.value,
                )
            )
        except Exception:
            pass
        return True

    cb = callback_type(callback)
    hdesk = None
    enumerated = False
    try:
        hdesk = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
        if hdesk:
            enumerated = bool(user32.EnumDesktopWindows(hdesk, cb, 0))
    except (OSError, ValueError, ctypes.ArgumentError):
        enumerated = False
    finally:
        if hdesk:
            try:
                user32.CloseDesktop(hdesk)
            except OSError:
                pass
    if not enumerated:
        user32.EnumWindows(cb, 0)
    return list({(window.handle, window.pid): window for window in result}.values())


def _start_spotify_client() -> None:
    if not IS_WINDOWS:
        raise OSError("spotify_desktop_windows_only")

    try:
        os.startfile("spotify:")
        return
    except OSError:
        pass

    executable = os.path.join(os.getenv("APPDATA", ""), "Spotify", "Spotify.exe")
    if not os.path.isfile(executable):
        raise FileNotFoundError("spotify_not_installed")
    os.startfile(executable)


def _focus_window(handle: int) -> bool:
    if not IS_WINDOWS or handle <= 0:
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        signatures = {
            "IsWindow": (wintypes.BOOL, [wintypes.HWND]),
            "IsIconic": (wintypes.BOOL, [wintypes.HWND]),
            "ShowWindow": (wintypes.BOOL, [wintypes.HWND, ctypes.c_int]),
            "GetWindowThreadProcessId": (wintypes.DWORD, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]),
            "AttachThreadInput": (wintypes.BOOL, [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]),
            "SetForegroundWindow": (wintypes.BOOL, [wintypes.HWND]),
            "SetFocus": (wintypes.HWND, [wintypes.HWND]),
            "GetForegroundWindow": (wintypes.HWND, []),
        }
        for name, (return_type, arguments) in signatures.items():
            function = getattr(user32, name)
            function.restype = return_type
            function.argtypes = arguments

        hwnd = wintypes.HWND(handle)
        if not user32.IsWindow(hwnd):
            return False
        current_thread = kernel32.GetCurrentThreadId()
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        if not target_thread:
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        foreground_handle = user32.GetForegroundWindow()
        if int(foreground_handle or 0) == handle:
            return True
        foreground_thread = (
            user32.GetWindowThreadProcessId(foreground_handle, None) if foreground_handle else 0
        )
        attached_threads: list[int] = []
        try:
            for thread_id in dict.fromkeys((foreground_thread, target_thread)):
                if not thread_id or thread_id == current_thread:
                    continue
                if not user32.AttachThreadInput(current_thread, thread_id, True):
                    return False
                attached_threads.append(thread_id)
            if not user32.SetForegroundWindow(hwnd):
                return False
            user32.SetFocus(hwnd)
            return int(user32.GetForegroundWindow() or 0) == handle
        finally:
            for thread_id in reversed(attached_threads):
                user32.AttachThreadInput(current_thread, thread_id, False)
    except (AttributeError, OSError, ValueError):
        return False


def _activate_process_window(pid: int) -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import pythoncom
        from win32com.client import Dispatch

        pythoncom.CoInitialize()
        try:
            shell = Dispatch("WScript.Shell")
            activated = bool(shell.AppActivate(int(pid)))
            del shell
            return activated
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        return False


def _foreground_window() -> int:
    if not IS_WINDOWS:
        return 0
    function = ctypes.windll.user32.GetForegroundWindow
    function.argtypes = []
    function.restype = wintypes.HWND
    return int(function() or 0)


def _is_spotify_foreground(handle: int) -> bool:
    if not IS_WINDOWS or handle <= 0 or _foreground_window() != handle:
        return False
    try:
        process_id = wintypes.DWORD()
        function = ctypes.windll.user32.GetWindowThreadProcessId
        function.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        function.restype = wintypes.DWORD
        return bool(function(wintypes.HWND(handle), ctypes.byref(process_id))) and (
            int(process_id.value) in _spotify_process_ids()
        )
    except (AttributeError, OSError, ValueError):
        return False


def _window_bounds(handle: int) -> tuple[int, int, int, int]:
    if not IS_WINDOWS:
        raise OSError("spotify_desktop_windows_only")
    rectangle = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(wintypes.HWND(handle), ctypes.byref(rectangle)):
        raise OSError("spotify_window_bounds_unavailable")
    return rectangle.left, rectangle.top, rectangle.right, rectangle.bottom


def _is_usable_spotify_window(window: SpotifyWindow) -> bool:
    try:
        if IS_WINDOWS and ctypes.windll.user32.IsIconic(wintypes.HWND(window.handle)):
            return True
        left, top, right, bottom = _window_bounds(window.handle)
    except (AttributeError, OSError):
        return False
    return right - left >= 320 and bottom - top >= 200


class WindowsSpotifyWindowAdapter:
    def __init__(
        self,
        *,
        process_ids: Callable[[], set[int]] = _spotify_process_ids,
        windows: Callable[[], list[SpotifyWindow]] = _visible_windows,
        start_client: Callable[[], None] = _start_spotify_client,
        focus_window: Callable[[int], bool] = _focus_window,
        activate_process_window: Callable[[int], bool] = _activate_process_window,
        foreground_window: Callable[[], int] = _foreground_window,
        window_bounds: Callable[[int], tuple[int, int, int, int]] = _window_bounds,
        window_validator: Callable[[SpotifyWindow], bool] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.25,
        focus_timeout: float = 1.0,
    ) -> None:
        self._process_ids = process_ids
        self._windows = windows
        self._start_client = start_client
        self._focus_window = focus_window
        self._activate_process_window = activate_process_window
        self._foreground_window = foreground_window
        self._window_bounds = window_bounds
        self._window_validator = window_validator or (
            _is_usable_spotify_window if windows is _visible_windows else lambda _window: True
        )
        self._monotonic = monotonic
        self._sleep = sleep
        self._poll_interval = max(0.01, poll_interval)
        self._focus_timeout = max(0.0, focus_timeout)

    def discover_window(self) -> SpotifyWindow | None:
        process_ids = self._process_ids()
        candidates = [
            window for window in self._windows() if window.pid in process_ids and self._window_validator(window)
        ]
        return max(candidates, key=lambda item: len(item.title), default=None)

    def ensure_window(self, timeout: float) -> SpotifyWindow:
        window = self.discover_window()
        if window is not None:
            return window

        self._start_client()
        deadline = self._monotonic() + max(0.0, timeout)
        while True:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise TimeoutError("spotify_window_not_found")
            self._sleep(min(self._poll_interval, remaining))
            window = self.discover_window()
            if window is not None:
                return window

    def focus(self, window: SpotifyWindow) -> bool:
        if not self._matches_window(window):
            return False
        focused = self._focus_window(window.handle)
        if not focused or not self.is_foreground(window):
            if not self._matches_window(window):
                return False
            self._activate_process_window(window.pid)
        deadline = self._monotonic() + self._focus_timeout
        while True:
            if self.is_foreground(window):
                return True
            if not self._matches_window(window):
                return False
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return False
            self._sleep(min(self._poll_interval, remaining))

    def is_foreground(self, window: SpotifyWindow) -> bool:
        return self._foreground_window() == window.handle and self._matches_window(window)

    def _matches_window(self, window: SpotifyWindow) -> bool:
        return window.pid in self._process_ids() and any(
            candidate.handle == window.handle and candidate.pid == window.pid
            for candidate in self._windows()
        )

    def current_title(self, window: SpotifyWindow) -> str:
        for candidate in self._windows():
            if candidate.handle == window.handle and candidate.pid == window.pid:
                return candidate.title
        return ""

    def bounds(self, window: SpotifyWindow) -> tuple[int, int, int, int]:
        return self._window_bounds(window.handle)


_SEARCH_NAMES = {
    "buscar",
    "que quieres reproducir",
    "search",
    "what do you want to play",
}
_CONTROL_NAMES = {
    "pause": {"pause", "pausar"},
    "resume": {"play", "reanudar", "reproducir", "resume"},
    "next": {"next", "siguiente"},
    "previous": {"anterior", "previous"},
    "shuffle_on": {"activar aleatorio", "enable shuffle", "habilitar el modo aleatorio"},
    "shuffle_off": {
        "desactivar aleatorio",
        "disable shuffle",
        "deshabilitar el modo aleatorio",
    },
    "repeat_on": {
        "activar repeticion",
        "activar repetición",
        "enable repeat",
        "repetir",
        "activar bucle",
        "modo repeticion",
        "repeat",
    },
    "repeat_off": {
        "desactivar repeticion",
        "desactivar repetición",
        "disable repeat",
        "no repetir",
        "desactivar bucle",
    },
    "volume_up": {
        "subir volumen",
        "aumentar volumen",
        "volume up",
    },
    "volume_down": {
        "bajar volumen",
        "disminuir volumen",
        "volume down",
    },
    "mute": {
        "silenciar",
        "mute",
        "mutear",
    },
    "unmute": {
        "desilenciar",
        "unmute",
    },
    "like": {
        "guardar en tus me gusta",
        "guardar en tu biblioteca",
        "guardar en tus canciones que te gustan",
        "guardar en canciones que te gustan",
        "anadir a canciones que te gustan",
        "anadir a tus canciones que te gustan",
        "anadir a tu biblioteca",
        "anadir a la biblioteca",
        "save to your library",
        "save to your liked songs",
        "add to your library",
        "add to liked songs",
        "save to liked songs",
        "guardar en me gusta",
    },
    "unlike": {
        "quitar de tus me gusta",
        "quitar de tu biblioteca",
        "quitar de canciones que te gustan",
        "quitar de tus canciones que te gustan",
        "eliminar de tus me gusta",
        "eliminar de tu biblioteca",
        "eliminar de canciones que te gustan",
        "eliminar de tus canciones que te gustan",
        "remove from your library",
        "remove from liked songs",
        "remove from your liked songs",
        "ya no me gusta",
    },
}
_ACTION_ALIASES = {
    "pause": "pause",
    "pausar": "pause",
    "pausa": "pause",
    "detener": "pause",
    "deten": "pause",
    "resume": "resume",
    "reanudar": "resume",
    "reproducir": "resume",
    "next": "next",
    "siguiente": "next",
    "previous": "previous",
    "anterior": "previous",
    "prev": "previous",
    "atras": "previous",
    "shuffle on": "shuffle_on",
    "activar shuffle": "shuffle_on",
    "activa shuffle": "shuffle_on",
    "mezcla on": "shuffle_on",
    "aleatorio on": "shuffle_on",
    "aleatorio": "shuffle_on",
    "shuffle off": "shuffle_off",
    "desactivar shuffle": "shuffle_off",
    "desactiva shuffle": "shuffle_off",
    "mezcla off": "shuffle_off",
    "aleatorio off": "shuffle_off",
    "desactivar aleatorio": "shuffle_off",
    "dar like": "like",
    "me gusta": "like",
    "guardar": "like",
    "favorito": "like",
    "anadir a favoritos": "like",
    "añadir a favoritos": "like",
    "guardar en favoritos": "like",
    "quitar like": "unlike",
    "dislike": "unlike",
    "no me gusta": "unlike",
    "quitar me gusta": "unlike",
    "eliminar de favoritos": "unlike",
    "quitar de favoritos": "unlike",
    "repetir": "repeat_on",
    "activar repetir": "repeat_on",
    "activar repeticion": "repeat_on",
    "activar repetición": "repeat_on",
    "repeat on": "repeat_on",
    "repeat": "repeat_on",
    "no repetir": "repeat_off",
    "desactivar repeticion": "repeat_off",
    "desactivar repetición": "repeat_off",
    "repeat off": "repeat_off",
    "subir volumen": "volume_up",
    "mas volumen": "volume_up",
    "más volumen": "volume_up",
    "aumentar volumen": "volume_up",
    "volume up": "volume_up",
    "bajar volumen": "volume_down",
    "menos volumen": "volume_down",
    "disminuir volumen": "volume_down",
    "volume down": "volume_down",
    "silenciar": "mute",
    "mute": "mute",
    "mutear": "mute",
    "desilenciar": "unmute",
    "unmute": "unmute",
}
_MENU_ACTION_TERMS = {
    "queue": {
        "add to queue",
        "anadir a la cola",
        "agregar a la cola",
        "anadir a la fila",
        "agregar a la fila",
    },
    "like": {
        "save to your liked songs",
        "save to liked songs",
        "add to your liked songs",
        "add to liked songs",
        "guardar en tus me gusta",
        "anadir a tus canciones que te gustan",
        "anadir a canciones que te gustan",
    },
    "unlike": {
        "remove from your liked songs",
        "remove from liked songs",
        "quitar de tus me gusta",
        "eliminar de tus me gusta",
        "quitar de tus canciones que te gustan",
        "eliminar de tus canciones que te gustan",
    },
}
_PLAY_PATTERNS = (
    re.compile(r"^(?:Reproducir|Reanudar)\s+(?P<title>.+),\s+de\s+(?P<artist>.+)$", re.I),
    re.compile(r"^(?:Play|Resume)\s+(?P<title>.+?)\s+by\s+(?P<artist>.+)$", re.I),
    re.compile(r"^(?:Lire|Reproduzir)\s+(?P<title>.+),\s+de\s+(?P<artist>.+)$", re.I),
    re.compile(r"^Riproduci\s+(?P<title>.+),\s+di\s+(?P<artist>.+)$", re.I),
    re.compile(r"^Abspielen\s+(?P<title>.+),\s+von\s+(?P<artist>.+)$", re.I),
)
_NOW_PLAYING_PATTERNS = (
    re.compile(r"^Est[a\u00e1]s escuchando:\s*(?P<title>.+)\s+de\s+(?P<artist>.+)$", re.I),
    re.compile(r"^Now playing:\s*(?P<title>.+)\s+by\s+(?P<artist>.+)$", re.I),
)


def _default_uia_root(handle: int):
    from pywinauto import Desktop

    # top_window() can select an auxiliary Chromium window instead of the
    # exact Spotify HWND that the controller validated.
    return Desktop(backend="uia").window(handle=handle).wrapper_object()


def _default_shortcut(shortcut: str) -> None:
    from pywinauto.keyboard import send_keys

    send_keys(shortcut, pause=0.05)


def _default_text_input(value: str) -> None:
    from pywinauto.keyboard import send_keys

    send_keys(
        _escape_key_sequence(value),
        with_spaces=True,
        pause=0.01,
    )


def _control_name(control: Any) -> str:
    try:
        info = getattr(control, "element_info", None)
        name = str(getattr(info, "name", "") or "").strip()
        if name:
            return name
        return str(control.window_text() or "").strip()
    except Exception:
        return ""


def _control_type(control: Any) -> str:
    explicit = getattr(control, "control_type", "")
    if explicit:
        return str(explicit)
    info = getattr(control, "element_info", None)
    return str(getattr(info, "control_type", "") or "")


def _optional_pattern(control: Any, name: str):
    # pywinauto properties raise NoPatternInterfaceError while being read,
    # not only when their methods are called.
    try:
        return getattr(control, name, None)
    except Exception:
        return None


def _invoke_control(
    control: Any,
    *,
    can_activate: Callable[[], bool] | None = None,
    handle: int | None = None,
) -> bool:
    allowed = can_activate or (lambda: True)
    # 1. Direct UIA Invoke pattern
    invoke = getattr(control, "invoke", None)
    if callable(invoke) and allowed():
        try:
            invoke()
            return True
        except Exception:
            pass

    # 2. iface_invoke pattern COM call
    iface_invoke = _optional_pattern(control, "iface_invoke")
    if iface_invoke is not None and allowed():
        try:
            iface_invoke.Invoke()
            return True
        except Exception:
            pass

    # 3. Legacy IAccessible DoDefaultAction
    iface_legacy = _optional_pattern(control, "iface_legacy_iaccessible")
    if iface_legacy is not None and allowed():
        try:
            iface_legacy.DoDefaultAction()
            return True
        except Exception:
            pass

    # Selection highlights a result but does not necessarily play it.
    # Only an actual activation or click counts as a dispatched action.
    click = getattr(control, "click_input", None)
    if callable(click) and allowed():
        return _click_control(control, can_activate=allowed, handle=handle)

    return False


def _click_control(
    control: Any,
    *,
    can_activate: Callable[[], bool] | None = None,
    handle: int | None = None,
) -> bool:
    try:
        if handle is not None:
            rectangle = control.rectangle()
            left, top, right, bottom = _window_bounds(handle)
            if not (
                left <= rectangle.left < rectangle.right <= right
                and top <= rectangle.top < rectangle.bottom <= bottom
            ):
                return False
        if can_activate is not None and not can_activate():
            return False
        control.click_input()
        return True
    except Exception:
        return False


def _is_safe_click_rect(rect: Any, win_bounds: tuple[int, int, int, int] | None = None) -> bool:
    """Validate that clicking this rectangle stays strictly inside Spotify and far from the taskbar."""
    if rect is None:
        return False
    try:
        left = int(getattr(rect, "left", 0))
        right = int(getattr(rect, "right", 0))
        top = int(getattr(rect, "top", 0))
        bottom = int(getattr(rect, "bottom", 0))
        if right <= left or bottom <= top:
            return False
        cx = (left + right) // 2
        cy = (top + bottom) // 2

        if win_bounds is not None:
            w_left, w_top, w_right, w_bottom = win_bounds
            # Verify coordinates are safely inside Spotify's window
            if not (w_left + 15 < cx < w_right - 15):
                return False
            # Verify coordinates avoid window titlebar (top 45px) and taskbar (bottom 55px)
            if not (w_top + 45 < cy < w_bottom - 55):
                return False
            return True

        # No guessed screen coordinates when the target bounds are unknown.
        return False
    except Exception:
        return False


def _prepare_control_for_activation(control: Any) -> tuple[bool, bool]:
    visibility = getattr(control, "is_visible", None)
    if not callable(visibility):
        return True, False
    try:
        if visibility():
            return True, False
    except Exception:
        return True, False

    scroll_item = _optional_pattern(control, "iface_scroll_item")
    if scroll_item is None:
        return False, False
    try:
        scroll_item.ScrollIntoView()
    except Exception:
        return False, False
    return True, True


def _is_player_control(control: Any) -> bool:
    parent_method = getattr(control, "parent", None)
    if not callable(parent_method):
        return True
    try:
        parent = parent_method()
    except Exception:
        return True
    labels = (
        "controles del reproductor",
        "player controls",
        "commandes du lecteur",
        "controlli del lettore",
        "controles do player",
        "wiedergabesteuerung",
        "now playing",
        "reproduciendo ahora",
        "now playing bar",
        "barra de reproduccion",
        "now-playing-bar",
        "bottom-bar",
    )
    for _depth in range(4):
        if parent is None:
            break
        observed = normalize_text(_control_name(parent))
        if any(label in observed for label in labels):
            return True
        next_parent = getattr(parent, "parent", None)
        if not callable(next_parent):
            break
        try:
            parent = next_parent()
        except Exception:
            break
    return False


def _parse_play_button(name: str) -> tuple[str, str] | None:
    for pattern in _PLAY_PATTERNS:
        match = pattern.match(name)
        if match:
            title = match.group("title").strip()
            artist = match.group("artist").strip()
            if title and artist:
                return title, artist
    return None


def _escape_key_sequence(value: str) -> str:
    replacements = {
        "+": "{+}",
        "^": "{^}",
        "%": "{%}",
        "~": "{~}",
        "(": "{(}",
        ")": "{)}",
        "{": "{{}",
        "}": "{}}",
    }
    return "".join(replacements.get(character, character) for character in value)


class SpotifyUIAutomationAdapter:
    def __init__(
        self,
        *,
        root_factory: Callable[[int], Any] = _default_uia_root,
        send_shortcut: Callable[[str], None] = _default_shortcut,
        send_text: Callable[[str], None] = _default_text_input,
        max_candidates: int = 25,
    ) -> None:
        self._root_factory = root_factory
        self._send_shortcut = send_shortcut
        self._send_text = send_text
        self._max_candidates = max(1, max_candidates)
        self._elements: dict[str, Any] = {}
        self._elements_handle: int | None = None

    @staticmethod
    def _require_foreground(handle: int) -> None:
        if not _is_spotify_foreground(handle):
            raise RuntimeError("spotify_focus_lost")

    def _shortcut(self, handle: int, shortcut: str) -> None:
        self._require_foreground(handle)
        self._send_shortcut(shortcut)

    def _controls(self, handle: int, *, control_types: tuple[str, ...] = ()) -> list[Any]:
        try:
            root = self._root_factory(handle)
            if control_types:
                try:
                    return [control for kind in control_types for control in root.descendants(control_type=kind)]
                except TypeError:
                    # Small injected adapters may expose only descendants().
                    pass
            return list(root.descendants())
        except Exception:
            return []

    def _search_control(self, handle: int):
        for control in self._controls(handle, control_types=("ComboBox", "Edit")):
            control_type = _control_type(control).lower()
            name = normalize_text(_control_name(control))
            if control_type not in {"combobox", "edit"} or name not in _SEARCH_NAMES:
                continue
            visible = getattr(control, "is_visible", None)
            enabled = getattr(control, "is_enabled", None)
            try:
                if callable(visible) and not visible():
                    continue
                if callable(enabled) and not enabled():
                    continue
            except Exception:
                continue
            return control
        return None

    def search_available(self, handle: int) -> bool:
        return self._search_control(handle) is not None

    def search(self, handle: int, query: str) -> None:
        clean_query = re.sub(r"\s+", " ", str(query or "")).strip()
        if not clean_query:
            raise ValueError("spotify_search_query_empty")

        control = self._search_control(handle)
        if control is None:
            self._shortcut(handle, "^k")
            control = self._search_control(handle)
        if control is None:
            raise RuntimeError("spotify_search_unavailable")

        self._require_foreground(handle)
        # Chromium may acknowledge UIA SetFocus while keeping keyboard focus
        # on Home or its hidden address bar. Click the semantically located
        # input, never a guessed coordinate, before entering/submitting text.
        if not _click_control(control, can_activate=lambda: _is_spotify_foreground(handle), handle=handle):
            self._require_foreground(handle)
            try:
                control.set_focus()
            except Exception:
                raise RuntimeError("spotify_search_unavailable") from None

        query_written = False
        setter = getattr(control, "set_edit_text", None)
        if callable(setter):
            self._require_foreground(handle)
            try:
                setter(clean_query)
                query_written = True
            except Exception:
                pass

        if not query_written:
            try:
                value_pattern = getattr(control, "iface_value", None)
            except Exception:
                value_pattern = None
            if value_pattern is not None:
                self._require_foreground(handle)
                try:
                    value_pattern.SetValue(clean_query)
                    self._shortcut(handle, "{END}x{BACKSPACE}")
                    query_written = True
                except RuntimeError:
                    raise
                except Exception:
                    pass

        if not query_written:
            self._shortcut(handle, "^a{BACKSPACE}")
            self._require_foreground(handle)
            self._send_text(clean_query)
        self._shortcut(handle, "{ENTER}")

    def read_candidates(self, handle: int) -> list[SpotifyCandidate]:
        self._elements.clear()
        self._elements_handle = handle
        candidates: list[SpotifyCandidate] = []
        seen: set[tuple[str, str]] = set()
        for control in self._controls(handle, control_types=("Button",)):
            if _control_type(control).lower() != "button":
                continue
            parsed = _parse_play_button(_control_name(control))
            if parsed is None:
                continue
            title, artist = parsed
            identity = (normalize_text(title), normalize_text(artist))
            if identity in seen:
                continue
            seen.add(identity)
            element_id = f"candidate-{len(candidates)}"
            candidate = SpotifyCandidate(
                element_id=element_id,
                title=title,
                artist=artist,
                kind="track",
                subtitle="Song",
            )
            self._elements[element_id] = control
            candidates.append(candidate)
            if len(candidates) >= self._max_candidates:
                break
        return candidates

    def activate(self, candidate: SpotifyCandidate) -> bool:
        control = self._elements.get(candidate.element_id)
        handle = self._elements_handle
        if control is None or handle is None or not _is_spotify_foreground(handle):
            return False
        ready, scrolled = _prepare_control_for_activation(control)
        if not ready:
            return False

        def allowed() -> bool:
            return _is_spotify_foreground(handle)

        if scrolled:
            return _invoke_control(control, can_activate=allowed, handle=handle)
        return _click_control(control, can_activate=allowed, handle=handle) or _invoke_control(
            control, can_activate=allowed, handle=handle,
        )

    def activate_fallback(self, candidate: SpotifyCandidate) -> bool:
        control = self._elements.get(candidate.element_id)
        handle = self._elements_handle
        if control is None or handle is None or not _is_spotify_foreground(handle):
            return False
        ready, _scrolled = _prepare_control_for_activation(control)
        return ready and _invoke_control(control, can_activate=lambda: _is_spotify_foreground(handle), handle=handle)

    def _find_context_menu_item(self, handle: int, action: str) -> Any | None:
        terms = _MENU_ACTION_TERMS.get(normalize_text(action), set())
        if not terms:
            return None
        for control in self._controls(handle):
            c_type = _control_type(control).lower()
            if c_type in ("menuitem", "button", "text", "custom"):
                name = normalize_text(_control_name(control))
                if any(term == name or term in name for term in terms):
                    return control
        return None

    def context_action_candidate(
        self,
        candidate: SpotifyCandidate,
        action: str,
        handle: int | None = None,
    ) -> bool:
        canonical = normalize_text(action)
        if canonical not in _MENU_ACTION_TERMS:
            return False
        control = self._elements.get(candidate.element_id)
        if control is None or handle is None or not _is_spotify_foreground(handle):
            return False
        ready, _scrolled = _prepare_control_for_activation(control)
        if not ready:
            return False

        win_bounds = None
        if handle and IS_WINDOWS:
            try:
                win_bounds = _window_bounds(handle)
            except Exception:
                win_bounds = None

        opened = False
        try:
            rect = getattr(control, "rectangle", None)
            if callable(rect):
                r = rect()
                if _is_safe_click_rect(r, win_bounds) and _is_spotify_foreground(handle):
                    control.click_input(button="right")
                    opened = True
        except Exception:
            pass

        if not opened:
            try:
                self._require_foreground(handle)
                control.set_focus()
                self._shortcut(handle, "+{F10}")
                opened = True
            except Exception:
                pass

        if not opened:
            return False

        import time

        time.sleep(0.18)

        if handle:
            menu_item = self._find_context_menu_item(handle, canonical)
            if menu_item is not None:
                if _invoke_control(menu_item, can_activate=lambda: _is_spotify_foreground(handle), handle=handle):
                    return True

        try:
            self._shortcut(handle, "{ESC}")
        except Exception:
            pass
        return False

    def queue_candidate(self, candidate: SpotifyCandidate, handle: int | None = None) -> bool:
        return self.context_action_candidate(candidate, "queue", handle=handle)

    def control(self, handle: int, action: str) -> bool:
        normalized_action = normalize_text(action)
        canonical = _ACTION_ALIASES.get(normalized_action, normalized_action)
        names = _CONTROL_NAMES.get(canonical, set())
        for control in self._controls(handle, control_types=("Button", "CheckBox")):
            if _control_type(control).lower() not in {"button", "checkbox"}:
                continue
            observed = normalize_text(_control_name(control))
            # Match if control is inside player controls or specifically matches like/unlike buttons
            is_specific = canonical in {"like", "unlike"} and any(name in observed for name in names)
            if not is_specific and not _is_player_control(control):
                continue
            if any(observed == name or observed.startswith(name + " ") for name in names):
                if not _is_spotify_foreground(handle):
                    return False
                if _invoke_control(control, can_activate=lambda: _is_spotify_foreground(handle), handle=handle):
                    return True

        # Foreground-scoped Spotify shortcuts are fallbacks when semantic player
        # controls are unavailable. The controller still verifies observable state.
        if canonical in {"pause", "resume"}:
            try:
                self._shortcut(handle, " ")
                return True
            except Exception:
                pass
        elif canonical == "next":
            try:
                self._shortcut(handle, "^{RIGHT}")
                return True
            except Exception:
                pass
        elif canonical == "previous":
            try:
                self._shortcut(handle, "^{LEFT}")
                return True
            except Exception:
                pass
        elif canonical in {"shuffle_on", "shuffle_off"}:
            try:
                self._shortcut(handle, "^s")
                return True
            except Exception:
                pass
        elif canonical in {"repeat_on", "repeat_off"}:
            try:
                self._shortcut(handle, "^r")
                return True
            except Exception:
                pass
        elif canonical == "volume_up":
            try:
                self._shortcut(handle, "^{UP}")
                return True
            except Exception:
                pass
        elif canonical == "volume_down":
            try:
                self._shortcut(handle, "^{DOWN}")
                return True
            except Exception:
                pass
        elif canonical == "mute":
            try:
                self._shortcut(handle, "^+{DOWN}")
                return True
            except Exception:
                pass

        return False

    def playback_state(self, handle: int) -> str | None:
        button_names = {
            normalize_text(_control_name(control))
            for control in self._controls(handle, control_types=("Button",))
            if _control_type(control).lower() == "button" and _is_player_control(control)
        }
        if button_names & {"pause", "pausar"}:
            return "playing"
        if button_names & {"play", "reanudar", "reproducir", "resume"}:
            return "paused"
        return None

    def shuffle_state(self, handle: int) -> bool | None:
        for control in self._controls(handle):
            if _control_type(control).lower() not in {"button", "checkbox"}:
                continue
            if not _is_player_control(control):
                continue
            observed = normalize_text(_control_name(control))
            if any(
                label in observed
                for label in (
                    "deshabilitar el modo aleatorio",
                    "disable shuffle",
                )
            ):
                return True
            if any(
                label in observed
                for label in (
                    "habilitar el modo aleatorio",
                    "enable shuffle",
                )
            ):
                return False
        return None

    def repeat_state(self, handle: int) -> bool | None:
        for control in self._controls(handle):
            if _control_type(control).lower() not in {"button", "checkbox"}:
                continue
            if not _is_player_control(control):
                continue
            observed = normalize_text(_control_name(control))
            if any(
                label in observed
                for label in (
                    "disable repeat",
                    "desactivar repeticion",
                    "no repetir",
                )
            ):
                return True
            if any(
                label in observed
                for label in (
                    "enable repeat",
                    "activar repeticion",
                    "repetir",
                )
            ):
                return False
        return None

    def saved_state(self, handle: int) -> bool | None:
        for control in self._controls(handle):
            if _control_type(control).lower() not in {"button", "checkbox"}:
                continue
            observed = normalize_text(_control_name(control))
            if any(term == observed or term in observed for term in _MENU_ACTION_TERMS["unlike"]):
                return True
            if any(term == observed or term in observed for term in _MENU_ACTION_TERMS["like"]):
                return False
        return None

    def mute_state(self, handle: int) -> bool | None:
        for control in self._controls(handle):
            if _control_type(control).lower() not in {"button", "checkbox"}:
                continue
            if not _is_player_control(control):
                continue
            observed = normalize_text(_control_name(control))
            if observed in {"unmute", "desilenciar"} or "unmute" in observed:
                return True
            if observed in {"mute", "silenciar"} or observed.startswith("mute "):
                return False
        return None

    def now_playing(self, handle: int) -> tuple[str, str] | None:
        document_name = ""
        for control in self._controls(handle):
            name = _control_name(control)
            control_type = _control_type(control).lower()
            if control_type in {"group", "pane", "custom", "region"}:
                for pattern in _NOW_PLAYING_PATTERNS:
                    match = pattern.match(name)
                    if match:
                        return match.group("title").strip(), match.group("artist").strip()
            elif control_type == "document":
                document_name = name

        if " \u2022 " in document_name:
            title, artist = document_name.split(" \u2022 ", 1)
            if title.strip() and artist.strip():
                return title.strip(), artist.strip()
        return None


def send_media_key_event(action: str) -> bool:
    """Send a global media key only while the Spotify process is running."""
    if not IS_WINDOWS or not _spotify_process_ids():
        return False
    user32 = ctypes.windll.user32
    key_map = {
        "pause": 0xB3,
        "resume": 0xB3,
        "next": 0xB0,
        "previous": 0xB1,
    }
    vk_code = key_map.get(str(action or "").lower())
    if not vk_code:
        return False
    user32.keybd_event(vk_code, 0, 0, 0)
    user32.keybd_event(vk_code, 0, 2, 0)
    return True
