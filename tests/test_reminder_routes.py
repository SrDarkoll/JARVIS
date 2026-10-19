from __future__ import annotations

import asyncio

from core import jarvis_state
from core.service_container import services
from services.reminder_service import ReminderService


def test_service_container_persists_reminder_for_active_profile(
    monkeypatch,
    tmp_path,
) -> None:
    reminder_service = ReminderService(tmp_path / "reminders.db")
    monkeypatch.setattr(services, "reminder_service", reminder_service, raising=False)

    with jarvis_state.active_profile("guest_Alex"):
        created = services.add_reminder("Revisar horno", 5)

    records = reminder_service.list_active("guest_alex")
    assert created.reminder_id == records[0].reminder_id
    assert records[0].text == "Revisar horno"


def test_reminder_routes_require_token_and_complete_claim_ack_cycle(
    monkeypatch,
    tmp_path,
) -> None:
    async def run() -> None:
        import jarvis_backend

        reminder_service = ReminderService(tmp_path / "reminders.db")
        monkeypatch.setattr(
            jarvis_backend.services,
            "reminder_service",
            reminder_service,
            raising=False,
        )
        monkeypatch.setenv("JARVIS_API_TOKEN", "reminder-route-secret")
        created = reminder_service.add("Tomar agua", 0, "admin")
        client = jarvis_backend.app.test_client()

        denied = await client.get("/api/reminders?profile_id=admin")
        assert denied.status_code == 401

        async with client.session_transaction() as browser_session:
            browser_session["profile_id"] = "admin"
        headers = {"X-JARVIS-API-TOKEN": "reminder-route-secret"}
        listed = await client.get(
            "/api/reminders?profile_id=admin",
            headers=headers,
        )
        assert listed.status_code == 200
        listed_payload = await listed.get_json()
        assert listed_payload["reminders"][0]["id"] == created.reminder_id

        claimed = await client.post(
            "/api/reminders/claim",
            json={"profile_id": "admin"},
            headers=headers,
        )
        assert claimed.status_code == 200
        claimed_payload = await claimed.get_json()
        assert claimed_payload["reminders"][0]["id"] == created.reminder_id

        acknowledged = await client.post(
            f"/api/reminders/{created.reminder_id}/ack",
            json={"profile_id": "admin"},
            headers=headers,
        )
        assert acknowledged.status_code == 200
        assert await acknowledged.get_json() == {"ok": True}

        duplicate_ack = await client.post(
            f"/api/reminders/{created.reminder_id}/ack",
            json={"profile_id": "admin"},
            headers=headers,
        )
        assert duplicate_ack.status_code == 404

    asyncio.run(run())
