"""Data contracts and immutable models for proactive desktop intelligence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class DesktopContext:
    """Current desktop activity and foreground application state."""

    active_app: str
    window_title: str
    pid: int = 0
    is_terminal: bool = False
    is_ide: bool = False
    is_browser: bool = False
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    detected_errors: tuple[str, ...] = ()
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ProactiveAction:
    """Proposed quick-fix or automated remedy."""

    action_id: str
    title: str
    command_or_tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass(frozen=True, slots=True)
class ProactiveAlert:
    """A prioritized, actionable suggestion or notification for the user."""

    alert_id: str
    key: str
    message: str
    severity: AlertSeverity = AlertSeverity.INFO
    suggested_action: ProactiveAction | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "key": self.key,
            "message": self.message,
            "severity": self.severity.value,
            "ts": self.created_at.strftime("%H:%M:%S"),
            "action": {
                "id": self.suggested_action.action_id,
                "title": self.suggested_action.title,
                "description": self.suggested_action.description,
            }
            if self.suggested_action
            else None,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class UserRoutine:
    """A learned or user-defined behavioral pattern / routine."""

    routine_id: str
    name: str
    trigger_type: str  # "time", "app", "context"
    trigger_condition: str  # e.g. "09:00", "code.exe", "spotify"
    actions: tuple[dict[str, Any], ...]
    days_of_week: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)
    confidence_threshold: float = 0.70
    execution_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "routine_id": self.routine_id,
            "name": self.name,
            "trigger_type": self.trigger_type,
            "trigger_condition": self.trigger_condition,
            "actions": list(self.actions),
            "days_of_week": list(self.days_of_week),
            "confidence_threshold": self.confidence_threshold,
            "execution_count": self.execution_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserRoutine:
        return cls(
            routine_id=str(data.get("routine_id") or ""),
            name=str(data.get("name") or ""),
            trigger_type=str(data.get("trigger_type") or "time"),
            trigger_condition=str(data.get("trigger_condition") or ""),
            actions=tuple(data.get("actions") or ()),
            days_of_week=tuple(data.get("days_of_week") or (0, 1, 2, 3, 4, 5, 6)),
            confidence_threshold=float(data.get("confidence_threshold") or 0.70),
            execution_count=int(data.get("execution_count") or 0),
        )


@dataclass(frozen=True, slots=True)
class PredictedIntention:
    """An anticipatory intention scored by the workflow predictor engine."""

    prediction_id: str
    title: str
    rationale: str
    confidence: float
    action: ProactiveAction | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "title": self.title,
            "rationale": self.rationale,
            "confidence": round(self.confidence, 2),
            "action": {
                "id": self.action.action_id,
                "title": self.action.title,
                "tool": self.action.command_or_tool,
            }
            if self.action
            else None,
        }
