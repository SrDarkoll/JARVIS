from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from time import sleep

from tools import utilities


def _reset_geo_state(monkeypatch):
    monkeypatch.setattr(utilities, "_DETECTED_IP_GEO", None)
    monkeypatch.setattr(utilities, "_IP_GEO_LAST_ATTEMPT", 0.0)
    return utilities


def test_ip_geolocation_is_disabled_by_default(monkeypatch):
    utilities = _reset_geo_state(monkeypatch)
    monkeypatch.delenv("JARVIS_IP_GEOLOCATION_ENABLED", raising=False)
    monkeypatch.setattr(
        utilities.http_requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network request must not run")),
    )

    assert utilities._auto_detect_ip_location() is None


def test_ip_geolocation_uses_https_only(monkeypatch):
    utilities = _reset_geo_state(monkeypatch)
    urls = []

    class Response:
        status_code = 503

        @staticmethod
        def json():
            return {}

    monkeypatch.setenv("JARVIS_IP_GEOLOCATION_ENABLED", "true")
    monkeypatch.setattr(
        utilities.http_requests,
        "get",
        lambda url, **_kwargs: urls.append(url) or Response(),
    )

    assert utilities._auto_detect_ip_location() is None
    assert urls
    assert all(url.startswith("https://") for url in urls)


def test_failed_ip_geolocation_uses_negative_cache(monkeypatch):
    utilities = _reset_geo_state(monkeypatch)
    calls = []

    def fail(_url, **_kwargs):
        calls.append(True)
        raise OSError("offline")

    monkeypatch.setenv("JARVIS_IP_GEOLOCATION_ENABLED", "true")
    monkeypatch.setenv("JARVIS_IP_GEOLOCATION_COOLDOWN_SECONDS", "300")
    monkeypatch.setattr(utilities.http_requests, "get", fail)

    assert utilities._auto_detect_ip_location() is None
    first_attempt_calls = len(calls)
    assert utilities._auto_detect_ip_location() is None

    assert first_attempt_calls > 0
    assert len(calls) == first_attempt_calls


def test_concurrent_ip_geolocation_is_single_flight(monkeypatch):
    utilities = _reset_geo_state(monkeypatch)
    calls = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "city": "Matamoros",
                "latitude": 25.87,
                "longitude": -97.50,
                "country_name": "Mexico",
            }

    def respond(_url, **_kwargs):
        calls.append(True)
        sleep(0.03)
        return Response()

    monkeypatch.setenv("JARVIS_IP_GEOLOCATION_ENABLED", "true")
    monkeypatch.setattr(utilities.http_requests, "get", respond)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda _index: utilities._auto_detect_ip_location(),
                range(6),
            )
        )

    assert len(calls) == 1
    assert all(result and result["city"] == "Matamoros" for result in results)
