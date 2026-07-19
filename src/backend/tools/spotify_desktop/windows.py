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

from tools.spotify_desktop.matching import normalize_text
from tools.spotify_desktop.models import SpotifyCandidate

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
                result.add(int(process.info["pid"]))
        except (psutil.Error, OSError, TypeError, ValueError):
            continue
    return result


def _visible_windows() -> list[SpotifyWindow]:
    if not IS_WINDOWS:
        return []

    user32 = ctypes.windll.user32
    result: list[SpotifyWindow] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(handle, _parameter):
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
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return result


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
    if not IS_WINDOWS:
        return False
    user32 = ctypes.windll.user32
    user32.ShowWindow(wintypes.HWND(handle), 9)
    return bool(user32.SetForegroundWindow(wintypes.HWND(handle)))


def _foreground_window() -> int:
    if not IS_WINDOWS:
        return 0
    return int(ctypes.windll.user32.GetForegroundWindow())


def _window_bounds(handle: int) -> tuple[int, int, int, int]:
    if not IS_WINDOWS:
        raise OSError("spotify_desktop_windows_only")
    rectangle = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(
        wintypes.HWND(handle), ctypes.byref(rectangle)
    ):
        raise OSError("spotify_window_bounds_unavailable")
    return rectangle.left, rectangle.top, rectangle.right, rectangle.bottom


class WindowsSpotifyWindowAdapter:
    def __init__(
        self,
        *,
        process_ids: Callable[[], set[int]] = _spotify_process_ids,
        windows: Callable[[], list[SpotifyWindow]] = _visible_windows,
        start_client: Callable[[], None] = _start_spotify_client,
        focus_window: Callable[[int], bool] = _focus_window,
        foreground_window: Callable[[], int] = _foreground_window,
        window_bounds: Callable[[int], tuple[int, int, int, int]] = _window_bounds,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.25,
    ) -> None:
        self._process_ids = process_ids
        self._windows = windows
        self._start_client = start_client
        self._focus_window = focus_window
        self._foreground_window = foreground_window
        self._window_bounds = window_bounds
        self._monotonic = monotonic
        self._sleep = sleep
        self._poll_interval = max(0.01, poll_interval)

    def discover_window(self) -> SpotifyWindow | None:
        process_ids = self._process_ids()
        candidates = [
            window for window in self._windows() if window.pid in process_ids
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
        return self._focus_window(window.handle) and self.is_foreground(window)

    def is_foreground(self, window: SpotifyWindow) -> bool:
        return self._foreground_window() == window.handle

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
    "repeat_on": {"activar repeticion", "enable repeat"},
    "repeat_off": {"desactivar repeticion", "disable repeat"},
}
_ACTION_ALIASES = {
    "anterior": "previous",
    "pausar": "pause",
    "reanudar": "resume",
    "reproducir": "resume",
    "siguiente": "next",
}
_PLAY_PATTERNS = (
    re.compile(r"^(?:Reproducir|Reanudar)\s+(?P<title>.+),\s+de\s+(?P<artist>.+)$", re.I),
    re.compile(r"^(?:Play|Resume)\s+(?P<title>.+?)\s+by\s+(?P<artist>.+)$", re.I),
    re.compile(r"^(?:Lire|Reproduzir)\s+(?P<title>.+),\s+de\s+(?P<artist>.+)$", re.I),
    re.compile(r"^Riproduci\s+(?P<title>.+),\s+di\s+(?P<artist>.+)$", re.I),
    re.compile(r"^Abspielen\s+(?P<title>.+),\s+von\s+(?P<artist>.+)$", re.I),
)
_NOW_PLAYING_PATTERNS = (
    re.compile(r"^Est[a\u00e1]s escuchando:\s*(?P<title>.+?)\s+de\s+(?P<artist>.+)$", re.I),
    re.compile(r"^Now playing:\s*(?P<title>.+?)\s+by\s+(?P<artist>.+)$", re.I),
)


def _default_uia_root(handle: int):
    from pywinauto import Desktop

    return Desktop(backend="uia").window(handle=handle)


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
        return str(control.window_text() or "").strip()
    except Exception:
        return ""


def _control_type(control: Any) -> str:
    explicit = getattr(control, "control_type", "")
    if explicit:
        return str(explicit)
    info = getattr(control, "element_info", None)
    return str(getattr(info, "control_type", "") or "")


def _invoke_control(control: Any) -> bool:
    try:
        control.invoke()
        return True
    except Exception:
        try:
            control.click_input()
            return True
        except Exception:
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

    def _controls(self, handle: int) -> list[Any]:
        return list(self._root_factory(handle).descendants())

    def _search_control(self, handle: int):
        fallback = None
        for control in self._controls(handle):
            control_type = _control_type(control).lower()
            name = normalize_text(_control_name(control))
            if control_type == "combobox":
                if any(label in name for label in _SEARCH_NAMES):
                    return control
                fallback = fallback or control
            elif control_type == "edit" and (
                not name or any(label in name for label in _SEARCH_NAMES)
            ):
                fallback = fallback or control
        return fallback

    def search(self, handle: int, query: str) -> None:
        clean_query = re.sub(r"\s+", " ", str(query or "")).strip()
        if not clean_query:
            raise ValueError("spotify_search_query_empty")

        control = self._search_control(handle)
        if control is None:
            self._send_shortcut("^k")
            control = self._search_control(handle)
        if control is None:
            raise RuntimeError("spotify_search_unavailable")

        try:
            control.set_focus()
        except Exception:
            pass

        setter = getattr(control, "set_edit_text", None)
        if callable(setter):
            setter(clean_query)
        else:
            value_pattern = getattr(control, "iface_value", None)
            if value_pattern is not None:
                value_pattern.SetValue(clean_query)
            else:
                self._send_shortcut("^a{BACKSPACE}")
                self._send_text(clean_query)
        self._send_shortcut("{ENTER}")

    def read_candidates(self, handle: int) -> list[SpotifyCandidate]:
        self._elements.clear()
        candidates: list[SpotifyCandidate] = []
        seen: set[tuple[str, str]] = set()
        for control in self._controls(handle):
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
        return bool(control is not None and _invoke_control(control))

    def control(self, handle: int, action: str) -> bool:
        normalized_action = normalize_text(action)
        canonical = _ACTION_ALIASES.get(normalized_action, normalized_action)
        names = _CONTROL_NAMES.get(canonical, set())
        for control in self._controls(handle):
            if _control_type(control).lower() not in {"button", "checkbox"}:
                continue
            observed = normalize_text(_control_name(control))
            if any(name in observed for name in names) and _invoke_control(control):
                return True
        return False

    def now_playing(self, handle: int) -> tuple[str, str] | None:
        document_name = ""
        for control in self._controls(handle):
            name = _control_name(control)
            control_type = _control_type(control).lower()
            if control_type == "group":
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
