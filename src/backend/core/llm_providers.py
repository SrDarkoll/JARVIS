"""Provider-neutral LLM configuration resolution."""

from __future__ import annotations

import ipaddress
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from core.runtime_logger import log_warning

JARVIS_USER_AGENT = "JARVIS/0.1.0"
SUPPORTED_PROVIDERS = ("deepseek", "gemini", "groq", "ollama")
OLLAMA_PLACEHOLDER_KEY = "ollama"

_DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
_DEFAULT_GROQ_MODEL = "qwen/qwen3.6-27b"
_DEFAULT_DEEPSEEK_MODEL = "deepseek-v4.1-flash"
_DEFAULT_DEEPSEEK_BASE_URL = "https://opencode.ai/inference/go/openai/v1"
_DEFAULT_DEEPSEEK_TIMEOUT = 20.0
_DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
_DEFAULT_SLOT_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class ProviderSlot:
    provider: str
    model: str
    api_key: str = field(repr=False)
    base_url: str
    timeout: float


@dataclass(frozen=True)
class LLMProviderConfig:
    primary_provider: str
    primary_model: str
    primary_api_key: str = field(repr=False)
    fallback_provider: str = ""
    fallback_model: str = ""
    fallback_api_key: str = field(default="", repr=False)
    chain: tuple[ProviderSlot, ...] = ()

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
    if provider == "gemini":
        configured = configured.removeprefix("models/")
    normalized = configured.lower()
    if provider == "groq" and normalized.startswith("gemini-"):
        return default
    if provider == "gemini" and normalized.startswith(
        ("groq/", "llama-", "meta-llama/", "openai/", "qwen/")
    ):
        return default
    return configured


def resolve_gemini_vision_model(env: Mapping[str, str] | None = None) -> str:
    """Use the selected Gemini text model unless vision explicitly overrides it."""
    source = os.environ if env is None else env
    text_model = _provider_model(source, "JARVIS_GEMINI_MODEL", provider="gemini", default=_DEFAULT_GEMINI_MODEL)
    return _provider_model(source, "JARVIS_GEMINI_VISION_MODEL", provider="gemini", default=text_model)


def _validated_base_url(raw: str, *, allow_private_http: bool) -> str:
    """Return the normalized URL, or "" when it is not an acceptable endpoint."""
    url = str(raw or "").strip()
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
    except ValueError:
        return ""
    if not host:
        return ""
    if parsed.scheme == "https":
        return url
    if parsed.scheme != "http" or not allow_private_http:
        return ""
    if host.lower() == "localhost":
        return url
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return ""
    return url if address.is_loopback or address.is_private else ""


def _deepseek_timeout(source: Mapping[str, str]) -> float:
    try:
        value = float(str(source.get("JARVIS_DEEPSEEK_TIMEOUT") or "").strip())
    except ValueError:
        return _DEFAULT_DEEPSEEK_TIMEOUT
    if math.isnan(value):
        return _DEFAULT_DEEPSEEK_TIMEOUT
    return min(max(value, 1.0), 120.0)


def _configured_base_url(source: Mapping[str, str], variable: str, default: str, *, provider: str,
                         allow_private_http: bool) -> str:
    """Validated base URL; a rejected non-empty value is logged (provider only, never the URL)."""
    raw = str(source.get(variable) or default).strip()
    url = _validated_base_url(raw, allow_private_http=allow_private_http)
    if raw and not url:
        log_warning("llm_provider_url_rejected", provider=provider)
    return url


def uses_opencode_session(slot: ProviderSlot) -> bool:
    """True when the slot's requests carry ``x-opencode-session`` (DeepSeek on opencode.ai)."""
    if slot.provider != "deepseek":
        return False
    host = (urlsplit(slot.base_url).hostname or "").lower()
    return host == "opencode.ai" or host.endswith(".opencode.ai")


def provider_headers(slot: ProviderSlot, *, session_id: str) -> dict[str, str]:
    """Extra HTTP headers for a slot; the session id only goes to opencode.ai."""
    if slot.provider != "deepseek":
        return {}
    headers = {"User-Agent": JARVIS_USER_AGENT}
    if session_id and uses_opencode_session(slot):
        headers["x-opencode-session"] = session_id
    return headers


