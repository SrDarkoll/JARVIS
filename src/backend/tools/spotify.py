"""Integración completa de Spotify: autenticación, búsqueda, reproducción, cola, AutoMix, similares."""

import os, re, time as _time
import requests as http_requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from langchain_core.tools import tool

from tools._common import BASE_DIR, _open_url_or_app
from core.service_container import services
from core import jarvis_config
from utils.jarvis_i18n import get_current_language

# ─────────────────────────────────────────
# Configuración y autenticación
# ─────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPOTIFY_CACHE = os.path.join(_BASE_DIR, ".cache-jarvis")
print(f"  [SPOTIFY] Cache: {SPOTIFY_CACHE}")

SPOTIFY_CLIENT_ID = jarvis_config.SPOTIPY_CLIENT_ID
SPOTIFY_CLIENT_SECRET = jarvis_config.SPOTIPY_CLIENT_SECRET
SPOTIFY_REDIRECT_URI = jarvis_config.SPOTIPY_REDIRECT_URI
SPOTIFY_SCOPE = "user-modify-playback-state user-read-playback-state playlist-read-private playlist-modify-private"

SPOTIFY_ENABLED = bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)

if SPOTIFY_ENABLED:
    sp = spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope=SPOTIFY_SCOPE,
            cache_path=SPOTIFY_CACHE,
        )
    )
else:
    sp = None
    print(
        "  [SPOTIFY] SPOTIPY_CLIENT_ID/SPOTIPY_CLIENT_SECRET no configurados en .env. Funciones Spotify deshabilitadas."
    )

SPOTIFY_RADIO_QUEUE_SIZE = 12
SPOTIFY_MODO_SIMILARES = jarvis_config.SPOTIFY_MODO_SIMILARES
SPOTIFY_AUTO_SHUFFLE = jarvis_config.SPOTIFY_AUTO_SHUFFLE
SPOTIFY_AUTOMIX_PLAYLIST_NAME = jarvis_config.SPOTIFY_AUTOMIX_PLAYLIST_NAME or "JARVIS AutoMix"
_ULTIMA_CANCION_SOLICITADA = ""
_SPOTIFY_USER_COUNTRY = ""


def _spotify_is_english() -> bool:
    return get_current_language().startswith("en")


def _spotify_text(en: str, es: str) -> str:
    return en if _spotify_is_english() else es


def _spotify_track_label(track_name: str, artist: str) -> str:
    connector = "by" if _spotify_is_english() else "de"
    return f"'{track_name}' {connector} {artist}"


def _spotify_track_plain_label(track_name: str, artist: str) -> str:
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
    except Exception as e:
        print(f"  [SPOTIFY] current_user error: {e}")
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
    except Exception as e:
        print(f"  [SPOTIFY] current_user country error: {e}")
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


def _spotify_search_tracks(q: str, limit: int = 10) -> list:
    ready, _ = _spotify_ready()
    if not ready:
        return []
    market = _spotify_market_objetivo()
    try:
        if market:
            payload = sp.search(q=q, limit=limit, type="track", market=market)
        else:
            payload = sp.search(q=q, limit=limit, type="track")
        return _spotify_items_search(payload)
    except TypeError:
        try:
            payload = sp.search(q=q, limit=limit, type="track")
            return _spotify_items_search(payload)
        except Exception as e:
            print(f"  [SPOTIFY] search error: {e}")
            return []
    except Exception as e:
        print(f"  [SPOTIFY] search error: {e}")
        return []


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
        except Exception as e:
            print(f"  [SPOTIFY] album_tracks error: {e}")
            return []
    except Exception as e:
        print(f"  [SPOTIFY] album_tracks error: {e}")
        return []


def _spotify_access_token() -> str | None:
    ready, _ = _spotify_ready()
    if not ready:
        return None
    try:
        token = sp.auth_manager.get_access_token(as_dict=False)
        if isinstance(token, str) and token.strip():
            return token.strip()
    except TypeError:
        try:
            token_info = sp.auth_manager.get_access_token()
            if isinstance(token_info, dict):
                tok = token_info.get("access_token")
                if isinstance(tok, str) and tok.strip():
                    return tok.strip()
            elif isinstance(token_info, str) and token_info.strip():
                return token_info.strip()
        except Exception as e:
            print(f"  [SPOTIFY] get_access_token error (legacy): {e}")
    except Exception as e:
        print(f"  [SPOTIFY] get_access_token error: {e}")

    try:
        cached = sp.auth_manager.get_cached_token() or {}
        tok = cached.get("access_token")
        if isinstance(tok, str) and tok.strip():
            return tok.strip()
    except Exception as e:
        print(f"  [SPOTIFY] get_cached_token error: {e}")

    return None


