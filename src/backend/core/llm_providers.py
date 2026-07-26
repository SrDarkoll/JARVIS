"""Provider-neutral LLM configuration resolution."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

_DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
_DEFAULT_GROQ_MODEL = "qwen/qwen3.6-27b"


@dataclass(frozen=True)
class LLMProviderConfig:
    primary_provider: str
    primary_model: str
    primary_api_key: str = field(repr=False)
    fallback_provider: str = ""
    fallback_model: str = ""
    fallback_api_key: str = field(default="", repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.primary_provider and self.primary_api_key)


def _provider_model(
    source: Mapping[str, str],
    variable: str,
    *,
    provider: str,
    default: str,
) -> str:
    configured = str(source.get(variable) or default).strip() or default
    normalized = configured.lower()
    if provider == "groq" and normalized.startswith("gemini-"):
        return default
    if provider == "gemini" and normalized.startswith(
        ("groq/", "llama-", "meta-llama/", "openai/", "qwen/")
    ):
        return default
    return configured


def resolve_llm_provider_config(
    env: Mapping[str, str] | None = None,
) -> LLMProviderConfig:
    """Resolve the usable primary and fallback providers from one env view."""
    source = os.environ if env is None else env
    requested = str(source.get("JARVIS_LLM_PROVIDER") or "").strip().lower()
    if requested == "google":
        requested = "gemini"
    if requested not in {"", "gemini", "groq"}:
        requested = ""

    gemini_key = str(source.get("GEMINI_API_KEY") or source.get("GOOGLE_API_KEY") or "").strip()
    groq_key = str(source.get("GROQ_API_KEY") or "").strip()
    gemini_model = _provider_model(
        source,
        "JARVIS_GEMINI_MODEL",
        provider="gemini",
        default=_DEFAULT_GEMINI_MODEL,
    )
    groq_model = _provider_model(
        source,
        "JARVIS_GROQ_MODEL",
        provider="groq",
        default=_DEFAULT_GROQ_MODEL,
    )

    if requested == "groq" and groq_key:
        primary = ("groq", groq_model, groq_key)
        fallback = ("gemini", gemini_model, gemini_key) if gemini_key else ("", "", "")
    elif requested == "gemini" and gemini_key:
        primary = ("gemini", gemini_model, gemini_key)
        fallback = ("groq", groq_model, groq_key) if groq_key else ("", "", "")
    elif gemini_key:
        primary = ("gemini", gemini_model, gemini_key)
        fallback = ("groq", groq_model, groq_key) if groq_key else ("", "", "")
    elif groq_key:
        primary = ("groq", groq_model, groq_key)
        fallback = ("", "", "")
    else:
        primary = ("", "", "")
        fallback = ("", "", "")

    return LLMProviderConfig(
        primary_provider=primary[0],
        primary_model=primary[1],
        primary_api_key=primary[2],
        fallback_provider=fallback[0],
        fallback_model=fallback[1],
        fallback_api_key=fallback[2],
    )


def provider_base_url(provider: str) -> str:
    if provider == "gemini":
        return "https://generativelanguage.googleapis.com/v1beta/openai/"
    if provider == "groq":
        return "https://api.groq.com/openai/v1"
    raise ValueError("unsupported_llm_provider")
