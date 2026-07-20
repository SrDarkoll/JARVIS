"""Integración completa de Spotify: autenticación, búsqueda, reproducción, cola, AutoMix, similares."""

import ipaddress
import os
import re
import threading
import time as _time
from dataclasses import dataclass
from urllib.parse import urlsplit

import spotipy
from core import jarvis_config, jarvis_state
from langchain_core.tools import tool
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth
from utils.jarvis_i18n import get_current_language

from tools._common import _open_url_or_app
from tools.spotify_desktop import (
    DesktopResultStatus,
    SpotifyDesktopResult,
    SpotifyRequest,
    build_windows_controller,
)
from tools.spotify_desktop.followup import pending_spotify_selections
from tools.spotify_desktop.matching import normalize_text


# ─────────────────────────────────────────
# Configuración y autenticación
# ─────────────────────────────────────────
def _spotify_log_error(operation: str, error: BaseException) -> None:
    print(f"  [SPOTIFY] {operation} failed ({type(error).__name__}).")


def _spotify_redirect_error(redirect_uri: str) -> str | None:
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
print(f"  [SPOTIFY] Cache: {SPOTIFY_CACHE}")

SPOTIFY_CLIENT_ID = jarvis_config.SPOTIPY_CLIENT_ID
SPOTIFY_CLIENT_SECRET = jarvis_config.SPOTIPY_CLIENT_SECRET
SPOTIFY_REDIRECT_URI = jarvis_config.SPOTIPY_REDIRECT_URI
SPOTIFY_SCOPE = (
    "user-modify-playback-state user-read-playback-state "
    "playlist-read-private playlist-modify-private "
    "user-top-read user-read-recently-played"
)

SPOTIFY_REDIRECT_ERROR = _spotify_redirect_error(SPOTIFY_REDIRECT_URI)
SPOTIFY_ENABLED = bool(
    SPOTIFY_CLIENT_ID
    and SPOTIFY_CLIENT_SECRET
    and SPOTIFY_REDIRECT_ERROR is None
)

if SPOTIFY_ENABLED:
    sp = spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope=SPOTIFY_SCOPE,
            cache_handler=CacheFileHandler(cache_path=SPOTIFY_CACHE),
        )
    )
else:
    sp = None
    if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET and SPOTIFY_REDIRECT_ERROR:
        print("  [SPOTIFY] Invalid redirect URI. Use an explicit loopback IP.")
    else:
        print(
            "  [SPOTIFY] Web API credentials are not configured; Windows desktop fallback remains available."
        )

SPOTIFY_RADIO_QUEUE_SIZE = 12
SPOTIFY_MODO_SIMILARES = jarvis_config.SPOTIFY_MODO_SIMILARES
SPOTIFY_AUTO_SHUFFLE = jarvis_config.SPOTIFY_AUTO_SHUFFLE
SPOTIFY_EXTENDED_QUOTA_MODE = jarvis_config.SPOTIFY_EXTENDED_QUOTA_MODE
SPOTIFY_AUTOMIX_PLAYLIST_NAME = jarvis_config.SPOTIFY_AUTOMIX_PLAYLIST_NAME or "JARVIS AutoMix"
SPOTIFY_PLAYBACK_MODE = jarvis_config.SPOTIFY_PLAYBACK_MODE
_ULTIMA_CANCION_SOLICITADA = ""
_SPOTIFY_USER_COUNTRY = ""
_desktop_controller = None
_desktop_controller_lock = threading.Lock()
_spotify_api_capability_failed = False


@dataclass(frozen=True)
class SpotifyAPIPlaybackResult:
    ok: bool
    message: str
    capability_failure: bool = False


def _spotify_is_english() -> bool:
    return get_current_language().startswith("en")


def _spotify_text(en: str, es: str) -> str:
    return en if _spotify_is_english() else es


def _spotify_track_label(track_name: str, artist: str) -> str:
    if not artist:
        return f"'{track_name}'"
    connector = "by" if _spotify_is_english() else "de"
    return f"'{track_name}' {connector} {artist}"


def _spotify_track_plain_label(track_name: str, artist: str) -> str:
    if not artist:
        return track_name
    connector = "by" if _spotify_is_english() else "de"
    return f"{track_name} {connector} {artist}"


def _spotify_playback_success_message(
    track_name: str,
    artist: str,
    similar_count: int = 0,
    automix_ok: bool = False,
) -> str:
    label = _spotify_track_label(track_name, artist)
    if similar_count:
        if automix_ok:
            return _spotify_text(
                f"Playing {label}. AutoMix updated with {similar_count} similar tracks.",
                f"Reproduciendo {label}. AutoMix actualizado con {similar_count} similares.",
            )
        return _spotify_text(
            f"Playing {label}. Queue loaded with {similar_count} similar tracks.",
            f"Reproduciendo {label}. Cola con {similar_count} similares.",
        )
    return _spotify_text(f"Playing {label}.", f"Reproduciendo {label}.")


# ─────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────
def _spotify_ready() -> tuple[bool, str | None]:
    if sp is None:
        if SPOTIFY_REDIRECT_ERROR:
            return (
                False,
                _spotify_text(
                    "Spotify redirect configuration is invalid. Use an explicit loopback IP such as 127.0.0.1.",
                    "La redireccion de Spotify no es valida. Use una IP loopback explicita como 127.0.0.1.",
                ),
            )
        return (
            False,
            _spotify_text(
                "Spotify is not configured. Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET in .env.",
                "Spotify no está configurado. Defina SPOTIPY_CLIENT_ID y SPOTIPY_CLIENT_SECRET en .env.",
            ),
        )
    return True, None


def _spotify_chunks(items: list[str], size: int = 100):
    for i in range(0, len(items), max(1, size)):
        yield items[i : i + max(1, size)]


def _spotify_usuario_actual_id() -> str | None:
    ready, _ = _spotify_ready()
    if not ready:
        return None
    try:
        me = sp.current_user()
        return me.get("id")
    except Exception as error:
        _spotify_log_error("current_user", error)
        return None


def _spotify_market_objetivo() -> str | None:
    global _SPOTIFY_USER_COUNTRY
    ready, _ = _spotify_ready()
    if not ready:
        return None
    if _SPOTIFY_USER_COUNTRY:
        return _SPOTIFY_USER_COUNTRY
    try:
        me = sp.current_user() or {}
        country = (me.get("country") or "").strip().upper()
        if len(country) == 2:
            _SPOTIFY_USER_COUNTRY = country
            return country
    except Exception as error:
        _spotify_log_error("current_user_country", error)
    env_market = (os.getenv("SPOTIFY_MARKET") or "").strip().upper()
    return env_market if len(env_market) == 2 else None


def _spotify_track_available_en_mercado(track: dict) -> bool:
    if not isinstance(track, dict):
        return False
    if track.get("is_playable") is False:
        return False
    reason = ((track.get("restrictions") or {}).get("reason") or "").lower()
    if reason in {"market", "product"}:
        return False
    market = _spotify_market_objetivo()
    markets = track.get("available_markets")
    if market and isinstance(markets, list) and markets and market not in markets:
        return False
    return True


def _spotify_items_search(payload: dict, item_type: str = "track") -> list:
    if not isinstance(payload, dict):
        return []
    root_items = payload.get("items")
    if isinstance(root_items, list):
        return root_items
    node = payload.get(f"{item_type}s")
    if isinstance(node, dict):
        nested_items = node.get("items")
        if isinstance(nested_items, list):
            return nested_items
    tracks_node = payload.get("tracks")
    if isinstance(tracks_node, dict):
        nested_items = tracks_node.get("items")
        if isinstance(nested_items, list):
            return nested_items
    return []


