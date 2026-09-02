"""Shared API-token primitives for HTTP and WebSocket authentication."""

from __future__ import annotations

import hashlib
import hmac
import os

LOCAL_UI_AUTH_COOKIE = "jarvis_local_auth"
_LOCAL_UI_CONTEXT = b"jarvis-local-ui-auth-v1"


def configured_api_token() -> str:
    """Read the current token dynamically so runtime/test rotation is honored."""
    return (os.getenv("JARVIS_API_TOKEN") or "").strip()


def local_ui_cookie_value(token: str | None = None) -> str:
    """Derive a browser credential without exposing the raw API token."""
    secret = configured_api_token() if token is None else str(token or "").strip()
    if not secret:
        return ""
    return hmac.new(
        secret.encode("utf-8"),
        _LOCAL_UI_CONTEXT,
        hashlib.sha256,
    ).hexdigest()


def is_valid_raw_api_token(supplied: str | None) -> bool:
    configured = configured_api_token()
    candidate = str(supplied or "").strip()
    return bool(configured and candidate) and hmac.compare_digest(candidate, configured)


def is_valid_local_ui_cookie(supplied: str | None) -> bool:
    expected = local_ui_cookie_value()
    candidate = str(supplied or "").strip()
    return bool(expected and candidate) and hmac.compare_digest(candidate, expected)
