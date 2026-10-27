from __future__ import annotations

import pytest


def _policy(path):
    from services.notification_policy import NotificationPolicy

    return NotificationPolicy(path)


def test_quiet_policy_persists_and_owner_aliases_match(tmp_path):
    path = tmp_path / "notifications.db"
    policy = _policy(path)
    assert policy.snapshot("web_default")["quiet"] is False
    policy.set_quiet("web_default", True)
    reloaded = _policy(path)
    assert reloaded.snapshot("admin")["quiet"] is True
    assert reloaded.snapshot("guest")["quiet"] is False


@pytest.mark.parametrize("severity", ["warning", "error", "critical", "urgent"])
def test_quiet_mode_does_not_suppress_important_alerts(tmp_path, severity):
    policy = _policy(tmp_path / "notifications.db")
    policy.set_quiet("admin", True)
    assert policy.allows_automatic("admin", severity=severity) is True
    assert policy.allows_automatic("admin", severity="info") is False


def test_notifications_for_another_profile_are_not_suppressed(tmp_path):
    policy = _policy(tmp_path / "notifications.db")
    policy.set_quiet("guest_one", True)
    assert policy.allows_automatic("guest_one") is False
    assert policy.allows_automatic("guest_two") is True


def test_invalid_policy_does_not_allow_automatic_interruptions(tmp_path, monkeypatch):
    policy = _policy(tmp_path / "notifications.db")
    monkeypatch.setattr(policy, "_connect", lambda: (_ for _ in ()).throw(OSError("private path")))
    snapshot = policy.snapshot("admin")
    assert snapshot["state"] == "unavailable"
    assert snapshot["auto_announce"] is False
    assert "private path" not in repr(snapshot)
    assert policy.allows_automatic("admin", severity="error") is True


def test_quiet_tool_does_not_touch_system_audio(tmp_path, monkeypatch):
    from core import jarvis_state
    from tools import system

    policy = _policy(tmp_path / "notifications.db")
    monkeypatch.setattr(system, "notification_policy", policy, raising=False)
    monkeypatch.setattr(system, "_obtener_control_volumen", lambda: pytest.fail("Quiet mode must not touch audio"))
    with jarvis_state.active_profile("guest_one"):
        result = system.modo_no_molestar.invoke({"activar": True})
    assert result.verified is True
    assert policy.snapshot("guest_one")["quiet"] is True
    assert policy.snapshot("admin")["quiet"] is False


def test_quiet_mode_storage_failure_is_controlled_and_does_not_touch_audio(tmp_path, monkeypatch):
    from tools import system

    policy = _policy(tmp_path / "notifications.db")
    monkeypatch.setattr(policy, "_connect", lambda: (_ for _ in ()).throw(OSError("private storage path")))
    monkeypatch.setattr(system, "notification_policy", policy)
    monkeypatch.setattr(system, "_obtener_control_volumen", lambda: pytest.fail("Audio must remain unchanged"))
    result = system.modo_no_molestar.invoke({"activar": True})
    assert result.status.value == "failed"
    assert result.verified is False
    assert "private storage" not in result.user_message
