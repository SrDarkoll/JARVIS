"""Provider-neutral Spotify orchestration and fallback policy."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, replace

from core import jarvis_config, jarvis_state
from core.media_state import set_last_media_source

from modules.spotify import config
from modules.spotify.api.client import _spotify_has_valid_cached_token
from modules.spotify.api.playback import (
    SpotifyAPIPlaybackResult,
    _spotify_add_to_queue_api,
    _spotify_control_api,
    _spotify_current_track_api,
    _spotify_like_track_api,
    _spotify_play_api,
    _spotify_unlike_track_api,
)
from modules.spotify.desktop import (
    AutomationState,
    DesktopResultStatus,
    SpotifyCandidate,
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
    diagnostic_code: str = ""
    verified: bool = False
    requires_follow_up: bool = False
    accepted: bool = False
    status: DesktopResultStatus | None = None
    choices: tuple[SpotifyCandidate, ...] = ()


@dataclass(frozen=True)
class SpotifyServiceMutationOutcome:
    message: str
    status: DesktopResultStatus
    provider: str = ""
    diagnostic_code: str = ""
    verified: bool = False
    choices: tuple[SpotifyCandidate, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.status is DesktopResultStatus.UNVERIFIED

    @property
    def requires_follow_up(self) -> bool:
        return self.status is DesktopResultStatus.AMBIGUOUS


def _spotify_desktop_effect_accepted(result: SpotifyDesktopResult) -> bool:
    if result.status is DesktopResultStatus.UNVERIFIED:
        return True
    if result.status is not DesktopResultStatus.FAILED or result.message_key not in {
        "spotify_playback_not_verified", "spotify_control_not_verified",
    }:
        return False
    # Older providers omit stages; these codes are emitted only after sending input.
    return not result.states or any(
        state in {AutomationState.PLAYING, AutomationState.VERIFYING} for state in result.states
    )


def _desktop_mutation_outcome(result: SpotifyDesktopResult, message: str) -> SpotifyServiceMutationOutcome:
    return SpotifyServiceMutationOutcome(
        message=message,
        status=DesktopResultStatus.UNVERIFIED if _spotify_desktop_effect_accepted(result) else result.status,
        provider="desktop",
        diagnostic_code=result.message_key or f"spotify_desktop_{result.status.value}",
        verified=result.status is DesktopResultStatus.SUCCESS,
        choices=result.choices,
    )


def _api_mutation_outcome(result: SpotifyAPIPlaybackResult, operation: str) -> SpotifyServiceMutationOutcome:
    if result.ok:
        status, diagnostic = DesktopResultStatus.UNVERIFIED, "accepted"
    elif result.capability_failure:
        status, diagnostic = DesktopResultStatus.UNAVAILABLE, "unavailable"
    else:
        status, diagnostic = DesktopResultStatus.FAILED, "failed"
    return SpotifyServiceMutationOutcome(
        message=result.message, status=status, provider="api",
        diagnostic_code=f"spotify_api_{operation}_{diagnostic}",
    )


def _mutation_outcome(operation: str, argument: str) -> SpotifyServiceMutationOutcome:
    api_handler, desktop_handler = {
        "control": (_spotify_control_api, _spotify_control_desktop_outcome),
        "queue": (_spotify_add_to_queue_api, _spotify_add_to_queue_desktop_outcome),
        "like": (_spotify_like_track_api, _spotify_like_desktop_outcome),
        "unlike": (_spotify_unlike_track_api, _spotify_unlike_desktop_outcome),
    }[operation]
    if SPOTIFY_PLAYBACK_MODE == "desktop" or (
        SPOTIFY_PLAYBACK_MODE == "auto" and (
            _spotify_api_temporarily_blocked(operation) or not _spotify_has_valid_cached_token()
        )
    ):
        return desktop_handler(argument)

    result = api_handler(argument)
    if result.ok:
        _spotify_api_clear_capability_failure(operation)
    elif SPOTIFY_PLAYBACK_MODE == "auto" and result.capability_failure:
        _spotify_api_mark_capability_failure(operation)
        return desktop_handler(argument)
    return _api_mutation_outcome(result, operation)


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
            diagnostic_code=result.message_key or "spotify_playback_started",
            verified=True,
            status=result.status,
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
            diagnostic_code="spotify_ambiguous_results",
            requires_follow_up=True,
            status=result.status,
            choices=result.choices,
        )

    pending_spotify_selections.clear(profile_id)
    messages = {
        "spotify_client_not_found": _spotify_text(
            "I could not open the Spotify desktop client. Open Spotify and try again.",
            "No pude abrir el cliente de Spotify. Abre Spotify y vuelve a intentarlo.",
        ),
        "spotify_uia_unavailable": _spotify_text(
            "The Windows accessibility component needed to control Spotify is unavailable.",
            "No esta disponible el componente de accesibilidad de Windows necesario para controlar Spotify.",
        ),
        "spotify_no_results": _spotify_text(
            "I could not find that item in Spotify Desktop.",
            "No encontre ese contenido en Spotify Desktop.",
        ),
        "spotify_focus_lost": _spotify_text(
            "I could not safely activate Spotify. Bring the Spotify window to the foreground and repeat the command.",
            "No pude activar Spotify de forma segura. Pon su ventana en primer plano y repite el comando.",
        ),
        "spotify_start_timeout": _spotify_text(
            "I could not find the Spotify window in time. Open Spotify and try again.",
            "No pude encontrar la ventana de Spotify a tiempo. Abre Spotify y vuelve a intentarlo.",
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
    accepted = _spotify_desktop_effect_accepted(result)
    if accepted:
        message = messages["spotify_playback_not_verified"]
    elif result.message_key == "spotify_playback_not_verified":
        message = _spotify_text(
            "Spotify Desktop could not start the requested track.",
            "Spotify Desktop no pudo iniciar la cancion solicitada.",
        )
    else:
        message = messages.get(
            result.message_key, _spotify_text(
                "Spotify Desktop automation is unavailable.",
                "La automatizacion de Spotify Desktop no esta disponible.",
            ),
        )
    return SpotifyServicePlaybackOutcome(
        message,
        ok=False,
        provider="desktop",
        diagnostic_code=result.message_key or (
            "spotify_playback_not_verified" if accepted else "spotify_desktop_unavailable"
        ),
        accepted=accepted,
        status=DesktopResultStatus.UNVERIFIED if accepted else result.status,
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

    if result.status is DesktopResultStatus.AMBIGUOUS:
        choices = "; ".join(_spotify_track_plain_label(item.title, item.artist) for item in result.choices)
        return _spotify_text(
            f"I found several close matches: {choices}. Repeat the command with the title and artist.",
            f"Encontre varias coincidencias: {choices}. Repite el comando con el titulo y el artista.",
        )
    if result.message_key == "spotify_focus_lost":
        return _spotify_text(
            "I could not safely activate Spotify. Bring the Spotify window to the foreground and repeat the command.",
            "No pude activar Spotify de forma segura. Pon su ventana en primer plano y repite el comando.",
        )
    if result.message_key == "spotify_action_restricted":
        return _spotify_text(
            "That control is not available in the current Spotify state.",
            "Ese control no esta disponible en el estado actual de Spotify.",
        )
    if result.message_key == "spotify_control_not_verified" and _spotify_desktop_effect_accepted(result):
        return _spotify_text(
            "Spotify received the control, but I could not verify the state change.",
            "Spotify recibio el control, pero no pude verificar el cambio de estado.",
        )
    return _spotify_text(
        "Spotify Desktop could not complete that playback action.",
        "Spotify Desktop no pudo completar esa accion de reproduccion.",
    )


def _spotify_desktop_control_outcome(canonical: str, result: SpotifyDesktopResult) -> SpotifyServiceMutationOutcome:
    if result.status is DesktopResultStatus.UNAVAILABLE and canonical in {"pause", "resume", "next", "previous"}:
        try:
            from modules.spotify.desktop.windows import send_media_key_event

            if send_media_key_event(canonical):
                return SpotifyServiceMutationOutcome(
                    _spotify_text(
                        "I sent a global media key, but I could not verify which application handled it.",
                        "Envie una tecla multimedia global, pero no pude verificar que aplicacion la recibio.",
                    ),
                    status=DesktopResultStatus.UNVERIFIED,
                    provider="global_media_key",
                    diagnostic_code="spotify_global_media_key_unverified",
                )
        except Exception:
            pass
    return _desktop_mutation_outcome(result, _spotify_desktop_control_message(canonical, result))


def _spotify_control_desktop_outcome(action: str) -> SpotifyServiceMutationOutcome:
    canonical = _spotify_desktop_control_action(action)
    if canonical is None:
        return SpotifyServiceMutationOutcome(
            _spotify_text(f"Action '{action}' not recognized.", f"Accion '{action}' no reconocida."),
            status=DesktopResultStatus.FAILED,
            provider="desktop",
            diagnostic_code="spotify_action_not_recognized",
        )

    if canonical == "info":
        return _current_track_outcome()

    result = _get_desktop_controller().control(canonical)
    return _spotify_desktop_control_outcome(canonical, result)


def _spotify_control_desktop(action: str) -> str:
    return _spotify_control_desktop_outcome(action).message


def _play_outcome(song: str) -> SpotifyServicePlaybackOutcome:
    clean_song = str(song or "").strip()
    if not clean_song:
        return SpotifyServicePlaybackOutcome(
            _spotify_text("Tell me what to play.", "Dime que deseas reproducir."),
            ok=False,
            diagnostic_code="spotify_query_required",
            requires_follow_up=True,
            status=DesktopResultStatus.AMBIGUOUS,
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
        return SpotifyServicePlaybackOutcome(
            api_result.message, ok=True, provider="api", diagnostic_code="spotify_api_playback_accepted",
            accepted=True, status=DesktopResultStatus.UNVERIFIED,
        )
    if SPOTIFY_PLAYBACK_MODE == "api":
        return SpotifyServicePlaybackOutcome(
            api_result.message, ok=False, provider="api",
            diagnostic_code="spotify_api_playback_unavailable" if api_result.capability_failure else "spotify_api_playback_failed",
            status=DesktopResultStatus.UNAVAILABLE if api_result.capability_failure else DesktopResultStatus.FAILED,
        )
    if api_result.capability_failure:
        _spotify_api_mark_capability_failure("play")
        return _spotify_play_desktop_outcome(clean_song)
    return SpotifyServicePlaybackOutcome(
        api_result.message, ok=False, provider="api", diagnostic_code="spotify_api_playback_failed",
        status=DesktopResultStatus.FAILED,
    )


def play(song: str) -> str:
    """Play music using a cached Spotify API session or Spotify Desktop."""
    return _play_outcome(song).message


def _play_spotify_seed(seed: str) -> SpotifyServicePlaybackOutcome:
    return _play_outcome(seed)


def _mix_outcome(seed: str) -> SpotifyServicePlaybackOutcome:
    """Play a seed and build a dynamic AutoMix around it."""
    clean_seed = str(seed or "").strip()
    if not clean_seed:
        return SpotifyServicePlaybackOutcome(
            _spotify_text(
                "Tell me an artist, genre, playlist, or song to build the mix.",
                "Dime un artista, genero, playlist o cancion para construir el mix.",
            ),
            ok=False,
            diagnostic_code="spotify_seed_required",
            requires_follow_up=True,
            status=DesktopResultStatus.AMBIGUOUS,
        )
    outcome = _play_spotify_seed(clean_seed)
    if outcome.ok and outcome.provider == "desktop":
        return replace(
            outcome, message=outcome.message
            + " "
            + _spotify_text(
                "Spotify Desktop will continue the mix using its own autoplay and recommendations.",
                "Spotify Desktop continuara el mix con su reproduccion automatica y recomendaciones.",
            )
        )
    return outcome


def play_mix(seed: str) -> str:
    """Play a seed and build a dynamic AutoMix around it."""
    return _mix_outcome(seed).message


def _control_outcome(action: str) -> SpotifyServiceMutationOutcome:
    clean_action = str(action or "").strip()
    if not clean_action:
        return SpotifyServiceMutationOutcome(
            _spotify_text(
                "Tell me which playback action to perform.",
                "Dime que accion de reproduccion debo realizar.",
            ),
            status=DesktopResultStatus.AMBIGUOUS,
            diagnostic_code="spotify_control_action_required",
        )
    if _spotify_desktop_control_action(clean_action) == "info":
        return _current_track_outcome()
    return _mutation_outcome("control", clean_action)


def control(action: str) -> str:
    """Control Spotify through a cached API session or Spotify Desktop."""
    return _control_outcome(action).message


def _spotify_add_to_queue_desktop_outcome(cancion: str) -> SpotifyServiceMutationOutcome:
    clean_song = str(cancion or "").strip()
    if not clean_song:
        return _queue_outcome(clean_song)
    result = _get_desktop_controller().queue(_spotify_desktop_request(clean_song))
    if result.status is DesktopResultStatus.SUCCESS:
        message = _spotify_text(
            f"Added to Spotify Desktop queue: {_spotify_track_label(result.title, result.artist)}.",
            f"Añadido a la cola en Spotify Desktop: {_spotify_track_label(result.title, result.artist)}.",
        )
    elif result.status is DesktopResultStatus.UNVERIFIED:
        message = _spotify_text(
            f"I sent the queue command for {_spotify_track_label(result.title, result.artist)}, but I could not verify that Spotify added it.",
            f"Envié el comando de cola para {_spotify_track_label(result.title, result.artist)}, pero no pude verificar que Spotify lo añadiera.",
        )
    elif result.status is DesktopResultStatus.AMBIGUOUS:
        choices = "; ".join(_spotify_track_plain_label(item.title, item.artist) for item in result.choices)
        message = _spotify_text(
            f"I found several close matches: {choices}. Which one should I queue?",
            f"Encontré varias coincidencias: {choices}. ¿Cuál debo añadir a la cola?",
        )
    else:
        message = _spotify_text(
            f"Could not add '{clean_song}' to Spotify Desktop queue.",
            f"No se pudo añadir '{clean_song}' a la cola en Spotify Desktop.",
        )
    return _desktop_mutation_outcome(result, message)


def _spotify_add_to_queue_desktop(cancion: str) -> str:
    return _spotify_add_to_queue_desktop_outcome(cancion).message


def _queue_outcome(cancion: str) -> SpotifyServiceMutationOutcome:
    clean_song = str(cancion or "").strip()
    if not clean_song:
        return SpotifyServiceMutationOutcome(
            _spotify_text(
                "Please specify a song to add to the queue.",
                "Por favor indique una canción para añadir a la cola.",
            ),
            status=DesktopResultStatus.AMBIGUOUS,
            diagnostic_code="spotify_queue_query_required",
        )
    return _mutation_outcome("queue", clean_song)


def add_to_queue(cancion: str) -> str:
    """Add a song to the Spotify playback queue."""
    return _queue_outcome(cancion).message


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


def _spotify_library_desktop_outcome(cancion: str, action: str) -> SpotifyServiceMutationOutcome:
    controller = _get_desktop_controller()
    if _spotify_is_current_track_reference(cancion):
        result = controller.control(action)
        if result.status is DesktopResultStatus.SUCCESS and result.title:
            label = _spotify_track_label(result.title, result.artist)
            if action == "like":
                message = _spotify_text(
                    f"Saved {label} to your Liked Songs on Spotify Desktop.",
                    f"Guardé {label} en tus Me Gusta de Spotify Desktop.",
                )
            else:
                message = _spotify_text(
                    f"Removed {label} from your Liked Songs on Spotify Desktop.",
                    f"Eliminé {label} de tus Me Gusta de Spotify Desktop.",
                )
            return _desktop_mutation_outcome(result, message)
        return _spotify_desktop_control_outcome(action, result)

    result = controller.library(_spotify_desktop_request(cancion), action)
    label = _spotify_track_label(result.title, result.artist)
    if result.status is DesktopResultStatus.SUCCESS:
        if action == "like":
            message = _spotify_text(
                f"Saved {label} to your Liked Songs on Spotify Desktop.",
                f"Guardé {label} en tus Me Gusta de Spotify Desktop.",
            )
        else:
            message = _spotify_text(
                f"Removed {label} from your Liked Songs on Spotify Desktop.",
                f"Eliminé {label} de tus Me Gusta de Spotify Desktop.",
            )
    elif result.status is DesktopResultStatus.UNVERIFIED:
        verb_en = "save" if action == "like" else "remove"
        verb_es = "guardar" if action == "like" else "eliminar"
        message = _spotify_text(
            f"I sent the command to {verb_en} {label}, but I could not verify the library change.",
            f"Envié el comando para {verb_es} {label}, pero no pude verificar el cambio en la biblioteca.",
        )
    elif result.status is DesktopResultStatus.AMBIGUOUS:
        choices = "; ".join(_spotify_track_plain_label(item.title, item.artist) for item in result.choices)
        message = _spotify_text(
            f"I found several close matches: {choices}. Repeat the command with the title and artist.",
            f"Encontré varias coincidencias: {choices}. Repite el comando con el título y el artista.",
        )
    elif result.status is DesktopResultStatus.NOT_FOUND:
        message = _spotify_text(
            "I could not find that track in Spotify Desktop.",
            "No pude encontrar esa canción en Spotify Desktop.",
        )
    else:
        message = _spotify_text(
            "Spotify Desktop could not change that track safely without interrupting playback.",
            "Spotify Desktop no pudo cambiar esa canción de forma segura sin interrumpir la reproducción.",
        )
    return _desktop_mutation_outcome(result, message)


def _spotify_library_desktop(cancion: str, action: str) -> str:
    return _spotify_library_desktop_outcome(cancion, action).message


def _spotify_like_desktop_outcome(cancion: str = "") -> SpotifyServiceMutationOutcome:
    return _spotify_library_desktop_outcome(cancion, "like")


def _spotify_like_desktop(cancion: str = "") -> str:
    return _spotify_like_desktop_outcome(cancion).message


def _like_outcome(cancion: str = "") -> SpotifyServiceMutationOutcome:
    return _mutation_outcome("like", cancion)


def like_track(cancion: str = "") -> str:
    """Save the currently playing or specified track to Liked Songs."""
    return _like_outcome(cancion).message


def _spotify_unlike_desktop_outcome(cancion: str = "") -> SpotifyServiceMutationOutcome:
    return _spotify_library_desktop_outcome(cancion, "unlike")


def _spotify_unlike_desktop(cancion: str = "") -> str:
    return _spotify_unlike_desktop_outcome(cancion).message


def _unlike_outcome(cancion: str = "") -> SpotifyServiceMutationOutcome:
    return _mutation_outcome("unlike", cancion)


def unlike_track(cancion: str = "") -> str:
    """Remove the currently playing or specified track from Liked Songs."""
    return _unlike_outcome(cancion).message


def _current_track_outcome() -> SpotifyServiceMutationOutcome:
    api_result = None
    if SPOTIFY_PLAYBACK_MODE != "desktop":
        api_result = _spotify_current_track_api()
        if api_result.ok:
            return SpotifyServiceMutationOutcome(
                api_result.message, status=DesktopResultStatus.SUCCESS, provider="api",
                diagnostic_code="spotify_current_track", verified=True,
            )

    result = _get_desktop_controller().current()
    if result.status in {DesktopResultStatus.SUCCESS, DesktopResultStatus.UNVERIFIED} and result.title:
        title = result.title.replace("Spotify - ", "").replace("Spotify – ", "").strip()
        artist_en = f" by {result.artist}" if result.artist else ""
        artist_es = f" de {result.artist}" if result.artist else ""
        if result.playback_state == "playing":
            message = _spotify_text(
                f"Playing on Spotify Desktop: '{title}'{artist_en}.",
                f"Reproduciendo en Spotify Desktop: '{title}'{artist_es}.",
            )
        elif result.playback_state == "paused":
            message = _spotify_text(
                f"Paused on Spotify Desktop: '{title}'{artist_en}.",
                f"Pausado en Spotify Desktop: '{title}'{artist_es}.",
            )
        else:
            message = _spotify_text(
                f"Current track on Spotify Desktop: '{title}'{artist_en}; playback state could not be verified.",
                f"Canción actual en Spotify Desktop: '{title}'{artist_es}; no pude verificar el estado de reproducción.",
            )
        return SpotifyServiceMutationOutcome(
            message, status=DesktopResultStatus.SUCCESS, provider="desktop",
            diagnostic_code=result.message_key or "spotify_current_track",
            verified=result.status is DesktopResultStatus.SUCCESS,
        )
    if result.status is DesktopResultStatus.NOT_FOUND:
        return _desktop_mutation_outcome(
            result, _spotify_text(
                "No current track could be identified on Spotify Desktop.",
                "No pude identificar una canción actual en Spotify Desktop.",
            ),
        )

    if SPOTIFY_PLAYBACK_MODE == "api" and api_result is not None:
        return _api_mutation_outcome(api_result, "current_track")
    return SpotifyServiceMutationOutcome(
        _spotify_text(
            "No track is currently playing on Spotify.",
            "No hay ninguna canción reproduciéndose actualmente en Spotify.",
        ),
        status=DesktopResultStatus.FAILED if result.status is DesktopResultStatus.UNVERIFIED else result.status,
        provider="desktop",
        diagnostic_code=result.message_key or "spotify_current_track_unavailable",
        choices=result.choices,
    )


def current_track() -> str:
    """Get information about the currently playing track on Spotify."""
    return _current_track_outcome().message
