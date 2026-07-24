"""Provider-neutral Spotify orchestration and fallback policy."""

from __future__ import annotations

import re
import threading

from core import jarvis_config, jarvis_state
from modules.spotify import config
from modules.spotify.api.client import _spotify_has_valid_cached_token
from modules.spotify.api.playback import _spotify_control_api, _spotify_play_api
from modules.spotify.desktop import (
    DesktopResultStatus,
    SpotifyDesktopResult,
    SpotifyRequest,
    build_windows_controller,
)
from modules.spotify.desktop.matching import normalize_text
from modules.spotify.followup import pending_spotify_selections
from modules.spotify.messages import (
    text as _spotify_text,
    track_label as _spotify_track_label,
    track_plain_label as _spotify_track_plain_label,
)
from modules.spotify.state import set_last_requested_track

SPOTIFY_PLAYBACK_MODE = config.SPOTIFY_PLAYBACK_MODE
_desktop_controller = None
_desktop_controller_lock = threading.Lock()
_spotify_api_capability_failed = False
_PREFIJOS_SPOTIFY = re.compile(
    r"^(?:pon|reproduce|play|toca|ponme|dale|oye|ponle|pon me|"
    r"quiero escuchar|escucha|esc?chame|pon ahora|pon ya)\s+",
    re.IGNORECASE,
)
_DESKTOP_CONTROL_ALIASES = {
    "pausar": "pause",
    "pausa": "pause",
    "detener": "pause",
    "deten": "pause",
    "reanudar": "resume",
    "continuar": "resume",
    "play": "resume",
    "resume": "resume",
    "siguiente": "next",
    "next": "next",
    "skip": "next",
    "anterior": "previous",
    "prev": "previous",
    "atras": "previous",
    "shuffle on": "shuffle_on",
    "activar shuffle": "shuffle_on",
    "activa shuffle": "shuffle_on",
    "mezcla on": "shuffle_on",
    "aleatorio on": "shuffle_on",
    "aleatorio": "shuffle_on",
    "shuffle off": "shuffle_off",
    "desactivar shuffle": "shuffle_off",
    "desactiva shuffle": "shuffle_off",
    "mezcla off": "shuffle_off",
    "aleatorio off": "shuffle_off",
}


def _get_desktop_controller():
    global _desktop_controller
    if _desktop_controller is None:
        with _desktop_controller_lock:
            if _desktop_controller is None:
                _desktop_controller = build_windows_controller(
                    start_timeout=jarvis_config.SPOTIFY_DESKTOP_START_TIMEOUT,
                    action_timeout=jarvis_config.SPOTIFY_DESKTOP_ACTION_TIMEOUT,
                )
    return _desktop_controller


def _parse_spotify_desktop_query(song: str) -> tuple[str, str]:
    clean_song = _PREFIJOS_SPOTIFY.sub("", str(song or "")).strip()
    connectors = list(
        re.finditer(r"\s+(de|by|of|del|por)\s+", clean_song, flags=re.IGNORECASE)
    )
    if not connectors:
        return clean_song.lower(), ""

    separator = connectors[-1]
    connector = separator.group(1).lower()
    title = clean_song[: separator.start()].strip()
    artist = clean_song[separator.end() :].strip()
    artist_words = normalize_text(artist).split()
    ambiguous_tail = bool(
        not artist_words
        or artist_words[0]
        in {"a", "al", "el", "la", "las", "los", "me", "mi", "mis", "my", "the", "ti"}
    )
    single_weak_connector = len(connectors) == 1 and connector in {"de", "del", "of"}
    if not title or not artist or (single_weak_connector and ambiguous_tail):
        return clean_song.lower(), ""
    return title.lower(), artist.lower()


def _spotify_desktop_request(song: str) -> SpotifyRequest:
    title, artist = _parse_spotify_desktop_query(song)
    natural_query = " ".join(part for part in (title, artist) if part).strip()
    if not natural_query:
        natural_query = _PREFIJOS_SPOTIFY.sub("", song).strip()
    return SpotifyRequest(
        raw=song,
        query=natural_query,
        title=title or natural_query,
        artist=artist,
    )