def resolve_llm_provider_config(
    env: Mapping[str, str] | None = None,
) -> LLMProviderConfig:
    """Resolve the ordered provider chain (and primary/fallback views) from one env view."""
    source = os.environ if env is None else env
    requested = str(source.get("JARVIS_LLM_PROVIDER") or "").strip().lower()
    if requested == "google":
        requested = "gemini"
    if requested not in SUPPORTED_PROVIDERS:
        requested = ""

    slots: list[ProviderSlot] = []

    deepseek_key = str(source.get("DEEPSEEK_API_KEY") or "").strip()
    # The URL only matters (and is only reported) when DeepSeek has a key.
    deepseek_url = deepseek_key and _configured_base_url(
        source, "JARVIS_DEEPSEEK_BASE_URL", _DEFAULT_DEEPSEEK_BASE_URL,
        provider="deepseek", allow_private_http=False,
    )
    if deepseek_key and deepseek_url:
        slots.append(
            ProviderSlot(
                "deepseek",
                _provider_model(
                    source, "JARVIS_DEEPSEEK_MODEL", provider="deepseek", default=_DEFAULT_DEEPSEEK_MODEL
                ),
                deepseek_key,
                deepseek_url,
                _deepseek_timeout(source),
            )
        )

    gemini_key = resolve_gemini_api_key(source)
    if gemini_key:
        slots.append(
            ProviderSlot(
                "gemini",
                _provider_model(
                    source, "JARVIS_GEMINI_MODEL", provider="gemini", default=_DEFAULT_GEMINI_MODEL
                ),
                gemini_key,
                provider_base_url("gemini"),
                _DEFAULT_SLOT_TIMEOUT,
            )
        )

    groq_key = resolve_groq_api_key(source)
    if groq_key:
        slots.append(
            ProviderSlot(
                "groq",
                _provider_model(source, "JARVIS_GROQ_MODEL", provider="groq", default=_DEFAULT_GROQ_MODEL),
                groq_key,
                provider_base_url("groq"),
                _DEFAULT_SLOT_TIMEOUT,
            )
        )

    ollama_url = _configured_base_url(
        source, "JARVIS_OLLAMA_BASE_URL", "", provider="ollama", allow_private_http=True
    )
    if ollama_url:
        slots.append(
            ProviderSlot(
                "ollama",
                _provider_model(
                    source, "JARVIS_OLLAMA_MODEL", provider="ollama", default=_DEFAULT_OLLAMA_MODEL
                ),
                OLLAMA_PLACEHOLDER_KEY,
                ollama_url,
                _DEFAULT_SLOT_TIMEOUT,
            )
        )

    if requested:
        slots.sort(key=lambda slot: slot.provider != requested)  # stable: only moves the match

    primary = slots[0] if slots else None
    fallback = slots[1] if len(slots) > 1 else None
    return LLMProviderConfig(
        primary_provider=primary.provider if primary else "",
        primary_model=primary.model if primary else "",
        primary_api_key=primary.api_key if primary else "",
        fallback_provider=fallback.provider if fallback else "",
        fallback_model=fallback.model if fallback else "",
        fallback_api_key=fallback.api_key if fallback else "",
        chain=tuple(slots),
    )


def resolve_gemini_api_key(env: Mapping[str, str] | None = None) -> str:
    """Resolve Gemini API key independently of provider selection."""
    source = os.environ if env is None else env
    return str(source.get("GEMINI_API_KEY") or source.get("GOOGLE_API_KEY") or "").strip()


def resolve_groq_api_key(env: Mapping[str, str] | None = None) -> str:
    """Resolve Groq API key independently of provider selection."""
    source = os.environ if env is None else env
    return str(source.get("GROQ_API_KEY") or "").strip()


def provider_base_url(provider: str) -> str:
    if provider == "gemini":
        return "https://generativelanguage.googleapis.com/v1beta/openai/"
    if provider == "groq":
        return "https://api.groq.com/openai/v1"
    raise ValueError("unsupported_llm_provider")
