"""Desktop Context Watcher for detecting foreground apps and terminal anomalies."""

from __future__ import annotations

import re
import sys
from typing import Any

import psutil

from modules.proactive.models import DesktopContext

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32


_ERROR_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("modulenotfound", r"modulenotfounderror:\s*no module named\s*['\"]?([a-zA-Z0-9_\-]+)"),
    ("syntaxerror", r"syntaxerror:\s*(.+)"),
    ("npmerror", r"npm err!\s*(.+)"),
    ("gitfatal", r"fatal:\s*(.+)"),
    ("traceback", r"traceback \(most recent call last\)"),
    ("connectionrefused", r"connectionrefusederror"),
    ("file_not_found", r"filenotfounderror:\s*(.+)"),
)

_TERMINAL_EXES = frozenset({"powershell.exe", "pwsh.exe", "cmd.exe", "wt.exe", "conhost.exe", "bash.exe"})
_IDE_EXES = frozenset({"code.exe", "devenv.exe", "pycharm64.exe", "idea64.exe", "sublime_text.exe"})
_BROWSER_EXES = frozenset({"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"})


class DesktopContextWatcher:
    """Zero-overhead desktop perception scanner."""

    @staticmethod
    def get_foreground_window_info() -> dict[str, Any]:
        if not IS_WINDOWS:
            return {"handle": 0, "title": "", "pid": 0, "process_name": ""}

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {"handle": 0, "title": "", "pid": 0, "process_name": ""}

        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = str(buffer.value or "").strip()

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_id = int(pid.value)

        process_name = ""
        if process_id:
            try:
                proc = psutil.Process(process_id)
                process_name = proc.name().lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                process_name = ""

        return {
            "handle": int(hwnd),
            "title": title,
            "pid": process_id,
            "process_name": process_name,
        }

    def capture_context(self, inspect_buffer_text: str = "") -> DesktopContext:
        """Capture an instant desktop context snapshot."""
        fg = self.get_foreground_window_info()
        pname = fg.get("process_name", "")
        title = fg.get("title", "")
        pid = fg.get("pid", 0)

        is_term = pname in _TERMINAL_EXES or any(k in title.lower() for k in ("powershell", "cmd", "terminal", "bash"))
        is_ide = pname in _IDE_EXES or "visual studio code" in title.lower() or "vs code" in title.lower()
        is_browser = pname in _BROWSER_EXES or any(k in title.lower() for k in ("chrome", "edge", "firefox", "brave"))

        # Scan text for errors
        text_to_scan = f"{title}\n{inspect_buffer_text}".lower()
        detected: list[str] = []
        for key, pattern in _ERROR_SIGNATURES:
            if re.search(pattern, text_to_scan):
                detected.append(key)

        cpu = float(psutil.cpu_percent(interval=None))
        ram = float(psutil.virtual_memory().percent)

        return DesktopContext(
            active_app=pname or "desktop",
            window_title=title,
            pid=pid,
            is_terminal=is_term,
            is_ide=is_ide,
            is_browser=is_browser,
            cpu_percent=cpu,
            ram_percent=ram,
            detected_errors=tuple(detected),
        )