def _spotify_desktop_result(song: str) -> SpotifyDesktopResult:
    return _get_desktop_controller().play(_spotify_desktop_request(song))


def _spotify_play_desktop(song: str) -> str:
    result = _spotify_desktop_result(song)
    profile_id = jarvis_state.get_active_profile_id()
    if result.status is DesktopResultStatus.SUCCESS:
        pending_spotify_selections.clear(profile_id)
        set_last_requested_track(
            _spotify_track_plain_label(result.title, result.artist)
        )
        return _spotify_text(
            f"Playing {_spotify_track_label(result.title, result.artist)} through Spotify Desktop.",
            f"Reproduciendo {_spotify_track_label(result.title, result.artist)} mediante Spotify Desktop.",
        )
    if result.status is DesktopResultStatus.AMBIGUOUS:
        pending_spotify_selections.remember(profile_id, result.choices)
        choices = "; ".join(
            _spotify_track_plain_label(item.title, item.artist)
            for item in result.choices
        )
        return _spotify_text(
            f"I found several close matches: {choices}. Which one should I play?",
            f"Encontre varias coincidencias: {choices}. Cual debo reproducir?",
        )

    pending_spotify_selections.clear(profile_id)
    messages = {
        "spotify_no_results": _spotify_text(
            "I could not find that item in Spotify Desktop.",
            "No encontre ese contenido en Spotify Desktop.",
        ),
        "spotify_focus_lost": _spotify_text(
            "Spotify lost focus before I could safely complete the search.",
            "Spotify perdio el foco antes de completar la busqueda de forma segura.",
        ),
        "spotify_start_timeout": _spotify_text(
            "Spotify Desktop did not become ready in time.",
            "Spotify Desktop no estuvo listo a tiempo.",
        ),
        "spotify_playback_not_verified": _spotify_text(
            "Spotify received the command, but I could not verify the requested track.",
            "Spotify recibio el comando, pero no pude verificar la cancion solicitada.",
        ),
        "spotify_search_unavailable": _spotify_text(
            "Spotify Desktop search is not accessible in the current layout.",
            "La busqueda de Spotify Desktop no es accesible en el diseno actual.",
        ),
        "spotify_cancelled": _spotify_text(
            "The previous Spotify request was replaced by a newer command.",
            "La solicitud anterior de Spotify fue reemplazada por un comando nuevo.",
        ),
        "spotify_automation_busy": _spotify_text(
            "Spotify Desktop is still processing another command.",
            "Spotify Desktop todavia esta procesando otro comando.",
        ),
    }
    return messages.get(
        result.message_key,
        _spotify_text(
            "Spotify Desktop automation is unavailable.",
            "La automatizacion de Spotify Desktop no esta disponible.",
        ),
    )


def _spotify_desktop_control_action(action: str) -> str | None:
    return _DESKTOP_CONTROL_ALIASES.get(normalize_text(action))