def _spotify_search_items(q: str, item_type: str = "track", limit: int = 10) -> list:
    ready, _ = _spotify_ready()
    if not ready:
        return []
    market = _spotify_market_objetivo()
    total_limit = max(1, min(int(limit), 50))
    found = []
    offset = 0
    try:
        while len(found) < total_limit:
            page_limit = min(10, total_limit - len(found))
            kwargs = {"q": q, "limit": page_limit, "type": item_type}
            if market and item_type == "track":
                kwargs["market"] = market
            if offset:
                kwargs["offset"] = offset
            try:
                payload = sp.search(**kwargs)
            except TypeError:
                kwargs.pop("market", None)
                kwargs.pop("offset", None)
                payload = sp.search(**kwargs)
            items = _spotify_items_search(payload, item_type=item_type)
            if not items:
                break
            found.extend(items)
            if len(items) < page_limit:
                break
            offset += len(items)
        return found[:total_limit]
    except Exception as error:
        _spotify_log_error("search", error)
        return []


def _spotify_search_tracks(q: str, limit: int = 10) -> list:
    return _spotify_search_items(q=q, item_type="track", limit=limit)


def _spotify_album_tracks(album_id: str, limit: int = 50) -> list:
    ready, _ = _spotify_ready()
    if not ready:
        return []
    market = _spotify_market_objetivo()
    try:
        if market:
            return (sp.album_tracks(album_id, limit=limit, market=market) or {}).get(
                "items", []
            )
        return (sp.album_tracks(album_id, limit=limit) or {}).get("items", [])
    except TypeError:
        try:
            return (sp.album_tracks(album_id, limit=limit) or {}).get("items", [])
        except Exception as error:
            _spotify_log_error("album_tracks", error)
            return []
    except Exception as error:
        _spotify_log_error("album_tracks", error)
        return []


def _spotify_access_token() -> str | None:
    ready, _ = _spotify_ready()
    if not ready:
        return None
    auth_manager = getattr(sp, "auth_manager", None)
    if auth_manager is None:
        return None

    def _extract_token(token_data) -> str | None:
        if isinstance(token_data, str) and token_data.strip():
            return token_data.strip()
        if isinstance(token_data, dict):
            token = token_data.get("access_token")
            if isinstance(token, str) and token.strip():
                return token.strip()
        return None

    cache_handler = getattr(auth_manager, "cache_handler", None)
    if cache_handler is not None:
        try:
            token_info = cache_handler.get_cached_token()
            validator = getattr(auth_manager, "validate_token", None)
            if callable(validator):
                token_info = validator(token_info)
            token = _extract_token(token_info)
            if token:
                return token
        except Exception as error:
            _spotify_log_error("cache_handler.get_cached_token", error)

    try:
        # Spotipy 2.26 warns for the legacy as_dict=True return shape.
        return _extract_token(
            auth_manager.get_access_token(as_dict=False, check_cache=False)
        )
    except Exception as error:
        _spotify_log_error("get_access_token", error)

    return None


def _spotify_has_valid_cached_token() -> bool:
    """Check cached OAuth state without starting an authorization flow."""
    if sp is None:
        return False
    manager = getattr(sp, "auth_manager", None)
    handler = getattr(manager, "cache_handler", None)
    if manager is None or handler is None:
        return False
    try:
        token = handler.get_cached_token()
        return bool(token and manager.validate_token(token))
    except Exception as error:
        _spotify_log_error("cached_token_probe", error)
        return False


def _spotify_dispositivo_objetivo():
    """Devuelve el device_id activo o el primero available."""
    ready, _ = _spotify_ready()
    if not ready:
        return None
    try:
        devices = sp.devices().get("devices", [])
        if not devices:
            return None
        activo = next((d for d in devices if d.get("is_active")), None)
        return (activo or devices[0]).get("id")
    except Exception:
        return None


def _spotify_track_actual_id() -> str | None:
    ready, _ = _spotify_ready()
    if not ready:
        return None
    try:
        pb = sp.current_playback() or {}
        item = pb.get("item") or {}
        return item.get("id")
    except Exception:
        return None


def _spotify_mejor_track(
    cancion: str, tracks: list, track_hint: str = "", artist_hint: str = ""
):
    if not tracks:
        return None
    if len(tracks) == 1:
        return tracks[0] if _spotify_track_available_en_mercado(tracks[0]) else None

    tracks_filtrados = [t for t in tracks if _spotify_track_available_en_mercado(t)]
    candidatos = tracks_filtrados if tracks_filtrados else tracks

    th = track_hint or _PREFIJOS_SPOTIFY.sub("", cancion).lower().strip()
    ah = artist_hint

    mejor = candidatos[0]
    mejor_score = -999

    for t in candidatos:
        nombre = (t.get("name") or "").lower()
        artistas = " ".join((a.get("name") or "").lower() for a in t.get("artists", []))
        score = 0

        if th == nombre:
            score += 10
        elif th in nombre:
            score += 5
        elif nombre in th:
            score += 3

        if ah:
            if ah == artistas or ah in artistas:
                score += 8
            elif any(ah in a.get("name", "").lower() for a in t.get("artists", [])):
                score += 6
        else:
            score += int((t.get("popularity") or 0) / 20)

        if score > mejor_score:
            mejor = t
            mejor_score = score

    return mejor


# ─────────────────────────────────────────
# Playlist AutoMix
# ─────────────────────────────────────────
def _spotify_crear_playlist_me(
    name: str, public: bool = False, collaborative: bool = False, description: str = ""
) -> dict | None:
    ready, _ = _spotify_ready()
    if not ready:
        return None
    try:
        return sp.current_user_playlist_create(
            name=name,
            public=bool(public),
            collaborative=bool(collaborative),
            description=description or "",
        )
    except Exception as error:
        _spotify_log_error("current_user_playlist_create", error)
        return None


def _spotify_buscar_o_crear_playlist_automix(user_id: str) -> dict | None:
    ready, _ = _spotify_ready()
    if not ready:
        return None
    try:
        offset = 0
        while True:
            page = sp.current_user_playlists(limit=50, offset=offset) or {}
            items = page.get("items", []) or []
            for p in items:
                owner_id = ((p.get("owner") or {}).get("id") or "").strip()
                name = (p.get("name") or "").strip()
                accessible = owner_id == user_id or p.get("collaborative") is True
                if (
                    accessible
                    and name.lower() == SPOTIFY_AUTOMIX_PLAYLIST_NAME.lower()
                ):
                    return p
            if not page.get("next"):
                break
            offset += len(items)
            if len(items) == 0:
                break
    except Exception as error:
        _spotify_log_error("current_user_playlists", error)

    descripcion = "Playlist temporal de mezcla automatica de JARVIS."
    return _spotify_crear_playlist_me(
        name=SPOTIFY_AUTOMIX_PLAYLIST_NAME,
        public=False,
        collaborative=False,
        description=descripcion,
    )


