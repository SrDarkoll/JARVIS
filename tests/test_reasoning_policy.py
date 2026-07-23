from __future__ import annotations

import pytest
from core.command_pipeline.reasoning import (
    ReasoningMode,
    resolve_reasoning_mode,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("always", ReasoningMode.ALWAYS),
        ("ALWAYS", ReasoningMode.ALWAYS),
        ("hybrid", ReasoningMode.HYBRID),
        ("offline", ReasoningMode.OFFLINE),
        ("", ReasoningMode.ALWAYS),
        ("invalid", ReasoningMode.ALWAYS),
    ],
)
def test_resolve_reasoning_mode_uses_supported_values(raw, expected) -> None:
    assert resolve_reasoning_mode(
        {"JARVIS_REASONING_MODE": raw}
    ) is expected


def test_resolve_reasoning_mode_defaults_to_always() -> None:
    assert resolve_reasoning_mode({}) is ReasoningMode.ALWAYS


def test_runtime_configuration_exposes_reasoning_mode() -> None:
    from core import jarvis_config

    assert jarvis_config.REASONING_MODE in {
        ReasoningMode.ALWAYS,
        ReasoningMode.HYBRID,
        ReasoningMode.OFFLINE,
    }
