from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from services.reminder_service import ReminderService


class FakeClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_reminder_persists_and_is_profile_scoped(tmp_path) -> None:
    clock = FakeClock()
    db_path = tmp_path / "reminders.db"
    service = ReminderService(db_path, clock=clock)

    created = service.add("Pagar la luz", 5, "Admin")
    service.add("Revisar horno", 10, "guest_alex")

    reopened = ReminderService(db_path, clock=clock)
    admin_records = reopened.list_active("ADMIN")

    assert len(admin_records) == 1
    assert admin_records[0].reminder_id == created.reminder_id
    assert admin_records[0].profile_id == "admin"
    assert admin_records[0].text == "Pagar la luz"
    assert admin_records[0].due_at == 1_300.0
    assert reopened.list_active("guest_alex")[0].text == "Revisar horno"


def test_due_reminder_has_one_active_claim_and_retries_after_lease(tmp_path) -> None:
    clock = FakeClock()
    db_path = tmp_path / "reminders.db"
    first = ReminderService(db_path, clock=clock)
    second = ReminderService(db_path, clock=clock)
    created = first.add("Tomar agua", 0, "admin")

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(
            executor.map(
                lambda service: service.claim_due("admin", lease_seconds=60),
                (first, second),
            )
        )

    claimed = [record for batch in claims for record in batch]
    assert [record.reminder_id for record in claimed] == [created.reminder_id]
    assert first.claim_due("admin", lease_seconds=60) == []

    clock.advance(61)
    retried = second.claim_due("admin", lease_seconds=60)
    assert [record.reminder_id for record in retried] == [created.reminder_id]


def test_acknowledged_reminder_is_not_active_or_claimable(tmp_path) -> None:
    clock = FakeClock()
    service = ReminderService(tmp_path / "reminders.db", clock=clock)
    created = service.add("Cerrar ventana", 0, "admin")
    assert service.claim_due("admin")

    assert service.acknowledge(created.reminder_id, "admin") is True
    assert service.acknowledge(created.reminder_id, "admin") is False
    assert service.list_active("admin") == []
    assert service.claim_due("admin") == []


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("", 1),
        ("x" * 501, 1),
        ("válido", -1),
        ("válido", 525_601),
    ],
)
def test_invalid_reminders_are_rejected(tmp_path, text, minutes) -> None:
    service = ReminderService(tmp_path / "reminders.db")

    with pytest.raises(ValueError, match="invalid_reminder"):
        service.add(text, minutes, "admin")


def test_reminder_tool_returns_verified_persistence_evidence(monkeypatch) -> None:
    from core.command_pipeline.models import ToolExecutionResult
    from tools import utilities

    monkeypatch.setattr(
        utilities,
        "agregar_recordatorio",
        lambda _text, _minutes: SimpleNamespace(
            reminder_id="rem-verified",
            due_at=1_800_000_000.0,
        ),
    )

    result = utilities.poner_recordatorio.invoke(
        {"texto": "Pagar la luz", "minutos": 10}
    )

    assert isinstance(result, ToolExecutionResult)
    assert result.verified is True
    assert result.accepted is False
    assert result.diagnostic_code == "reminder_persisted"
    assert "Pagar la luz" in result.user_message
