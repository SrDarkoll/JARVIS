"""Immutable contracts shared by command pipeline components."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from core import jarvis_state


class PlanSource(StrEnum):
    """Component responsible for producing an action plan."""

    DETERMINISTIC = "deterministic"
    GROQ = "groq"


class ReceiptStatus(StrEnum):
    """Final outcome of one requested tool operation."""

    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    DUPLICATE = "duplicate"


def _frozen_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True, slots=True)
class CommandRequest:
    """Normalized user command and its isolated execution context."""

    request_id: str
    text: str
    profile_id: str
    channel: str
    language: str
    received_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def create(
        cls,
        *,
        text: str,
        profile_id: str,
        channel: str,
        language: str = "en",
        request_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> CommandRequest:
        normalized_text = str(text or "").strip()
        if not normalized_text:
            raise ValueError("command_text_required")

        return cls(
            request_id=str(request_id or uuid4()),
            text=normalized_text,
            profile_id=jarvis_state.normalize_profile_id(profile_id),
            channel=str(channel or "unknown").strip().lower(),
            language=str(language or "en").strip().lower(),
            received_at=datetime.now(UTC),
            metadata=_frozen_mapping(metadata),
        )


@dataclass(frozen=True, slots=True)
class ActionStep:
    """One validated operation requested by a planner."""

    step_id: str
    tool_name: str
    arguments: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    depends_on: tuple[str, ...] = ()
    parallel_safe: bool = False

    def __post_init__(self) -> None:
        if not self.step_id or not self.tool_name:
            raise ValueError("invalid_action_step")
        object.__setattr__(self, "arguments", _frozen_mapping(self.arguments))


@dataclass(frozen=True, slots=True)
class ActionPlan:
    """Validated deterministic or Groq-produced command plan."""

    request_id: str
    source: PlanSource
    steps: tuple[ActionStep, ...] = ()
    direct_response: str = ""
    requires_follow_up: bool = False
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    """Auditable result of exactly one tool operation."""

    request_id: str
    step_id: str
    tool_name: str
    status: ReceiptStatus
    result: Any
    user_message: str
    verified: bool
    diagnostic_code: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["started_at"] = self.started_at.isoformat()
        payload["finished_at"] = self.finished_at.isoformat()
        return payload


@dataclass(frozen=True, slots=True)
class CommandResponse:
    """Channel-neutral response returned by the command orchestrator."""

    request_id: str
    text: str
    should_listen: bool
    outcome: str
    receipts: tuple[ExecutionReceipt, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "text": self.text,
            "should_listen": self.should_listen,
            "outcome": self.outcome,
            "receipts": [receipt.to_dict() for receipt in self.receipts],
        }
