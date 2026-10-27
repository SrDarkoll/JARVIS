from __future__ import annotations

import sys
from types import SimpleNamespace

from api import status_routes
from api.api_routes import build_config_info
from core.api_contracts import validate_status_full_response


def _status_payload(temp, source: str) -> dict:
    return {
        "status": "online",
        "llm_ok": False,
        "llm_latency_ms": -1,
        "cpu": 10.0,
        "ram": 20.0,
        "temp": temp,
        "temp_source": source,
        "weather": {"temp": "--", "desc": "unavailable"},
        "security": {"strict_mode": False, "blocked_total": 0},
        "proactive": {"enabled": False},
    }


def test_cpu_temperature_is_not_fabricated_when_no_sensor_exists(monkeypatch):
    monkeypatch.setattr(status_routes.psutil, "sensors_temperatures", lambda: {}, raising=False)
    monkeypatch.setattr(status_routes.sys, "platform", "linux")

    value, source = status_routes._read_cpu_temperature()

    assert value is None
    assert source == "unavailable"


def test_cpu_temperature_uses_a_real_psutil_sensor(monkeypatch):
    sensor = SimpleNamespace(current=63.25, label="Package id 0")
    monkeypatch.setattr(
        status_routes.psutil,
        "sensors_temperatures",
        lambda: {"coretemp": [sensor]},
        raising=False,
    )

    value, source = status_routes._read_cpu_temperature()

    assert value == 63.2
    assert source == "psutil:coretemp"


def test_unavailable_wmi_temperature_uses_a_retry_cooldown(monkeypatch):
    now = [100.0]
    calls, warnings, com = [], [], []
    monkeypatch.setattr(status_routes.psutil, "sensors_temperatures", lambda: {}, raising=False)
    monkeypatch.setattr(status_routes.sys, "platform", "win32")
    monkeypatch.setattr(status_routes, "monotonic", lambda: now[0], raising=False)
    monkeypatch.setattr(status_routes, "_wmi_temperature_retry_at", 0.0, raising=False)
    monkeypatch.setattr(status_routes, "log_warning", lambda message, **_ctx: warnings.append(message))

    def unavailable(**_kwargs):
        calls.append(True)
        raise PermissionError("synthetic denied sensor")

    monkeypatch.setitem(sys.modules, "wmi", SimpleNamespace(WMI=unavailable))
    monkeypatch.setitem(sys.modules, "pythoncom", SimpleNamespace(
        CoInitialize=lambda: com.append("init"), CoUninitialize=lambda: com.append("uninit"),
    ))
    for _ in range(6):
        assert status_routes._read_cpu_temperature() == (None, "unavailable")
    assert len(calls) == 1
    assert warnings == ["cpu_temperature_wmi_unavailable"]
    assert com == ["init", "uninit"]
    now[0] += 301
    assert status_routes._read_cpu_temperature() == (None, "unavailable")
    assert len(calls) == 2


def test_status_contract_allows_explicitly_unavailable_temperature():
    assert validate_status_full_response(_status_payload(None, "unavailable")).ok is True


def test_config_info_reports_actual_disabled_capabilities():
    unavailable_tts = SimpleNamespace(voice=None)

    payload = build_config_info(
        "system",
        tts_engine=unavailable_tts,
        biometrics_enabled=False,
    )

    assert payload == {
        "browser_mode": "system",
        "tts_enabled": False,
        "biometrics_active": False,
    }