def _spotify_items_search(payload: dict) -> list:
    if not isinstance(payload, dict):
        return []
    root_items = payload.get("items")
    if isinstance(root_items, list):
        return root_items
    tracks_node = payload.get("tracks")
    if isinstance(tracks_node, dict):
        nested_items = tracks_node.get("items")
        if isinstance(nested_items, list):
            return nested_items
    return []


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
    token = _spotify_access_token()
    if not token:
        return None

    payload = {
        "name": name,
        "public": bool(public),
        "collaborative": bool(collaborative),
        "description": description or "",
    }
    try:
        r = http_requests.post(
            "https://api.spotify.com/v1/me/playlists",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        if r.status_code in (200, 201):
            return r.json()
        if r.status_code == 403:
            print(
                "  [SPOTIFY] POST /me/playlists -> 403. Verifique scope 'playlist-modify-private' y allowlist."
            )
        else:
            print(f"  [SPOTIFY] POST /me/playlists -> {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  [SPOTIFY] POST /me/playlists error: {e}")
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
                if (
                    owner_id == user_id
                    and name.lower() == SPOTIFY_AUTOMIX_PLAYLIST_NAME.lower()
                ):
                    return p
            if not page.get("next"):
                break
            offset += len(items)
            if len(items) == 0:
                break
    except Exception as e:
        print(f"  [SPOTIFY] current_user_playlists error: {e}")

    descripcion = "Playlist temporal de mezcla automatica de JARVIS."
    creada = _spotify_crear_playlist_me(
        name=SPOTIFY_AUTOMIX_PLAYLIST_NAME,
        public=False,
        collaborative=False,
        description=descripcion,
    )
    if creada:
        return creada

    try:
        return sp.current_user_create_playlist(
            name=SPOTIFY_AUTOMIX_PLAYLIST_NAME,
            public=False,
            collaborative=False,
            description=descripcion,
        )
    except TypeError:
        try:
            return sp.user_playlist_create(
                user=user_id,
                name=SPOTIFY_AUTOMIX_PLAYLIST_NAME,
                public=False,
                collaborative=False,
                description=descripcion,
            )
        except Exception as e:
            msg = str(e)
            if "403" in msg or "Forbidden" in msg:
                print(
                    "  [SPOTIFY] create playlist forbidden en fallback. Falta scope 'playlist-modify-private' o token viejo."
                )
            else:
                print(f"  [SPOTIFY] create playlist error (fallback): {e}")
            return None
    except Exception as e:
        msg = str(e)
        if "403" in msg or "Forbidden" in msg:
            print(
                "  [SPOTIFY] current_user_create_playlist forbidden. Reautentique Spotify para actualizar scopes."
            )
        else:
            print(f"  [SPOTIFY] current_user_create_playlist error: {e}")
        return None


def _spotify_obtener_uris_playlist(playlist_id: str, max_items: int = 300) -> list[str]:
    ready, _ = _spotify_ready()
    if not ready:
        return []
    try:
        uris = []
        offset = 0
        max_items = max(1, int(max_items))
        while len(uris) < max_items:
            page = (
                sp.playlist_items(
                    playlist_id=playlist_id,
                    limit=min(100, max_items - len(uris)),
                    offset=offset,
                    additional_types=("track",),
                )
                or {}
            )
            items = page.get("items", [])
            if not isinstance(items, list) or not items:
                break
            for it in items:
                track = (it or {}).get("track") or {}
                uri = track.get("uri")
                if uri:
                    uris.append(uri)
                    if len(uris) >= max_items:
                        break
            if not page.get("next"):
                break
            offset += len(items)
        return uris
    except Exception as e:
        print(f"  [SPOTIFY] playlist_items error: {e}")
        return []


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
    except Exception as e:
        print(f"  [SPOTIFY] replace playlist items error: {e}")
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
    except Exception as e:
        print(f"  [SPOTIFY] start playlist context error: {e}")
        return False


# ─────────────────────────────────────────
# Similares (radio)
# ─────────────────────────────────────────
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
    seed_artist_ids = [a.get("id") for a in seed_artists if a.get("id")]

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

    def _artist_top_tracks(artist_id: str) -> list[dict]:
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
            except Exception as e:
                print(f"  [SIMILARES] artist_top_tracks error: {e}")
                return []
        except Exception as e:
            print(f"  [SIMILARES] artist_top_tracks error: {e}")
            return []

    if len(similares) < limite:
        for artist_id in seed_artist_ids:
            _agregar_candidatos(_artist_top_tracks(artist_id), max_items=4)
            if len(similares) >= limite:
                break

    if len(similares) < limite:
        try:
            for artist_id in seed_artist_ids[:2]:
                related = sp.artist_related_artists(artist_id) or {}
                for ra in (related.get("artists", []) or [])[:6]:
                    if len(similares) >= limite:
                        break
                    related_id = ra.get("id")
                    if related_id:
                        _agregar_candidatos(
                            _artist_top_tracks(related_id),
                            max_items=2,
                        )
                if len(similares) >= limite:
                    break
        except Exception as e:
            print(f"  [SIMILARES] Error en fallback de artistas similares: {e}")

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
    except Exception as e:
        print(f"  [SPOTIFY] start playlist-like error: {e}")
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
    except Exception as e:
        print(f"  [SPOTIFY] shuffle error: {e}")
        return False


def _spotify_get_all_devices() -> list[dict]:
    """Retorna todas los dispositivos availables."""
    ready, _ = _spotify_ready()
    if not ready:
        return []
    try:
        return sp.devices().get("devices", []) or []
    except Exception as e:
        print(f"  [SPOTIFY] get_devices error: {e}")
        return []


def _spotify_classify_error(err: Exception | str) -> str:
    """Clasifica el error para dar mensaje específico."""
    txt = str(err or "")

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
    except Exception as e:
        orig_exc = e
    if orig_exc:
        err_str = str(orig_exc).lower()
        if any(p in err_str for p in ["no active device", "not found", "no devices"]):
            return False, "no_device"
        return False, str(orig_exc)
    try:
        sp.start_playback(device_id=device_id, uris=[track_uri])
        _time.sleep(0.5)
        return True, None
    except Exception as e:
        err_str = str(e).lower()
        if any(p in err_str for p in ["no active device", "not found", "no devices", "404"]):
            return False, "no_device"
        return False, str(e)


def _spotify_activar_cliente() -> bool:
    ready, _ = _spotify_ready()
    if not ready:
        return False
    try:
        _open_url_or_app("spotify:")
        return True
    except Exception as e:
        print(
            f"  [SPOTIFY] I could not open the Spotify client para activar dispositivo: {e}"
        )
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
        except Exception as e:
            print(f"  [SPOTIFY] queue error ({uri}): {e}")
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
@tool
def reproducir_en_spotify(cancion: str) -> str:
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

    except Exception as e:
        return _spotify_text(f"Error searching for the song: {e}", f"Error buscando la canción: {e}")

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
        except Exception as e:
            err_type = _spotify_classify_error(e)
            print(f"  [SPOTIFY] Estrategia A falló: {err_type} → {e}")
            if err_type == "no_device":
                pass  # sigue a estrategia B
            elif err_type in ("premium", "auth", "quota"):
                return _mensaje_error_spotify(err_type, track_name, artist)
            else:
                return _spotify_text(
                    f"I could not play '{track_name}' on Spotify. Details: {e}",
                    f"No pude reproducir '{track_name}' en Spotify. Detalle: {e}",
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
        print(f"  [SPOTIFY] Estrategia B falló: {err_type} → {err}")
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
        except Exception as e:
            err_type = _spotify_classify_error(e)
            print(f"  [SPOTIFY] Estrategia C falló: {err_type} → {e}")
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
    except Exception as e:
        err_type = _spotify_classify_error(e)
        print(f"  [SPOTIFY] Estrategia D falló: {err_type} → {e}")
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


@tool
def controlar_reproduccion(accion: str) -> str:
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
            if _es_error_sin_dispositivo(err):
                _spotify_activar_cliente()
                return _spotify_text(
                    f"No active Spotify device is available for {accion_humana}. Open Spotify, play any song once, and repeat the command.",
                    f"No hay dispositivo activo de Spotify para {accion_humana}. Abra Spotify, reproduzca cualquier canción una vez y repita el comando.",
                )
            return _spotify_text(f"I could not {accion_humana}. Details: {err}", f"No pude {accion_humana}. Detalle: {err}")

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
    except Exception as e:
        return f"Error: {e}"