def _spotify_control_desktop(action: str) -> str:
    canonical = _spotify_desktop_control_action(action)
    if canonical is None:
        return _spotify_text(
            f"Action '{action}' not recognized.",
            f"Accion '{action}' no reconocida.",
        )

    result = _get_desktop_controller().control(canonical)
    messages = {
        "pause": _spotify_text("Playback paused.", "Reproduccion pausada."),
        "resume": _spotify_text("Playback resumed.", "Reproduccion reanudada."),
        "next": _spotify_text("Next track.", "Siguiente cancion."),
        "previous": _spotify_text("Previous track.", "Cancion anterior."),
        "shuffle_on": _spotify_text("Shuffle enabled.", "Shuffle activado."),
        "shuffle_off": _spotify_text("Shuffle disabled.", "Shuffle desactivado."),
    }
    if result.status is DesktopResultStatus.SUCCESS:
        return messages[canonical]

    # Windows Media Key Fallback when desktop UI automation is unavailable
    if result.status is DesktopResultStatus.UNAVAILABLE:
        try:
            from modules.spotify.desktop.windows import send_media_key_event
            if send_media_key_event(canonical) and canonical in messages:
                return messages[canonical]
        except Exception:
            pass
    if result.message_key == "spotify_focus_lost":
        return _spotify_text(
            "Spotify lost focus before I could safely complete that action.",
            "Spotify perdio el foco antes de completar la accion de forma segura.",
        )
    if result.message_key == "spotify_action_restricted":
        return _spotify_text(
            "That control is not available in the current Spotify state.",
            "Ese control no esta disponible en el estado actual de Spotify.",
        )
    if result.message_key == "spotify_control_not_verified":
        return _spotify_text(
            "Spotify received the control, but I could not verify the state change.",
            "Spotify recibio el control, pero no pude verificar el cambio de estado.",
        )
    return _spotify_text(
        "Spotify Desktop could not complete that playback action.",
        "Spotify Desktop no pudo completar esa accion de reproduccion.",
    )


def play(song: str) -> str:
    """Play music using a cached Spotify API session or Spotify Desktop."""
    global _spotify_api_capability_failed
    clean_song = str(song or "").strip()
    if not clean_song:
        return _spotify_text("Tell me what to play.", "Dime que deseas reproducir.")
    pending_spotify_selections.clear(jarvis_state.get_active_profile_id())
    if SPOTIFY_PLAYBACK_MODE == "desktop":
        return _spotify_play_desktop(clean_song)
    if SPOTIFY_PLAYBACK_MODE == "auto" and (
        _spotify_api_capability_failed or not _spotify_has_valid_cached_token()
    ):
        return _spotify_play_desktop(clean_song)

    api_result = _spotify_play_api(clean_song)
    if api_result.ok or SPOTIFY_PLAYBACK_MODE == "api":
        return api_result.message
    if api_result.capability_failure:
        _spotify_api_capability_failed = True
        return _spotify_play_desktop(clean_song)
    return api_result.message


def _play_spotify_seed(seed: str) -> str:
    return play(seed)


def play_mix(seed: str) -> str:
    """Play a seed and build a dynamic AutoMix around it."""
    clean_seed = str(seed or "").strip()
    if not clean_seed:
        return _spotify_text(
            "Tell me an artist, genre, playlist, or song to build the mix.",
            "Dime un artista, genero, playlist o cancion para construir el mix.",
        )
    response = _play_spotify_seed(clean_seed)
    if "Spotify Desktop" in response and (
        "Playing " in response or "Reproduciendo " in response
    ):
        return response + " " + _spotify_text(
            "Spotify Desktop will continue the mix using its own autoplay and recommendations.",
            "Spotify Desktop continuara el mix con su reproduccion automatica y recomendaciones.",
        )
    return response


def control(action: str) -> str:
    """Control Spotify through a cached API session or Spotify Desktop."""
    global _spotify_api_capability_failed
    clean_action = str(action or "").strip()
    if not clean_action:
        return _spotify_text(
            "Tell me which playback action to perform.",
            "Dime que accion de reproduccion debo realizar.",
        )
    if SPOTIFY_PLAYBACK_MODE == "desktop":
        return _spotify_control_desktop(clean_action)
    if SPOTIFY_PLAYBACK_MODE == "auto" and (
        _spotify_api_capability_failed or not _spotify_has_valid_cached_token()
    ):
        return _spotify_control_desktop(clean_action)

    api_result = _spotify_control_api(clean_action)
    if api_result.ok or SPOTIFY_PLAYBACK_MODE == "api":
        return api_result.message
    if api_result.capability_failure:
        _spotify_api_capability_failed = True
        return _spotify_control_desktop(clean_action)
    return api_result.message
