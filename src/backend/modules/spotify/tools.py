"""LangChain tool adapters for the Spotify service."""

from langchain_core.tools import tool

from modules.spotify import service


@tool
def reproducir_en_spotify(cancion: str) -> str:
    """Play music using a cached Spotify API session or Spotify Desktop."""
    return service.play(cancion)


@tool
def reproducir_mix_spotify(semilla: str) -> str:
    """Play a seed and build a dynamic AutoMix around it."""
    return service.play_mix(semilla)


@tool
def controlar_reproduccion(accion: str) -> str:
    """Control Spotify through a cached API session or Spotify Desktop."""
    return service.control(accion)


@tool
def agregar_a_cola_spotify(cancion: str) -> str:
    """Añade una canción a la cola de reproducción de Spotify sin interrumpir lo que suena actualmente."""
    return service.add_to_queue(cancion)


@tool
def dar_like_spotify(cancion: str = "") -> str:
    """Guarda la canción que está sonando actualmente (o una específica) en tus 'Me Gusta' de Spotify."""
    return service.like_track(cancion)


@tool
def quitar_like_spotify(cancion: str = "") -> str:
    """Elimina la canción actual (o una específica) de tus 'Me Gusta' de Spotify."""
    return service.unlike_track(cancion)


@tool
def cancion_actual_spotify() -> str:
    """Obtiene el nombre, artista, álbum, progreso y estado de la canción que está sonando en Spotify."""
    return service.current_track()
