from __future__ import annotations

import pytest
from core.service_container import ServiceContainer, services


def test_container_is_singleton():
    assert ServiceContainer() is services


def test_unknown_attribute_assignment_is_rejected():
    # A typo such as `services.invoke_tool` must fail at wiring time instead of
    # silently leaving the real `invocar_tool` slot unset.
    with pytest.raises(AttributeError, match="invoke_tool"):
        services.invoke_tool = lambda *a, **k: None  # type: ignore[attr-defined]


def test_declared_attribute_assignment_still_works(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(services, "llm", sentinel)
    assert services.llm is sentinel


def test_missing_required_reports_unset_dependencies(monkeypatch):
    for name in ServiceContainer.REQUIRED:
        monkeypatch.setattr(services, name, None)
    assert services.missing_required() == sorted(ServiceContainer.REQUIRED)

    for name in ServiceContainer.REQUIRED:
        monkeypatch.setattr(services, name, lambda *a, **k: None)
    assert services.missing_required() == []


def test_log_event_failure_does_not_raise(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("observer down")

    monkeypatch.setattr(services, "obs_event", boom)
    services.log_event("anything", key="value")
