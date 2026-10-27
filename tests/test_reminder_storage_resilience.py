from __future__ import annotations

import asyncio
import sqlite3
import threading
from contextlib import closing

import pytest
from services.reminder_service import ReminderService


def test_every_reminder_operation_closes_its_connection(monkeypatch, tmp_path):
    import services.reminder_service as module

    connections = []
    original_connect = module.sqlite3.connect

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", connect)
    try:
        service = ReminderService(tmp_path / "reminders.db")
        record = service.add("Prueba sintética", 0, "admin")
        service.list_active("admin")
        service.claim_due("admin")
        service.claim_due("admin")  # Empty claim follows the early return path.
        service.acknowledge(record.reminder_id, "admin")
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
    finally:
        for connection in connections:
            connection.close()


def test_failed_reminder_operation_closes_its_connection(monkeypatch, tmp_path):
    service = ReminderService(tmp_path / "reminders.db")
    connection = sqlite3.connect(service.db_path)
    connection.execute("DROP TABLE reminders")
    connection.commit()
    monkeypatch.setattr(service, "_connect", lambda: connection)
    try:
        with pytest.raises(sqlite3.OperationalError):
            service.add("Prueba sintética", 0, "admin")
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    finally:
        connection.close()


def test_failed_connection_setup_closes_the_new_connection(monkeypatch, tmp_path):
    import services.reminder_service as module

    connections = []
    original_connect = sqlite3.connect

    class BrokenConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA busy_timeout = 5000":
                raise sqlite3.OperationalError("synthetic setup failure")
            return super().execute(sql, *args, **kwargs)

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs, factory=BrokenConnection)
        connections.append(connection)
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", connect)
    try:
        with pytest.raises(sqlite3.OperationalError, match="synthetic setup failure"):
            ReminderService(tmp_path / "reminders.db")
        assert len(connections) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connections[0].execute("SELECT 1")
    finally:
        for connection in connections:
            connection.close()


@pytest.mark.parametrize("operation", ["claim", "ack"])
def test_reminder_writes_do_not_execute_on_the_server_event_loop(monkeypatch, tmp_path, operation):
    async def run():
        import jarvis_backend

        service = ReminderService(tmp_path / "reminders.db")
        created = service.add("Prueba sintética", 0, "admin")
        if operation == "ack":
            service.claim_due("admin")
        original = service.claim_due if operation == "claim" else service.acknowledge
        thread_ids = []

        def tracked(*args, **kwargs):
            thread_ids.append(threading.get_ident())
            return original(*args, **kwargs)

        monkeypatch.setattr(service, "claim_due" if operation == "claim" else "acknowledge", tracked)
        monkeypatch.setattr(jarvis_backend.services, "reminder_service", service)
        client = jarvis_backend.app.test_client()
        async with client.session_transaction() as browser_session:
            browser_session["profile_id"] = "admin"
        path = "/api/reminders/claim" if operation == "claim" else f"/api/reminders/{created.reminder_id}/ack"
        response = await client.post(path, json={})
        assert response.status_code == 200
        assert thread_ids and thread_ids[0] != threading.get_ident()
    asyncio.run(run())


@pytest.mark.parametrize("operation", ["list", "claim", "ack"])
@pytest.mark.parametrize("error_type", [sqlite3.OperationalError, OSError])
def test_unavailable_reminder_storage_returns_a_sanitized_retryable_error(monkeypatch, tmp_path, caplog, operation, error_type):
    async def run():
        import jarvis_backend

        service = ReminderService(tmp_path / "reminders.db")
        method = {"list": "list_active", "claim": "claim_due", "ack": "acknowledge"}[operation]

        def fail(*_args, **_kwargs):
            raise error_type("private-path credential=secret sqlite detail")

        monkeypatch.setattr(service, method, fail)
        monkeypatch.setattr(jarvis_backend.services, "reminder_service", service)
        client = jarvis_backend.app.test_client()
        if operation == "list":
            response = await client.get("/api/reminders")
        else:
            path = "/api/reminders/claim" if operation == "claim" else "/api/reminders/synthetic/ack"
            response = await client.post(path, json={})
        assert response.status_code == 503
        assert await response.get_json() == {"ok": False, "error": "reminders_unavailable"}
        assert response.headers.get("Retry-After") == "5"
        assert "private-path" not in await response.get_data(as_text=True)
        assert "private-path" not in caplog.text
    asyncio.run(run())


def test_claim_waiting_for_sqlite_leaves_the_event_loop_responsive(monkeypatch, tmp_path):
    async def run():
        import jarvis_backend

        service = ReminderService(tmp_path / "reminders.db")
        service.add("Prueba sintética", 0, "admin")
        original_connect = service._connect
        loop = asyncio.get_running_loop()
        connection_opened = asyncio.Event()

        def short_timeout_connection():
            connection = original_connect()
            connection.execute("PRAGMA busy_timeout = 1000")
            loop.call_soon_threadsafe(connection_opened.set)
            return connection

        monkeypatch.setattr(service, "_connect", short_timeout_connection)
        monkeypatch.setattr(jarvis_backend.services, "reminder_service", service)
        client = jarvis_backend.app.test_client()
        async with client.session_transaction() as browser_session:
            browser_session["profile_id"] = "admin"
        with closing(sqlite3.connect(service.db_path)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")

            async def release_lock():
                await asyncio.wait_for(connection_opened.wait(), timeout=2)
                blocker.rollback()

            response, _ = await asyncio.gather(client.post("/api/reminders/claim", json={}), release_lock())
        assert response.status_code == 200
        assert len((await response.get_json())["reminders"]) == 1
    asyncio.run(run())
