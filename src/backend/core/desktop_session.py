"""Persistent desktop-shell session settings."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass(frozen=True)
class DesktopSession:
    origin: str
    webview_storage_dir: str
    session_file: str
    persist_permissions: bool = True


def _desktop_home() -> str:
    base = os.environ.get("JARVIS_DESKTOP_HOME") or os.environ.get("LOCALAPPDATA")
    return os.path.join(base or os.getcwd(), "JARVIS")


def _session_file(home: str) -> str:
    return os.path.join(home, "desktop_session.json")


def _read_session(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_session(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_desktop_session(port: int = 5002, *, persist: bool = True) -> DesktopSession:
    """Return stable desktop origin/storage so browser permissions can persist."""
    home = _desktop_home()
    path = _session_file(home)
    previous = _read_session(path)

    origin = (
        os.environ.get("JARVIS_DESKTOP_ORIGIN")
        or previous.get("origin")
        or f"http://localhost:{int(port)}"
    )
    if origin.startswith("http://127.0.0.1:"):
        origin = origin.replace("http://127.0.0.1:", "http://localhost:", 1)

    storage_dir = (
        os.environ.get("JARVIS_WEBVIEW_STORAGE")
        or previous.get("webview_storage_dir")
        or os.path.join(home, "WebView2")
    )
    os.makedirs(storage_dir, exist_ok=True)

    session = DesktopSession(
        origin=origin,
        webview_storage_dir=storage_dir,
        session_file=path,
        persist_permissions=True,
    )
    if persist:
        data = asdict(session)
        data.update(
            {
                "schema_version": 1,
                "last_launch": datetime.now().isoformat(timespec="seconds"),
            }
        )
        _write_session(path, data)
    return session
