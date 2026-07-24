"""YouTube followup state and selection resolution."""

import re, time, difflib
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping
from modules.youtube.models import YouTubeCandidate


class YouTubeSelectionStatus(Enum):
    SELECTED = "selected"
    CLARIFY = "clarify"
    CANCELLED = "cancelled"
    UNRELATED = "unrelated"


@dataclass(frozen=True)
class YouTubeSelectionResult:
    status: YouTubeSelectionStatus
    candidate: YouTubeCandidate | None = None
    choices: tuple[YouTubeCandidate, ...] = ()


class PendingYouTubeSelections:
    """Almacena opciones ambiguas de búsqueda en YouTube para resolución por ordinales o títulos."""

    def __init__(self, clock: Callable[[], float] | None = None, ttl_seconds: float = 120.0):
        self._clock = clock or time.time
        self._ttl_seconds = ttl_seconds
        self._store: dict[str, tuple[float, tuple[YouTubeCandidate, ...]]] = {}

    def remember(self, profile_id: str, candidates: list[YouTubeCandidate]) -> tuple[YouTubeCandidate, ...]:
        pid = str(profile_id or "default").strip().lower()
        snapshot = tuple(candidates[:3])
        if snapshot:
            self._store[pid] = (self._clock(), snapshot)
        return snapshot

    def has_pending(self, profile_id: str) -> bool:
        pid = str(profile_id or "default").strip().lower()
        entry = self._store.get(pid)
        if not entry:
            return False
        ts, _ = entry
        if (self._clock() - ts) > self._ttl_seconds:
            self._store.pop(pid, None)
            return False
        return True

    def clear(self, profile_id: str) -> None:
        pid = str(profile_id or "default").strip().lower()
        self._store.pop(pid, None)

    def resolve(self, profile_id: str, text: str) -> YouTubeSelectionResult | None:
        if not self.has_pending(profile_id):
            return None

        pid = str(profile_id or "default").strip().lower()
        ts, choices = self._store[pid]
        raw = str(text or "").strip().lower()

        if any(k in raw for k in ["cancela", "cancelar", "ninguno", "ninguna", "olvidalo", "cancel"]):
            self.clear(profile_id)
            return YouTubeSelectionResult(status=YouTubeSelectionStatus.CANCELLED)

        # Ordinal matching
        ordinal_map = {
            "primero": 0, "primera": 0, "uno": 0, "1": 0, "la 1": 0, "el 1": 0, "el primero": 0, "la primera": 0,
            "segundo": 1, "segunda": 1, "dos": 1, "2": 1, "la 2": 1, "el 2": 1, "el segundo": 1, "la segunda": 1,
            "tercero": 2, "tercera": 2, "tres": 2, "3": 2, "la 3": 2, "el 3": 2, "el tercero": 2, "la tercera": 2,
        }
        for kw, idx in ordinal_map.items():
            if kw in raw and idx < len(choices):
                self.clear(profile_id)
                return YouTubeSelectionResult(status=YouTubeSelectionStatus.SELECTED, candidate=choices[idx])

        # Candidate title / channel matching
        best_candidate = None
        best_score = 0.0
        for cand in choices:
            cand_text = f"{cand.title} {cand.channel}".lower()
            ratio = difflib.SequenceMatcher(None, raw, cand_text).ratio()
            if ratio > best_score:
                best_score = ratio
                best_candidate = cand

        if best_candidate and best_score >= 0.4:
            self.clear(profile_id)
            return YouTubeSelectionResult(status=YouTubeSelectionStatus.SELECTED, candidate=best_candidate)

        self.clear(profile_id)
        return YouTubeSelectionResult(status=YouTubeSelectionStatus.UNRELATED)


pending_youtube_selections = PendingYouTubeSelections()
