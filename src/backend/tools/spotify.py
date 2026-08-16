"""Compatibility facade for the Spotify first-party module."""

from modules.spotify.tools import (
    agregar_a_cola_spotify,
    cancion_actual_spotify,
    controlar_reproduccion,
    dar_like_spotify,
    quitar_like_spotify,
    reproducir_en_spotify,
    reproducir_mix_spotify,
)

__all__ = [
    "agregar_a_cola_spotify",
    "cancion_actual_spotify",
    "controlar_reproduccion",
    "dar_like_spotify",
    "quitar_like_spotify",
    "reproducir_en_spotify",
    "reproducir_mix_spotify",
]
