"""Initial setup status for the desktop assistant."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

from core.capabilities import CapabilityState


def _env_has(env: Mapping[str, str], *names: str) -> bool:
    return all(str(env.get(name) or "").strip() for name in names)


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = str(env.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _setup_item(
    *,
    configured: bool,
    optional: bool,
    label: str,
    code: str,
    action: str,
    enabled: bool = True,
    **extra,
) -> dict:
    if not enabled:
        state = CapabilityState.DISABLED
    elif configured:
        state = CapabilityState.AVAILABLE
    else:
        state = CapabilityState.UNCONFIGURED
    return {
        "configured": bool(configured),
        "optional": bool(optional),
        "label": label,
        "state": state.value,
        "code": code,
        "action": action,
        **extra,
    }


def build_setup_status(
    *,
    env: Mapping[str, str] | None = None,
    language: str = "",
    admin_voice_profiles: int = 0,
    weather_location: str = "",
    platform_name: str | None = None,
) -> dict:
    """Return setup readiness without mutating runtime state."""
    source = os.environ if env is None else env
    runtime_platform = sys.platform if platform_name is None else platform_name
    core_mode = _env_bool(source, "JARVIS_CORE_MODE", True)
    voice_id_enabled = _env_bool(
        source,
        "JARVIS_VOICE_ID_ENABLED",
        not core_mode,
    )
    telegram_enabled = _env_bool(
        source,
        "JARVIS_TELEGRAM_ENABLED",
        not core_mode,
    )
    spotify_mode = str(source.get("SPOTIFY_PLAYBACK_MODE") or "auto").strip().lower()
    if spotify_mode not in {"auto", "api", "desktop"}:
        spotify_mode = "auto"
    spotify_api_configured = _env_has(
        source, "SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET"
    ) or _env_has(source, "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET")
    groq_configured = _env_has(source, "GROQ_API_KEY")
    language_configured = bool(str(language or "").strip())
    admin_voice_configured = int(admin_voice_profiles or 0) > 0
    telegram_configured = _env_has(
        source,
        "TELEGRAM_TOKEN",
        "TELEGRAM_CHAT_ID",
    )
    weather_configured = bool(str(weather_location or "").strip())
    api_token_configured = _env_has(source, "JARVIS_API_TOKEN")
    spotify_desktop_available = runtime_platform == "win32"
    if spotify_mode == "api":
        spotify_configured = spotify_api_configured
    elif spotify_mode == "desktop":
        spotify_configured = spotify_desktop_available
    else:
        spotify_configured = spotify_api_configured or spotify_desktop_available
    items = {
        "llm": _setup_item(
            configured=groq_configured,
            optional=False,
            label="Groq",
            code=(
                "groq_configured"
                if groq_configured
                else "groq_key_missing"
            ),
            action="Configure GROQ_API_KEY",
        ),
        "language": _setup_item(
            configured=language_configured,
            optional=False,
            label="Language",
            code=(
                "language_configured"
                if language_configured
                else "language_missing"
            ),
            action="Select a language",
        ),
        "admin_voice": _setup_item(
            configured=admin_voice_configured,
            optional=not voice_id_enabled,
            label="Admin voice enrollment",
            code=(
                "voice_id_disabled"
                if not voice_id_enabled
                else (
                    "admin_voice_configured"
                    if admin_voice_configured
                    else "admin_voice_missing"
                )
            ),
            action="Register the administrator voice",
            enabled=voice_id_enabled,
        ),
        "spotify": _setup_item(
            configured=spotify_configured,
            optional=True,
            label="Spotify",
            code=(
                "spotify_configured"
                if spotify_configured
                else "spotify_unconfigured"
            ),
            action="Configure Spotify or use Windows desktop playback",
            mode=spotify_mode,
            desktop_available=spotify_desktop_available,
            api_configured=spotify_api_configured,
        ),
        "telegram": _setup_item(
            configured=telegram_configured,
            optional=True,
            label="Telegram",
            code=(
                "telegram_disabled"
                if not telegram_enabled
                else (
                    "telegram_configured"
                    if telegram_configured
                    else "telegram_unconfigured"
                )
            ),
            action="Configure TELEGRAM_TOKEN and TELEGRAM_CHAT_ID",
            enabled=telegram_enabled,
        ),
        "weather_location": _setup_item(
            configured=weather_configured,
            optional=False,
            label="Weather location",
            code=(
                "weather_location_configured"
                if weather_configured
                else "weather_location_missing"
            ),
            action="Configure the default weather location",
        ),
        "api_token": _setup_item(
            configured=api_token_configured,
            optional=True,
            label="API token",
            code=(
                "api_token_configured"
                if api_token_configured
                else "api_token_missing"
            ),
            action="Configure JARVIS_API_TOKEN for remote access",
            recommended_for_remote_access=True,
        ),
    }
    required_complete = all(
        item["configured"] for item in items.values() if not item.get("optional")
    )
    return {"complete": required_complete, "items": items}
