from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class AutomationState(StrEnum):
    IDLE = "idle"
    DISCOVERING = "discovering"
    STARTING = "starting"
    FOCUSING = "focusing"
    SEARCHING = "searching"
    SELECTING = "selecting"
    PLAYING = "playing"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MatchStatus(StrEnum):
    SELECTED = "selected"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class DesktopResultStatus(StrEnum):
    SUCCESS = "success"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    RESTRICTED = "restricted"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class SpotifyRequest:
    raw: str
    query: str
    title: str = ""
    artist: str = ""
    kind: str = "track"


@dataclass(frozen=True)
class SpotifyCandidate:
    element_id: str
    title: str
    artist: str = ""
    kind: str = "track"
    subtitle: str = ""
    score: float = 0.0


@dataclass(frozen=True)
class MatchDecision:
    status: MatchStatus
    selected: SpotifyCandidate | None = None
    alternatives: tuple[SpotifyCandidate, ...] = ()
    confidence: float = 0.0


@dataclass(frozen=True)
class SpotifyDesktopResult:
    status: DesktopResultStatus
    title: str = ""
    artist: str = ""
    message_key: str = ""
    choices: tuple[SpotifyCandidate, ...] = ()
    states: tuple[AutomationState, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.status is DesktopResultStatus.SUCCESS
