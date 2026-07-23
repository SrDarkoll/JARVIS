"""Environment-backed Spotify configuration without provider side effects."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from core import jarvis_config


def redirect_error(redirect_uri: str) -> str | None:
    raw_uri = str(redirect_uri or "").strip()
    if not raw_uri:
        return "missing_redirect_uri"
    try:
        parsed = urlsplit(raw_uri)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return "invalid_redirect_uri"
    if parsed.scheme not in {"http", "https"}:
        return "invalid_redirect_scheme"
    if not host or host.lower() == "localhost":
        return "explicit_loopback_ip_required"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "explicit_loopback_ip_required"
    if not address.is_loopback:
        return "explicit_loopback_ip_required"
    if port is None:
        return "redirect_port_required"
    return None


SPOTIFY_CACHE = jarvis_config.SPOTIFY_CACHE
SPOTIFY_CLIENT_ID = jarvis_config.SPOTIPY_CLIENT_ID
SPOTIFY_CLIENT_SECRET = jarvis_config.SPOTIPY_CLIENT_SECRET
SPOTIFY_REDIRECT_URI = jarvis_config.SPOTIPY_REDIRECT_URI
SPOTIFY_SCOPE = (
    "user-modify-playback-state user-read-playback-state "
    "playlist-read-private playlist-modify-private "
    "user-top-read user-read-recently-played"
)
SPOTIFY_REDIRECT_ERROR = redirect_error(SPOTIFY_REDIRECT_URI)
SPOTIFY_ENABLED = bool(
    SPOTIFY_CLIENT_ID
    and SPOTIFY_CLIENT_SECRET
    and SPOTIFY_REDIRECT_ERROR is None
)
SPOTIFY_RADIO_QUEUE_SIZE = 12
SPOTIFY_MODO_SIMILARES = jarvis_config.SPOTIFY_MODO_SIMILARES
SPOTIFY_AUTO_SHUFFLE = jarvis_config.SPOTIFY_AUTO_SHUFFLE
SPOTIFY_EXTENDED_QUOTA_MODE = jarvis_config.SPOTIFY_EXTENDED_QUOTA_MODE
SPOTIFY_AUTOMIX_PLAYLIST_NAME = (
    jarvis_config.SPOTIFY_AUTOMIX_PLAYLIST_NAME or "JARVIS AutoMix"
)
SPOTIFY_PLAYBACK_MODE = jarvis_config.SPOTIFY_PLAYBACK_MODE


# Compatibility with the previous private helper name.
_spotify_redirect_error = redirect_error
