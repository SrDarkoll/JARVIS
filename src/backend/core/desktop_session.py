"""Persistent desktop-shell session settings."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DesktopSession:
    origin: str
    webview_storage_dir: str
    session_file: str
    persist_permissions: bool = True
    cleanup_dir: str | None = None


def _non_empty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _desktop_home() -> str:
    explicit_home = _non_empty_string(os.environ.get("JARVIS_DESKTOP_HOME"))
    if explicit_home:
        return os.path.abspath(os.path.expanduser(explicit_home))

    user_home = os.path.expanduser("~")
    if sys.platform == "win32":
        base = _non_empty_string(os.environ.get("LOCALAPPDATA"))
        base = base or os.path.join(user_home, "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.join(user_home, "Library", "Application Support")
    else:
        base = _non_empty_string(os.environ.get("XDG_DATA_HOME"))
        base = base or os.path.join(user_home, ".local", "share")
    return os.path.join(base, "JARVIS")


def _session_file(home: str) -> str:
    return os.path.join(home, "desktop_session.json")


def _read_session(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return {}

    if not isinstance(data, dict):
        return {}

    result = {}
    for key in ("origin", "webview_storage_dir"):
        value = _non_empty_string(data.get(key))
        if value:
            result[key] = value
    return result


def _ensure_directory(path: str) -> bool:
    descriptor = -1
    probe_path = ""
    probe_ready = False
    cleanup_failed = False
    try:
        os.makedirs(path, exist_ok=True)
        descriptor, probe_path = tempfile.mkstemp(prefix=".jarvis-write-probe-", dir=path)
        os.close(descriptor)
        descriptor = -1
        os.remove(probe_path)
        probe_path = ""
        probe_ready = True
    except OSError:
        probe_ready = False
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                cleanup_failed = True
        if probe_path:
            try:
                os.remove(probe_path)
            except OSError:
                cleanup_failed = True
    return probe_ready and not cleanup_failed


def _temporary_desktop_home() -> str:
    home = tempfile.mkdtemp(prefix="jarvis-desktop-")
    try:
        os.chmod(home, 0o700)
    except OSError:
        pass
    return home


def _write_session(path: str, payload: dict) -> bool:
    directory = os.path.dirname(path) or "."
    if not _ensure_directory(directory):
        return False

    descriptor = -1
    temp_path = ""
    try:
        descriptor, temp_path = tempfile.mkstemp(prefix=".desktop-session-", suffix=".tmp", dir=directory)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = ""
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def load_desktop_session(port: int = 5002, *, persist: bool = True) -> DesktopSession:
    """Return stable desktop origin/storage so browser permissions can persist."""
    preferred_home = _desktop_home()
    path = _session_file(preferred_home)
    previous = _read_session(path)

    origin = (
        _non_empty_string(os.environ.get("JARVIS_DESKTOP_ORIGIN"))
        or previous.get("origin")
        or f"http://localhost:{int(port)}"
    )
    if origin.startswith("http://127.0.0.1:"):
        origin = origin.replace("http://127.0.0.1:", "http://localhost:", 1)

    storage_dir = (
        _non_empty_string(os.environ.get("JARVIS_WEBVIEW_STORAGE"))
        or previous.get("webview_storage_dir")
        or os.path.join(preferred_home, "WebView2")
    )
    persistent_storage = _ensure_directory(storage_dir)
    cleanup_dir = None
    if not persistent_storage:
        cleanup_dir = _temporary_desktop_home()
        storage_dir = os.path.join(cleanup_dir, "WebView2")
        if not _ensure_directory(storage_dir):
            shutil.rmtree(cleanup_dir, ignore_errors=True)
            raise OSError("JARVIS could not create temporary desktop storage")
        path = _session_file(cleanup_dir)
        logger.warning("Desktop persistence unavailable; using temporary WebView storage.")

    session = DesktopSession(
        origin=origin,
        webview_storage_dir=storage_dir,
        session_file=path,
        persist_permissions=bool(persist and persistent_storage),
        cleanup_dir=cleanup_dir,
    )
    if persist:
        data = asdict(session)
        data.pop("cleanup_dir", None)
        data.update(
            {
                "schema_version": 1,
                "last_launch": datetime.now().isoformat(timespec="seconds"),
            }
        )
        if not _write_session(path, data):
            logger.warning("Desktop session metadata could not be persisted.")
            session = replace(session, persist_permissions=False)
    return session
