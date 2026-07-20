from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum

from tools.spotify_desktop.matching import normalize_text
from tools.spotify_desktop.models import SpotifyCandidate

_SELECTION_STOP_WORDS = {
    "by",
    "cancion",
    "con",
    "de",
    "del",
    "el",
    "esa",
    "ese",
    "feat",
    "featuring",
    "la",
    "las",
    "los",
    "pon",
    "poner",
    "por",
    "quiero",
    "reproduce",
    "reproducir",
    "tema",
    "version",
    "y",
}
_CANCEL_PHRASES = {
    "cancel",
    "cancel it",
    "cancela",
    "dejalo",
    "forget it",
    "ninguna",
    "none",
    "olvidalo",
}
_SELECTION_CUES = {
    "esa",
    "ese",
    "la de",
    "la primera",
    "la segunda",
    "la tercera",
    "primera",
    "primero",
    "segunda",
    "segundo",
    "tercera",
    "tercero",
}
_ORDINALS = {
    "1": 0,
    "first": 0,
    "one": 0,
    "primera": 0,
    "primero": 0,
    "uno": 0,
    "2": 1,
    "dos": 1,
    "second": 1,
    "segunda": 1,
    "segundo": 1,
    "3": 2,
    "tercera": 2,
    "tercero": 2,
    "third": 2,
    "three": 2,
    "tres": 2,
}


class SpotifySelectionStatus(Enum):
    SELECTED = "selected"
    CLARIFY = "clarify"
    CANCELLED = "cancelled"
    UNRELATED = "unrelated"


@dataclass(frozen=True)
class SpotifySelectionResolution:
    status: SpotifySelectionStatus
    candidate: SpotifyCandidate | None = None
    choices: tuple[SpotifyCandidate, ...] = ()


@dataclass(frozen=True)
class _PendingSelection:
    choices: tuple[SpotifyCandidate, ...]
    created_at: float


def _profile_key(profile_id: str) -> str:
    return str(profile_id or "admin").strip().lower() or "admin"


def _meaningful_tokens(value: str) -> list[str]:
    return [token for token in normalize_text(value).split() if token not in _SELECTION_STOP_WORDS]


def _choice_score(user_input: str, candidate: SpotifyCandidate) -> float:
    requested_tokens = _meaningful_tokens(user_input)
    candidate_text = f"{candidate.title} {candidate.artist}"
    candidate_tokens = _meaningful_tokens(candidate_text)
    if not requested_tokens or not candidate_tokens:
        return 0.0

    token_scores = [
        max(SequenceMatcher(None, requested, observed).ratio() for observed in candidate_tokens)
        for requested in requested_tokens
    ]
    coverage = sum(score for score in token_scores if score >= 0.62) / len(requested_tokens)
    sequence = SequenceMatcher(
        None,
        " ".join(requested_tokens),
        " ".join(candidate_tokens),
    ).ratio()
    return min(1.0, (coverage * 0.78) + (sequence * 0.22))


def _ordinal_index(value: str) -> int | None:
    normalized = normalize_text(value)
    for token in re.findall(r"\b(?:1|2|3|[a-z]+)\b", normalized):
        if token in _ORDINALS:
            return _ORDINALS[token]
    return None


class PendingSpotifySelections:
    def __init__(
        self,
        *,
        timeout: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout = max(1.0, float(timeout))
        self._clock = clock
        self._items: dict[str, _PendingSelection] = {}
        self._lock = threading.RLock()

    def remember(
        self,
        profile_id: str,
        choices: Iterable[SpotifyCandidate],
    ) -> None:
        candidates = tuple(choices)[:3]
        key = _profile_key(profile_id)
        with self._lock:
            if not candidates:
                self._items.pop(key, None)
                return
            self._items[key] = _PendingSelection(candidates, self._clock())

    def clear(self, profile_id: str) -> None:
        with self._lock:
            self._items.pop(_profile_key(profile_id), None)

    def _current(self, profile_id: str) -> _PendingSelection | None:
        key = _profile_key(profile_id)
        pending = self._items.get(key)
        if pending and self._clock() - pending.created_at > self._timeout:
            self._items.pop(key, None)
            return None
        return pending

    def has_pending(self, profile_id: str) -> bool:
        with self._lock:
            return self._current(profile_id) is not None

    def resolve(
        self,
        profile_id: str,
        user_input: str,
    ) -> SpotifySelectionResolution | None:
        key = _profile_key(profile_id)
        normalized = normalize_text(user_input)
        with self._lock:
            pending = self._current(key)
            if pending is None:
                return None

            if normalized in _CANCEL_PHRASES:
                self._items.pop(key, None)
                return SpotifySelectionResolution(
                    SpotifySelectionStatus.CANCELLED,
                    choices=pending.choices,
                )

            ordinal = _ordinal_index(normalized)
            if ordinal is not None:
                if ordinal < len(pending.choices):
                    candidate = pending.choices[ordinal]
                    self._items.pop(key, None)
                    return SpotifySelectionResolution(
                        SpotifySelectionStatus.SELECTED,
                        candidate=candidate,
                        choices=pending.choices,
                    )
                return SpotifySelectionResolution(
                    SpotifySelectionStatus.CLARIFY,
                    choices=pending.choices,
                )

            ranked = sorted(
                ((_choice_score(normalized, candidate), candidate) for candidate in pending.choices),
                key=lambda item: item[0],
                reverse=True,
            )
            best_score, best = ranked[0]
            runner_up_score = ranked[1][0] if len(ranked) > 1 else 0.0
            if best_score >= 0.48 and best_score - runner_up_score >= 0.12:
                self._items.pop(key, None)
                return SpotifySelectionResolution(
                    SpotifySelectionStatus.SELECTED,
                    candidate=best,
                    choices=pending.choices,
                )

            selection_like = any(normalized == cue or normalized.startswith(f"{cue} ") for cue in _SELECTION_CUES)
            status = (
                SpotifySelectionStatus.CLARIFY
                if selection_like or best_score >= 0.20
                else SpotifySelectionStatus.UNRELATED
            )
            return SpotifySelectionResolution(
                status,
                choices=pending.choices,
            )


pending_spotify_selections = PendingSpotifySelections()
