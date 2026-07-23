"""Thread-safe process state shared by Spotify providers."""

import threading

_lock = threading.RLock()
_last_requested_track = ""


def set_last_requested_track(value: str) -> None:
    global _last_requested_track
    with _lock:
        _last_requested_track = str(value or "").strip()


def get_last_requested_track() -> str:
    with _lock:
        return _last_requested_track
