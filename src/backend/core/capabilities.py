"""Uniform runtime capability states for setup and status endpoints."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from core.unified_log import redact_text


class CapabilityState(StrEnum):
    AVAILABLE = "available"
    UNCONFIGURED = "unconfigured"
    DEGRADED = "degraded"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    name: str
    state: CapabilityState
    code: str
    action: str
    detail: str = ""
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("capability_name_required")
        if not self.code.strip():
            raise ValueError("capability_code_required")

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "state": self.state.value,
            "code": self.code,
            "action": self.action,
            "detail": redact_text(self.detail),
            "checked_at": self.checked_at.isoformat(),
        }


class CapabilityRegistry:
    """Publish coherent capability reports under one state vocabulary."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._reports: dict[str, CapabilityReport] = {}

    def set(self, report: CapabilityReport) -> None:
        with self._lock:
            self._reports[report.name] = report

    def get(self, name: str) -> CapabilityReport | None:
        with self._lock:
            return self._reports.get(str(name or "").strip())

    def snapshot(self) -> dict[str, dict[str, str]]:
        with self._lock:
            return {name: report.to_dict() for name, report in sorted(self._reports.items())}
