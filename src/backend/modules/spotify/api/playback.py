"""Spotify Web API playback and playback-control orchestration."""

from __future__ import annotations

import re
import time as _time
from dataclasses import dataclass

from modules.spotify import config
from modules.spotify.api import client
from modules.spotify.api.client import (
    _spotify_activar_cliente,
    _spotify_classify_error,
    _spotify_dispositivo_objetivo,
    _spotify_get_all_devices,
    _spotify_log_error,
    _spotify_mejor_track,
    _spotify_queue_tracks,
    _spotify_ready,
    _spotify_search_tracks,
    _spotify_set_shuffle,
    _spotify_transfer_and_play,
    _spotify_usuario_actual_id,
)
from modules.spotify.api.recommendations import (
    _spotify_buscar_o_crear_playlist_automix,
    _spotify_obtener_similares,
    _spotify_reemplazar_playlist_con_uris,
    _spotify_start_playlist_context,
)
from modules.spotify.desktop.matching import normalize_text
from modules.spotify.messages import (
    is_english as _spotify_is_english,
    playback_success_message as _spotify_playback_success_message,
    text as _spotify_text,
    track_label as _spotify_track_label,
    track_plain_label as _spotify_track_plain_label,
)
from modules.spotify.state import set_last_requested_track

SPOTIFY_RADIO_QUEUE_SIZE = config.SPOTIFY_RADIO_QUEUE_SIZE
SPOTIFY_MODO_SIMILARES = config.SPOTIFY_MODO_SIMILARES
SPOTIFY_AUTO_SHUFFLE = config.SPOTIFY_AUTO_SHUFFLE

_PREFIJOS_SPOTIFY = re.compile(
    r"^(?:pon|reproduce|play|toca|ponme|dale|oye|ponle|pon me|"
    r"quiero escuchar|escucha|esc?chame|pon ahora|pon ya)\s+",
    re.IGNORECASE,
)
_PATRON_DE_ARTISTA = re.compile(
    r"^(.+?)\s+(?:de|by|of|del|por)\s+(.+)$",
    re.IGNORECASE,
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


@dataclass(frozen=True)
class SpotifyAPIPlaybackResult:
    ok: bool
    message: str
    capability_failure: bool = False


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


def _spotify_play_api_message(cancion: str) -> str:
    """Reproduce una canción con estrategia en cascada: dispositivo activo, transferencia, apertura, cola.

    Nunca retorna error genérico. Cada fallo tiene mensaje específico.
    """
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

        set_last_requested_track(_spotify_track_plain_label(track_name, artist))
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
            client.sp.start_playback(device_id=activo["id"], uris=[track_uri])
            _time.sleep(0.5)
            print(f"  [SPOTIFY] Estrategia A: play en activo '{activo['name']}'")
            set_last_requested_track(_spotify_track_plain_label(track_name, artist))
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
            client.sp.start_playback(device_id=activo_new["id"], uris=[track_uri])
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
        client.sp.add_to_queue(track_uri)  # Sin device_id → apunta al dispositivo activo
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
                lambda did: client.sp.pause_playback(device_id=did),
                lambda: client.sp.pause_playback(),
            )
            if err is None:
                return _spotify_text("Playback paused.", "Reproducción pausada.")
            return _resolver_error_player(err, _spotify_text("pause playback", "pausar"))
        elif accion in ["reanudar", "continuar", "play", "resume"]:
            err = _try_player_action(
                lambda did: client.sp.start_playback(device_id=did),
                lambda: client.sp.start_playback(),
            )
            if err is None:
                return _spotify_text("Playback resumed.", "Reproducción reanudada.")
            return _resolver_error_player(err, _spotify_text("resume playback", "reanudar"))
        elif accion in ["siguiente", "next", "skip"]:
            err = _try_player_action(
                lambda did: client.sp.next_track(device_id=did),
                lambda: client.sp.next_track(),
            )
            if err is None:
                return _spotify_text("Next track.", "Siguiente canción.")
            return _resolver_error_player(err, _spotify_text("skip to the next track", "pasar a la siguiente canción"))
        elif accion in ["anterior", "prev", "atrás", "atras"]:
            err = _try_player_action(
                lambda did: client.sp.previous_track(device_id=did),
                lambda: client.sp.previous_track(),
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
