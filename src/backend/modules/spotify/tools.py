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
