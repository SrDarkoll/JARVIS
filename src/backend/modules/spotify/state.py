"""Thread-safe process state shared by Spotify providers."""

import threading

_lock = threading.RLock()
_last_requested_tracks: dict[str, str] = {}


def _profile_key(profile_id: str | None = None) -> str:
    value = profile_id
    if value is None:
        try:
            from core import jarvis_state

            value = jarvis_state.get_active_profile_id()
        except Exception:
            value = "admin"
    return str(value or "admin").strip().lower() or "admin"


def set_last_requested_track(value: str, profile_id: str | None = None) -> None:
    key = _profile_key(profile_id)
    clean_value = str(value or "").strip()
    with _lock:
        if clean_value:
            _last_requested_tracks[key] = clean_value
        else:
            _last_requested_tracks.pop(key, None)


def get_last_requested_track(profile_id: str | None = None) -> str:
    key = _profile_key(profile_id)
    with _lock:
        return _last_requested_tracks.get(key, "")
