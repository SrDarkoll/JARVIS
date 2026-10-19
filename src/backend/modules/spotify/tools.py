"""LangChain tool adapters for the Spotify service."""

from core.command_pipeline.models import ReceiptStatus, ToolExecutionResult
from langchain_core.tools import tool

from modules.spotify import service
from modules.spotify.desktop import DesktopResultStatus


def _outcome_metadata(
    outcome: service.SpotifyServicePlaybackOutcome | service.SpotifyServiceMutationOutcome,
) -> dict:
    return {
        "provider": outcome.provider,
        "status": outcome.status.value if outcome.status is not None else "",
        "requires_follow_up": outcome.requires_follow_up,
        "choices": [
            {"title": choice.title, "artist": choice.artist, "kind": choice.kind} for choice in outcome.choices
        ],
    }


def _mutation_result(outcome: service.SpotifyServiceMutationOutcome) -> ToolExecutionResult:
    status = {
        DesktopResultStatus.SUCCESS: ReceiptStatus.SUCCEEDED,
        DesktopResultStatus.UNVERIFIED: ReceiptStatus.ACCEPTED,
        DesktopResultStatus.AMBIGUOUS: ReceiptStatus.PARTIAL,
        DesktopResultStatus.UNAVAILABLE: ReceiptStatus.UNAVAILABLE,
    }.get(outcome.status, ReceiptStatus.FAILED)
    return ToolExecutionResult(
        result=_outcome_metadata(outcome), user_message=outcome.message,
        status=status, diagnostic_code=outcome.diagnostic_code,
        verified=outcome.verified, accepted=outcome.accepted,
    )


def _playback_result(outcome: service.SpotifyServicePlaybackOutcome) -> ToolExecutionResult:
    if outcome.accepted:
        status = ReceiptStatus.ACCEPTED
    elif outcome.ok:
        status = ReceiptStatus.SUCCEEDED
    elif outcome.requires_follow_up:
        status = ReceiptStatus.PARTIAL
    elif outcome.status is DesktopResultStatus.UNAVAILABLE or outcome.diagnostic_code in {
        "spotify_start_timeout", "spotify_desktop_unavailable", "spotify_client_not_found", "spotify_uia_unavailable",
    }:
        status = ReceiptStatus.UNAVAILABLE
    else:
        status = ReceiptStatus.FAILED
    return ToolExecutionResult(
        result=_outcome_metadata(outcome), user_message=outcome.message,
        status=status, diagnostic_code=outcome.diagnostic_code,
        verified=outcome.ok and outcome.verified,
        accepted=outcome.accepted,
    )


@tool
def reproducir_en_spotify(cancion: str) -> ToolExecutionResult:
    """Play music using a cached Spotify API session or Spotify Desktop."""
    return _playback_result(service._play_outcome(cancion))


@tool
def reproducir_mix_spotify(semilla: str) -> ToolExecutionResult:
    """Play a seed and build a dynamic AutoMix around it."""
    return _playback_result(service._mix_outcome(semilla))


@tool
def controlar_reproduccion(accion: str) -> ToolExecutionResult:
    """Control Spotify through a cached API session or Spotify Desktop."""
    return _mutation_result(service._control_outcome(accion))


@tool
def agregar_a_cola_spotify(cancion: str) -> ToolExecutionResult:
    """Añade una canción a la cola de reproducción de Spotify sin interrumpir lo que suena actualmente."""
    return _mutation_result(service._queue_outcome(cancion))


@tool
def dar_like_spotify(cancion: str = "") -> ToolExecutionResult:
    """Guarda la canción que está sonando actualmente (o una específica) en tus 'Me Gusta' de Spotify."""
    return _mutation_result(service._like_outcome(cancion))


@tool
def quitar_like_spotify(cancion: str = "") -> ToolExecutionResult:
    """Elimina la canción actual (o una específica) de tus 'Me Gusta' de Spotify."""
    return _mutation_result(service._unlike_outcome(cancion))


@tool
def cancion_actual_spotify() -> str:
    """Obtiene el nombre, artista, álbum, progreso y estado de la canción que está sonando en Spotify."""
    return service.current_track()
