"""Initial setup status for the desktop assistant."""

from __future__ import annotations

import os
from typing import Mapping


def _env_has(env: Mapping[str, str], *names: str) -> bool:
    return all(str(env.get(name) or "").strip() for name in names)


def build_setup_status(
    *,
    env: Mapping[str, str] | None = None,
    language: str = "",
    admin_voice_profiles: int = 0,
    weather_location: str = "",
) -> dict:
    """Return setup readiness without mutating runtime state."""
    source = env or os.environ
    items = {
        "language": {
            "configured": bool(str(language or "").strip()),
            "optional": False,
            "label": "Language",
        },
        "admin_voice": {
            "configured": int(admin_voice_profiles or 0) > 0,
            "optional": False,
            "label": "Admin voice enrollment",
        },
        "spotify": {
            "configured": _env_has(source, "SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET")
            or _env_has(source, "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"),
            "optional": True,
            "label": "Spotify",
        },
        "telegram": {
            "configured": _env_has(source, "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"),
            "optional": True,
            "label": "Telegram",
        },
        "weather_location": {
            "configured": bool(str(weather_location or "").strip()),
            "optional": False,
            "label": "Weather location",
        },
        "api_token": {
            "configured": _env_has(source, "JARVIS_API_TOKEN"),
            "optional": False,
            "label": "API token",
        },
    }
    required_complete = all(
        item["configured"] for item in items.values() if not item.get("optional")
    )
    return {"complete": required_complete, "items": items}
