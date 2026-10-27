"""Bounded, profile-scoped context construction for Gemini Live."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

_SECRET_PATTERN = re.compile(r"\b(?:AIza|gsk_)[A-Za-z0-9_-]{6,}\b")
_CREDENTIAL_QUERY_PATTERN = re.compile(
    r"(?i)(?P<name>key|api_key|token|access_token)=(?P<value>[^&\s]+)"
)
_WINDOWS_PATH_PATTERN = re.compile(r"(?i)\b[A-Z]:\\[^\r\n\t ]+")
_HIDDEN_PREFIXES = ("thought:", "reasoning:")


@dataclass(frozen=True, slots=True)
class LiveContextPolicy:
    max_chars: int = 6000
    recent_turns: int = 6

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_chars", max(1, int(self.max_chars)))
        object.__setattr__(self, "recent_turns", max(0, int(self.recent_turns)))


def _safe_text(value: object) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split()).strip()
    if text.lower().startswith(_HIDDEN_PREFIXES):
        return ""
    text = _CREDENTIAL_QUERY_PATTERN.sub(
        lambda match: f"{match.group('name')}=[redacted]",
        text,
    )
    text = _SECRET_PATTERN.sub("[redacted]", text)
    return _WINDOWS_PATH_PATTERN.sub("[local-path]", text)


def _message_line(message: object) -> str:
    role = _safe_text(
        getattr(message, "role", getattr(message, "type", "message"))
    )
    content = _safe_text(getattr(message, "content", ""))
    return f"{role}: {content}" if content else ""


def _language_directive(language: str) -> str:
    default = (
        "RESPOND UNMISTAKABLY IN ENGLISH."
        if str(language or "").strip().lower().startswith("en")
        else "RESPONDE INEQUÍVOCAMENTE EN ESPAÑOL."
    )
    return (
        f"Idioma conversacional persistente / persistent conversation language: {default} "
        "Mantén este idioma hasta que el usuario pida explícitamente responder en otro. "
        "Los nombres, títulos musicales, citas, resultados y transcripciones accidentales en otro idioma "
        "no son solicitudes de cambio de idioma. Never auto-switch based on detected speech language."
    )


def resolve_live_language(text: str, default: str = "es") -> str:
    """Keep the preference unless a direct, affirmative language request changes it."""
    fallback = "en" if str(default).strip().lower().startswith("en") else "es"
    normalized = "".join(char for char in unicodedata.normalize("NFKD", str(text or "").lower())
                         if not unicodedata.combining(char))
    prefix = r"(?:jarvis[,\s]+)?(?:(?:por favor|please)[,\s]+)?"
    request = re.match(
        rf"^\s*{prefix}(?:(?:quiero que|puedes|podrias|can you|could you)\s+)?"
        r"(?:me\s+)?(?:respond\w*|contest\w*|habl\w*|answer|speak|talk|reply)\s+"
        r"(?:(?:me|to me)\s+)?(?:en|in)\s+(espanol|ingles|spanish|english)\b", normalized,
    )
    if not request:
        return fallback
    return "en" if request.group(1) in {"ingles", "english"} else "es"


def build_live_context(
    *,
    base_instruction: str,
    language: str,
    profile_name: str,
    profile_facts: str,
    history: Sequence[object] | Iterable[object],
    allowed_tool_names: Sequence[str],
    policy: LiveContextPolicy,
) -> str:
    """Return a deterministic, bounded prompt for one active profile."""

    safe_tools = sorted(
        {
            safe_name
            for name in allowed_tool_names
            if (safe_name := _safe_text(name))
        }
    )
    header_lines = [
        _language_directive(language),
        _safe_text(base_instruction),
        f"Active profile: {_safe_text(profile_name)}",
        f"Relevant profile facts: {_safe_text(profile_facts)}",
        "Allowed tools: " + ", ".join(safe_tools),
    ]
    header = "\n".join(line for line in header_lines if line).strip()
    if len(header) >= policy.max_chars:
        return header[: policy.max_chars]

    remaining = policy.max_chars - len(header) - 1
    selected_reversed: list[str] = []
    recent = list(history)[-policy.recent_turns :] if policy.recent_turns else []
    for message in reversed(recent):
        line = _message_line(message)
        if not line:
            continue
        required = len(line) + (1 if selected_reversed else 0)
        if required > remaining:
            if not selected_reversed and remaining > 0:
                selected_reversed.append(line[:remaining])
            break
        selected_reversed.append(line)
        remaining -= required

    history_lines = list(reversed(selected_reversed))
    return "\n".join([header, *history_lines]).strip()[: policy.max_chars]


__all__ = ["LiveContextPolicy", "build_live_context"]
