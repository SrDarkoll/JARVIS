"""Desktop window-control tools for Windows-first local operation."""

from __future__ import annotations

import sys
from typing import Any

from langchain_core.tools import tool

IS_WINDOWS = sys.platform == "win32"


def _window_snapshot(maximo: int = 10) -> list[dict[str, Any]]:
    max_items = max(1, min(int(maximo or 10), 50))
    if not IS_WINDOWS:
        return []

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    windows: list[dict[str, Any]] = []

    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd, _lparam):
        if len(windows) >= max_items:
            return False
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = (buffer.value or "").strip()
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        windows.append({"handle": int(hwnd), "title": title, "pid": int(pid.value)})
        return True

    user32.EnumWindows(enum_proc(_callback), 0)
    return windows


def _find_window(identifier: str, windows: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    needle = str(identifier or "").strip()
    if not needle:
        return None
    snapshot = windows if windows is not None else _window_snapshot(25)
    if needle.isdigit():
        wanted = int(needle)
        for window in snapshot:
            if int(window.get("handle", 0)) == wanted:
                return window
    needle_lower = needle.lower()
    for window in snapshot:
        if needle_lower in str(window.get("title", "")).lower():
            return window
    return None


def _apply_window_action(handle: int, action: str) -> str:
    if not IS_WINDOWS:
        return "Control de ventanas no available fuera de Windows."

    import ctypes

    user32 = ctypes.windll.user32
    accion = str(action or "").strip().lower()
    hwnd = int(handle)

    if accion in {"enfocar", "focus", "activar", "traer"}:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return "Ventana enfocada."
    if accion in {"minimizar", "minimize"}:
        user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        return "Ventana minimizada."
    if accion in {"maximizar", "maximize"}:
        user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
        return "Ventana maximizada."
    if accion in {"restaurar", "restore"}:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        return "Ventana restaurada."
    if accion in {"cerrar", "close"}:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        return "Solicitud de cierre enviada."
    return "Accion de ventana no reconocida. Use enfocar, minimizar, maximizar, restaurar o cerrar."


@tool
def listar_ventanas(maximo: int = 10) -> str:
    """Lista ventanas visibles del escritorio local."""
    try:
        windows = _window_snapshot(maximo)
        if not windows:
            return "No se detectaron ventanas visibles o el control de ventanas no esta available."
        lines = ["Ventanas detectadas:"]
        for window in windows:
            lines.append(
                f"- [{window['handle']}] {window['title']} (PID {window.get('pid', 0)})"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error listando ventanas: {exc}"


@tool
def enfocar_ventana(titulo_o_id: str) -> str:
    """Enfoca una ventana visible por titulo parcial o identificador."""
    try:
        window = _find_window(titulo_o_id)
        if not window:
            return f"No encontre una ventana que coincida con '{titulo_o_id}'."
        result = _apply_window_action(int(window["handle"]), "enfocar")
        return f"{result} Objetivo: {window['title']}."
    except Exception as exc:
        return f"Error enfocando ventana: {exc}"


@tool
def controlar_ventana(titulo_o_id: str, accion: str) -> str:
    """Controla una ventana visible: enfocar, minimizar, maximizar, restaurar o cerrar."""
    try:
        window = _find_window(titulo_o_id)
        if not window:
            return f"No encontre una ventana que coincida con '{titulo_o_id}'."
        result = _apply_window_action(int(window["handle"]), accion)
        return f"{result} Objetivo: {window['title']}."
    except Exception as exc:
        return f"Error controlando ventana: {exc}"
