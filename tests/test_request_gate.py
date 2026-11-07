from __future__ import annotations

import pytest
from core.security import request_gate

ALLOWED = ["http://localhost:5002", "http://127.0.0.1:5002"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://LOCALHOST:5002/path?q=1", "http://localhost:5002"),
        ("https://example.com", "https://example.com"),
        ("", ""),
        (None, ""),
        ("null", ""),
        ("not a url", ""),
        ("http://localhost:99999", ""),
        ("http://localhost:abc", ""),
    ],
)
def test_normalize_origin(value, expected):
    assert request_gate.normalize_origin(value) == expected


@pytest.mark.parametrize(
    ("origin", "referer", "fetch_site", "expected"),
    [
        ("http://localhost:5002", None, None, True),
        (None, "http://127.0.0.1:5002/index", None, True),
        ("https://evil.example", None, None, False),
        # Present-but-opaque Origin must not fall through to "absent".
        ("null", None, None, False),
        ("null", "http://localhost:5002/", None, False),
        ("http://localhost:99999", None, None, False),
        # A trusted Origin wins; an untrusted Referer is not consulted.
        ("http://localhost:5002", "https://evil.example/", None, True),
        # No Origin/Referer: Fetch Metadata decides.
        (None, None, "same-origin", True),
        (None, None, "none", True),
        (None, None, "cross-site", False),
        (None, None, "same-site", False),
        (None, None, "SAME-ORIGIN", True),
        # Non-browser client: no provenance headers at all.
        (None, None, None, True),
        ("", "", "", True),
    ],
)
def test_is_trusted_origin_header(origin, referer, fetch_site, expected):
    assert (
        request_gate.is_trusted_origin_header(
            origin,
            referer,
            allowed_origins=ALLOWED,
            fetch_site=fetch_site,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("http://localhost:5002", True),
        ("http://127.0.0.1:5002", True),
        ("http://localhost:6000", False),
        ("https://jarvis.tailnet.ts.net", False),
        ("null", False),
        (None, False),
    ],
)
def test_is_local_browser_origin_header(origin, expected):
    assert request_gate.is_local_browser_origin_header(origin, None, allowed_origins=ALLOWED) is expected


@pytest.mark.parametrize(
    ("addr", "host", "expected"),
    [
        ("127.0.0.1", "localhost:5002", True),
        ("::1", "[::1]:5002", True),
        ("::ffff:127.0.0.1", "127.0.0.1", True),
        ("127.0.0.1", "attacker.example", False),
        ("192.168.1.10", "localhost:5002", False),
        ("127.0.0.1", "localhost:bad", False),
    ],
)
def test_is_trusted_local_peer(addr, host, expected):
    assert request_gate.is_trusted_local_peer(addr, host) is expected
