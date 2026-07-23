"""Reasoning-mode policy for command planning."""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum


class ReasoningMode(StrEnum):
    """How the command orchestrator arbitrates deterministic and Groq plans."""

    ALWAYS = "always"
    HYBRID = "hybrid"
    OFFLINE = "offline"


def resolve_reasoning_mode(
    env: Mapping[str, str] | None = None,
) -> ReasoningMode:
    """Return a supported mode, defaulting invalid values to reasoning-first."""
    source = os.environ if env is None else env
    raw = str(source.get("JARVIS_REASONING_MODE") or "").strip().lower()
    try:
        return ReasoningMode(raw or ReasoningMode.ALWAYS.value)
    except ValueError:
        return ReasoningMode.ALWAYS
