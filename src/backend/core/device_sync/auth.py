"""Narrow authentication helpers for the private mobile device API."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

DEVICE_TOKEN_HTTP_PATHS = frozenset(
    {
        "/api/sync/status",
        "/api/sync/snapshot",
        "/api/sync/history",
        "/api/sync/dispatch",
        "/api/sync/ws-ticket",
        "/api/sync/devices/self/revoke",
    }
)


def device_token_http_path_allowed(path: str) -> bool:
    """Only explicitly scoped mobile endpoints may accept a device token."""
    normalized = str(path or "").rstrip("/") or "/"
    return normalized in DEVICE_TOKEN_HTTP_PATHS


def _authority(host_value: str, *, default_port: int) -> tuple[str, int] | None:
    raw = str(host_value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(f"//{raw}")
        hostname = str(parsed.hostname or "").lower().rstrip(".")
        if not hostname or parsed.username or parsed.password:
            return None
        return hostname, parsed.port or default_port
    except ValueError:
        return None


def _origin_matches_remote_base(headers: Mapping[str, str], remote_base_url: str) -> bool:
    raw_base = str(remote_base_url or "").strip()
    if not raw_base:
        return False
    candidate = str(headers.get("Origin") or headers.get("Referer") or "").strip()
    if not candidate:
        return False
    try:
        base = urlsplit(raw_base)
        supplied = urlsplit(candidate)
    except ValueError:
        return False
    if base.scheme.lower() != "https" or supplied.scheme.lower() != "https":
        return False
    if not base.hostname or not supplied.hostname:
        return False
    return (
        supplied.hostname.lower().rstrip(".") == base.hostname.lower().rstrip(".")
        and (supplied.port or 443) == (base.port or 443)
    )


def is_remote_proxy_request(
    headers: Mapping[str, str],
    request_host: str,
    remote_base_url: str,
) -> bool:
    """Identify Tailscale/proxy traffic so loopback is not treated as local UI auth.

    Serve forwards to the loopback listener, so its identity header and configured
    HTTPS origin distinguish a proxied mobile request from the desktop browser.
    The identity header is never itself an authorization credential.
    """
    if str(headers.get("Tailscale-User-Login") or "").strip():
        return True
    if _origin_matches_remote_base(headers, remote_base_url):
        return True

    remote = urlsplit(str(remote_base_url or "").strip())
    if remote.scheme.lower() != "https" or not remote.hostname or remote.username or remote.password:
        return False
    expected = _authority(remote.netloc, default_port=443)
    observed = _authority(request_host, default_port=443)
    return bool(expected and observed and expected == observed)


def protected_http_request_allowed(
    path: str,
    *,
    token_configured: bool,
    master_token_valid: bool,
    local_cookie_valid: bool,
    device_token_valid: bool,
    local_loopback: bool,
    remote_proxy: bool,
    remote_base_configured: bool = False,
    local_browser_origin: bool = True,
) -> bool:
    """Apply the local, master-token, and narrowly scoped device-token policy."""
    if master_token_valid or local_cookie_valid:
        return True
    if device_token_valid and device_token_http_path_allowed(path):
        return True
    if token_configured or remote_proxy:
        return False
    if remote_base_configured:
        # Remote mode must fail closed. A loopback request without an explicit
        # local browser Origin is indistinguishable from a proxy that rewrites
        # Host, so it does not inherit desktop owner access.
        return local_loopback and local_browser_origin
    return local_loopback
