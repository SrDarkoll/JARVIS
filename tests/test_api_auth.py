from __future__ import annotations

from core.security.api_auth import (
    configured_api_token,
    is_valid_local_ui_cookie,
    is_valid_raw_api_token,
    local_ui_cookie_value,
)


def test_local_ui_cookie_is_derived_and_rotates_with_token(monkeypatch):
    monkeypatch.setenv("JARVIS_API_TOKEN", "first-secret")
    first = local_ui_cookie_value()

    assert configured_api_token() == "first-secret"
    assert first
    assert first != "first-secret"
    assert is_valid_local_ui_cookie(first) is True
    assert is_valid_raw_api_token("first-secret") is True
    assert is_valid_raw_api_token("wrong") is False

    monkeypatch.setenv("JARVIS_API_TOKEN", "second-secret")
    second = local_ui_cookie_value()

    assert second != first
    assert is_valid_local_ui_cookie(first) is False
    assert is_valid_local_ui_cookie(second) is True


def test_api_auth_rejects_all_credentials_when_token_is_unconfigured(monkeypatch):
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)

    assert configured_api_token() == ""
    assert local_ui_cookie_value() == ""
    assert is_valid_raw_api_token("anything") is False
    assert is_valid_local_ui_cookie("anything") is False
