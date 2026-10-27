"""Affirmative operation checks for non-idempotent Spotify mutations."""

from __future__ import annotations

import re
import unicodedata

SPOTIFY_MUTATION_TOOLS = frozenset({"agregar_a_cola_spotify", "dar_like_spotify", "quitar_like_spotify"})
_PREFIX = (
    r"(?:jarvis[,\s]+)?(?:(?:por favor|please)[,\s]+)?"
    r"(?:(?:puedes|podrias|quiero que|can you|could you|would you)\s+)?"
)
_PATTERNS = {
    "agregar_a_cola_spotify": (
        r"(?:anade|agrega|mete|pon|ponme|encola|add)\b.+\b(?:a|en|to)\s+(?:(?:la|the|mi|my)\s+)?(?:cola|queue)\b",
        r"(?:queue|enqueue)\s+\S",
    ),
    "dar_like_spotify": (
        r"(?:dale|da|ponle|pon)\s+(?:un\s+)?(?:like|me gusta)\b",
        r"(?:guarda|guardala|guardalo|agrega|anade|save|add)\b.+\b(?:en|a|to)\s+"
        r"(?:(?:mis|mi|tus|los|las|my|the)\s+)?(?:favoritos|me gusta|liked songs|favorites)\b",
        r"like\s+(?:this|the current|that)\s+(?:song|track)\b",
    ),
    "quitar_like_spotify": (
        r"(?:quita|quitale|elimina|saca|remove|delete)\b.+\b(?:de|from)\s+"
        r"(?:(?:mis|mi|tus|los|las|my|the)\s+)?(?:favoritos|me gusta|liked songs|favorites)\b",
        r"unlike\s+\S",
    ),
}


def requested_mutation(tool_name: str, text: str) -> bool:
    """A play request, mention, quotation or negation is not queue/library consent."""
    normalized = "".join(char for char in unicodedata.normalize("NFKD", str(text or "").lower())
                         if not unicodedata.combining(char))
    return any(re.match(rf"^\s*{_PREFIX}{pattern}", normalized) for pattern in _PATTERNS.get(tool_name, ()))


def mutation_tool_for_control(action: str) -> str:
    from modules.spotify.service import _spotify_desktop_control_action

    canonical = _spotify_desktop_control_action(action)
    if not canonical:
        return ""
    return {"like": "dar_like_spotify", "unlike": "quitar_like_spotify"}.get(canonical, "")
