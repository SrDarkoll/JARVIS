"""Low-level Spotify Web API client, OAuth state, search, and devices."""

from __future__ import annotations

import os
import re
import time as _time

import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth
from tools._common import _open_url_or_app

from modules.spotify import config
from modules.spotify.messages import text as _spotify_text

SPOTIFY_CACHE = config.SPOTIFY_CACHE
SPOTIFY_CLIENT_ID = config.SPOTIFY_CLIENT_ID
SPOTIFY_CLIENT_SECRET = config.SPOTIFY_CLIENT_SECRET
SPOTIFY_REDIRECT_URI = config.SPOTIFY_REDIRECT_URI
SPOTIFY_SCOPE = config.SPOTIFY_SCOPE
SPOTIFY_REDIRECT_ERROR = config.SPOTIFY_REDIRECT_ERROR
SPOTIFY_ENABLED = config.SPOTIFY_ENABLED

print(f"  [SPOTIFY] Cache: {SPOTIFY_CACHE}")

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
            "  [SPOTIFY] Web API credentials are not configured; "
            "Windows desktop fallback remains available."
        )

_SPOTIFY_USER_COUNTRY = ""
_PREFIJOS_SPOTIFY = re.compile(
    r"^(?:pon|reproduce|play|toca|ponme|dale|oye|ponle|pon me|"
    r"quiero escuchar|escucha|esc?chame|pon ahora|pon ya)\s+",
    re.IGNORECASE,
)


def _spotify_log_error(operation: str, error: BaseException) -> None:
    print(f"  [SPOTIFY] {operation} failed ({type(error).__name__}).")


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
