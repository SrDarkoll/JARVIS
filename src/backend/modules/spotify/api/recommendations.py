"""Spotify recommendation, AutoMix, playlist, and queue strategies."""

from __future__ import annotations

import time as _time

from modules.spotify import config
from modules.spotify.api import client
from modules.spotify.api.client import (
    _spotify_album_tracks,
    _spotify_chunks,
    _spotify_log_error,
    _spotify_market_objetivo,
    _spotify_queue_tracks,
    _spotify_ready,
    _spotify_search_tracks,
    _spotify_track_actual_id,
    _spotify_track_available_en_mercado,
    _spotify_usuario_actual_id,
)

SPOTIFY_RADIO_QUEUE_SIZE = config.SPOTIFY_RADIO_QUEUE_SIZE
SPOTIFY_AUTO_SHUFFLE = config.SPOTIFY_AUTO_SHUFFLE
SPOTIFY_EXTENDED_QUOTA_MODE = config.SPOTIFY_EXTENDED_QUOTA_MODE
SPOTIFY_AUTOMIX_PLAYLIST_NAME = config.SPOTIFY_AUTOMIX_PLAYLIST_NAME


def _spotify_crear_playlist_me(
    name: str, public: bool = False, collaborative: bool = False, description: str = ""
) -> dict | None:
    ready, _ = _spotify_ready()
    if not ready:
        return None
    try:
        return client.sp.current_user_playlist_create(
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
            page = client.sp.current_user_playlists(limit=50, offset=offset) or {}
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
                client.sp.playlist_items(
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
        client.sp.playlist_replace_items(playlist_id, partes[0])
        for parte in partes[1:]:
            client.sp.playlist_add_items(playlist_id, parte)
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
            client.sp.start_playback(device_id=device_id, **kwargs)
        else:
            client.sp.start_playback(**kwargs)
        _time.sleep(0.5)
        return True
    except Exception as error:
        _spotify_log_error("start_playlist_context", error)
        return False


def _spotify_current_user_top_tracks(limit: int = 10) -> list[dict]:
    if client.sp is None or not hasattr(client.sp, "current_user_top_tracks"):
        return []
    tracks = []
    for time_range in ("short_term", "medium_term"):
        try:
            page = client.sp.current_user_top_tracks(
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
    if client.sp is None or not hasattr(client.sp, "current_user_recently_played"):
        return []
    try:
        page = client.sp.current_user_recently_played(limit=max(1, min(int(limit), 10))) or {}
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
    if client.sp is None or not hasattr(client.sp, "artist"):
        return genres
    for artist_id in artist_ids[:3]:
        if not artist_id:
            continue
        try:
            artist = client.sp.artist(artist_id) or {}
            for genre in artist.get("genres", []) or []:
                genre = str(genre or "").strip().lower()
                if genre and genre not in genres:
                    genres.append(genre)
        except Exception as error:
            _spotify_log_error("artist_genres", error)
    return genres


def _spotify_current_user_top_artists(limit: int = 8) -> list[dict]:
    if client.sp is None or not hasattr(client.sp, "current_user_top_artists"):
        return []
    try:
        page = client.sp.current_user_top_artists(
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
        or client.sp is None
        or not hasattr(client.sp, "audio_features")
    ):
        return []
    try:
        features = (client.sp.audio_features([track_id]) or [None])[0] or {}
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
    if client.sp is None or not hasattr(client.sp, "current_user_playlists"):
        return []
    user_id = _spotify_usuario_actual_id()
    if not user_id:
        return []
    try:
        offset = 0
        while True:
            page = client.sp.current_user_playlists(limit=50, offset=offset) or {}
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
    if not SPOTIFY_EXTENDED_QUOTA_MODE or client.sp is None:
        return []

    limit = max(1, min(int(limit), 30))
    track_id = track.get("id")
    artist_ids = [
        artist.get("id")
        for artist in track.get("artists") or []
        if artist.get("id")
    ]
    candidates = []

    if hasattr(client.sp, "recommendations") and (track_id or artist_ids):
        kwargs = {"limit": limit}
        if track_id:
            kwargs["seed_tracks"] = [track_id]
        if artist_ids:
            kwargs["seed_artists"] = artist_ids[:2]
        try:
            candidates.extend((client.sp.recommendations(**kwargs) or {}).get("tracks", []))
        except Exception as error:
            _spotify_log_error("recommendations", error)

    for query in _spotify_audio_feature_queries(track_id):
        candidates.extend(
            _spotify_search_tracks(q=query, limit=min(limit, 10))
        )

    def _artist_top_tracks(artist_id: str) -> list[dict]:
        if not hasattr(client.sp, "artist_top_tracks"):
            return []
        market = _spotify_market_objetivo()
        try:
            if market:
                return (client.sp.artist_top_tracks(artist_id, country=market) or {}).get(
                    "tracks", []
                )
            return (client.sp.artist_top_tracks(artist_id) or {}).get("tracks", [])
        except TypeError:
            try:
                return (client.sp.artist_top_tracks(artist_id) or {}).get("tracks", [])
            except Exception as error:
                _spotify_log_error("artist_top_tracks", error)
                return []
        except Exception as error:
            _spotify_log_error("artist_top_tracks", error)
            return []

    for artist_id in artist_ids[:2]:
        candidates.extend(_artist_top_tracks(artist_id))

    if hasattr(client.sp, "artist_related_artists"):
        for artist_id in artist_ids[:2]:
            try:
                related = client.sp.artist_related_artists(artist_id) or {}
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


def _spotify_start_playlist_like(uris: list[str], device_id: str | None) -> bool:
    ready, _ = _spotify_ready()
    if not ready:
        return False
    try:
        if not uris:
            return False
        if device_id:
            client.sp.start_playback(device_id=device_id, uris=uris)
        else:
            client.sp.start_playback(uris=uris)
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
        client.sp.start_playback(device_id=device_id, uris=[track_uri])
    else:
        client.sp.start_playback(uris=[track_uri])


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
