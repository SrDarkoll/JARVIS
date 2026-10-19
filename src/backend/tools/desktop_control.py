"""Desktop window-control tools for Windows-first local operation."""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from typing import Any

from core.command_pipeline.models import ReceiptStatus, ToolExecutionResult
from langchain_core.tools import tool

IS_WINDOWS = sys.platform == "win32"
WINDOW_VERIFY_SECONDS = 0.4


def _get_user32():
    import ctypes
    from ctypes import wintypes

    api = ctypes.windll.user32
    signatures = {
        "IsWindow": (wintypes.BOOL, [wintypes.HWND]),
        "IsWindowVisible": (wintypes.BOOL, [wintypes.HWND]),
        "IsIconic": (wintypes.BOOL, [wintypes.HWND]),
        "IsZoomed": (wintypes.BOOL, [wintypes.HWND]),
        "GetForegroundWindow": (wintypes.HWND, []),
        "GetWindowTextLengthW": (ctypes.c_int, [wintypes.HWND]),
        "GetWindowTextW": (ctypes.c_int, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]),
        "GetWindowThreadProcessId": (wintypes.DWORD, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]),
        "ShowWindow": (wintypes.BOOL, [wintypes.HWND, ctypes.c_int]),
        "SetForegroundWindow": (wintypes.BOOL, [wintypes.HWND]),
        "PostMessageW": (wintypes.BOOL, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]),
    }
    for name, (return_type, arguments) in signatures.items():
        function = getattr(api, name)
        function.restype = return_type
        function.argtypes = arguments
    return api


def _result(
    message: str, *, status=ReceiptStatus.SUCCEEDED, verified=False, accepted=False, diagnostic_code=""
) -> ToolExecutionResult:
    return ToolExecutionResult(
        result=message,
        user_message=message,
        status=status,
        verified=verified,
        accepted=accepted,
        diagnostic_code=diagnostic_code,
    )


def _window_snapshot(maximo: int = 10, *, enumerate_all: bool = False) -> list[dict[str, Any]]:
    max_items = max(1, min(int(maximo or 10), 50))
    if not IS_WINDOWS:
        return []

    import ctypes
    from ctypes import wintypes

    user32 = _get_user32()
    windows: list[dict[str, Any]] = []

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]

    def _callback(hwnd, _lparam):
        if not enumerate_all and len(windows) >= max_items:
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
    snapshot = windows if windows is not None else _window_snapshot(50, enumerate_all=True)
    if needle.isdigit():
        wanted = int(needle)
        for window in snapshot:
            if int(window.get("handle", 0)) == wanted:
                return window
        return None
    needle_lower = needle.lower()
    exact = [window for window in snapshot if needle_lower == str(window.get("title", "")).lower()]
    matches = exact or [window for window in snapshot if needle_lower in str(window.get("title", "")).lower()]
    if len(matches) > 1:
        raise ValueError("window_target_ambiguous")
    return matches[0] if matches else None


def _window_state_matches(api, hwnd: int, action: str) -> bool:
    if action == "focus":
        return api.GetForegroundWindow() == hwnd
    if action == "minimize":
        return bool(api.IsIconic(hwnd))
    if action == "maximize":
        return bool(api.IsZoomed(hwnd))
    return bool(api.IsWindowVisible(hwnd) and not api.IsIconic(hwnd) and not api.IsZoomed(hwnd))