def _spotify_playlist_item_track(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    for key in ("item", "track"):
        track = item.get(key)
        if not isinstance(track, dict):
            continue
        if track.get("type") not in (None, "track"):
            continue
        return track
    return None


def _spotify_obtener_tracks_playlist(
    playlist_id: str, max_items: int = 300
) -> list[dict]:
    ready, _ = _spotify_ready()
    if not ready:
        return []
    try:
        tracks = []
        offset = 0
        max_items = max(1, int(max_items))
        while len(tracks) < max_items:
            page = (
                sp.playlist_items(
                    playlist_id=playlist_id,
                    limit=min(100, max_items - len(tracks)),
                    offset=offset,
                    additional_types=("track",),
                )
                or {}
            )
            items = page.get("items", [])
            if not isinstance(items, list) or not items:
                break
            for item in items:
                track = _spotify_playlist_item_track(item)
                if track and track.get("uri"):
                    tracks.append(track)
                    if len(tracks) >= max_items:
                        break
            if not page.get("next"):
                break
            offset += len(items)
        return tracks
    except Exception as error:
        _spotify_log_error("playlist_items", error)
        return []


def _spotify_obtener_uris_playlist(
    playlist_id: str, max_items: int = 300
) -> list[str]:
    return [
        track["uri"]
        for track in _spotify_obtener_tracks_playlist(playlist_id, max_items=max_items)
    ]


def _spotify_reemplazar_playlist_con_uris(playlist_id: str, uris: list[str]) -> bool:
    ready, _ = _spotify_ready()
    if not ready:
        return False
    try:
        uris_limpias = []
        vistos = set()
        for u in uris:
            if not u or u in vistos:
                continue
            vistos.add(u)
            uris_limpias.append(u)
        if not uris_limpias:
            return False

        actual_uris = _spotify_obtener_uris_playlist(
            playlist_id, max_items=len(uris_limpias) + 5
        )
        if actual_uris and actual_uris == uris_limpias:
            print("  [SPOTIFY] AutoMix ya estaba sincronizada.")
            return True

        partes = list(_spotify_chunks(uris_limpias, 100))
        sp.playlist_replace_items(playlist_id, partes[0])
        for parte in partes[1:]:
            sp.playlist_add_items(playlist_id, parte)
        _time.sleep(0.35)
        return True
    except Exception as error:
        _spotify_log_error("replace_playlist_items", error)
        return False


def _spotify_start_playlist_context(
    playlist_id: str,
    device_id: str | None,
    offset_uri: str | None = None,
    offset_position: int | None = None,
) -> bool:
    ready, _ = _spotify_ready()
    if not ready:
        return False
    try:
        kwargs = {"context_uri": f"spotify:playlist:{playlist_id}"}
        if offset_position is not None:
            kwargs["offset"] = {"position": int(offset_position)}
        elif offset_uri:
            kwargs["offset"] = {"uri": offset_uri}

        if device_id:
            sp.start_playback(device_id=device_id, **kwargs)
        else:
            sp.start_playback(**kwargs)
        _time.sleep(0.5)
        return True
    except Exception as error:
        _spotify_log_error("start_playlist_context", error)
        return False


# ─────────────────────────────────────────
# Similares (radio)
# ─────────────────────────────────────────
def _spotify_current_user_top_tracks(limit: int = 10) -> list[dict]:
    if sp is None or not hasattr(sp, "current_user_top_tracks"):
        return []
    tracks = []
    for time_range in ("short_term", "medium_term"):
        try:
            page = sp.current_user_top_tracks(
                limit=max(1, min(int(limit), 10)),
                time_range=time_range,
            ) or {}
            for item in page.get("items", []) or []:
                if isinstance(item, dict):
                    tracks.append(item)
        except Exception as error:
            _spotify_log_error("current_user_top_tracks", error)
            break
        if tracks:
            break
    return tracks


def _spotify_recently_played_tracks(limit: int = 10) -> list[dict]:
    if sp is None or not hasattr(sp, "current_user_recently_played"):
        return []
    try:
        page = sp.current_user_recently_played(limit=max(1, min(int(limit), 10))) or {}
        tracks = []
        for item in page.get("items", []) or []:
            track = (item or {}).get("track")
            if isinstance(track, dict):
                tracks.append(track)
        return tracks
    except Exception as error:
        _spotify_log_error("current_user_recently_played", error)
        return []


def _spotify_artist_genres(artist_ids: list[str]) -> list[str]:
    genres = []
    if sp is None or not hasattr(sp, "artist"):
        return genres
    for artist_id in artist_ids[:3]:
        if not artist_id:
            continue
        try:
            artist = sp.artist(artist_id) or {}
            for genre in artist.get("genres", []) or []:
                genre = str(genre or "").strip().lower()
                if genre and genre not in genres:
                    genres.append(genre)
        except Exception as error:
            _spotify_log_error("artist_genres", error)
    return genres


def _spotify_current_user_top_artists(limit: int = 8) -> list[dict]:
    if sp is None or not hasattr(sp, "current_user_top_artists"):
        return []
    try:
        page = sp.current_user_top_artists(
            limit=max(1, min(int(limit), 10)),
            time_range="medium_term",
        ) or {}
        return [
            artist
            for artist in page.get("items", []) or []
            if isinstance(artist, dict)
        ]
    except Exception as error:
        _spotify_log_error("current_user_top_artists", error)
        return []


def _spotify_user_top_genres(artists: list[dict]) -> list[str]:
    genres = []
    for artist in artists:
        for genre in artist.get("genres", []) or []:
            genre = str(genre or "").strip().lower()
            if genre and genre not in genres:
                genres.append(genre)
    return genres


def _spotify_audio_feature_queries(track_id: str | None) -> list[str]:
    if (
        not SPOTIFY_EXTENDED_QUOTA_MODE
        or not track_id
        or sp is None
        or not hasattr(sp, "audio_features")
    ):
        return []
    try:
        features = (sp.audio_features([track_id]) or [None])[0] or {}
    except Exception as error:
        _spotify_log_error("audio_features", error)
        return []
    queries = []
    danceability = float(features.get("danceability") or 0)
    energy = float(features.get("energy") or 0)
    valence = float(features.get("valence") or 0)
    acousticness = float(features.get("acousticness") or 0)
    if danceability >= 0.65:
        queries.append("dance mix")
    if energy >= 0.70:
        queries.append("high energy mix")
    if valence <= 0.35:
        queries.append("moody chill")
    if acousticness >= 0.55:
        queries.append("acoustic mix")
    return queries[:3]


def _spotify_automix_context_tracks(limit: int) -> list[dict]:
    if sp is None or not hasattr(sp, "current_user_playlists"):
        return []
    user_id = _spotify_usuario_actual_id()
    if not user_id:
        return []
    try:
        offset = 0
        while True:
            page = sp.current_user_playlists(limit=50, offset=offset) or {}
            items = page.get("items", []) or []
            for playlist in items:
                if not isinstance(playlist, dict):
                    continue
                if (
                    str(playlist.get("name") or "").strip().lower()
                    != SPOTIFY_AUTOMIX_PLAYLIST_NAME.lower()
                ):
                    continue
                owner_id = (
                    (playlist.get("owner") or {}).get("id") or ""
                ).strip()
                if owner_id != user_id and playlist.get("collaborative") is not True:
                    continue
                playlist_id = playlist.get("id")
                if playlist_id:
                    return _spotify_obtener_tracks_playlist(
                        playlist_id,
                        max_items=max(1, min(int(limit), 100)),
                    )
            if not page.get("next") or not items:
                break
            offset += len(items)
    except Exception as error:
        _spotify_log_error("automix_context", error)
    return []


def _spotify_dynamic_mix_candidates(track: dict, limit: int) -> list[dict]:
    limit = max(1, min(int(limit), 30))
    seed_artists = track.get("artists") or []
    seed_artist_names = [a.get("name") for a in seed_artists if a.get("name")]
    seed_artist_ids = [a.get("id") for a in seed_artists if a.get("id")]
    seed_name = str(track.get("name") or "").strip()
    top_artists = _spotify_current_user_top_artists(limit=min(limit, 10))

    candidates = []
    candidates.extend(_spotify_current_user_top_tracks(limit=limit))
    candidates.extend(_spotify_recently_played_tracks(limit=limit))
    candidates.extend(_spotify_automix_context_tracks(limit=limit))

    genres = []
    for genre in (
        _spotify_artist_genres(seed_artist_ids)
        + _spotify_user_top_genres(top_artists)
    ):
        if genre and genre not in genres:
            genres.append(genre)

    queries = [f'genre:"{genre}"' for genre in genres[:4]]
    queries.extend(f'artist:"{name}"' for name in seed_artist_names[:2])
    queries.extend(
        f'artist:"{artist["name"]}"'
        for artist in top_artists[:3]
        if artist.get("name")
    )
    if seed_name:
        artist_query = (
            f' artist:"{seed_artist_names[0]}"' if seed_artist_names else ""
        )
        queries.append(f'track:"{seed_name}"{artist_query}')
        queries.append(
            " ".join([seed_name, *seed_artist_names[:1]]).strip()
        )

    seen_queries = set()
    for query in queries:
        normalized = query.strip().lower()
        if not normalized or normalized in seen_queries:
            continue
        seen_queries.add(normalized)
        candidates.extend(
            _spotify_search_tracks(q=query, limit=min(limit, 10))
        )
        if len(candidates) >= limit * 4:
            break
    return candidates


def _spotify_extended_quota_candidates(track: dict, limit: int) -> list[dict]:
    if not SPOTIFY_EXTENDED_QUOTA_MODE or sp is None:
        return []

    limit = max(1, min(int(limit), 30))
    track_id = track.get("id")
    artist_ids = [
        artist.get("id")
        for artist in track.get("artists") or []
        if artist.get("id")
    ]
    candidates = []

    if hasattr(sp, "recommendations") and (track_id or artist_ids):
        kwargs = {"limit": limit}
        if track_id:
            kwargs["seed_tracks"] = [track_id]
        if artist_ids:
            kwargs["seed_artists"] = artist_ids[:2]
        try:
            candidates.extend((sp.recommendations(**kwargs) or {}).get("tracks", []))
        except Exception as error:
            _spotify_log_error("recommendations", error)

    for query in _spotify_audio_feature_queries(track_id):
        candidates.extend(
            _spotify_search_tracks(q=query, limit=min(limit, 10))
        )

    def _artist_top_tracks(artist_id: str) -> list[dict]:
        if not hasattr(sp, "artist_top_tracks"):
            return []
        market = _spotify_market_objetivo()
        try:
            if market:
                return (sp.artist_top_tracks(artist_id, country=market) or {}).get(
                    "tracks", []
                )
            return (sp.artist_top_tracks(artist_id) or {}).get("tracks", [])
        except TypeError:
            try:
                return (sp.artist_top_tracks(artist_id) or {}).get("tracks", [])
            except Exception as error:
                _spotify_log_error("artist_top_tracks", error)
                return []
        except Exception as error:
            _spotify_log_error("artist_top_tracks", error)
            return []

    for artist_id in artist_ids[:2]:
        candidates.extend(_artist_top_tracks(artist_id))

    if hasattr(sp, "artist_related_artists"):
        for artist_id in artist_ids[:2]:
            try:
                related = sp.artist_related_artists(artist_id) or {}
                for artist in (related.get("artists", []) or [])[:4]:
                    related_id = artist.get("id")
                    if related_id:
                        candidates.extend(_artist_top_tracks(related_id))
            except Exception as error:
                _spotify_log_error("artist_related_artists", error)

    return candidates


def _spotify_obtener_similares(track: dict, limite: int = SPOTIFY_RADIO_QUEUE_SIZE):
    limite = max(1, min(int(limite), 30))
    vistos_ids = {track.get("id")}
    vistos_uris = {track.get("uri")}
    similares = []

    track_id = track.get("id")
    track_uri = track.get("uri")
    seed_name = (track.get("name") or "").lower()
    seed_artists = track.get("artists") or []
    seed_artist_names = [a.get("name") for a in seed_artists if a.get("name")]

    print(
        f"  [SIMILARES] Construyendo mix contextual para: '{seed_name}' | Artistas: {seed_artist_names}"
    )

    def _agregar_candidatos(candidatos: list[dict], max_items: int | None = None) -> int:
        agregados = 0
        for t in candidatos or []:
            if len(similares) >= limite:
                break
            tid = t.get("id")
            uri = t.get("uri")
            if (tid and tid in vistos_ids) or (uri and uri in vistos_uris):
                continue
            if track_id and tid == track_id:
                continue
            if track_uri and uri == track_uri:
                continue
            if not uri or not _spotify_track_available_en_mercado(t):
                continue
            vistos_ids.add(tid)
            vistos_uris.add(uri)
            similares.append(t)
            agregados += 1
            if max_items is not None and agregados >= max_items:
                break
        return agregados

    album_id = ((track.get("album") or {}).get("id") or "").strip()
    if album_id and len(similares) < limite:
        album_tracks = _spotify_album_tracks(album_id, limit=50)
        seed_index = next(
            (
                i
                for i, item in enumerate(album_tracks)
                if item.get("id") == track_id or item.get("uri") == track_uri
            ),
            -1,
        )
        if seed_index >= 0:
            album_tracks = album_tracks[seed_index + 1 :] + album_tracks[:seed_index]
        _agregar_candidatos(album_tracks, max_items=max(1, min(4, limite)))

    if len(similares) < limite:
        dynamic_candidates = _spotify_dynamic_mix_candidates(track, limit=limite)
        _agregar_candidatos(dynamic_candidates, max_items=limite - len(similares))

    if len(similares) < limite and SPOTIFY_EXTENDED_QUOTA_MODE:
        extended_candidates = _spotify_extended_quota_candidates(track, limit=limite)
        _agregar_candidatos(
            extended_candidates,
            max_items=limite - len(similares),
        )

    if len(similares) < limite:
        for artist_name in seed_artist_names[:2]:
            q = f'artist:"{artist_name}"'
            _agregar_candidatos(_spotify_search_tracks(q=q, limit=limite * 2))
            if len(similares) >= limite:
                break

    artistas_result = list(
        {
            a.get("name")
            for t in similares
            for a in (t.get("artists") or [])
            if a.get("name")
        }
    )
    print(
        f"  [SIMILARES] Encontrados: {len(similares)} tracks | Artistas en mix: {artistas_result[:5]}"
    )
    return similares


# ─────────────────────────────────────────
# Playback helpers
# ─────────────────────────────────────────
def _spotify_start_playlist_like(uris: list[str], device_id: str | None) -> bool:
    ready, _ = _spotify_ready()
    if not ready:
        return False
    try:
        if not uris:
            return False
        if device_id:
            sp.start_playback(device_id=device_id, uris=uris)
        else:
            sp.start_playback(uris=uris)
        _time.sleep(0.5)
        return True
    except Exception as error:
        _spotify_log_error("start_playlist_like", error)
        return False


def _spotify_start_track(track_uri: str, device_id: str | None):
    ready, _ = _spotify_ready()
    if not ready:
        raise RuntimeError("spotify_not_configured")
    if device_id:
        sp.start_playback(device_id=device_id, uris=[track_uri])
    else:
        sp.start_playback(uris=[track_uri])


def _spotify_esperar_track(
    track_id: str, intentos: int = 4, delay: float = 0.35
) -> bool:
    for _ in range(max(1, intentos)):
        actual = _spotify_track_actual_id()
        if actual == track_id:
            return True
        _time.sleep(max(0.1, float(delay)))
    return False


def _spotify_iniciar_determinista(
    track_uri: str, track_id: str, lista_uris: list[str], device_id: str | None
) -> bool:
    if not track_uri or not track_id:
        return False
    lista = [u for u in lista_uris if u]
    if not lista:
        lista = [track_uri]

    # Si shuffle está activo y modo automix, usar playlist como contexto (shuffle-compatible)
    # La playlist puede estar desactualizada, pero shuffle requiere contexto de playlist
    if SPOTIFY_AUTO_SHUFFLE:
        user_id = _spotify_usuario_actual_id()
        if user_id:
            automix = _spotify_buscar_o_crear_playlist_automix(user_id)
            if automix and automix.get("id"):
                if _spotify_start_playlist_context(automix["id"], device_id, offset_uri=track_uri):
                    if _spotify_esperar_track(track_id, intentos=5, delay=0.30):
                        return True

    if _spotify_start_playlist_like(lista, device_id) or _spotify_start_playlist_like(
        lista, None
    ):
        if _spotify_esperar_track(track_id, intentos=5, delay=0.30):
            return True

    try:
        _spotify_start_track(track_uri, device_id)
    except Exception:
        try:
            _spotify_start_track(track_uri, None)
        except Exception:
            return False

    if not _spotify_esperar_track(track_id, intentos=5, delay=0.30):
        return False

    extras = [u for u in lista[1:] if u and u != track_uri]
    if extras:
        encoladas = _spotify_queue_tracks(extras, device_id)
        if encoladas < len(extras):
            faltantes = extras[encoladas:]
            _spotify_queue_tracks(faltantes, None)
    return True


def _spotify_set_shuffle(activar: bool, device_id: str | None) -> bool:
    ready, _ = _spotify_ready()
    if not ready:
        return False
    try:
        if device_id:
            sp.shuffle(state=activar, device_id=device_id)
        else:
            sp.shuffle(state=activar)
        return True
    except Exception as error:
        _spotify_log_error("shuffle", error)
        return False


def _spotify_get_all_devices() -> list[dict]:
    """Retorna todas los dispositivos availables."""
    ready, _ = _spotify_ready()
    if not ready:
        return []
    try:
        return sp.devices().get("devices", []) or []
    except Exception as error:
        _spotify_log_error("get_devices", error)
        return []


def _spotify_classify_error(err: Exception | str) -> str:
    """Clasifica el error para dar mensaje específico."""
    txt = str(err or "")
    normalized = txt.strip().lower()
    if normalized in {"auth", "network", "no_device", "premium", "quota"}:
        return normalized

    if err.__class__.__name__ in ("SpotifyException", "HTTPError"):
        status = getattr(err, "status", None) or getattr(err, "code", None)
        if status == 401 or "token" in txt.lower() and "invalid" in txt.lower():
            return "auth"
        if status == 403 or "premium" in txt.lower() or "restrict" in txt.lower():
            return "premium"
        if status == 429 or "rate limit" in txt.lower() or "cuota" in txt.lower():
            return "quota"

    if any(p in txt.lower() for p in [
        "no active device", "no_active_device",
        "player command failed: no active device found",
        "404", "not found", "dispositivo",
    ]):
        return "no_device"
    if any(p in txt.lower() for p in ["network", "connection", "timeout", "dns"]):
        return "network"
    return "unknown"


def _spotify_transfer_and_play(track_uri: str, device_id: str) -> tuple[bool, str | None]:
    """Transfiere playback a device_id y reproduce track_uri."""
    orig_exc = None
    try:
        sp.transfer_playback(device_id=device_id, force_play=True)
        _time.sleep(0.8)
    except Exception as error:
        orig_exc = error
    if orig_exc:
        _spotify_log_error("transfer_playback", orig_exc)
        return False, _spotify_classify_error(orig_exc)
    try:
        sp.start_playback(device_id=device_id, uris=[track_uri])
        _time.sleep(0.5)
        return True, None
    except Exception as error:
        _spotify_log_error("start_playback_after_transfer", error)
        return False, _spotify_classify_error(error)


def _spotify_activar_cliente() -> bool:
    ready, _ = _spotify_ready()
    if not ready:
        return False
    try:
        _open_url_or_app("spotify:")
        return True
    except Exception as error:
        _spotify_log_error("open_spotify_client", error)
        return False


def _es_error_sin_dispositivo(err: Exception | str) -> bool:
    return _spotify_classify_error(err) == "no_device"


def _spotify_queue_tracks(uris: list[str], device_id: str | None) -> int:
    ready, _ = _spotify_ready()
    if not ready:
        return 0
    encoladas = 0
    for uri in uris:
        try:
            if device_id:
                sp.add_to_queue(uri, device_id=device_id)
            else:
                sp.add_to_queue(uri)
            encoladas += 1
        except Exception as error:
            _spotify_log_error("add_to_queue", error)
    return encoladas


# ─────────────────────────────────────────
# Query parsing
# ─────────────────────────────────────────
_PREFIJOS_SPOTIFY = re.compile(
    r"^(?:pon|reproduce|play|toca|ponme|dale|oye|ponle|pon me|"
    r"quiero escuchar|escucha|escúchame|pon ahora|pon ya)\s+",
    re.IGNORECASE,
)
_PATRON_DE_ARTISTA = re.compile(
    r"^(.+?)\s+(?:de|by|of|del|por)\s+(.+)$",
    re.IGNORECASE,
)


def _parsear_query_spotify(raw: str) -> tuple[str, str, str]:
    limpio = _PREFIJOS_SPOTIFY.sub("", raw).strip()
    m = _PATRON_DE_ARTISTA.match(limpio)
    if m:
        track_hint = m.group(1).strip()
        artist_hint = m.group(2).strip()

        all_words = re.findall(r"[a-zA-Z0-9áéíóúñü]+", limpio)
        track_words = re.findall(r"[a-zA-Z0-9áéíóúñü]+", track_hint)
        artist_words = re.findall(r"[a-zA-Z0-9áéíóúñü]+", artist_hint)
        if len(track_words) <= 2 and len(artist_words) >= 3 and len(all_words) >= 4:
            alt_artist_hint = " ".join(all_words[-2:]).strip()
            alt_track_hint = " ".join(all_words[:-2]).strip()
            if alt_track_hint and alt_artist_hint:
                track_hint = alt_track_hint
                artist_hint = alt_artist_hint

        query = f'track:"{track_hint}" artist:"{artist_hint}"'
        return query, track_hint.lower(), artist_hint.lower()

    return limpio, limpio.lower(), ""


def _inferir_query_track_artist_tail(raw: str) -> tuple[str, str, str] | None:
    limpio = _PREFIJOS_SPOTIFY.sub("", raw or "").strip()
    palabras = re.findall(r"[a-zA-Z0-9áéíóúñü]+", limpio)
    if len(palabras) < 3:
        return None

    mejor = None
    mejor_score = -1
    for n in (2, 3, 1):
        if len(palabras) <= n:
            continue
        track_guess = " ".join(palabras[:-n]).strip()
        artist_guess = " ".join(palabras[-n:]).strip()
        if not track_guess or not artist_guess:
            continue

        q = f'track:"{track_guess}" artist:"{artist_guess}"'
        items = _spotify_search_tracks(q=q, limit=8)
        if not items:
            continue

        cand = _spotify_mejor_track(
            raw, items, track_guess.lower(), artist_guess.lower()
        )
        if not cand:
            continue

        artist_text = " ".join(
            (a.get("name") or "").lower() for a in (cand.get("artists") or [])
        )
        track_name = (cand.get("name") or "").lower()
        score = 0
        if artist_guess.lower() in artist_text:
            score += 8
        if track_guess.lower() == track_name:
            score += 7
        elif track_guess.lower() in track_name:
            score += 4
        score += int((cand.get("popularity") or 0) / 25)

        if score > mejor_score:
            mejor_score = score
            mejor = (q, track_guess.lower(), artist_guess.lower())

    if mejor_score >= 10:
        return mejor
    return None


# ─────────────────────────────────────────
# Tools
# ─────────────────────────────────────────
def _spotify_play_api_message(cancion: str) -> str:
    """Reproduce una canción con estrategia en cascada: dispositivo activo, transferencia, apertura, cola.

    Nunca retorna error genérico. Cada fallo tiene mensaje específico.
    """
    global _ULTIMA_CANCION_SOLICITADA
    ready, err = _spotify_ready()
    if not ready:
        return err or _spotify_text("Spotify is not configured.", "Spotify no está configurado.")

    # ── 1. Buscar track ────────────────────────────────────────────────
    try:
        query, track_hint, artist_hint = _parsear_query_spotify(cancion)
        if not artist_hint:
            inferida = _inferir_query_track_artist_tail(cancion)
            if inferida:
                query, track_hint, artist_hint = inferida
        print(
            f"  [SPOTIFY] Query: '{query}' | track='{track_hint}' | artist='{artist_hint}'"
        )

        tracks = _spotify_search_tracks(q=query, limit=10)

        if not tracks and ("track:" in query or "artist:" in query):
            fallback_q = f"{track_hint} {artist_hint}".strip()
            print(f"  [SPOTIFY] Fallback query: '{fallback_q}'")
            tracks = _spotify_search_tracks(q=fallback_q, limit=10)

        if not tracks:
            return _spotify_text(
                f"I could not find '{track_hint}'. Try another title or check that the song is available in your market.",
                f"No encontré '{track_hint}'. Intentá con otro nombre o verificá que sea una canción available en tu mercado.",
            )

        track = _spotify_mejor_track(cancion, tracks, track_hint, artist_hint)
        if not track:
            return _spotify_text(
                f"I could not find '{track_hint}'. Try another title or add the artist.",
                f"No encontré '{track_hint}'. Intentá con otro nombre o agregá el artista.",
            )

        track_name = track.get("name", "desconocida")
        artist = ((track.get("artists") or [{}])[0]).get("name", "artista desconocido")
        track_uri = track.get("uri")
        track_id = track.get("id")

        if not track_uri or not track_id:
            return _spotify_text(
                "I could not read the information for that song from Spotify.",
                "No pude leer la información de esa canción desde Spotify.",
            )

        _ULTIMA_CANCION_SOLICITADA = _spotify_track_plain_label(track_name, artist)
        print(f"  [SPOTIFY] Track: '{track_name}' de {artist} | URI: {track_uri}")

    except Exception as error:
        _spotify_log_error("prepare_playback", error)
        return _spotify_text(
            "Spotify could not complete the song search.",
            "Spotify no pudo completar la búsqueda de la canción.",
        )

    # ── 2. Obtener dispositivos availables ────────────────────────────
    devices = _spotify_get_all_devices()
    activo = next((d for d in devices if d.get("is_active")), None)
    availables = [d for d in devices if not d.get("is_active")]
    print(f"  [SPOTIFY] Dispositivos: {len(devices)} | Activo: {activo.get('name') if activo else 'ninguno'}")

    # ── 3. Estrategia A: dispositivo activo ──────────────────────────────
    if activo:
        try:
            sp.start_playback(device_id=activo["id"], uris=[track_uri])
            _time.sleep(0.5)
            print(f"  [SPOTIFY] Estrategia A: play en activo '{activo['name']}'")
            _ULTIMA_CANCION_SOLICITADA = _spotify_track_plain_label(track_name, artist)
        except Exception as error:
            err_type = _spotify_classify_error(error)
            _spotify_log_error(f"active_device_playback_{err_type}", error)
            if err_type == "no_device":
                pass  # sigue a estrategia B
            elif err_type in ("premium", "auth", "quota"):
                return _mensaje_error_spotify(err_type, track_name, artist)
            else:
                return _spotify_text(
                    f"I could not play '{track_name}' on Spotify right now.",
                    f"No pude reproducir '{track_name}' en Spotify en este momento.",
                )
        else:
            return _post_playback_ok(track_name, artist, track_uri, track_id, track=track)
    else:
        print("  [SPOTIFY] No hay dispositivo activo.")

    # ── 4. Estrategia B: transferir a primer available y reproducir ────
    if availables:
        target = availables[0]
        print(f"  [SPOTIFY] Estrategia B: transfiriendo a '{target['name']}'...")
        ok, err = _spotify_transfer_and_play(track_uri, target["id"])
        if ok:
            print("  [SPOTIFY] Estrategia B: OK")
            return _post_playback_ok(track_name, artist, track_uri, track_id, track=track)
        err_type = _spotify_classify_error(err)
        print(f"  [SPOTIFY] Estrategia B failed ({err_type}).")
        if err_type in ("premium", "auth", "quota"):
            return _mensaje_error_spotify(err_type, track_name, artist)

    # ── 5. Estrategia C: abrir cliente, esperar, reintentar ───────────────
    print("  [SPOTIFY] Estrategia C: abriendo Spotify cliente, esperando 5s...")
    _spotify_activar_cliente()
    _time.sleep(5)
    devices_after = _spotify_get_all_devices()
    activo_new = next((d for d in devices_after if d.get("is_active")), None)

    if activo_new:
        try:
            sp.start_playback(device_id=activo_new["id"], uris=[track_uri])
            _time.sleep(0.5)
            print(f"  [SPOTIFY] Estrategia C: OK en '{activo_new['name']}'")
            return _post_playback_ok(track_name, artist, track_uri, track_id, track=track)
        except Exception as error:
            err_type = _spotify_classify_error(error)
            _spotify_log_error(f"reopened_device_playback_{err_type}", error)
            if err_type in ("premium", "auth", "quota"):
                return _mensaje_error_spotify(err_type, track_name, artist)
    elif availables:
        target = availables[0]
        ok, err = _spotify_transfer_and_play(track_uri, target["id"])
        if ok:
            print("  [SPOTIFY] Estrategia C (B fallback): OK")
            return _post_playback_ok(track_name, artist, track_uri, track_id, track=track)
        err_type = _spotify_classify_error(err)
        print(f"  [SPOTIFY] Estrategia C (B fallback) falló: {err_type}")
        if err_type in ("premium", "auth", "quota"):
            return _mensaje_error_spotify(err_type, track_name, artist)

    # ── 6. Estrategia D: agregar a cola ────────────────────────────────
    print("  [SPOTIFY] Estrategia D: intentando agregar a cola...")
    try:
        sp.add_to_queue(track_uri)  # Sin device_id → apunta al dispositivo activo
        _time.sleep(0.3)
        print("  [SPOTIFY] Estrategia D: track en cola.")
        label = _spotify_track_label(track_name, artist)
        return _spotify_text(
            f"{label} could not be started automatically because there is no active device, but it was added to the Spotify queue. Open Spotify on your device and press play.",
            f"{label} no pudo reproducirse automáticamente (no hay dispositivo activo), pero fue agregada a la cola de Spotify. Abrí Spotify en tu dispositivo y dale play.",
        )
    except Exception as error:
        err_type = _spotify_classify_error(error)
        _spotify_log_error(f"fallback_queue_{err_type}", error)
        if err_type in ("premium", "auth", "quota"):
            return _mensaje_error_spotify(err_type, track_name, artist)

    # ── 7. Todo falló ──────────────────────────────────────────────────
    devices_final = _spotify_get_all_devices()
    if not devices_final:
        return _spotify_text(
            "No Spotify device is available. Open Spotify on your phone, computer, or TV, play one song once, and try again.",
            "No hay ningún dispositivo Spotify available. Abrí Spotify en tu teléfono, computadora o TV, reproducí una canción una vez, y volvé a intentarlo.",
        )
    label = _spotify_track_label(track_name, artist)
    return _spotify_text(
        f"I could not play {label} right now. Check that Spotify is open and playing on a device.",
        f"No pude reproducir {label} en este momento. Revisá que Spotify esté abierto y con una canción sonando en algún dispositivo.",
    )


_API_CAPABILITY_MARKERS = (
    "blocked",
    "developer access",
    "forbidden",
    "invalid grant",
    "no active spotify device",
    "no hay dispositivo activo",
    "not configured",
    "no esta configurado",
    "premium",
    "redirect",
    "redireccion",
    "session expired",
    "sesion de spotify vencio",
)


def _spotify_api_message_is_success(message: str) -> bool:
    normalized = normalize_text(message)
    return normalized.startswith(("playing ", "reproduciendo "))


def _spotify_play_api(song: str) -> SpotifyAPIPlaybackResult:
    ready, error_message = _spotify_ready()
    if not ready:
        return SpotifyAPIPlaybackResult(
            ok=False,
            message=error_message
            or _spotify_text(
                "Spotify API is not configured.",
                "La API de Spotify no esta configurada.",
            ),
            capability_failure=True,
        )

    message = _spotify_play_api_message(song)
    normalized = normalize_text(message)
    ok = _spotify_api_message_is_success(message)
    capability_failure = not ok or any(
        normalize_text(marker) in normalized for marker in _API_CAPABILITY_MARKERS
    )
    return SpotifyAPIPlaybackResult(
        ok=ok,
        message=message,
        capability_failure=capability_failure,
    )


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
    global _ULTIMA_CANCION_SOLICITADA
    result = _spotify_desktop_result(song)
    profile_id = jarvis_state.get_active_profile_id()
    if result.status is DesktopResultStatus.SUCCESS:
        pending_spotify_selections.clear(profile_id)
        _ULTIMA_CANCION_SOLICITADA = _spotify_track_plain_label(
            result.title,
            result.artist,
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


@tool
def reproducir_en_spotify(cancion: str) -> str:
    """Play music using a cached Spotify API session or Spotify Desktop on Windows."""
    global _spotify_api_capability_failed
    song = str(cancion or "").strip()
    if not song:
        return _spotify_text("Tell me what to play.", "Dime que deseas reproducir.")
    pending_spotify_selections.clear(jarvis_state.get_active_profile_id())
    if SPOTIFY_PLAYBACK_MODE == "desktop":
        return _spotify_play_desktop(song)
    if SPOTIFY_PLAYBACK_MODE == "auto" and (
        _spotify_api_capability_failed or not _spotify_has_valid_cached_token()
    ):
        return _spotify_play_desktop(song)

    api_result = _spotify_play_api(song)
    if api_result.ok or SPOTIFY_PLAYBACK_MODE == "api":
        return api_result.message
    if api_result.capability_failure:
        _spotify_api_capability_failed = True
        return _spotify_play_desktop(song)
    return api_result.message


def _play_spotify_seed(seed: str) -> str:
    return reproducir_en_spotify.invoke({"cancion": seed})


@tool
def reproducir_mix_spotify(semilla: str) -> str:
    """Reproduce una semilla de Spotify y construye un AutoMix dinamico alrededor de ella."""
    seed = str(semilla or "").strip()
    if not seed:
        return _spotify_text(
            "Tell me an artist, genre, playlist, or song to build the mix.",
            "Dime un artista, genero, playlist o cancion para construir el mix.",
        )
    response = _play_spotify_seed(seed)
    if "Spotify Desktop" in response and (
        "Playing " in response or "Reproduciendo " in response
    ):
        return response + " " + _spotify_text(
            "Spotify Desktop will continue the mix using its own autoplay and recommendations.",
            "Spotify Desktop continuara el mix con su reproduccion automatica y recomendaciones.",
        )
    return response


def _mensaje_error_spotify(tipo: str, track_name: str, artist: str) -> str:
    msgs_es = {
        "premium": (
            "La reproducción explícita requiere Spotify Premium. "
            "Alternativas: usá una versión instrumental, otra canción, o actualizá tu cuenta."
        ),
        "auth": (
            "La sesión de Spotify venció. Necesitás re-autenticar JARVIS: "
            "borren el archivo .cache-jarvis en src/backend/ y reinicien el backend."
        ),
        "quota": (
            "Spotify detectó demasiadas solicitudes desde JARVIS. "
            " Esperá unos minutos y volvé a intentarlo."
        ),
    }
    msgs_en = {
        "premium": (
            "Explicit playback requires Spotify Premium. "
            "Use an instrumental version, choose another song, or upgrade your account."
        ),
        "auth": (
            "The Spotify session expired. Re-authenticate JARVIS: "
            "delete .cache-jarvis in src/backend/ and restart the backend."
        ),
        "quota": (
            "Spotify detected too many requests from JARVIS. "
            "Wait a few minutes and try again."
        ),
    }
    fallback = _spotify_text(
        f"I could not play {_spotify_track_label(track_name, artist)}. Error: {tipo}.",
        f"No pude reproducir {_spotify_track_label(track_name, artist)}. Error: {tipo}.",
    )
    return (msgs_en if _spotify_is_english() else msgs_es).get(tipo) or fallback


def _post_playback_ok(
    track_name: str,
    artist: str,
    track_uri: str,
    track_id: str,
    track: dict | None = None,
) -> str:
    """Hanldr post-playback: shuffle, cola de similares, AutoMix."""
    device_id = _spotify_dispositivo_objetivo()
    _spotify_set_shuffle(False, device_id) or _spotify_set_shuffle(False, None)

    seed_track = dict(track or {})
    seed_track.setdefault("id", track_id)
    seed_track.setdefault("name", track_name)
    seed_track.setdefault("uri", track_uri)
    if not seed_track.get("artists"):
        seed_track["artists"] = [{"name": artist}]

    similares = _spotify_obtener_similares(
        seed_track,
        limite=SPOTIFY_RADIO_QUEUE_SIZE,
    )
    similares_uris = [t.get("uri") for t in similares if t.get("uri")]
    lista_completa = [track_uri] + similares_uris

    automix_ok = False
    automix_started = False
    if SPOTIFY_MODO_SIMILARES in {"native", "hybrid"}:
        user_id = _spotify_usuario_actual_id()
        if user_id:
            playlist = _spotify_buscar_o_crear_playlist_automix(user_id)
            playlist_id = (playlist or {}).get("id")
            if playlist_id:
                automix_ok = _spotify_reemplazar_playlist_con_uris(playlist_id, lista_completa)
                if automix_ok:
                    automix_started = _spotify_start_playlist_context(
                        playlist_id,
                        device_id,
                        offset_uri=track_uri,
                    )

    queued_count = 0
    if similares_uris and not automix_started:
        queued_count = _spotify_queue_tracks(similares_uris, device_id)
        if queued_count < len(similares_uris):
            queued_count += _spotify_queue_tracks(similares_uris[queued_count:], None)

    if SPOTIFY_AUTO_SHUFFLE:
        _spotify_set_shuffle(True, device_id) or _spotify_set_shuffle(True, None)

    return _spotify_playback_success_message(
        track_name,
        artist,
        similar_count=len(similares_uris) if automix_started else queued_count,
        automix_ok=automix_started,
    )


def _spotify_control_api(action: str) -> SpotifyAPIPlaybackResult:
    ready, error_message = _spotify_ready()
    if not ready:
        return SpotifyAPIPlaybackResult(
            ok=False,
            message=error_message
            or _spotify_text(
                "Spotify API is not configured.",
                "La API de Spotify no esta configurada.",
            ),
            capability_failure=True,
        )
    message = _spotify_control_api_message(action)
    normalized = normalize_text(message)
    success_prefixes = (
        "playback paused",
        "playback resumed",
        "reproduccion pausada",
        "reproduccion reanudada",
        "next track",
        "siguiente cancion",
        "previous track",
        "cancion anterior",
        "shuffle enabled",
        "shuffle activado",
        "shuffle disabled",
        "shuffle desactivado",
    )
    ok = normalized.startswith(success_prefixes)
    unrecognized = "not recognized" in normalized or "no reconocida" in normalized
    capability_failure = (not ok and not unrecognized) or any(
        normalize_text(marker) in normalized for marker in _API_CAPABILITY_MARKERS
    )
    return SpotifyAPIPlaybackResult(
        ok=ok,
        message=message,
        capability_failure=capability_failure,
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
    if result.status is DesktopResultStatus.SUCCESS:
        messages = {
            "pause": _spotify_text("Playback paused.", "Reproduccion pausada."),
            "resume": _spotify_text("Playback resumed.", "Reproduccion reanudada."),
            "next": _spotify_text("Next track.", "Siguiente cancion."),
            "previous": _spotify_text("Previous track.", "Cancion anterior."),
            "shuffle_on": _spotify_text("Shuffle enabled.", "Shuffle activado."),
            "shuffle_off": _spotify_text("Shuffle disabled.", "Shuffle desactivado."),
        }
        return messages[canonical]
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


@tool
def controlar_reproduccion(accion: str) -> str:
    """Control Spotify through a cached API session or Spotify Desktop."""
    global _spotify_api_capability_failed
    action = str(accion or "").strip()
    if not action:
        return _spotify_text(
            "Tell me which playback action to perform.",
            "Dime que accion de reproduccion debo realizar.",
        )
    if SPOTIFY_PLAYBACK_MODE == "desktop":
        return _spotify_control_desktop(action)
    if SPOTIFY_PLAYBACK_MODE == "auto" and (
        _spotify_api_capability_failed or not _spotify_has_valid_cached_token()
    ):
        return _spotify_control_desktop(action)

    api_result = _spotify_control_api(action)
    if api_result.ok or SPOTIFY_PLAYBACK_MODE == "api":
        return api_result.message
    if api_result.capability_failure:
        _spotify_api_capability_failed = True
        return _spotify_control_desktop(action)
    return api_result.message


def _spotify_control_api_message(accion: str) -> str:
    """Controla Spotify: pausar, reanudar, siguiente, anterior, shuffle on/off."""
    ready, err = _spotify_ready()
    if not ready:
        return err or _spotify_text("Spotify is not configured.", "Spotify no está configurado.")
    try:
        accion = accion.lower().strip()
        device_id = _spotify_dispositivo_objetivo()

        def _try_player_action(with_device, without_device):
            errores = []
            if device_id:
                try:
                    with_device(device_id)
                    return None
                except Exception as e1:
                    errores.append(e1)
            try:
                without_device()
                return None
            except Exception as e2:
                errores.append(e2)
                return errores[-1]

        def _resolver_error_player(err: Exception, accion_humana: str) -> str:
            error_type = _spotify_classify_error(err)
            _spotify_log_error(f"player_control_{error_type}", err)
            if error_type == "no_device":
                _spotify_activar_cliente()
                return _spotify_text(
                    f"No active Spotify device is available for {accion_humana}. Open Spotify, play any song once, and repeat the command.",
                    f"No hay dispositivo activo de Spotify para {accion_humana}. Abra Spotify, reproduzca cualquier canción una vez y repita el comando.",
                )
            if error_type in {"auth", "premium", "quota"}:
                return _mensaje_error_spotify(error_type, "playback", "Spotify")
            return _spotify_text(
                f"I could not {accion_humana} right now.",
                f"No pude {accion_humana} en este momento.",
            )

        if accion in ["pausar", "pausa", "detener", "detén"]:
            err = _try_player_action(
                lambda did: sp.pause_playback(device_id=did),
                lambda: sp.pause_playback(),
            )
            if err is None:
                return _spotify_text("Playback paused.", "Reproducción pausada.")
            return _resolver_error_player(err, _spotify_text("pause playback", "pausar"))
        elif accion in ["reanudar", "continuar", "play", "resume"]:
            err = _try_player_action(
                lambda did: sp.start_playback(device_id=did),
                lambda: sp.start_playback(),
            )
            if err is None:
                return _spotify_text("Playback resumed.", "Reproducción reanudada.")
            return _resolver_error_player(err, _spotify_text("resume playback", "reanudar"))
        elif accion in ["siguiente", "next", "skip"]:
            err = _try_player_action(
                lambda did: sp.next_track(device_id=did),
                lambda: sp.next_track(),
            )
            if err is None:
                return _spotify_text("Next track.", "Siguiente canción.")
            return _resolver_error_player(err, _spotify_text("skip to the next track", "pasar a la siguiente canción"))
        elif accion in ["anterior", "prev", "atrás", "atras"]:
            err = _try_player_action(
                lambda did: sp.previous_track(device_id=did),
                lambda: sp.previous_track(),
            )
            if err is None:
                return _spotify_text("Previous track.", "Canción anterior.")
            return _resolver_error_player(err, _spotify_text("go back to the previous track", "volver a la canción anterior"))
        elif accion in [
            "shuffle on", "activar shuffle", "activa shuffle",
            "mezcla on", "aleatorio on", "aleatorio",
        ]:
            if _spotify_set_shuffle(True, device_id) or _spotify_set_shuffle(True, None):
                return _spotify_text("Shuffle enabled.", "Shuffle activado.")
            if not device_id:
                _spotify_activar_cliente()
                return _spotify_text(
                    "No active Spotify device is available. Open Spotify and play something once to enable shuffle.",
                    "No hay dispositivo activo de Spotify. Abra Spotify y reproduzca algo una vez para activar shuffle.",
                )
            return _spotify_text("I could not enable shuffle right now.", "No pude activar shuffle en este momento.")
        elif accion in [
            "shuffle off", "desactivar shuffle", "desactiva shuffle",
            "mezcla off", "aleatorio off",
        ]:
            if _spotify_set_shuffle(False, device_id) or _spotify_set_shuffle(False, None):
                return _spotify_text("Shuffle disabled.", "Shuffle desactivado.")
            if not device_id:
                _spotify_activar_cliente()
                return _spotify_text(
                    "No active Spotify device is available. Open Spotify and play something once to disable shuffle.",
                    "No hay dispositivo activo de Spotify. Abra Spotify y reproduzca algo una vez para desactivar shuffle.",
                )
            return _spotify_text("I could not disable shuffle right now.", "No pude desactivar shuffle en este momento.")
        return _spotify_text(f"Action '{accion}' not recognized.", f"Acción '{accion}' no reconocida.")
    except Exception as error:
        _spotify_log_error("control_playback", error)
        return _spotify_text(
            "Spotify could not complete that playback action.",
            "Spotify no pudo completar esa acción de reproducción.",
        )



