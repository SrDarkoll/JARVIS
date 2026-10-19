from __future__ import annotations

from types import SimpleNamespace

import pytest
from modules.spotify.desktop import windows


class NativeFunction:
    def __init__(self, implementation):
        self.implementation = implementation
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.implementation(*args)


@pytest.fixture
def focus_api(monkeypatch):
    state = {"foreground": 100, "deny_thread": None, "readback": True}
    attached = set()
    threads = {100: 20, 200: 30}

    def thread_id(handle, _process_id):
        return threads.get(getattr(handle, "value", handle), 0)

    def attach(current, target, enabled):
        if enabled:
            if target == state["deny_thread"]:
                return 0
            attached.add((current, target))
        else:
            attached.discard((current, target))
        return 1

    def foreground(handle):
        if not {(10, 20), (10, 30)}.issubset(attached):
            return 0
        if state["readback"]:
            state["foreground"] = handle.value
        return 1

    api = SimpleNamespace(
        IsWindow=NativeFunction(lambda _handle: 1),
        IsIconic=NativeFunction(lambda _handle: 0),
        ShowWindow=NativeFunction(lambda *_args: 1),
        GetWindowThreadProcessId=NativeFunction(thread_id),
        AttachThreadInput=NativeFunction(attach),
        GetForegroundWindow=NativeFunction(lambda: state["foreground"]),
        SetForegroundWindow=NativeFunction(foreground),
        SetFocus=NativeFunction(lambda handle: handle.value),
    )
    kernel = SimpleNamespace(GetCurrentThreadId=NativeFunction(lambda: 10))
    monkeypatch.setattr(windows, "IS_WINDOWS", True)
    monkeypatch.setattr(windows.ctypes, "windll", SimpleNamespace(user32=api, kernel32=kernel), raising=False)
    return api, state, attached


def test_focus_shares_foreground_and_target_input_then_detaches_in_reverse(focus_api):
    api, state, attached = focus_api
    assert windows._focus_window(200) is True
    assert state["foreground"] == 200
    assert api.AttachThreadInput.calls == [
        (10, 20, True), (10, 30, True), (10, 30, False), (10, 20, False),
    ]
    assert attached == set()


def test_focus_denial_cleans_up_successful_attachments_without_sending_input(focus_api):
    api, state, attached = focus_api
    state["deny_thread"] = 30
    assert windows._focus_window(200) is False
    assert api.AttachThreadInput.calls == [(10, 20, True), (10, 30, True), (10, 20, False)]
    assert api.SetForegroundWindow.calls == []
    assert api.SetFocus.calls == []
    assert attached == set()


def test_focus_requires_actual_foreground_readback(focus_api):
    api, state, attached = focus_api
    state["readback"] = False
    assert windows._focus_window(200) is False
    assert len(api.SetForegroundWindow.calls) == 1
    assert state["foreground"] == 100
    assert attached == set()


def test_already_foreground_does_not_attach_or_send_input(focus_api):
    api, state, attached = focus_api
    state["foreground"] = 200
    assert windows._focus_window(200) is True
    assert api.AttachThreadInput.calls == []
    assert api.SetForegroundWindow.calls == []
    assert api.SetFocus.calls == []
    assert attached == set()
