import asyncio
from pathlib import Path

from services import monitoring_service as monitoring_module

ROOT = Path(__file__).resolve().parent.parent


class FakeCronTrigger:
    def __init__(self, **kwargs):
        self.options = kwargs


class FakeScheduler:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.jobs = []
        self.start_calls = 0
        self.shutdown_calls = []

    def add_job(self, func, *args, **kwargs):
        self.jobs.append((func, args, kwargs))

    def start(self):
        self.start_calls += 1

    def shutdown(self, *, wait=True):
        self.shutdown_calls.append(wait)

    def get_jobs(self):
        return list(self.jobs)


def _enabled_service(monkeypatch):
    monkeypatch.setattr(monitoring_module, "SCHEDULER_AVAILABLE", True)
    monkeypatch.setattr(monitoring_module, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(monitoring_module, "CronTrigger", FakeCronTrigger)
    return monitoring_module.MonitoringService(enabled=True)


def test_disabled_monitoring_never_constructs_scheduler(monkeypatch):
    created = []

    class FakeScheduler:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(monitoring_module, "SCHEDULER_AVAILABLE", True)
    monkeypatch.setattr(monitoring_module, "BackgroundScheduler", FakeScheduler)

    service = monitoring_module.MonitoringService(enabled=False)

    assert service._scheduler is None
    assert created == []
    assert service.start_heartbeat() is False


def test_enabled_monitoring_constructs_available_scheduler(monkeypatch):
    created = []

    class FakeScheduler:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(monitoring_module, "SCHEDULER_AVAILABLE", True)
    monkeypatch.setattr(monitoring_module, "BackgroundScheduler", FakeScheduler)

    service = monitoring_module.MonitoringService(enabled=True)

    assert isinstance(service._scheduler, FakeScheduler)
    assert created == [{"daemon": True}]


def test_enabled_monitoring_without_apscheduler_degrades_cleanly(monkeypatch):
    monkeypatch.setattr(monitoring_module, "SCHEDULER_AVAILABLE", False)
    monkeypatch.setattr(monitoring_module, "BackgroundScheduler", None)

    service = monitoring_module.MonitoringService(enabled=True)

    assert service.start_heartbeat() is False
    assert service.snapshot() == {
        "configured": True,
        "available": False,
        "running": False,
    }


def test_start_registers_jobs_once_and_is_idempotent(monkeypatch):
    service = _enabled_service(monkeypatch)

    assert service.start_heartbeat() is True
    assert service.start_heartbeat() is True

    assert len(service._scheduler.jobs) == 5
    assert service._scheduler.start_calls == 1
    assert service.snapshot()["running"] is True


def test_stop_shuts_scheduler_down_once(monkeypatch):
    service = _enabled_service(monkeypatch)
    service.start_heartbeat()

    assert service.stop() is True
    assert service.stop() is False

    assert service._scheduler.shutdown_calls == [False]
    assert service.snapshot()["running"] is False


def test_backend_lifecycle_hooks_start_and_stop_monitoring(monkeypatch):
    import jarvis_backend

    calls = []

    class FakeMonitoringService:
        configured = True

        def start_heartbeat(self):
            calls.append("start")
            return True

        def stop(self):
            calls.append("stop")
            return True

        def snapshot(self):
            return {"configured": True, "available": True, "running": False}

    start_hook = jarvis_backend._start_monitoring_service
    stop_hook = jarvis_backend._stop_monitoring_service
    monkeypatch.setattr(jarvis_backend, "monitoring_service", FakeMonitoringService())
    monkeypatch.setattr(
        jarvis_backend,
        "_configure_monitoring_service",
        lambda: calls.append("configure"),
    )

    asyncio.run(start_hook())
    asyncio.run(stop_hook())

    assert start_hook in jarvis_backend.app.before_serving_funcs
    assert stop_hook in jarvis_backend.app.after_serving_funcs
    assert calls == ["configure", "start", "stop"]


def test_status_reports_monitoring_runtime_snapshot(monkeypatch):
    import jarvis_backend
    from api import status_routes

    monkeypatch.setattr(
        status_routes,
        "_monitoring_snapshot",
        lambda: {"configured": True, "available": True, "running": True},
        raising=False,
    )

    async def fetch_status():
        response = await jarvis_backend.app.test_client().get("/api/status")
        return await response.get_json()

    payload = asyncio.run(fetch_status())

    assert payload["features"]["monitoring"] is True
    assert payload["features"]["monitoring_available"] is True
    assert payload["features"]["monitoring_running"] is True


def test_optional_requirements_pin_apscheduler_to_v3():
    requirements = (ROOT / "requirements-optional.txt").read_text(encoding="utf-8")

    assert "apscheduler>=3.10,<4" in requirements.splitlines()
