from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from core.capabilities import (
    CapabilityRegistry,
    CapabilityReport,
    CapabilityState,
)


def test_capability_registry_uses_one_state_vocabulary() -> None:
    registry = CapabilityRegistry()
    registry.set(
        CapabilityReport(
            "llm",
            CapabilityState.UNCONFIGURED,
            "groq_key_missing",
            "Configure GROQ_API_KEY",
        )
    )
    registry.set(
        CapabilityReport(
            "rag",
            CapabilityState.DISABLED,
            "core_mode",
            "Enable JARVIS_RAG_ENABLED",
        )
    )

    snapshot = registry.snapshot()

    assert snapshot["llm"]["state"] == "unconfigured"
    assert snapshot["rag"]["state"] == "disabled"


def test_capability_payload_redacts_environment_and_bearer_secrets(
    monkeypatch,
) -> None:
    monkeypatch.setenv("JARVIS_TEST_API_KEY", "environment-private-token")
    report = CapabilityReport(
        "spotify_api",
        CapabilityState.FAILED,
        "oauth_failed",
        "Reconnect Spotify",
        detail=(
            "Bearer private-token and "
            "environment-private-token"
        ),
    )

    detail = report.to_dict()["detail"]

    assert "private-token" not in detail
    assert "environment-private-token" not in detail
    assert "[REDACTED]" in detail


def test_capability_registry_concurrent_snapshots_are_complete() -> None:
    registry = CapabilityRegistry()

    def set_report(index: int) -> None:
        registry.set(
            CapabilityReport(
                name=f"capability-{index}",
                state=CapabilityState.AVAILABLE,
                code="ready",
                action="",
            )
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(set_report, range(100)))

    snapshot = registry.snapshot()

    assert len(snapshot) == 100
    assert all(
        payload["state"] == "available"
        for payload in snapshot.values()
    )
