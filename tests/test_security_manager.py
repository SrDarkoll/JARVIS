"""Tests for security_manager service."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "src", "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


@pytest.fixture
def mock_security_deps(monkeypatch):
    """Setup minimal dependencies for security_manager tests."""
    from services import security_manager

    monkeypatch.setattr(security_manager, "_obs_event", MagicMock())
    monkeypatch.setattr(security_manager, "_obs_inc", MagicMock())
    monkeypatch.setattr(security_manager, "_normalizar_destino_web", lambda x: x)
    monkeypatch.setattr(security_manager, "verificar_autorizacion", lambda pid: pid == "admin")
    monkeypatch.setattr(security_manager, "normalizar_tratamiento_admin", lambda x: x)

    return security_manager


class TestSecurityPolicyDefaults:
    def test_security_policy_default_structure(self):
        from services import security_manager

        defaults = security_manager.SECURITY_POLICY_DEFAULT
        assert isinstance(defaults, dict)
        assert defaults["strict_mode"] is False
        assert isinstance(defaults["blocked_tools"], list)
        assert isinstance(defaults["allowed_web_domains"], list)
        assert "google.com" in defaults["allowed_web_domains"]
        assert isinstance(defaults["safe_apps"], list)

    def test_proactive_state_defaults(self):
        from services import security_manager

        state = security_manager.PROACTIVE_STATE
        assert state["enabled"] is True
        assert state["cooldown_seconds"] == 600
        assert isinstance(state["alerts"], list)
        assert isinstance(state["tool_errors_window"], list)


class TestSecurityAudit:
    def test_security_audit_calls_obs_event(self, mock_security_deps):
        mock_security_deps._security_audit(
            "test_action", level="info", tool="test_tool", reason="test reason"
        )
        mock_security_deps._obs_event.assert_called_once()
        call_args = mock_security_deps._obs_event.call_args
        assert call_args[0][0] == "security_audit"

    def test_security_audit_truncates_long_reason(self, mock_security_deps):
        mock_security_deps._security_audit("test", reason="x" * 500)
        call_args = mock_security_deps._obs_event.call_args
        metadata = call_args[1]
        assert len(metadata.get("reason", "")) <= 300


class TestSecurityGuard:
    def test_security_guard_blocks_disabled_tool(self, mock_security_deps):
        with patch.object(mock_security_deps, "SECURITY_POLICY", {"blocked_tools": ["abrir_aplicacion"]}):
            allowed, reason = mock_security_deps._security_guard(
                "abrir_aplicacion", {}, "open app", "test", profile_id="admin"
            )
            assert allowed is False
            assert "blocked" in reason.lower()

    def test_security_guard_allows_non_blocked_tool(self, mock_security_deps):
        with patch.object(mock_security_deps, "SECURITY_POLICY", {"blocked_tools": [], "strict_mode": False}):
            with patch.object(mock_security_deps, "verificar_autorizacion", lambda pid: True):
                allowed, reason = mock_security_deps._security_guard(
                    "listar_ventanas", {}, "list windows", "test", profile_id="admin"
                )
                assert allowed is True
                assert reason == ""

    def test_security_guard_strict_mode_domain_block(self, mock_security_deps):
        with patch.object(mock_security_deps, "SECURITY_POLICY", {
            "strict_mode": True,
            "blocked_tools": [],
            "allowed_web_domains": ["google.com"],
        }):
            allowed, reason = mock_security_deps._security_guard(
                "abrir_navegador", {"destino": "https://evil.com"},
                "open evil", "test", profile_id="admin"
            )
            assert allowed is False
            assert "outside" in reason.lower() or "not permitted" in reason.lower()

    def test_security_guard_strict_mode_domain_allowed(self, mock_security_deps):
        with patch.object(mock_security_deps, "SECURITY_POLICY", {
            "strict_mode": True,
            "blocked_tools": [],
            "allowed_web_domains": ["google.com", "youtube.com"],
        }):
            allowed, reason = mock_security_deps._security_guard(
                "abrir_navegador", {"destino": "https://google.com/search"},
                "open google", "test", profile_id="admin"
            )
            assert allowed is True


class TestSecuritySnapshot:
    def test_security_snapshot_returns_policy_and_state(self, mock_security_deps):
        with patch.object(mock_security_deps, "SECURITY_POLICY", {"strict_mode": True}):
            with patch.object(mock_security_deps, "SECURITY_STATE", {"last_update": "2024-01-01"}):
                snap = mock_security_deps._security_snapshot()
                assert "policy" in snap
                assert "state" in snap
                assert "tool_policies" in snap


class TestSecurityNormalizePolicy:
    def test_normalize_blocks_invalid_max_tool_errors(self, mock_security_deps):
        raw = {"max_tool_errors_5m": 200}
        normalized = mock_security_deps._security_normalizar_policy(raw)
        assert normalized["max_tool_errors_5m"] == 100

    def test_normalize_blocks_negative_max_tool_errors(self, mock_security_deps):
        raw = {"max_tool_errors_5m": -5}
        normalized = mock_security_deps._security_normalizar_policy(raw)
        assert normalized["max_tool_errors_5m"] == 3

    def test_normalize_merges_allowed_web_domains(self, mock_security_deps):
        raw = {"allowed_web_domains": ["https://example.com", "https://test.org"]}
        normalized = mock_security_deps._security_normalizar_policy(raw)
        assert "example.com" in normalized["allowed_web_domains"]
        assert "test.org" in normalized["allowed_web_domains"]

    def test_normalize_strips_www_prefix(self, mock_security_deps):
        raw = {"allowed_web_domains": ["www.google.com"]}
        normalized = mock_security_deps._security_normalizar_policy(raw)
        assert "google.com" in normalized["allowed_web_domains"]


class TestProactiveState:
    def test_proactive_snapshot_returns_alerts(self, mock_security_deps):
        with patch.object(mock_security_deps, "PROACTIVE_STATE", {
            "enabled": True,
            "cooldown_seconds": 600,
            "alerts": [{"ts": "2024-01-01", "kind": "test", "message": "hello"}],
            "tool_errors_window": [],
        }):
            snap = mock_security_deps._proactive_snapshot()
            assert snap["enabled"] is True
            assert len(snap["alerts"]) == 1


class TestSecurityDomainAllowed:
    def test_domain_allowed_exact_match(self, mock_security_deps):
        result = mock_security_deps._security_domain_allowed("google.com", ["google.com"])
        assert result is True

    def test_domain_allowed_subdomain_match(self, mock_security_deps):
        result = mock_security_deps._security_domain_allowed("mail.google.com", ["google.com"])
        assert result is True

    def test_domain_allowed_no_match(self, mock_security_deps):
        result = mock_security_deps._security_domain_allowed("evil.com", ["google.com"])
        assert result is False

    def test_domain_allowed_empty_host(self, mock_security_deps):
        result = mock_security_deps._security_domain_allowed("", ["google.com"])
        assert result is False


class TestLoadSaveSecurityPolicy:
    def test_load_security_policy_uses_default_when_file_missing(self, mock_security_deps, tmp_path):
        nonexistent = tmp_path / "nonexistent.json"
        with patch.object(mock_security_deps, "SECURITY_POLICY_FILE", str(nonexistent)):
            mock_security_deps._load_security_policy()
            policy = mock_security_deps.SECURITY_POLICY
            assert policy.get("strict_mode") is False
            assert "google.com" in policy.get("allowed_web_domains", [])

    def test_load_security_policy_valid_file(self, mock_security_deps, tmp_path):
        policy_file = tmp_path / "policy.json"
        policy_file.write_text('{"strict_mode": true, "blocked_tools": ["test_tool"]}')

        with patch.object(mock_security_deps, "SECURITY_POLICY_FILE", str(policy_file)):
            mock_security_deps._load_security_policy()
            assert mock_security_deps.SECURITY_POLICY["strict_mode"] is True
            assert "test_tool" in mock_security_deps.SECURITY_POLICY["blocked_tools"]