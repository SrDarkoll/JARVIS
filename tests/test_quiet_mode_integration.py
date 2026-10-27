from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from core import jarvis_state
from core.command_pipeline.models import CommandRequest
from services.notification_policy import NotificationPolicy


@pytest.mark.parametrize(
    ("text", "active"),
    [
        ("activa no molestar", True),
        ("activa el modo no molestar", True),
        ("desactiva no molestar", False),
        ("enable do not disturb", True),
        ("turn off do not disturb", False),
    ],
)
def test_quiet_command_is_planned_locally_without_running_a_tool(text, active):
    from core.brain import router

    request = CommandRequest.create(text=text, profile_id="guest", channel="chat")
    plan = router.plan_hybrid(request)
    assert plan is not None
    assert plan.steps[0].tool_name == "modo_no_molestar"
    assert dict(plan.steps[0].arguments) == {"activar": active}


def test_quiet_mode_defers_auto_briefing_but_keeps_explicit_news(tmp_path, monkeypatch):
    async def run():
        import jarvis_backend
        from api import status_routes

        policy = NotificationPolicy(tmp_path / "notifications.db")
        policy.set_quiet("web_default", True)
        monkeypatch.setattr(status_routes, "notification_policy", policy, raising=False)
        monkeypatch.setattr(
            status_routes,
            "_services",
            SimpleNamespace(
                news_cache={
                    "listo": True,
                    "resumen": "Resumen sintético conservado.",
                    "language": "es",
                }
            ),
        )
        monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
        client = jarvis_backend.app.test_client()
        headers = {"Origin": "http://127.0.0.1:5002"}
        automatic = await client.get("/api/noticias?automatic=1", headers=headers)
        explicit = await client.get("/api/noticias", headers=headers)
        assert automatic.status_code == 202
        assert (await automatic.get_json())["deferred"] is True
        assert (await automatic.get_json())["resumen"] == ""
        assert explicit.status_code == 200
        assert (await explicit.get_json())["resumen"] == "Resumen sintético conservado."
        policy.set_quiet("web_default", False)
        resumed = await client.get("/api/noticias?automatic=1", headers=headers)
        assert resumed.status_code == 200
        assert (await resumed.get_json())["listo"] is True

    asyncio.run(run())


def test_news_quiet_scope_comes_from_signed_session_not_query(tmp_path, monkeypatch):
    async def run():
        import jarvis_backend
        from api import status_routes

        policy = NotificationPolicy(tmp_path / "notifications.db")
        policy.set_quiet("guest_quiet", True)
        monkeypatch.setattr(status_routes, "notification_policy", policy)
        monkeypatch.setattr(
            status_routes,
            "_services",
            SimpleNamespace(
                news_cache={
                    "listo": True,
                    "resumen": "Resumen de prueba.",
                }
            ),
        )
        monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
        client = jarvis_backend.app.test_client()
        response = await client.get(
            "/api/noticias?automatic=1&profile_id=guest_quiet", headers={"Origin": "http://127.0.0.1:5002"}
        )
        assert response.status_code == 200
        assert (await response.get_json())["resumen"] == "Resumen de prueba."

    asyncio.run(run())


def test_explicit_reminder_is_delivered_while_quiet_mode_is_active(tmp_path, monkeypatch):
    async def run():
        import jarvis_backend
        from api import status_routes
        from services.reminder_service import ReminderService

        policy = NotificationPolicy(tmp_path / "notifications.db")
        policy.set_quiet("web_default", True)
        monkeypatch.setattr("services.notification_policy.notification_policy", policy)
        monkeypatch.setattr(status_routes, "notification_policy", policy)
        reminders = ReminderService(tmp_path / "reminders.db")
        monkeypatch.setattr(jarvis_backend.services, "reminder_service", reminders)
        monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
        created = reminders.add("Recordatorio solicitado", 0, "web_default")
        client = jarvis_backend.app.test_client()
        headers = {"Origin": "http://127.0.0.1:5002"}
        status = await client.get("/api/status", headers=headers)
        assert (await status.get_json())["notifications"]["quiet"] is True
        response = await client.post("/api/reminders/claim", json={}, headers=headers)
        assert response.status_code == 200
        assert (await response.get_json())["reminders"][0]["id"] == created.reminder_id

    asyncio.run(run())


@pytest.mark.parametrize(("severity", "delivered"), [("info", False), ("warning", True)])
def test_quiet_mode_preserves_alert_history_and_important_delivery(tmp_path, monkeypatch, severity, delivered):
    from services import security_manager

    policy = NotificationPolicy(tmp_path / "notifications.db")
    policy.set_quiet("web_default", True)
    monkeypatch.setattr(security_manager, "notification_policy", policy, raising=False)
    monkeypatch.setattr(
        security_manager,
        "PROACTIVE_STATE",
        {
            "enabled": True,
            "cooldown_seconds": 600,
            "last_alert_by_key": {},
            "alerts": [],
        },
    )
    monkeypatch.setattr(security_manager, "_obs_event", None)
    monkeypatch.setattr(security_manager, "_obs_inc", None)
    sent = []

    class ImmediateThread:
        def __init__(self, target, args, **_kwargs):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(security_manager, "threading", SimpleNamespace(Thread=ImmediateThread))
    monkeypatch.setattr(security_manager, "enviar_telegram_sync", sent.append)
    with jarvis_state.active_profile("web_default"):
        assert security_manager._proactive_push_alert(
            "test", "Aviso sintético", severity=severity, key="synthetic", send_telegram=True
        )
    assert bool(sent) is delivered
    assert len(security_manager.PROACTIVE_STATE["alerts"]) == 1
    assert security_manager.PROACTIVE_STATE["alerts"][0]["delivery_deferred"] is (not delivered)
