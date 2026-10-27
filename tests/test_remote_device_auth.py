from __future__ import annotations

from core.device_sync.auth import (
    device_token_http_path_allowed,
    is_remote_proxy_request,
    protected_http_request_allowed,
)


def test_device_token_http_allowlist_does_not_grant_global_api_access():
    assert device_token_http_path_allowed("/api/sync/dispatch") is True
    assert device_token_http_path_allowed("/api/sync/snapshot") is True
    assert device_token_http_path_allowed("/api/security/policy") is False
    assert device_token_http_path_allowed("/api/chat") is False


def test_tailscale_proxy_is_not_mistaken_for_local_loopback():
    headers = {"Tailscale-User-Login": "owner@example.test"}
    assert is_remote_proxy_request(headers, "127.0.0.1:5002", "") is True
    assert is_remote_proxy_request({}, "jarvis.tail123.ts.net", "https://jarvis.tail123.ts.net") is True
    assert is_remote_proxy_request({}, "localhost:5002", "https://jarvis.tail123.ts.net") is False


def test_tailscale_identity_never_substitutes_for_device_auth():
    assert protected_http_request_allowed(
        "/api/sync/status",
        token_configured=False,
        master_token_valid=False,
        local_cookie_valid=False,
        device_token_valid=False,
        local_loopback=True,
        remote_proxy=True,
    ) is False


def test_device_token_cannot_inherit_local_owner_permissions():
    assert protected_http_request_allowed(
        "/api/security/policy",
        token_configured=False,
        master_token_valid=False,
        local_cookie_valid=False,
        device_token_valid=True,
        local_loopback=True,
        remote_proxy=True,
    ) is False
    assert protected_http_request_allowed(
        "/api/sync/dispatch",
        token_configured=False,
        master_token_valid=False,
        local_cookie_valid=False,
        device_token_valid=True,
        local_loopback=True,
        remote_proxy=True,
    ) is True


def test_chat_pipeline_preserves_remote_request_id(monkeypatch):
    from core.brain import processor
    from core.command_pipeline.models import CommandOutcome, CommandResponse

    captured = []

    def fake_process(request, *, emit=None):
        captured.append(request)
        return CommandResponse(
            request_id=request.request_id,
            text="ok",
            should_listen=False,
            outcome=CommandOutcome.RESPONDED,
        )

    monkeypatch.setattr(processor, "_legacy_pipeline_enabled", lambda: False)
    monkeypatch.setattr(processor, "process_command", fake_process)

    reply, should_listen = processor.procesar_mensaje(
        "hello",
        profile_id="owner",
        count_inbound=False,
        request_id="12345678-1234-5678-1234-567812345678",
    )

    assert (reply, should_listen) == ("ok", False)
    assert captured[0].request_id == "12345678-1234-5678-1234-567812345678"
    assert captured[0].profile_id == "owner"
