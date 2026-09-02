"""Native OS input automation engine for Windows."""

from __future__ import annotations

import sys
import time

from modules.computer_use.models import (
    ComputerActionResult,
    MouseButton,
    ScreenCoordinate,
    ScrollDirection,
    VirtualScreenBounds,
)

IS_WINDOWS = sys.platform == "win32"

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_ESCAPE = 0x1B

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [
            ("type", wintypes.DWORD),
            ("union", _INPUTUNION),
        ]

    # Ensure DPI awareness for accurate coordinates
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


class FailsafeTriggeredError(Exception):
    """Raised when mouse moves to a screen corner to abort automation."""


_VK_MAP: dict[str, int] = {
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "tab": 0x09,
    "space": 0x20,
    "backspace": 0x08,
    "bksp": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
    "windows": 0x5B,
    "cmd": 0x5B,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
}


class OSInputEngine:
    """Zero-dependency Windows input driver using native Win32 User32 APIs."""

    def __init__(self, failsafe: bool = True) -> None:
        self.failsafe = failsafe

    def get_screen_size(self) -> tuple[int, int]:
        bounds = self.get_screen_bounds()
        return bounds.width, bounds.height

    def get_screen_bounds(self) -> VirtualScreenBounds:
        if not IS_WINDOWS:
            return VirtualScreenBounds(0, 0, 1920, 1080)
        left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        if width <= 0 or height <= 0:
            left = 0
            top = 0
            width = max(1, int(user32.GetSystemMetrics(0)))
            height = max(1, int(user32.GetSystemMetrics(1)))
        return VirtualScreenBounds(left, top, width, height)

    def get_cursor_position(self) -> ScreenCoordinate:
        if not IS_WINDOWS:
            return ScreenCoordinate(0, 0)
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        return ScreenCoordinate(x=int(point.x), y=int(point.y))

    def _check_failsafe(self) -> None:
        if not self.failsafe or not IS_WINDOWS:
            return
        if int(user32.GetAsyncKeyState(VK_ESCAPE)) & 0x8000:
            raise FailsafeTriggeredError("Failsafe activado: la tecla Escape esta presionada.")
        pos = self.get_cursor_position()
        bounds = self.get_screen_bounds()
        near_left = pos.x <= bounds.left + 2
        near_right = pos.x >= bounds.right - 3
        near_top = pos.y <= bounds.top + 2
        near_bottom = pos.y >= bounds.bottom - 3
        if (near_left or near_right) and (near_top or near_bottom):
            raise FailsafeTriggeredError("Failsafe activado: el cursor se movio a una esquina.")

    def move_to(self, x: int, y: int, smooth: bool = False) -> None:
        if not IS_WINDOWS:
            return
        self._check_failsafe()
        target = self.get_screen_bounds().clamp(x, y)
        target_x = target.x
        target_y = target.y

        if smooth:
            current = self.get_cursor_position()
            steps = 15
            for i in range(1, steps + 1):
                cur_x = int(current.x + (target_x - current.x) * (i / steps))
                cur_y = int(current.y + (target_y - current.y) * (i / steps))
                user32.SetCursorPos(cur_x, cur_y)
                time.sleep(0.005)
        else:
            user32.SetCursorPos(target_x, target_y)

    def click(
        self,
        x: int | None = None,
        y: int | None = None,
        button: MouseButton | str = MouseButton.LEFT,
        clicks: int = 1,
    ) -> ComputerActionResult:
        if not IS_WINDOWS:
            return ComputerActionResult(False, "click", "No disponible fuera de Windows.")

        self._check_failsafe()
        if x is not None and y is not None:
            self.move_to(x, y)

        btn_str = str(button.value if isinstance(button, MouseButton) else button).lower()
        if btn_str == "right":
            down_flag = 0x0008  # MOUSEEVENTF_RIGHTDOWN
            up_flag = 0x0010    # MOUSEEVENTF_RIGHTUP
        elif btn_str == "middle":
            down_flag = 0x0020  # MOUSEEVENTF_MIDDLEDOWN
            up_flag = 0x0040    # MOUSEEVENTF_MIDDLEUP
        else:
            down_flag = 0x0002  # MOUSEEVENTF_LEFTDOWN
            up_flag = 0x0004    # MOUSEEVENTF_LEFTUP

        coord = self.get_cursor_position()
        count = max(1, min(int(clicks), 5))
        for i in range(count):
            user32.mouse_event(down_flag, 0, 0, 0, 0)
            time.sleep(0.03)
            user32.mouse_event(up_flag, 0, 0, 0, 0)
            if i < count - 1:
                time.sleep(0.08)

        msg = f"Clic {btn_str} ejecutado en ({coord.x}, {coord.y})" if count == 1 else f"Doble clic {btn_str} en ({coord.x}, {coord.y})"
        return ComputerActionResult(True, "click", msg, coordinate=coord)

    def drag_and_drop(self, start_x: int, start_y: int, end_x: int, end_y: int) -> ComputerActionResult:
        if not IS_WINDOWS:
            return ComputerActionResult(False, "drag_and_drop", "No disponible fuera de Windows.")

        self._check_failsafe()
        self.move_to(start_x, start_y)
        time.sleep(0.05)
        # Left button down
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.05)
        # Smooth drag
        self.move_to(end_x, end_y, smooth=True)
        time.sleep(0.05)
        # Left button up
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.05)

        return ComputerActionResult(
            True,
            "drag_and_drop",
            f"Arrastrado desde ({start_x}, {start_y}) hasta ({end_x}, {end_y}).",
            coordinate=ScreenCoordinate(end_x, end_y),
        )

    def scroll(
        self,
        amount: int = 5,
        direction: ScrollDirection | str = ScrollDirection.DOWN,
    ) -> ComputerActionResult:
        if not IS_WINDOWS:
            return ComputerActionResult(False, "scroll", "No disponible fuera de Windows.")

        self._check_failsafe()
        dir_str = str(direction.value if isinstance(direction, ScrollDirection) else direction).lower()
        delta = int(amount or 5) * 120
        if dir_str in {"down", "abajo"}:
            wheel_delta = -delta
            user32.mouse_event(0x0800, 0, 0, wheel_delta, 0)  # MOUSEEVENTF_WHEEL
        elif dir_str in {"up", "arriba"}:
            wheel_delta = delta
            user32.mouse_event(0x0800, 0, 0, wheel_delta, 0)
        elif dir_str in {"left", "izquierda"}:
            wheel_delta = -delta
            user32.mouse_event(0x01000, 0, 0, wheel_delta, 0)  # MOUSEEVENTF_HWHEEL
        elif dir_str in {"right", "derecha"}:
            wheel_delta = delta
            user32.mouse_event(0x01000, 0, 0, wheel_delta, 0)
        else:
            return ComputerActionResult(False, "scroll", f"Direccion de scroll desconocida: {direction}")

        coord = self.get_cursor_position()
        return ComputerActionResult(
            True,
            "scroll",
            f"Scroll {dir_str} de {amount} unidades realizado.",
            coordinate=coord,
        )

    def type_text(
        self,
        text: str,
        clear_first: bool = False,
        press_enter: bool = False,
    ) -> ComputerActionResult:
        if not IS_WINDOWS:
            return ComputerActionResult(False, "type", "No disponible fuera de Windows.")

        content = str(text or "")
        if not content and not press_enter:
            return ComputerActionResult(False, "type", "No se especifico texto para escribir.")

        self._check_failsafe()

        if clear_first:
            # Select all and delete
            self.press_hotkey("ctrl+a")
            time.sleep(0.05)
            self.press_key("backspace")
            time.sleep(0.05)

        # Send UTF-16 code units so surrogate pairs (for example emoji) remain valid.
        utf16 = content.encode("utf-16-le")
        for index in range(0, len(utf16), 2):
            self._check_failsafe()
            code_unit = int.from_bytes(utf16[index : index + 2], "little")
            self._send_keyboard_input(
                scan_code=code_unit,
                flags=KEYEVENTF_UNICODE,
            )
            self._send_keyboard_input(
                scan_code=code_unit,
                flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
            )
            time.sleep(0.005)

        if press_enter:
            time.sleep(0.03)
            self.press_key("enter")

        return ComputerActionResult(
            True,
            "type",
            f"Texto escrito: '{content[:40]}{'...' if len(content) > 40 else ''}'" + (" [Enter]" if press_enter else ""),
        )

    def press_key(self, key_name: str) -> bool:
        if not IS_WINDOWS:
            return False
        self._check_failsafe()
        k = str(key_name or "").strip().lower()
        vk = _VK_MAP.get(k)
        if vk is None:
            if len(k) == 1 and k.isalnum():
                vk = ord(k.upper())
            else:
                return False

        self._send_keyboard_input(virtual_key=vk)
        time.sleep(0.02)
        self._send_keyboard_input(virtual_key=vk, flags=KEYEVENTF_KEYUP)
        return True

    def press_hotkey(self, keys: str) -> ComputerActionResult:
        if not IS_WINDOWS:
            return ComputerActionResult(False, "hotkey", "No disponible fuera de Windows.")

        parts = [p.strip().lower() for p in str(keys or "").split("+") if p.strip()]
        if not parts:
            return ComputerActionResult(False, "hotkey", "No se especifico combinacion de teclas.")

        self._check_failsafe()
        vk_list: list[int] = []
        for p in parts:
            vk = _VK_MAP.get(p)
            if vk is None:
                if len(p) == 1 and p.isalnum():
                    vk = ord(p.upper())
                else:
                    return ComputerActionResult(False, "hotkey", f"Tecla no reconocida: '{p}'.")
            vk_list.append(vk)

        # Press down in order
        for vk in vk_list:
            self._send_keyboard_input(virtual_key=vk)
            time.sleep(0.01)

        time.sleep(0.04)

        # Release in reverse order
        for vk in reversed(vk_list):
            self._send_keyboard_input(virtual_key=vk, flags=KEYEVENTF_KEYUP)
            time.sleep(0.01)

        return ComputerActionResult(
            True,
            "hotkey",
            f"Atajo de teclado ejecutado: {' + '.join(parts).upper()}",
            details={"keys": parts},
        )

    def _send_keyboard_input(
        self,
        *,
        virtual_key: int = 0,
        scan_code: int = 0,
        flags: int = 0,
    ) -> None:
        if not IS_WINDOWS:
            return
        event = INPUT(
            type=INPUT_KEYBOARD,
            ki=KEYBDINPUT(
                wVk=int(virtual_key),
                wScan=int(scan_code),
                dwFlags=int(flags),
                time=0,
                dwExtraInfo=0,
            ),
        )
        sent = int(user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)))
        if sent != 1:
            raise OSError("send_input_failed")
