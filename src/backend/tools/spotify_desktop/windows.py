from __future__ import annotations

import ctypes
import os
import sys
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass

import psutil

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


class WindowsSpotifyWindowAdapter:
    def __init__(
        self,
        *,
        process_ids: Callable[[], set[int]] = _spotify_process_ids,
        windows: Callable[[], list[SpotifyWindow]] = _visible_windows,
        start_client: Callable[[], None] = _start_spotify_client,
        focus_window: Callable[[int], bool] = _focus_window,
        foreground_window: Callable[[], int] = _foreground_window,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.25,
    ) -> None:
        self._process_ids = process_ids
        self._windows = windows
        self._start_client = start_client
        self._focus_window = focus_window
        self._foreground_window = foreground_window
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
