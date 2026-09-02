"""Provider-neutral Spotify orchestration and fallback policy."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

from core import jarvis_config, jarvis_state
from core.media_state import set_last_media_source

from modules.spotify import config
from modules.spotify.api.client import _spotify_has_valid_cached_token
from modules.spotify.api.playback import (
    _spotify_add_to_queue_api,
    _spotify_control_api,
    _spotify_current_track_api,
    _spotify_like_track_api,
    _spotify_play_api,
    _spotify_unlike_track_api,
)
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
)
from modules.spotify.messages import (
    track_label as _spotify_track_label,
)
from modules.spotify.messages import (
    track_plain_label as _spotify_track_plain_label,
)
from modules.spotify.state import set_last_requested_track

SPOTIFY_PLAYBACK_MODE = config.SPOTIFY_PLAYBACK_MODE
_desktop_controller = None
_desktop_controller_lock = threading.Lock()
_SPOTIFY_API_RETRY_COOLDOWN_SECONDS = 60.0
_spotify_api_blocked_until: dict[str, float] = {}
_spotify_api_capability_lock = threading.RLock()
_PREFIJOS_SPOTIFY = re.compile(
    r"^(?:pon|reproduce|play|toca|ponme|dale|oye|ponle|pon me|"
    r"quiero escuchar|escucha|esc?chame|pon ahora|pon ya)\s+",
    re.IGNORECASE,
)
_DESKTOP_CONTROL_ALIASES = {
    "pause": "pause",
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
    "previous": "previous",
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
    "desactivar aleatorio": "shuffle_off",
    "repetir": "repeat_on",
    "activar repetir": "repeat_on",
    "activar repeticion": "repeat_on",
    "activar repetición": "repeat_on",
    "repeat on": "repeat_on",
    "repeat": "repeat_on",
    "activar bucle": "repeat_on",
    "bucle on": "repeat_on",
    "no repetir": "repeat_off",
    "desactivar repeticion": "repeat_off",
    "desactivar repetición": "repeat_off",
    "desactivar repetir": "repeat_off",
    "repeat off": "repeat_off",
    "desactivar bucle": "repeat_off",
    "bucle off": "repeat_off",
    "subir volumen": "volume_up",
    "mas volumen": "volume_up",
    "más volumen": "volume_up",
    "aumentar volumen": "volume_up",
    "volume up": "volume_up",
    "bajar volumen": "volume_down",
    "menos volumen": "volume_down",
    "disminuir volumen": "volume_down",
    "volume down": "volume_down",
    "silenciar": "mute",
    "mute": "mute",
    "mutear": "mute",
    "desilenciar": "unmute",
    "unmute": "unmute",
    "like": "like",
    "dar like": "like",
    "me gusta": "like",
    "guardar": "like",
    "guardar cancion": "like",
    "favorito": "like",
    "anadir a favoritos": "like",
    "añadir a favoritos": "like",
    "guardar en favoritos": "like",
    "marcar como favorito": "like",
    "unlike": "unlike",
    "quitar like": "unlike",
    "dislike": "unlike",
    "no me gusta": "unlike",
    "eliminar de me gusta": "unlike",
    "quitar me gusta": "unlike",
    "eliminar de favoritos": "unlike",
    "quitar de favoritos": "unlike",
    "info": "info",
    "cancion actual": "info",
    "canción actual": "info",
    "que suena": "info",
    "qué suena": "info",
    "now playing": "info",
    "estado": "info",
    "que cancion es": "info",
    "qué canción es": "info",
}


@dataclass(frozen=True)
class SpotifyServicePlaybackOutcome:
    message: str
    ok: bool
    provider: str = ""


def _spotify_api_temporarily_blocked(operation: str) -> bool:
    key = str(operation or "").strip().lower()
    with _spotify_api_capability_lock:
        blocked_until = _spotify_api_blocked_until.get(key, 0.0)
        if blocked_until <= time.monotonic():
            _spotify_api_blocked_until.pop(key, None)
            return False
        return True


def _spotify_api_mark_capability_failure(operation: str) -> None:
    key = str(operation or "").strip().lower()
    if key:
        with _spotify_api_capability_lock:
            _spotify_api_blocked_until[key] = time.monotonic() + _SPOTIFY_API_RETRY_COOLDOWN_SECONDS


def _spotify_api_clear_capability_failure(operation: str) -> None:
    with _spotify_api_capability_lock:
        _spotify_api_blocked_until.pop(str(operation or "").strip().lower(), None)


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
    connectors = list(re.finditer(r"\s+(de|by|of|del|por)\s+", clean_song, flags=re.IGNORECASE))
    if not connectors:
        return clean_song.lower(), ""

    separator = connectors[-1]
    connector = separator.group(1).lower()
    title = clean_song[: separator.start()].strip()
    artist = clean_song[separator.end() :].strip()
    artist_words = normalize_text(artist).split()
    ambiguous_tail = bool(
        not artist_words
        or artist_words[0] in {"a", "al", "el", "la", "las", "los", "me", "mi", "mis", "my", "the", "ti"}
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


def _spotify_play_desktop_outcome(song: str) -> SpotifyServicePlaybackOutcome:
    result = _spotify_desktop_result(song)
    profile_id = jarvis_state.get_active_profile_id()
    if result.status is DesktopResultStatus.SUCCESS:
        pending_spotify_selections.clear(profile_id)
        set_last_media_source(profile_id, "spotify")
        set_last_requested_track(
            _spotify_track_plain_label(result.title, result.artist),
            profile_id,
        )
        return SpotifyServicePlaybackOutcome(
            _spotify_text(
                f"Playing {_spotify_track_label(result.title, result.artist)} through Spotify Desktop.",
                f"Reproduciendo {_spotify_track_label(result.title, result.artist)} mediante Spotify Desktop.",
            ),
            ok=True,
            provider="desktop",
        )
    if result.status is DesktopResultStatus.AMBIGUOUS:
        pending_spotify_selections.remember(profile_id, result.choices)
        choices = "; ".join(_spotify_track_plain_label(item.title, item.artist) for item in result.choices)
        return SpotifyServicePlaybackOutcome(
            _spotify_text(
                f"I found several close matches: {choices}. Which one should I play?",
                f"Encontre varias coincidencias: {choices}. Cual debo reproducir?",
            ),
            ok=False,
            provider="desktop",
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
    return SpotifyServicePlaybackOutcome(
        messages.get(
            result.message_key,
            _spotify_text(
                "Spotify Desktop automation is unavailable.",
                "La automatizacion de Spotify Desktop no esta disponible.",
            ),
        ),
        ok=False,
        provider="desktop",
    )


def _spotify_play_desktop(song: str) -> str:
    return _spotify_play_desktop_outcome(song).message


def _spotify_desktop_control_action(action: str) -> str | None:
    return _DESKTOP_CONTROL_ALIASES.get(normalize_text(action))


def _spotify_desktop_control_message(canonical: str, result: SpotifyDesktopResult) -> str:
    messages = {
        "pause": _spotify_text("Playback paused.", "Reproduccion pausada."),
        "resume": _spotify_text("Playback resumed.", "Reproduccion reanudada."),
        "next": _spotify_text("Next track.", "Siguiente cancion."),
        "previous": _spotify_text("Previous track.", "Cancion anterior."),
        "shuffle_on": _spotify_text("Shuffle enabled.", "Shuffle activado."),
        "shuffle_off": _spotify_text("Shuffle disabled.", "Shuffle desactivado."),
        "repeat_on": _spotify_text("Repeat mode enabled.", "Modo repeticion activado."),
        "repeat_off": _spotify_text("Repeat mode disabled.", "Modo repeticion desactivado."),
        "volume_up": _spotify_text("Volume increased.", "Volumen aumentado."),
        "volume_down": _spotify_text("Volume decreased.", "Volumen disminuido."),
        "mute": _spotify_text("Spotify muted.", "Spotify silenciado."),
        "unmute": _spotify_text("Spotify unmuted.", "Audio de Spotify restaurado."),
        "like": _spotify_text("Saved to your Liked Songs on Spotify.", "Guardado en tus Me Gusta de Spotify."),
        "unlike": _spotify_text("Removed from your Liked Songs on Spotify.", "Eliminado de tus Me Gusta de Spotify."),
    }
    if result.status is DesktopResultStatus.SUCCESS:
        return messages[canonical]

    if result.status is DesktopResultStatus.UNVERIFIED:
        return _spotify_text(
            "I sent the Spotify Desktop control, but I could not verify the state change.",
            "Envié el control a Spotify Desktop, pero no pude verificar el cambio de estado.",
        )

    # Windows Media Key Fallback when desktop UI automation is unavailable
    if result.status is DesktopResultStatus.UNAVAILABLE:
        try:
            from modules.spotify.desktop.windows import send_media_key_event

            if send_media_key_event(canonical) and canonical in messages:
                return _spotify_text(
                    "I sent a global media key, but I could not verify which application handled it.",
                    "Envie una tecla multimedia global, pero no pude verificar que aplicacion la recibio.",
                )
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


def _spotify_control_desktop(action: str) -> str:
    canonical = _spotify_desktop_control_action(action)
    if canonical is None:
        return _spotify_text(
            f"Action '{action}' not recognized.",
            f"Accion '{action}' no reconocida.",
        )

    if canonical == "info":
        return current_track()

    result = _get_desktop_controller().control(canonical)
    return _spotify_desktop_control_message(canonical, result)


def _play_outcome(song: str) -> SpotifyServicePlaybackOutcome:
    clean_song = str(song or "").strip()
    if not clean_song:
        return SpotifyServicePlaybackOutcome(
            _spotify_text("Tell me what to play.", "Dime que deseas reproducir."),
            ok=False,
        )
    pending_spotify_selections.clear(jarvis_state.get_active_profile_id())
    if SPOTIFY_PLAYBACK_MODE == "desktop":
        return _spotify_play_desktop_outcome(clean_song)
    if SPOTIFY_PLAYBACK_MODE == "auto" and (
        _spotify_api_temporarily_blocked("play") or not _spotify_has_valid_cached_token()
    ):
        return _spotify_play_desktop_outcome(clean_song)

    api_result = _spotify_play_api(clean_song)
    if api_result.ok:
        _spotify_api_clear_capability_failure("play")
        set_last_media_source(
            jarvis_state.get_active_profile_id(),
            "spotify",
        )
        return SpotifyServicePlaybackOutcome(api_result.message, ok=True, provider="api")
    if SPOTIFY_PLAYBACK_MODE == "api":
        return SpotifyServicePlaybackOutcome(api_result.message, ok=False, provider="api")
    if api_result.capability_failure:
        _spotify_api_mark_capability_failure("play")
        return _spotify_play_desktop_outcome(clean_song)
    return SpotifyServicePlaybackOutcome(api_result.message, ok=False, provider="api")


def play(song: str) -> str:
    """Play music using a cached Spotify API session or Spotify Desktop."""
    return _play_outcome(song).message


def _play_spotify_seed(seed: str) -> SpotifyServicePlaybackOutcome:
    return _play_outcome(seed)


def play_mix(seed: str) -> str:
    """Play a seed and build a dynamic AutoMix around it."""
    clean_seed = str(seed or "").strip()
    if not clean_seed:
        return _spotify_text(
            "Tell me an artist, genre, playlist, or song to build the mix.",
            "Dime un artista, genero, playlist o cancion para construir el mix.",
        )
    outcome = _play_spotify_seed(clean_seed)
    if outcome.ok and outcome.provider == "desktop":
        return (
            outcome.message
            + " "
            + _spotify_text(
                "Spotify Desktop will continue the mix using its own autoplay and recommendations.",
                "Spotify Desktop continuara el mix con su reproduccion automatica y recomendaciones.",
            )
        )
    return outcome.message


def control(action: str) -> str:
    """Control Spotify through a cached API session or Spotify Desktop."""
    clean_action = str(action or "").strip()
    if not clean_action:
        return _spotify_text(
            "Tell me which playback action to perform.",
            "Dime que accion de reproduccion debo realizar.",
        )
    if SPOTIFY_PLAYBACK_MODE == "desktop":
        return _spotify_control_desktop(clean_action)
    if SPOTIFY_PLAYBACK_MODE == "auto" and (
        _spotify_api_temporarily_blocked("control") or not _spotify_has_valid_cached_token()
    ):
        return _spotify_control_desktop(clean_action)

    api_result = _spotify_control_api(clean_action)
    if api_result.ok:
        _spotify_api_clear_capability_failure("control")
        return api_result.message
    if SPOTIFY_PLAYBACK_MODE == "api":
        return api_result.message
    if api_result.capability_failure:
        _spotify_api_mark_capability_failure("control")
        return _spotify_control_desktop(clean_action)
    return api_result.message


def _spotify_add_to_queue_desktop(cancion: str) -> str:
    clean_song = str(cancion or "").strip()
    if not clean_song:
        return _spotify_text(
            "Please specify a song to add to the queue.",
            "Por favor indique una canción para añadir a la cola.",
        )
    result = _get_desktop_controller().queue(_spotify_desktop_request(clean_song))
    if result.status is DesktopResultStatus.SUCCESS:
        return _spotify_text(
            f"Added to Spotify Desktop queue: {_spotify_track_label(result.title, result.artist)}.",
            f"Añadido a la cola en Spotify Desktop: {_spotify_track_label(result.title, result.artist)}.",
        )
    if result.status is DesktopResultStatus.UNVERIFIED:
        return _spotify_text(
            f"I sent the queue command for {_spotify_track_label(result.title, result.artist)}, but I could not verify that Spotify added it.",
            f"Envié el comando de cola para {_spotify_track_label(result.title, result.artist)}, pero no pude verificar que Spotify lo añadiera.",
        )
    if result.status is DesktopResultStatus.AMBIGUOUS:
        choices = "; ".join(_spotify_track_plain_label(item.title, item.artist) for item in result.choices)
        return _spotify_text(
            f"I found several close matches: {choices}. Which one should I queue?",
            f"Encontré varias coincidencias: {choices}. ¿Cuál debo añadir a la cola?",
        )
    return _spotify_text(
        f"Could not add '{clean_song}' to Spotify Desktop queue.",
        f"No se pudo añadir '{clean_song}' a la cola en Spotify Desktop.",
    )


def add_to_queue(cancion: str) -> str:
    """Add a song to the Spotify playback queue."""
    clean_song = str(cancion or "").strip()
    if not clean_song:
        return _spotify_text(
            "Please specify a song to add to the queue.",
            "Por favor indique una canción para añadir a la cola.",
        )
    if SPOTIFY_PLAYBACK_MODE == "desktop":
        return _spotify_add_to_queue_desktop(clean_song)
    if SPOTIFY_PLAYBACK_MODE == "auto" and (
        _spotify_api_temporarily_blocked("queue") or not _spotify_has_valid_cached_token()
    ):
        return _spotify_add_to_queue_desktop(clean_song)

    res = _spotify_add_to_queue_api(clean_song)
    if res.ok:
        _spotify_api_clear_capability_failure("queue")
        return res.message
    if SPOTIFY_PLAYBACK_MODE == "api":
        return res.message
    if res.capability_failure:
        _spotify_api_mark_capability_failure("queue")
        return _spotify_add_to_queue_desktop(clean_song)
    return res.message


def _spotify_is_current_track_reference(cancion: str) -> bool:
    clean_song = _PREFIJOS_SPOTIFY.sub("", str(cancion or "")).strip().lower()
    return not clean_song or clean_song in {
        "esta",
        "esta cancion",
        "cancion actual",
        "actual",
        "lo que suena",
        "this",
        "this song",
        "current",
    }


def _spotify_library_desktop(cancion: str, action: str) -> str:
    controller = _get_desktop_controller()
    if _spotify_is_current_track_reference(cancion):
        result = controller.control(action)
        if result.status is DesktopResultStatus.SUCCESS and result.title:
            label = _spotify_track_label(result.title, result.artist)
            if action == "like":
                return _spotify_text(
                    f"Saved {label} to your Liked Songs on Spotify Desktop.",
                    f"Guardé {label} en tus Me Gusta de Spotify Desktop.",
                )
            return _spotify_text(
                f"Removed {label} from your Liked Songs on Spotify Desktop.",
                f"Eliminé {label} de tus Me Gusta de Spotify Desktop.",
            )
        return _spotify_desktop_control_message(action, result)

    result = controller.library(_spotify_desktop_request(cancion), action)
    label = _spotify_track_label(result.title, result.artist)
    if result.status is DesktopResultStatus.SUCCESS:
        if action == "like":
            return _spotify_text(
                f"Saved {label} to your Liked Songs on Spotify Desktop.",
                f"Guardé {label} en tus Me Gusta de Spotify Desktop.",
            )
        return _spotify_text(
            f"Removed {label} from your Liked Songs on Spotify Desktop.",
            f"Eliminé {label} de tus Me Gusta de Spotify Desktop.",
        )
    if result.status is DesktopResultStatus.UNVERIFIED:
        verb_en = "save" if action == "like" else "remove"
        verb_es = "guardar" if action == "like" else "eliminar"
        return _spotify_text(
            f"I sent the command to {verb_en} {label}, but I could not verify the library change.",
            f"Envié el comando para {verb_es} {label}, pero no pude verificar el cambio en la biblioteca.",
        )
    if result.status is DesktopResultStatus.AMBIGUOUS:
        choices = "; ".join(_spotify_track_plain_label(item.title, item.artist) for item in result.choices)
        return _spotify_text(
            f"I found several close matches: {choices}. Repeat the command with the title and artist.",
            f"Encontré varias coincidencias: {choices}. Repite el comando con el título y el artista.",
        )
    if result.status is DesktopResultStatus.NOT_FOUND:
        return _spotify_text(
            "I could not find that track in Spotify Desktop.",
            "No pude encontrar esa canción en Spotify Desktop.",
        )
    return _spotify_text(
        "Spotify Desktop could not change that track safely without interrupting playback.",
        "Spotify Desktop no pudo cambiar esa canción de forma segura sin interrumpir la reproducción.",
    )


def _spotify_like_desktop(cancion: str = "") -> str:
    return _spotify_library_desktop(cancion, "like")


def like_track(cancion: str = "") -> str:
    """Save the currently playing or specified track to Liked Songs."""
    if SPOTIFY_PLAYBACK_MODE == "desktop":
        return _spotify_like_desktop(cancion)
    if SPOTIFY_PLAYBACK_MODE == "auto" and (
        _spotify_api_temporarily_blocked("like") or not _spotify_has_valid_cached_token()
    ):
        return _spotify_like_desktop(cancion)

    res = _spotify_like_track_api(cancion)
    if res.ok:
        _spotify_api_clear_capability_failure("like")
        return res.message
    if SPOTIFY_PLAYBACK_MODE == "api":
        return res.message
    if res.capability_failure:
        _spotify_api_mark_capability_failure("like")
        return _spotify_like_desktop(cancion)
    return res.message


def _spotify_unlike_desktop(cancion: str = "") -> str:
    return _spotify_library_desktop(cancion, "unlike")


def unlike_track(cancion: str = "") -> str:
    """Remove the currently playing or specified track from Liked Songs."""
    if SPOTIFY_PLAYBACK_MODE == "desktop":
        return _spotify_unlike_desktop(cancion)
    if SPOTIFY_PLAYBACK_MODE == "auto" and (
        _spotify_api_temporarily_blocked("unlike") or not _spotify_has_valid_cached_token()
    ):
        return _spotify_unlike_desktop(cancion)

    res = _spotify_unlike_track_api(cancion)
    if res.ok:
        _spotify_api_clear_capability_failure("unlike")
        return res.message
    if SPOTIFY_PLAYBACK_MODE == "api":
        return res.message
    if res.capability_failure:
        _spotify_api_mark_capability_failure("unlike")
        return _spotify_unlike_desktop(cancion)
    return res.message


def current_track() -> str:
    """Get information about the currently playing track on Spotify."""
    api_result = None
    if SPOTIFY_PLAYBACK_MODE != "desktop":
        api_result = _spotify_current_track_api()
        if api_result.ok:
            return api_result.message

    result = _get_desktop_controller().current()
    if result.status in {DesktopResultStatus.SUCCESS, DesktopResultStatus.UNVERIFIED} and result.title:
        title = result.title.replace("Spotify - ", "").replace("Spotify – ", "").strip()
        artist_en = f" by {result.artist}" if result.artist else ""
        artist_es = f" de {result.artist}" if result.artist else ""
        if result.playback_state == "playing":
            return _spotify_text(
                f"Playing on Spotify Desktop: '{title}'{artist_en}.",
                f"Reproduciendo en Spotify Desktop: '{title}'{artist_es}.",
            )
        if result.playback_state == "paused":
            return _spotify_text(
                f"Paused on Spotify Desktop: '{title}'{artist_en}.",
                f"Pausado en Spotify Desktop: '{title}'{artist_es}.",
            )
        return _spotify_text(
            f"Current track on Spotify Desktop: '{title}'{artist_en}; playback state could not be verified.",
            f"Canción actual en Spotify Desktop: '{title}'{artist_es}; no pude verificar el estado de reproducción.",
        )
    if result.status is DesktopResultStatus.NOT_FOUND:
        return _spotify_text(
            "No current track could be identified on Spotify Desktop.",
            "No pude identificar una canción actual en Spotify Desktop.",
        )

    if SPOTIFY_PLAYBACK_MODE == "api" and api_result is not None:
        return api_result.message
    return _spotify_text(
        "No track is currently playing on Spotify.",
        "No hay ninguna canción reproduciéndose actualmente en Spotify.",
    )
