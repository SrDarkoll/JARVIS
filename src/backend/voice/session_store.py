"""Thread-safe storage for short-lived voice interaction sessions."""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable, Iterator, Mapping, MutableMapping
from typing import Any

Session = dict[str, Any]
SessionTransform = Callable[[Session], Mapping[str, Any]]


class VoiceSessionStore:
    """Own voice session mutations and return independent snapshots."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        ttl_seconds: float = 300.0,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("voice_session_ttl_must_be_positive")
        self._clock = clock
        self._ttl_seconds = float(ttl_seconds)
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}

    @staticmethod
    def _key(key: str) -> str:
        normalized = str(key or "").strip()
        if not normalized:
            raise ValueError("voice_session_key_required")
        return normalized

    @staticmethod
    def _copy(value: Mapping[str, Any]) -> Session:
        return copy.deepcopy(dict(value))

    def start(self, key: str, value: Mapping[str, Any]) -> Session:
        session = self._copy(value)
        session["created_at"] = float(self._clock())
        return self.replace(key, session)

    def replace(self, key: str, value: Mapping[str, Any]) -> Session:
        session = self._copy(value)
        session.setdefault("created_at", float(self._clock()))
        with self._lock:
            self._sessions[self._key(key)] = session
            return self._copy(session)

    def get(self, key: str) -> Session | None:
        with self._lock:
            value = self._sessions.get(self._key(key))
            return self._copy(value) if value is not None else None

    def update(
        self,
        key: str,
        transform: SessionTransform,
    ) -> Session:
        session_key = self._key(key)
        with self._lock:
            current = self._copy(self._sessions.get(session_key, {}))
            updated = self._copy(transform(current))
            updated.setdefault(
                "created_at",
                current.get("created_at", float(self._clock())),
            )
            self._sessions[session_key] = updated
            return self._copy(updated)

    def pop(self, key: str) -> Session | None:
        with self._lock:
            value = self._sessions.pop(self._key(key), None)
            return self._copy(value) if value is not None else None

    def cancel(self, key: str | None = None) -> bool:
        with self._lock:
            if key is None:
                changed = bool(self._sessions)
                self._sessions.clear()
                return changed
            return self._sessions.pop(self._key(key), None) is not None

    def keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._sessions)

    def cleanup_expired(self) -> int:
        cutoff = float(self._clock()) - self._ttl_seconds
        with self._lock:
            expired = [key for key, value in self._sessions.items() if float(value.get("created_at", 0.0)) < cutoff]
            for key in expired:
                self._sessions.pop(key, None)
            return len(expired)


class VoiceSessionMapping(MutableMapping[str, Session]):
    """Compatibility mapping backed by :class:`VoiceSessionStore`."""

    def __init__(self, store: VoiceSessionStore) -> None:
        self._store = store

    def __getitem__(self, key: str) -> Session:
        value = self._store.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def __setitem__(self, key: str, value: Session) -> None:
        self._store.replace(key, value)

    def __delitem__(self, key: str) -> None:
        if self._store.pop(key) is None:
            raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._store.keys())

    def __len__(self) -> int:
        return len(self._store.keys())

    def clear(self) -> None:
        self._store.cancel()
