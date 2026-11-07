"""Affirmative operation checks for non-idempotent Spotify mutations."""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from collections.abc import Mapping
from difflib import SequenceMatcher
from typing import Any

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


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text or "").lower())
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9']+", " ", ascii_text)).strip()


# ---------------------------------------------------------------------------
# Grounded play intent
#
# The LLM decides *that* the user asked for music and *what* (it can fix STT
# errors with its own knowledge). This guard only verifies that the decision is
# anchored in the user's words, so a hallucinated call, a negation, or song
# lyrics captured from the speakers cannot start playback.
# ---------------------------------------------------------------------------

MUSIC_FOLLOWUP_SECONDS = 180.0

_PLAY_VERB_RE = re.compile(
    r"\b(?:pon|ponme|ponla|ponlo|pones|ponga|pongas|poner|reproduce|reproduceme|reproducir|"
    r"reproduzcas|toca|tocame|tocar|play|escuchar|escuchemos|oir|put on)\b"
)
_NEGATION_RE = re.compile(
    r"\b(?:no|nunca|never|dont|don't|do not)\b(?:\s+\S+){0,3}?\s+"
    r"(?:pon|pongas|ponga|poner|reproduzcas|reproduce|reproducir|toques|toca|play|escuchar)\b"
)
_STOPWORDS = frozenset(
    "a al de del el en la las los lo un una y the of and by feat ft mi me tu to on in".split()
)
_GENERIC_TITLES = frozenset(
    {"musica", "music", "cancion", "una cancion", "la cancion", "song", "a song", "algo", "something",
     "nada", "nothing", "una", "algo de musica"}
)
_NON_MUSIC_TITLE_RE = re.compile(
    r"^(?:(?:un|una|el|la|los|las|the|a|an)\s+)?"
    r"(?:ejemplos?|atencion|attention|error|bug|codigo|code|ajustes|settings|recordatorios?|"
    r"alarma|reminder|chess|ajedrez|juego|game|programa)\b"
)
_EXPLICIT_SONG_RE = re.compile(r"\b(?:cancion|song|tema|track)\b")

_attempts_lock = threading.Lock()
_recent_attempts: dict[str, tuple[float, str, str]] = {}


def remember_music_attempt(profile_id: str, title: str, artist: str, *, succeeded: bool) -> None:
    """Open (or close) the follow-up window used to accept corrections."""
    key = str(profile_id or "").strip().lower()
    with _attempts_lock:
        if succeeded:
            _recent_attempts.pop(key, None)
        else:
            _recent_attempts[key] = (time.monotonic(), str(title or ""), str(artist or ""))


def clear_music_attempts() -> None:
    with _attempts_lock:
        _recent_attempts.clear()


def _recent_failed_attempt(profile_id: str) -> tuple[str, str] | None:
    key = str(profile_id or "").strip().lower()
    with _attempts_lock:
        entry = _recent_attempts.get(key)
        if entry is None:
            return None
        started, title, artist = entry
        if time.monotonic() - started > MUSIC_FOLLOWUP_SECONDS:
            _recent_attempts.pop(key, None)
            return None
        return title, artist


def _significant_tokens(text: str) -> list[str]:
    return [token for token in _normalize(text).split() if token not in _STOPWORDS and len(token) >= 2]


def _token_present(token: str, spoken: list[str]) -> bool:
    for candidate in spoken:
        if candidate == token:
            return True
        if len(token) >= 4 and len(candidate) >= 4 and SequenceMatcher(None, token, candidate).ratio() >= 0.8:
            return True
    return False


def _grounded(value: str, spoken: list[str]) -> bool:
    """At least half of the value's significant words appear (fuzzily) in the utterance."""
    tokens = _significant_tokens(value)
    if not tokens:
        return False
    hits = sum(1 for token in tokens if _token_present(token, spoken))
    return hits / len(tokens) >= 0.5


def music_request_grounded(user_text: str, arguments: Mapping[str, Any] | None, *, profile_id: str) -> bool:
    """Whether a proposed play call is anchored in what the user actually said."""
    from core.brain.router import _extract_music_request

    if _extract_music_request(user_text):
        return True

    spoken_text = _normalize(user_text)
    if not spoken_text or _NEGATION_RE.search(spoken_text):
        return False

    args = dict(arguments or {})
    title = str(args.get("cancion") or args.get("semilla") or "")
    artist = str(args.get("artista") or "")
    normalized_title = _normalize(title)
    if not normalized_title or normalized_title in _GENERIC_TITLES:
        return False
    if _NON_MUSIC_TITLE_RE.match(normalized_title) and not _EXPLICIT_SONG_RE.search(spoken_text):
        return False

    spoken = spoken_text.split()
    if _PLAY_VERB_RE.search(spoken_text) and _grounded(title, spoken):
        return True

    previous = _recent_failed_attempt(profile_id)
    if previous is None:
        return False
    previous_title, _previous_artist = previous
    return _grounded(title, spoken) or _grounded(artist, spoken) or (
        bool(previous_title) and _grounded(previous_title, spoken)
    )


def mutation_tool_for_control(action: str) -> str:
    from modules.spotify.service import _spotify_desktop_control_action

    canonical = _spotify_desktop_control_action(action)
    if not canonical:
        return ""
    return {"like": "dar_like_spotify", "unlike": "quitar_like_spotify"}.get(canonical, "")
