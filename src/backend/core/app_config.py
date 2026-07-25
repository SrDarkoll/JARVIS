"""Centralized application configuration with validation.

This module provides a single runtime config object that merges settings from
`jarvis_settings.py` and environment-backed values from `core.jarvis_config`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from threading import RLock
from typing import Any

from utils.jarvis_i18n import LANGUAGE_CONFIG

from core import jarvis_config


class AppConfigValidationError(ValueError):
    """Raised when runtime app configuration is invalid."""


@dataclass(frozen=True)
class LocalizationConfig:
    language: str
    locale: str
    location: str


@dataclass(frozen=True)
class FeatureTogglesConfig:
    autocuracion_activa: bool
    proactive_activo: bool
    proactive_cooldown: int
    strict_web_search: bool


@dataclass(frozen=True)
class ProviderConfig:
    groq_api_key: str
    newsapi_key: str
    telegram_token: str
    telegram_chat_id: int
    google_api_key: str
    google_cse_id: str
    brave_api_key: str
    tavily_api_key: str
    youtube_api_key: str
    spotipy_client_id: str
    spotipy_client_secret: str


@dataclass(frozen=True)
class AppConfig:
    localization: LocalizationConfig
    toggles: FeatureTogglesConfig
    providers: ProviderConfig
    cors_origins: tuple[str, ...]
    validation_warnings: tuple[str, ...] = ()


_APP_CONFIG: AppConfig | None = None
_APP_CONFIG_LOCK = RLock()


def _normalize_language(raw: Any, warnings: list[str]) -> str:
    lang = str(raw or "en").strip().lower()
    if lang not in LANGUAGE_CONFIG:
        warnings.append(f"Unsupported language '{lang}', fallback to 'en'.")
        return "en"
    return lang


def _normalize_locale(raw: Any, language: str, warnings: list[str]) -> str:
    locale = str(raw or "").strip()
    if not locale:
        return LANGUAGE_CONFIG[language]["locale"]
    if len(locale) < 4 or "-" not in locale:
        warnings.append(f"Invalid locale '{locale}', using '{LANGUAGE_CONFIG[language]['locale']}'.")
        return LANGUAGE_CONFIG[language]["locale"]
    return locale


def _normalize_location(raw: Any, warnings: list[str]) -> str:
    location = str(raw or "").strip()
    if not location:
        warnings.append("Empty LOCATION, fallback to 'Madrid'.")
        return "Madrid"
    return location


def _normalize_cooldown(raw: Any, warnings: list[str]) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        warnings.append(f"Invalid proactive cooldown '{raw}', fallback to 600.")
        value = 600
    return max(120, min(value, 3600))


def _normalize_int(raw: Any, fallback: int, warnings: list[str], field: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        warnings.append(f"Invalid {field} '{raw}', fallback to {fallback}.")
        return int(fallback)


def build_app_config(jarvis_settings_module: Any | None = None) -> AppConfig:
    """Build and validate runtime app config from settings + env config."""
    warnings: list[str] = []

    language = _normalize_language(
        getattr(jarvis_settings_module, "LANGUAGE", "en") if jarvis_settings_module else "en",
        warnings,
    )
    locale = _normalize_locale(
        getattr(jarvis_settings_module, "LOCALE", "") if jarvis_settings_module else "",
        language,
        warnings,
    )
    location = _normalize_location(
        getattr(jarvis_settings_module, "LOCATION", "Madrid") if jarvis_settings_module else "Madrid",
        warnings,
    )

    localization = LocalizationConfig(language=language, locale=locale, location=location)
    toggles = FeatureTogglesConfig(
        autocuracion_activa=bool(jarvis_config.AUTOCURACION_ACTIVA),
        proactive_activo=bool(jarvis_config.PROACTIVE_ACTIVO),
        proactive_cooldown=_normalize_cooldown(jarvis_config.PROACTIVE_COOLDOWN, warnings),
        strict_web_search=bool(jarvis_config.STRICT_WEB_SEARCH),
    )
    providers = ProviderConfig(
        groq_api_key=str(jarvis_config.GROQ_API_KEY or ""),
        newsapi_key=str(jarvis_config.NEWSAPI_KEY or ""),
        telegram_token=str(jarvis_config.TELEGRAM_TOKEN or ""),
        telegram_chat_id=_normalize_int(
            jarvis_config.TELEGRAM_CHAT_ID,
            0,
            warnings,
            "TELEGRAM_CHAT_ID",
        ),
        google_api_key=str(jarvis_config.GOOGLE_API_KEY or ""),
        google_cse_id=str(jarvis_config.GOOGLE_CSE_ID or ""),
        brave_api_key=str(jarvis_config.BRAVE_API_KEY or ""),
        tavily_api_key=str(jarvis_config.TAVILY_API_KEY or ""),
        youtube_api_key=str(jarvis_config.YOUTUBE_API_KEY or ""),
        spotipy_client_id=str(jarvis_config.SPOTIPY_CLIENT_ID or ""),
        spotipy_client_secret=str(jarvis_config.SPOTIPY_CLIENT_SECRET or ""),
    )

    return AppConfig(
        localization=localization,
        toggles=toggles,
        providers=providers,
        cors_origins=tuple(jarvis_config.get_cors_origins()),
        validation_warnings=tuple(warnings),
    )


def init_app_config(jarvis_settings_module: Any | None = None) -> AppConfig:
    """Build and set global app config, syncing normalized values back to settings."""
    cfg = build_app_config(jarvis_settings_module)
    with _APP_CONFIG_LOCK:
        global _APP_CONFIG
        _APP_CONFIG = cfg

    if jarvis_settings_module:
        jarvis_settings_module.LANGUAGE = cfg.localization.language
        jarvis_settings_module.LOCALE = cfg.localization.locale
        jarvis_settings_module.LOCATION = cfg.localization.location

    return cfg


def get_app_config() -> AppConfig:
    """Get global app config; lazy-builds a default when needed."""
    with _APP_CONFIG_LOCK:
        global _APP_CONFIG
        if _APP_CONFIG is None:
            _APP_CONFIG = build_app_config(None)
        return _APP_CONFIG


def get_default_location() -> str:
    """Current default location configured for weather/context."""
    return get_app_config().localization.location


def get_active_language() -> str:
    """Current runtime language code."""
    return get_app_config().localization.language


def set_active_language(language: str) -> AppConfig:
    """Update only active language/locale in global app config."""
    normalized = str(language or "").strip().lower()
    if normalized not in LANGUAGE_CONFIG:
        raise AppConfigValidationError(f"Unsupported language: {language}")

    with _APP_CONFIG_LOCK:
        current = get_app_config()
        localization = replace(
            current.localization,
            language=normalized,
            locale=LANGUAGE_CONFIG[normalized]["locale"],
        )
        updated = replace(current, localization=localization)
        global _APP_CONFIG
        _APP_CONFIG = updated
        return updated
