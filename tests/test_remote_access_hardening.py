from __future__ import annotations

import asyncio

import pytest
from core.device_sync.auth import (
    is_remote_proxy_request,
    protected_http_request_allowed,
)

REMOTE_URL = "https://jarvis.tail123.ts.net"
LOCAL_ORIGIN = "http://localhost:5002"
LOCAL_SCOPE = {
    "client": ("127.0.0.1", 12345),
    "scheme": "http",
    "server": ("localhost", 5002),
}


def test_remote_origin_is_remote_even_when_host_is_rewritten():
    assert is_remote_proxy_request(
        {"Origin": REMOTE_URL},
        "localhost:5002",
        REMOTE_URL,
    ) is True
    assert is_remote_proxy_request(
        {"Referer": f"{REMOTE_URL}/mobile"},
        "127.0.0.1:5002",
        REMOTE_URL,
    ) is True
    assert is_remote_proxy_request(
        {"Origin": LOCAL_ORIGIN},
        "localhost:5002",
        REMOTE_URL,
    ) is False


def test_remote_mode_fails_closed_without_explicit_local_origin():
    assert protected_http_request_allowed(
        "/api/security",
        token_configured=False,
        master_token_valid=False,
        local_cookie_valid=False,
        device_token_valid=False,
        local_loopback=True,
        remote_proxy=False,
        remote_base_configured=True,
        local_browser_origin=False,
    ) is False
    assert protected_http_request_allowed(
        "/api/security",
        token_configured=False,
        master_token_valid=False,
        local_cookie_valid=False,
        device_token_valid=False,
        local_loopback=True,
        remote_proxy=False,
        remote_base_configured=True,
        local_browser_origin=True,
    ) is True


def _client():
    import jarvis_backend

    app = jarvis_backend.app
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        (None, 401),
        ("null", 401),
        (REMOTE_URL, 401),
        (LOCAL_ORIGIN, 200),
    ],
)
def test_protected_route_requires_local_origin_when_remote_mode_is_configured(
    monkeypatch,
    origin,
    expected,
):

    monkeypatch.setenv("JARVIS_REMOTE_BASE_URL", REMOTE_URL)
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    headers = {} if origin is None else {"Origin": origin}

    async def scenario():
        response = await _client().get(
            "/api/session/profile",
            headers=headers,
            scope_base=LOCAL_SCOPE,
        )
        return response.status_code

    assert asyncio.run(scenario()) == expected


def test_pair_init_requires_an_explicit_local_origin(monkeypatch):

    monkeypatch.setenv("JARVIS_REMOTE_BASE_URL", REMOTE_URL)
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)

    async def scenario():
        denied = await _client().post(
            "/api/sync/pair/init",
            scope_base=LOCAL_SCOPE,
        )
        accepted = await _client().post(
            "/api/sync/pair/init",
            headers={"Origin": LOCAL_ORIGIN},
            scope_base=LOCAL_SCOPE,
        )
        return denied.status_code, accepted.status_code, await accepted.get_json()

    denied, accepted, data = asyncio.run(scenario())
    assert denied == 401
    assert accepted == 200
    assert data["qr_data_url"].startswith("data:image/svg+xml;base64,")


def test_mobile_shell_and_manifest_are_served():
    async def scenario():
        client = _client()
        shell = await client.get("/mobile", scope_base=LOCAL_SCOPE)
        manifest = await client.get("/mobile-manifest.json", scope_base=LOCAL_SCOPE)
        return (
            shell.status_code,
            await shell.get_data(),
            manifest.status_code,
            await manifest.get_json(),
        )

    shell_status, shell_body, manifest_status, manifest = asyncio.run(scenario())
    assert shell_status == 200
    assert b"mobile.js" in shell_body
    assert manifest_status == 200
    assert manifest["start_url"] == "/mobile"
