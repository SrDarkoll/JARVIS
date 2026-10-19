from __future__ import annotations

import ctypes
from ctypes import wintypes
from types import SimpleNamespace

import pytest
from modules.spotify.desktop import windows


class NativeFunction:
    def __init__(self, implementation):
        self.implementation = implementation
        self.restype = ctypes.c_int
        self.argtypes = None
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.implementation(*args)


@pytest.fixture
def desktop_api(monkeypatch):
    desktop = 0x100000094
    hwnd = 0x200000900
    state = {"open": True, "enumerate": True}

    def open_input(*_args):
        return api.OpenInputDesktop.restype(desktop).value if state["open"] else 0

    def enumerate_desktop(handle, callback, parameter):
        if handle != desktop or not state["enumerate"]:
            return 0
        callback(hwnd, parameter)
        return 1

    def enumerate_windows(callback, parameter):
        callback(hwnd, parameter)
        return 1

    def window_text(_handle, buffer, _length):
        buffer.value = "Spotify"
        return len(buffer.value)

    def process_id(_handle, pointer):
        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD)).contents.value = 42
        return 101

    api = SimpleNamespace(
        OpenInputDesktop=NativeFunction(open_input),
        EnumDesktopWindows=NativeFunction(enumerate_desktop),
        EnumWindows=NativeFunction(enumerate_windows),
        CloseDesktop=NativeFunction(lambda _handle: 1),
        SetThreadDesktop=NativeFunction(lambda _handle: 1),
        IsWindowVisible=NativeFunction(lambda _handle: 1),
        GetWindowTextLengthW=NativeFunction(lambda _handle: 7),
        GetWindowTextW=NativeFunction(window_text),
        GetWindowThreadProcessId=NativeFunction(process_id),
    )
    monkeypatch.setattr(windows, "IS_WINDOWS", True)
    monkeypatch.setattr(windows.ctypes, "windll", SimpleNamespace(user32=api), raising=False)
    monkeypatch.setattr(windows.ctypes, "WINFUNCTYPE", lambda *_types: lambda callback: callback, raising=False)
    return api, state, desktop, hwnd


def test_desktop_enumeration_preserves_pointer_sized_handles_and_closes_them(desktop_api):
    api, _state, desktop, hwnd = desktop_api
    found = windows._visible_windows()
    assert found == [windows.SpotifyWindow(handle=hwnd, pid=42, title="Spotify")]
    assert api.OpenInputDesktop.restype is wintypes.HANDLE
    assert api.CloseDesktop.calls == [(desktop,)]


def test_discovery_never_changes_the_worker_thread_desktop(desktop_api):
    api, _state, _desktop, _hwnd = desktop_api
    windows._visible_windows()
    assert api.SetThreadDesktop.calls == []


def test_enumeration_failure_falls_back_and_releases_the_open_handle(desktop_api):
    api, state, desktop, hwnd = desktop_api
    state["enumerate"] = False
    found = windows._visible_windows()
    assert found[0].handle == hwnd
    assert len(api.EnumWindows.calls) == 1
    assert api.CloseDesktop.calls == [(desktop,)]


def test_denied_input_desktop_uses_current_desktop_without_changing_it(desktop_api):
    api, state, _desktop, hwnd = desktop_api
    state["open"] = False
    found = windows._visible_windows()
    assert found[0].handle == hwnd
    assert api.CloseDesktop.calls == []
    assert api.SetThreadDesktop.calls == []
