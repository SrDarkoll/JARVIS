"""Compatibility facade for the Spotify first-party module."""

from modules.spotify.tools import (
    controlar_reproduccion,
    reproducir_en_spotify,
    reproducir_mix_spotify,
)

__all__ = [
    "controlar_reproduccion",
    "reproducir_en_spotify",
    "reproducir_mix_spotify",
]
