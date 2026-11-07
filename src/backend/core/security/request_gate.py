"""Pure request-trust predicates used by the HTTP and WebSocket auth gates.

These helpers take raw header/peer values instead of reading the Quart request
context, so they can be unit-tested without an app and type-checked strictly.
`jarvis_backend` binds them to the live request.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterable
from urllib.parse import urlparse

LOOPBACK_NAMES = frozenset({"localhost"})

# Fetch Metadata values that prove a browser request did not come from another
# site. "none" is a user-initiated navigation (address bar, bookmark).
_SAFE_FETCH_SITES = frozenset({"same-origin", "none"})


def is_loopback(addr: str | None) -> bool:
    raw = str(addr or "").strip().lower()
    if raw in LOOPBACK_NAMES:
        return True
    if os.getenv("JARVIS_TEST_MODE") == "1" and (raw == "<local>" or raw.startswith("__test_")):
        return True
    try:
        parsed = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if parsed.is_loopback:
        return True
    ipv4_mapped = getattr(parsed, "ipv4_mapped", None)
    return bool(ipv4_mapped and ipv4_mapped.is_loopback)


def _is_loopback_hostname(hostname: str) -> bool:
    if hostname in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def is_local_host_header(host_value: str | None) -> bool:
    """True only when the Host header names a loopback address.

    Loopback peer trust must not extend to foreign Host names: a DNS-rebinding
    page reaches 127.0.0.1 with its own hostname and can omit Origin/Referer on
    same-origin GET requests.
    """
    raw = str(host_value or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(f"//{raw}")
        hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
        if parsed.username or parsed.password:
            return False
        _ = parsed.port  # raises ValueError on a malformed port
    except ValueError:
        return False
    return bool(hostname) and _is_loopback_hostname(hostname)


def is_trusted_local_peer(remote_addr: str | None, host_value: str | None) -> bool:
    return is_loopback(remote_addr) and is_local_host_header(host_value)


def normalize_origin(value: str | None) -> str:
    """Return `scheme://host[:port]`, or "" when the value is absent or unparseable."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError:
        return ""
    if not parsed.scheme or not parsed.hostname:
        return ""
    port_suffix = f":{port}" if port else ""
    return f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{port_suffix}"


def is_trusted_origin_header(
    origin_header: str | None,
    referer_header: str | None,
    *,
    allowed_origins: Iterable[str],
    fetch_site: str | None = None,
) -> bool:
    """Decide whether a request's browser provenance is acceptable.

    - An Origin/Referer that is present must normalize to an allowed origin.
      A present-but-opaque value (``Origin: null`` from a no-referrer form POST,
      sandboxed iframe, or file: page) is untrusted, never treated as absent.
    - With neither header, Fetch Metadata decides: browsers mark cross-site
      subresource requests even when the referrer is suppressed.
    - With no provenance headers at all the caller is a non-browser client;
      that is allowed here and left to the token/loopback policy.
    """
    allowed = {origin for origin in (normalize_origin(item) for item in allowed_origins) if origin}

    for header in (origin_header, referer_header):
        if header is None or not str(header).strip():
            continue
        return normalize_origin(header) in allowed

    site = str(fetch_site or "").strip().lower()
    if site:
        return site in _SAFE_FETCH_SITES
    return True


def is_local_browser_origin_header(
    origin_header: str | None,
    referer_header: str | None,
    *,
    allowed_origins: Iterable[str],
) -> bool:
    """True only for an explicit, allowed loopback Origin/Referer, never for a missing one."""
    raw = str(origin_header or referer_header or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False
    host = str(parsed.hostname or "").strip().lower().rstrip(".")
    if not parsed.scheme or not host or not _is_loopback_hostname(host):
        return False
    return is_trusted_origin_header(origin_header, referer_header, allowed_origins=allowed_origins)