def _apply_window_action(handle: int, action: str, expected_pid: int = 0) -> ToolExecutionResult:
    if not IS_WINDOWS:
        return _result(
            "Control de ventanas no disponible fuera de Windows.",
            status=ReceiptStatus.UNAVAILABLE,
            diagnostic_code="window_control_unsupported",
        )

    import ctypes
    from ctypes import wintypes

    user32 = _get_user32()
    accion = str(action or "").strip().lower()
    hwnd = int(handle)
    actions = {
        "enfocar": "focus",
        "focus": "focus",
        "activar": "focus",
        "traer": "focus",
        "minimizar": "minimize",
        "minimize": "minimize",
        "maximizar": "maximize",
        "maximize": "maximize",
        "restaurar": "restore",
        "restore": "restore",
        "cerrar": "close",
        "close": "close",
    }
    selected = actions.get(accion)
    if not selected:
        return _result(
            "Acción no reconocida. Usa enfocar, minimizar, maximizar, restaurar o cerrar.",
            status=ReceiptStatus.FAILED,
            diagnostic_code="window_action_invalid",
        )
    pid = wintypes.DWORD()
    if user32.IsWindow(hwnd):
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value or (expected_pid and pid.value != expected_pid):
        return _result(
            "La ventana cambió o ya no está disponible. Vuelve a listar las ventanas.",
            status=ReceiptStatus.UNAVAILABLE,
            diagnostic_code="window_target_changed",
        )
    original_pid = pid.value

    if selected == "close":
        if not user32.PostMessageW(hwnd, 0x0010, 0, 0):
            return _result(
                "No se pudo enviar la solicitud de cierre. La ventana puede seguir abierta.",
                status=ReceiptStatus.FAILED,
                diagnostic_code="window_close_rejected",
            )
        return _result(
            "Solicitud de cierre enviada; el cierre todavía no está confirmado.",
            accepted=True,
            diagnostic_code="window_close_requested",
        )
    if selected == "focus":
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        success_message = "Ventana enfocada."
    elif selected == "minimize":
        user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        success_message = "Ventana minimizada."
    elif selected == "maximize":
        user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
        success_message = "Ventana maximizada."
    else:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        success_message = "Ventana restaurada."

    deadline = time.monotonic() + WINDOW_VERIFY_SECONDS
    while True:
        current_pid = wintypes.DWORD()
        if user32.IsWindow(hwnd):
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(current_pid))
        if current_pid.value != original_pid:
            return _result(
                "La ventana cambió o desapareció antes de comprobar el resultado. Vuelve a listar las ventanas.",
                status=ReceiptStatus.UNAVAILABLE,
                diagnostic_code="window_target_changed",
            )
        if _window_state_matches(user32, hwnd, selected):
            return _result(success_message, verified=True, diagnostic_code="window_state_verified")
        if time.monotonic() >= deadline:
            return _result(
                "La acción se solicitó, pero no pude confirmar el estado de la ventana. "
                "Vuelve a listar las ventanas antes de intentarlo de nuevo.",
                accepted=True,
                diagnostic_code="window_state_unconfirmed",
            )
        time.sleep(0.02)


@tool
def listar_ventanas(maximo: int = 10) -> str:
    """Lista ventanas visibles del escritorio local."""
    try:
        windows = _window_snapshot(maximo)
        if not windows:
            return "No se detectaron ventanas visibles o el control de ventanas no esta available."
        lines = ["Ventanas detectadas:"]
        for window in windows:
            lines.append(f"- [{window['handle']}] {window['title']} (PID {window.get('pid', 0)})")
        return "\n".join(lines)
    except Exception:
        return "Error al listar ventanas. Vuelve a intentarlo."


def _control_target(titulo_o_id: str, accion: str) -> ToolExecutionResult:
    try:
        if not IS_WINDOWS:
            return _apply_window_action(0, accion)
        window = _find_window(titulo_o_id)
        if not window:
            return _result(
                "No encontré esa ventana. Usa su identificador o vuelve a listar las ventanas.",
                status=ReceiptStatus.UNAVAILABLE,
                diagnostic_code="window_target_not_found",
            )
        result = _apply_window_action(int(window["handle"]), accion, int(window.get("pid", 0)))
        message = f"{result.user_message} Objetivo: {window['title']}."
        return replace(result, result=message, user_message=message)
    except ValueError as exc:
        if str(exc) == "window_target_ambiguous":
            return _result(
                "Hay varias ventanas con ese título. Usa el identificador de la ventana deseada.",
                status=ReceiptStatus.BLOCKED,
                diagnostic_code="window_target_ambiguous",
            )
        return _result(
            "No se pudo identificar la ventana. Vuelve a listar las ventanas.",
            status=ReceiptStatus.FAILED,
            diagnostic_code="window_control_failed",
        )
    except Exception:
        return _result(
            "No se pudo controlar la ventana. Vuelve a listar las ventanas.",
            status=ReceiptStatus.FAILED,
            diagnostic_code="window_control_failed",
        )


@tool
def enfocar_ventana(titulo_o_id: str) -> ToolExecutionResult:
    """Enfoca una ventana visible por titulo parcial o identificador."""
    return _control_target(titulo_o_id, "enfocar")


@tool
def controlar_ventana(titulo_o_id: str, accion: str) -> ToolExecutionResult:
    """Controla una ventana visible: enfocar, minimizar, maximizar, restaurar o cerrar."""
    return _control_target(titulo_o_id, accion)
