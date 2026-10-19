"""Server-selected conversation namespace shared by browser HTTP and WebSockets."""

from __future__ import annotations

import re
import secrets

from quart import Quart, session

WEB_PROFILE_ID = "web_default"
_OWNER_NAMESPACES = {WEB_PROFILE_ID, "admin"}


def configure_profile_sessions(app: Quart) -> None:
    app.secret_key = secrets.token_bytes(32)
    app.config.update(
        SESSION_COOKIE_NAME="jarvis_profile_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
    )


def get_browser_profile_id() -> str:
    value = str(session.get("profile_id") or "").strip().lower()
    return value if re.fullmatch(r"[a-z0-9_][a-z0-9_.-]{0,63}", value) else WEB_PROFILE_ID


def bind_browser_profile_id(profile_id: str = WEB_PROFILE_ID) -> bool:
    """Called only with a default namespace or a server-produced voice identity."""
    value = str(profile_id or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_][a-z0-9_.-]{0,63}", value):
        return False
    session["profile_id"] = value
    return True


def ensure_browser_profile() -> None:
    profile_id = get_browser_profile_id()
    if session.get("profile_id") != profile_id:
        bind_browser_profile_id(profile_id)


def can_manage_browser_profiles() -> bool:
    return get_browser_profile_id() in _OWNER_NAMESPACES


def can_access_browser_profile(profile_id: str) -> bool:
    return can_manage_browser_profiles() or get_browser_profile_id() == str(profile_id).strip().lower()
