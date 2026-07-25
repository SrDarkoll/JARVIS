"""Small profile-scoped state for media follow-up routing."""

from __future__ import annotations

import threading

_ALLOWED_MEDIA_SOURCES = frozenset({"spotify", "youtube"})
_LAST_MEDIA_SOURCE_BY_PROFILE: dict[str, str] = {}
_MEDIA_STATE_LOCK = threading.RLock()


def _profile_key(profile_id: str | None) -> str:
    return str(profile_id or "admin").strip().lower() or "admin"


def set_last_media_source(profile_id: str | None, source: str) -> None:
    normalized_source = str(source or "").strip().lower()
    key = _profile_key(profile_id)
    with _MEDIA_STATE_LOCK:
        if normalized_source in _ALLOWED_MEDIA_SOURCES:
            _LAST_MEDIA_SOURCE_BY_PROFILE[key] = normalized_source
        else:
            _LAST_MEDIA_SOURCE_BY_PROFILE.pop(key, None)


def get_last_media_source(profile_id: str | None) -> str:
    with _MEDIA_STATE_LOCK:
        return _LAST_MEDIA_SOURCE_BY_PROFILE.get(
            _profile_key(profile_id),
            "",
        )


def clear_media_state(profile_id: str | None = None) -> None:
    with _MEDIA_STATE_LOCK:
        if profile_id is None:
            _LAST_MEDIA_SOURCE_BY_PROFILE.clear()
        else:
            _LAST_MEDIA_SOURCE_BY_PROFILE.pop(
                _profile_key(profile_id),
                None,
            )
