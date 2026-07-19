from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import desktop_session


def _clear_desktop_environment(monkeypatch):
    for name in (
        "JARVIS_DESKTOP_HOME",
        "JARVIS_DESKTOP_ORIGIN",
        "JARVIS_WEBVIEW_STORAGE",
        "LOCALAPPDATA",
        "XDG_DATA_HOME",
    ):
        monkeypatch.delenv(name, raising=False)


def test_desktop_home_honors_explicit_override(monkeypatch, tmp_path):
    explicit_home = tmp_path / "explicit"
    monkeypatch.setenv("JARVIS_DESKTOP_HOME", str(explicit_home))

    assert Path(desktop_session._desktop_home()) == explicit_home


@pytest.mark.parametrize(
    ("platform", "environment", "relative_path"),
    [
        ("win32", "LOCALAPPDATA", Path("JARVIS")),
        ("darwin", None, Path("Library") / "Application Support" / "JARVIS"),
        ("linux", "XDG_DATA_HOME", Path("JARVIS")),
        ("linux", None, Path(".local") / "share" / "JARVIS"),
    ],
)
def test_desktop_home_uses_platform_data_directory(
    monkeypatch, tmp_path, platform, environment, relative_path
):
    _clear_desktop_environment(monkeypatch)
    user_home = tmp_path / "user-home"
    data_home = tmp_path / "data-home"
    monkeypatch.setattr(desktop_session.sys, "platform", platform)
    monkeypatch.setattr(
        desktop_session.os.path, "expanduser", lambda _value: str(user_home)
    )
    if environment:
        monkeypatch.setenv(environment, str(data_home))
        expected_base = data_home
    else:
        expected_base = user_home

    assert Path(desktop_session._desktop_home()) == expected_base / relative_path


def test_ensure_directory_probes_real_writability(tmp_path):
    target = tmp_path / "writable"

    assert desktop_session._ensure_directory(str(target)) is True
    assert target.is_dir()
    assert list(target.iterdir()) == []


def test_ensure_directory_rejects_probe_failure(monkeypatch, tmp_path):
    target = tmp_path / "not-writable"

    def fail_probe(*_args, **_kwargs):
        raise OSError("probe denied")

    monkeypatch.setattr(desktop_session.tempfile, "mkstemp", fail_probe)

    assert desktop_session._ensure_directory(str(target)) is False


def test_session_metadata_is_written_as_valid_json(monkeypatch, tmp_path):
    home = tmp_path / "persistent"
    monkeypatch.setenv("JARVIS_DESKTOP_HOME", str(home))

    session = desktop_session.load_desktop_session(port=5002)
    payload = json.loads(Path(session.session_file).read_text(encoding="utf-8"))

    assert payload["origin"] == "http://localhost:5002"
    assert payload["webview_storage_dir"] == session.webview_storage_dir
    assert payload["persist_permissions"] is True
    assert "cleanup_dir" not in payload
    assert session.cleanup_dir is None
    assert list(home.glob(".desktop-session-*.tmp")) == []


@pytest.mark.parametrize(
    "raw_payload",
    [
        b"{not-json",
        b"\xff\xfe\x00",
        b"[]",
        b'{"origin": 123, "webview_storage_dir": []}',
    ],
)
def test_read_session_degrades_for_corrupt_or_invalid_payloads(tmp_path, raw_payload):
    session_file = tmp_path / "desktop_session.json"
    session_file.write_bytes(raw_payload)

    assert desktop_session._read_session(str(session_file)) == {}


def test_read_session_ignores_invalid_fields_but_keeps_valid_ones(tmp_path):
    session_file = tmp_path / "desktop_session.json"
    session_file.write_text(
        json.dumps(
            {
                "origin": "http://localhost:6123",
                "webview_storage_dir": "   ",
            }
        ),
        encoding="utf-8",
    )

    assert desktop_session._read_session(str(session_file)) == {
        "origin": "http://localhost:6123"
    }


def test_read_session_degrades_when_decoder_raises_type_error(monkeypatch, tmp_path):
    session_file = tmp_path / "desktop_session.json"
    session_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        desktop_session.json,
        "load",
        lambda _handle: (_ for _ in ()).throw(TypeError("bad decoder")),
    )

    assert desktop_session._read_session(str(session_file)) == {}


