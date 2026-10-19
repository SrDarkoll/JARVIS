from __future__ import annotations


def test_system_browser_fallback_never_invokes_command_shell(monkeypatch) -> None:
    import subprocess

    from tools import browser

    command_calls = []
    monkeypatch.setattr(browser.webbrowser, "open", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(browser, "_open_url_or_app", lambda _url: False)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: command_calls.append((args, kwargs)),
    )

    opened = browser._abrir_en_navegador_sistema(
        "https://example.com/&whoami",
        require_policy=False,
    )

    assert opened is False
    assert command_calls == []


def test_common_windows_url_opener_reports_browser_rejection(monkeypatch) -> None:
    import webbrowser

    from tools import _common

    monkeypatch.setattr(_common, "IS_WINDOWS", True)
    monkeypatch.setattr(webbrowser, "open", lambda *_args, **_kwargs: False)

    assert _common._open_url_or_app("https://example.com") is False
