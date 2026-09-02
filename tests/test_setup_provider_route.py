from __future__ import annotations

import asyncio

import pytest
from api import status_routes
from core.setup_configuration import SetupConfigurationError


async def _post_provider(app, *, payload, headers=None, remote_addr="127.0.0.1"):
    client = app.test_client()
    response = await client.post(
        "/api/setup/provider",
        json=payload,
        headers=headers,
        scope_base={"client": (remote_addr, 12345)},
    )
    return response.status_code, await response.get_json(), response.headers


async def _get_setup_status(app):
    response = await app.test_client().get(
        "/api/setup/status",
        headers={"Origin": "http://localhost:5002"},
        scope_base={"client": ("127.0.0.1", 12345)},
    )
    return response.status_code, await response.get_json(), response.headers


@pytest.fixture(autouse=True)
def _reset_setup_limiter():
    status_routes._SETUP_PROVIDER_LIMITER.reset()
    yield
    status_routes._SETUP_PROVIDER_LIMITER.reset()


def test_setup_provider_returns_secret_free_no_store_success(monkeypatch):
    import jarvis_backend

    secret = "g" * 24
    observed = {}

    def apply_payload(payload):
        observed.update(payload)
        return {
            "ok": True,
            "configured": True,
            "provider": "gemini",
            "runtime_ready": True,
            "restart_required": False,
        }

    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    monkeypatch.setattr(status_routes, "_apply_setup_provider_payload", apply_payload)

    status, data, headers = asyncio.run(
        _post_provider(
            jarvis_backend.app,
            payload={
                "provider": "gemini",
                "api_key": secret,
                "save_local": True,
            },
            headers={"Origin": "http://localhost:5002"},
        )
    )

    assert status == 200
    assert observed["api_key"] == secret
    assert data == {
        "ok": True,
        "configured": True,
        "provider": "gemini",
        "runtime_ready": True,
        "restart_required": False,
    }
    assert secret not in repr(data)
    assert headers["Cache-Control"] == "no-store"


def test_setup_provider_maps_controlled_validation_errors(monkeypatch):
    import jarvis_backend

    def fail_validation(_payload):
        raise SetupConfigurationError("invalid_key_format")

    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    monkeypatch.setattr(status_routes, "_apply_setup_provider_payload", fail_validation)

    status, data, headers = asyncio.run(
        _post_provider(
            jarvis_backend.app,
            payload={"provider": "groq", "api_key": "short", "save_local": True},
            headers={"Origin": "http://localhost:5002"},
        )
    )

    assert status == 400
    assert data == {"error": "invalid_key_format"}
    assert headers["Cache-Control"] == "no-store"


def test_setup_provider_is_protected_as_a_critical_route(monkeypatch):
    import jarvis_backend

    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    monkeypatch.setattr(
        status_routes,
        "_apply_setup_provider_payload",
        lambda _payload: pytest.fail("critical guard should run before route"),
    )

    status, data, headers = asyncio.run(
        _post_provider(
            jarvis_backend.app,
            payload={"provider": "gemini", "api_key": "g" * 24, "save_local": True},
            headers={"Origin": "https://evil.example"},
        )
    )

    assert status == 403
    assert data == {"error": "Untrusted origin for critical route."}
    assert headers["Cache-Control"] == "no-store"


def test_setup_status_is_never_cached(monkeypatch):
    import jarvis_backend

    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    status, data, headers = asyncio.run(_get_setup_status(jarvis_backend.app))

    assert status == 200
    assert "items" in data
    assert "capabilities" in data
    assert headers["Cache-Control"] == "no-store"