def test_write_session_replace_failure_preserves_previous_json(
    monkeypatch, tmp_path
):
    session_file = tmp_path / "desktop_session.json"
    previous = {"origin": "http://localhost:5002", "generation": "old"}
    session_file.write_text(json.dumps(previous), encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("replace denied")

    monkeypatch.setattr(desktop_session.os, "replace", fail_replace)

    assert desktop_session._write_session(
        str(session_file), {"origin": "http://localhost:6000"}
    ) is False
    assert json.loads(session_file.read_text(encoding="utf-8")) == previous
    assert list(tmp_path.glob(".desktop-session-*.tmp")) == []


def _force_preferred_storage_failure(monkeypatch, tmp_path):
    blocked_home = tmp_path / "blocked"
    real_ensure = desktop_session._ensure_directory
    real_mkdtemp = desktop_session.tempfile.mkdtemp

    def selective_ensure(path: str) -> bool:
        if str(blocked_home) in str(path):
            return False
        return real_ensure(path)

    def local_mkdtemp(*, prefix):
        return real_mkdtemp(prefix=prefix, dir=tmp_path)

    monkeypatch.setenv("JARVIS_DESKTOP_HOME", str(blocked_home))
    monkeypatch.setattr(desktop_session, "_ensure_directory", selective_ensure)
    monkeypatch.setattr(desktop_session.tempfile, "mkdtemp", local_mkdtemp)
    return blocked_home


def test_unwritable_preferred_storage_uses_private_temporary_fallback(
    monkeypatch, tmp_path
):
    blocked_home = _force_preferred_storage_failure(monkeypatch, tmp_path)
    chmod_calls = []
    real_chmod = desktop_session.os.chmod

    def recording_chmod(path, mode):
        chmod_calls.append((path, mode))
        real_chmod(path, mode)

    monkeypatch.setattr(desktop_session.os, "chmod", recording_chmod)

    session = desktop_session.load_desktop_session(port=5002)

    assert str(blocked_home) not in session.webview_storage_dir
    assert session.persist_permissions is False
    assert session.cleanup_dir is not None
    assert Path(session.cleanup_dir).is_dir()
    assert Path(session.webview_storage_dir).is_dir()
    assert chmod_calls == [(session.cleanup_dir, 0o700)]
    payload = json.loads(Path(session.session_file).read_text(encoding="utf-8"))
    assert "cleanup_dir" not in payload
    shutil.rmtree(session.cleanup_dir)


def test_fallback_directories_are_unique_and_expose_cleanup(monkeypatch, tmp_path):
    _force_preferred_storage_failure(monkeypatch, tmp_path)

    first = desktop_session.load_desktop_session(persist=False)
    second = desktop_session.load_desktop_session(persist=False)

    assert first.cleanup_dir
    assert second.cleanup_dir
    assert first.cleanup_dir != second.cleanup_dir
    for cleanup_dir in (first.cleanup_dir, second.cleanup_dir):
        shutil.rmtree(cleanup_dir)
        assert not Path(cleanup_dir).exists()


def test_session_write_failure_disables_permission_persistence(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("JARVIS_DESKTOP_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(desktop_session, "_write_session", lambda *_args: False)

    session = desktop_session.load_desktop_session(port=5002)

    assert session.persist_permissions is False
    assert session.cleanup_dir is None


class _ExitCalled(RuntimeError):
    pass


class _CloseEvent:
    def __init__(self):
        self.handler = None

    def __iadd__(self, handler):
        self.handler = handler
        return self


class _FakeWindow:
    def __init__(self):
        self.events = SimpleNamespace(closed=_CloseEvent())


def _run_launcher_until_close(monkeypatch, loader):
    import start_app

    fake_window = _FakeWindow()
    monkeypatch.setattr(desktop_session, "load_desktop_session", loader)
    monkeypatch.setattr(start_app, "is_backend_running", lambda _url: True)
    monkeypatch.setattr(start_app.webview, "create_window", lambda *_a, **_kw: fake_window)

    def close_immediately(**_kwargs):
        fake_window.events.closed.handler()

    monkeypatch.setattr(start_app.webview, "start", close_immediately)
    monkeypatch.setattr(
        start_app.os,
        "_exit",
        lambda _code: (_ for _ in ()).throw(_ExitCalled()),
    )

    with pytest.raises(_ExitCalled):
        start_app.start_app()


def test_launcher_cleans_session_fallback_before_forced_exit(monkeypatch, tmp_path):
    cleanup_dir = tmp_path / "session-fallback"
    cleanup_dir.mkdir()
    session = desktop_session.DesktopSession(
        origin="http://localhost:5002",
        webview_storage_dir=str(cleanup_dir),
        session_file=str(cleanup_dir / "desktop_session.json"),
        persist_permissions=False,
        cleanup_dir=str(cleanup_dir),
    )

    _run_launcher_until_close(monkeypatch, lambda **_kwargs: session)

    assert not cleanup_dir.exists()


def test_launcher_uses_unique_emergency_storage_and_cleans_it(
    monkeypatch, tmp_path, capsys
):
    import start_app

    real_mkdtemp = start_app.tempfile.mkdtemp
    created = []

    def local_mkdtemp(*, prefix):
        path = real_mkdtemp(prefix=prefix, dir=tmp_path)
        created.append(path)
        return path

    monkeypatch.setattr(start_app.tempfile, "mkdtemp", local_mkdtemp)

    def fail_load(**_kwargs):
        raise OSError("private path must not be logged")

    _run_launcher_until_close(monkeypatch, fail_load)

    output = capsys.readouterr().out
    assert len(created) == 1
    assert not Path(created[0]).exists()
    assert created[0] not in output
    assert "private path must not be logged" not in output

