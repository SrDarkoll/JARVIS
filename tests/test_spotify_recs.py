"""Pruebas unitarias para helpers de recomendaciones de Spotify."""

import os
from pathlib import Path

import pytest
from core import jarvis_config
from modules.spotify import config as spotify_config
from modules.spotify import service as spotify_service
from modules.spotify import tools as spotify_tools
from modules.spotify.api import client as spotify_client
from modules.spotify.api import playback as spotify_playback
from modules.spotify.api import recommendations as spotify_recommendations
from modules.spotify.desktop.models import (
    DesktopResultStatus,
    SpotifyCandidate,
    SpotifyDesktopResult,
)
from modules.spotify.followup import pending_spotify_selections


@pytest.fixture(autouse=True)
def _disable_real_spotify_client(monkeypatch):
    """Keep unit tests independent from local credentials and OAuth state."""
    monkeypatch.setattr(spotify_client, "sp", None)
    monkeypatch.setattr(spotify_service, "SPOTIFY_PLAYBACK_MODE", "api")
    monkeypatch.setattr(spotify_service, "_spotify_api_capability_failed", False)
    monkeypatch.setattr(spotify_service, "_desktop_controller", None)


def _track(track_id: str, artist: str = "Artist") -> dict:
    return {
        "id": track_id,
        "uri": f"spotify:track:{track_id}",
        "name": track_id,
        "artists": [{"id": f"{artist}-id", "name": artist}],
    }


def test_spotify_ready_contract():
    ready, reason = spotify_client._spotify_ready()
    assert isinstance(ready, bool)
    if not ready:
        assert isinstance(reason, str)


def test_spotify_scope_supports_dynamic_mix_inputs():
    assert "user-top-read" in spotify_config.SPOTIFY_SCOPE
    assert "user-read-recently-played" in spotify_config.SPOTIFY_SCOPE


def test_spotify_uses_single_supported_cache_path():
    expected = (
        Path(os.environ["JARVIS_CACHE_DIR"])
        / "spotify-oauth-cache"
    )

    assert Path(jarvis_config.SPOTIFY_CACHE) == expected
    assert Path(spotify_config.SPOTIFY_CACHE) == expected


def test_spotify_redirect_requires_an_explicit_loopback_ip():
    assert spotify_config.redirect_error("http://127.0.0.1:8888/callback") is None
    assert spotify_config.redirect_error("http://[::1]:8888/callback") is None
    assert spotify_config.redirect_error("http://localhost:8888/callback")
    assert spotify_config.redirect_error("https://example.com/callback")
    assert spotify_config.redirect_error("not-a-uri")

    expected_enabled = bool(
        spotify_config.SPOTIFY_CLIENT_ID
        and spotify_config.SPOTIFY_CLIENT_SECRET
        and spotify_config.SPOTIFY_REDIRECT_ERROR is None
    )
    assert spotify_config.SPOTIFY_ENABLED is expected_enabled


def test_spotify_access_token_uses_supported_cache_handler(monkeypatch):
    class CacheHandler:
        def get_cached_token(self):
            return {"access_token": "cached-token"}

    class AuthManager:
        def __init__(self):
            self.calls = 0
            self.validations = 0
            self.cache_handler = CacheHandler()

        def validate_token(self, token_info):
            self.validations += 1
            return token_info

        def get_access_token(self, **_kwargs):
            self.calls += 1
            return None

    class FakeSpotify:
        def __init__(self):
            self.auth_manager = AuthManager()

    fake = FakeSpotify()
    monkeypatch.setattr(spotify_client, "sp", fake)

    assert spotify_client._spotify_access_token() == "cached-token"
    assert fake.auth_manager.validations == 1
    assert fake.auth_manager.calls == 0


def test_spotify_access_token_requests_the_non_deprecated_string_shape(monkeypatch):
    class CacheHandler:
        def get_cached_token(self):
            return None

    class AuthManager:
        def __init__(self):
            self.calls = []
            self.cache_handler = CacheHandler()

        def validate_token(self, token_info):
            return token_info

        def get_access_token(self, **kwargs):
            self.calls.append(kwargs)
            return "interactive-token"

    class FakeSpotify:
        def __init__(self):
            self.auth_manager = AuthManager()

    fake = FakeSpotify()
    monkeypatch.setattr(spotify_client, "sp", fake)

    assert spotify_client._spotify_access_token() == "interactive-token"
    assert fake.auth_manager.calls == [
        {"as_dict": False, "check_cache": False}
    ]


def test_spotify_dynamic_mix_uses_user_taste_and_genre_candidates(monkeypatch):
    class FakeSpotify:
        def __init__(self):
            self.search_queries = []

        def current_user(self):
            return {}

        def album_tracks(self, album_id, limit=50):
            return {"items": []}

        def artist(self, artist_id):
            assert artist_id == "seed_artist"
            return {"id": artist_id, "name": "Seed Artist", "genres": ["latin pop"]}

        def current_user_top_tracks(self, limit=10, time_range="medium_term"):
            return {
                "items": [
                    {
                        "id": "top-user",
                        "uri": "spotify:track:top-user",
                        "name": "User Top Track",
                        "artists": [{"id": "other1", "name": "Other Artist"}],
                    }
                ]
            }

        def current_user_recently_played(self, limit=10):
            return {
                "items": [
                    {
                        "track": {
                            "id": "recent-user",
                            "uri": "spotify:track:recent-user",
                            "name": "Recent Track",
                            "artists": [{"id": "other2", "name": "Recent Artist"}],
                        }
                    }
                ]
            }

        def current_user_top_artists(self, limit=8, time_range="medium_term"):
            return {
                "items": [
                    {"id": "top-artist", "name": "Top Artist", "genres": ["reggaeton"]}
                ]
            }

        def search(self, q, limit=10, type="track", **kwargs):
            self.search_queries.append((q, type, limit, kwargs))
            return {
                "tracks": {
                    "items": [
                        {
                            "id": "genre-latin",
                            "uri": "spotify:track:genre-latin",
                            "name": "Genre Match",
                            "artists": [{"id": "other3", "name": "Genre Artist"}],
                        }
                    ]
                }
            }

        def artist_top_tracks(self, *args, **kwargs):
            raise AssertionError("dynamic mix should not depend on removed artist top tracks")

        def artist_related_artists(self, *args, **kwargs):
            return {"artists": []}

    fake = FakeSpotify()
    monkeypatch.setattr(spotify_client, "sp", fake)
    monkeypatch.setattr(
        spotify_recommendations, "_spotify_market_objetivo", lambda: None
    )

    seed = {
        "id": "seed",
        "uri": "spotify:track:seed",
        "name": "Seed",
        "album": {"id": "album1"},
        "artists": [{"id": "seed_artist", "name": "Seed Artist"}],
    }

    similares = spotify_recommendations._spotify_obtener_similares(seed, limite=3)

    assert [track["uri"] for track in similares] == [
        "spotify:track:top-user",
        "spotify:track:recent-user",
        "spotify:track:genre-latin",
    ]
    assert any('genre:"latin pop"' in q for q, _type, _limit, _kwargs in fake.search_queries)


def test_spotify_mix_tool_is_exported_in_base_tools():
    from tools import _get_base_tools_impl

    tool_names = {getattr(tool, "name", "") for tool in _get_base_tools_impl()}

    assert "reproducir_mix_spotify" in tool_names


def test_tools_package_preserves_legacy_spotify_exports():
    import tools

    assert tools.reproducir_en_spotify is spotify_tools.reproducir_en_spotify
    assert tools.reproducir_mix_spotify is spotify_tools.reproducir_mix_spotify
    assert tools.controlar_reproduccion is spotify_tools.controlar_reproduccion


def test_spotify_mix_tool_delegates_to_spotify_playback(monkeypatch):
    calls = []

    def fake_play(seed):
        calls.append(seed)
        return "mix-ok"

    monkeypatch.setattr(spotify_service, "_play_spotify_seed", fake_play)

    assert spotify_tools.reproducir_mix_spotify.invoke({"semilla": "latin pop"}) == "mix-ok"
    assert calls == ["latin pop"]


def test_spotify_obtener_similares_handles_missing_seed_id():
    seed = {
        "name": "Track sin id",
        "artists": [{"name": "Artista"}],
    }
    similares = spotify_recommendations._spotify_obtener_similares(seed, limite=3)
    assert isinstance(similares, list)


def test_spotify_obtener_similares_returns_bounded_list_for_minimal_track():
    seed = {
        "id": None,
        "name": "Track de prueba",
        "artists": [{"name": "Artista de prueba"}],
    }
    similares = spotify_recommendations._spotify_obtener_similares(seed, limite=5)
    assert isinstance(similares, list)
    assert len(similares) <= 5


def test_spotify_default_mix_never_calls_restricted_endpoints(monkeypatch):
    class FakeSpotify:
        def __init__(self):
            self.search_calls = []

        def recommendations(self, *args, **kwargs):
            raise AssertionError("recommendations is unavailable in development mode")

        def audio_features(self, *args, **kwargs):
            raise AssertionError("audio features is unavailable in development mode")

        def artist_related_artists(self, *args, **kwargs):
            raise AssertionError("related artists is unavailable in development mode")

        def artist_top_tracks(self, *args, **kwargs):
            raise AssertionError("artist top tracks is unavailable in development mode")

        def album_tracks(self, album_id, limit=50):
            assert album_id == "album1"
            return {
                "items": [
                    _track("seed"),
                    _track("album-next"),
                ]
            }

        def current_user_top_tracks(self, limit=10, time_range="medium_term"):
            return {"items": [_track("top-user", "Top Artist")]}

        def current_user_recently_played(self, limit=10):
            return {"items": [{"track": _track("recent-user", "Recent Artist")}]}

        def current_user_top_artists(self, limit=8, time_range="medium_term"):
            return {"items": [{"name": "Top Artist", "genres": ["latin pop"]}]}

        def artist(self, artist_id):
            return {"id": artist_id, "name": "Artist", "genres": ["rock"]}

        def current_user(self):
            return {"id": "owner"}

        def current_user_playlists(self, limit=50, offset=0):
            return {"items": [], "next": None}

        def search(self, q, limit=10, type="track", **kwargs):
            assert type == "track", "default mode must not inspect public playlist contents"
            assert limit <= 10
            self.search_calls.append((q, limit, kwargs))
            return {"tracks": {"items": [_track(f"search-{len(self.search_calls)}")]}}

    fake = FakeSpotify()
    monkeypatch.setattr(spotify_client, "sp", fake)
    monkeypatch.setattr(
        spotify_recommendations, "_spotify_market_objetivo", lambda: None
    )
    monkeypatch.setattr(
        spotify_recommendations, "SPOTIFY_EXTENDED_QUOTA_MODE", False
    )

    seed = {
        "id": "seed",
        "uri": "spotify:track:seed",
        "name": "Seed",
        "album": {"id": "album1"},
        "artists": [{"id": "artist1", "name": "Artist"}],
    }

    similares = spotify_recommendations._spotify_obtener_similares(seed, limite=8)

    assert similares
    assert len(similares) <= 8
    assert fake.search_calls
    assert all(limit <= 10 for _query, limit, _kwargs in fake.search_calls)


def test_spotify_extended_quota_endpoints_are_opt_in(monkeypatch):
    class FakeSpotify:
        def __init__(self):
            self.calls = []

        def recommendations(self, **kwargs):
            self.calls.append("recommendations")
            return {"tracks": [_track("recommended")]}

        def audio_features(self, track_ids):
            self.calls.append("audio_features")
            return [{"energy": 0.9, "danceability": 0.8}]

        def artist_top_tracks(self, artist_id, **kwargs):
            self.calls.append(f"top:{artist_id}")
            return {"tracks": [_track(f"top-{artist_id}")]}

        def artist_related_artists(self, artist_id):
            self.calls.append(f"related:{artist_id}")
            return {"artists": [{"id": "related", "name": "Related"}]}

        def search(self, q, limit=10, type="track", **kwargs):
            return {"tracks": {"items": [_track("feature-search")]}}

    fake = FakeSpotify()
    monkeypatch.setattr(spotify_client, "sp", fake)
    monkeypatch.setattr(
        spotify_recommendations, "_spotify_market_objetivo", lambda: None
    )
    seed = {
        "id": "seed",
        "uri": "spotify:track:seed",
        "name": "Seed",
        "artists": [{"id": "artist1", "name": "Artist"}],
    }

    monkeypatch.setattr(
        spotify_recommendations, "SPOTIFY_EXTENDED_QUOTA_MODE", False
    )
    assert spotify_recommendations._spotify_extended_quota_candidates(seed, limit=8) == []
    assert fake.calls == []

    monkeypatch.setattr(
        spotify_recommendations, "SPOTIFY_EXTENDED_QUOTA_MODE", True
    )
    candidates = spotify_recommendations._spotify_extended_quota_candidates(seed, limit=8)

    assert candidates
    assert "recommendations" in fake.calls
    assert "audio_features" in fake.calls
    assert "top:artist1" in fake.calls
    assert "related:artist1" in fake.calls


def test_spotify_automix_reads_new_and_legacy_playlist_item_shapes(monkeypatch):
    class FakeSpotify:
        def playlist_items(self, **kwargs):
            return {
                "items": [
                    {"item": _track("new-shape")},
                    {"track": _track("legacy-shape")},
                    {"item": {"type": "episode", "uri": "spotify:episode:skip"}},
                ],
                "next": None,
            }

    monkeypatch.setattr(spotify_client, "sp", FakeSpotify())

    assert spotify_recommendations._spotify_obtener_uris_playlist("automix") == [
        "spotify:track:new-shape",
        "spotify:track:legacy-shape",
    ]


def test_spotify_creates_automix_with_current_user_playlist_api(monkeypatch):
    class FakeSpotify:
        def __init__(self):
            self.created = []

        def current_user_playlist_create(self, **kwargs):
            self.created.append(kwargs)
            return {"id": "automix", **kwargs}

    fake = FakeSpotify()
    monkeypatch.setattr(spotify_client, "sp", fake)

    result = spotify_recommendations._spotify_crear_playlist_me(
        name="JARVIS AutoMix",
        public=False,
        collaborative=False,
        description="JARVIS mix",
    )

    assert result["id"] == "automix"
    assert fake.created == [
        {
            "name": "JARVIS AutoMix",
            "public": False,
            "collaborative": False,
            "description": "JARVIS mix",
        }
    ]


def test_spotify_logs_provider_error_types_without_raw_messages(monkeypatch, capsys):
    class FakeSpotify:
        def current_user(self):
            raise RuntimeError("provider-body-with-secret-token")

    monkeypatch.setattr(spotify_client, "sp", FakeSpotify())

    assert spotify_client._spotify_usuario_actual_id() is None
    output = capsys.readouterr().out
    assert "RuntimeError" in output
    assert "provider-body-with-secret-token" not in output


def test_spotify_playback_errors_do_not_expose_provider_details(monkeypatch, capsys):
    secret_error = r"provider failed through C:\Users\ramir\private\oauth-token"

    class FakeSpotify:
        def pause_playback(self, *args, **kwargs):
            raise RuntimeError(secret_error)

    monkeypatch.setattr(spotify_client, "sp", FakeSpotify())
    monkeypatch.setattr(spotify_playback, "_spotify_dispositivo_objetivo", lambda: None)
    monkeypatch.setattr(spotify_playback, "_spotify_activar_cliente", lambda: False)

    response = spotify_tools.controlar_reproduccion.invoke({"accion": "pausar"})
    output = capsys.readouterr().out

    assert secret_error not in response
    assert secret_error not in output
    assert "RuntimeError" in output


def test_post_playback_starts_automix_playlist_context(monkeypatch):
    started = {}
    queued = []

    monkeypatch.setattr(spotify_playback, "_spotify_dispositivo_objetivo", lambda: "device1")
    monkeypatch.setattr(spotify_playback, "_spotify_set_shuffle", lambda state, device_id: True)
    monkeypatch.setattr(
        spotify_playback,
        "_spotify_obtener_similares",
        lambda track, limite: [
            {"id": "sim1", "uri": "spotify:track:sim1"},
            {"id": "sim2", "uri": "spotify:track:sim2"},
        ],
    )
    monkeypatch.setattr(spotify_playback, "_spotify_usuario_actual_id", lambda: "user1")
    monkeypatch.setattr(
        spotify_playback,
        "_spotify_buscar_o_crear_playlist_automix",
        lambda user_id: {"id": "automix1"},
    )
    monkeypatch.setattr(
        spotify_playback,
        "_spotify_reemplazar_playlist_con_uris",
        lambda playlist_id, uris: True,
    )

    def fake_start_context(playlist_id, device_id, offset_uri=None, offset_position=None):
        started.update(
            {
                "playlist_id": playlist_id,
                "device_id": device_id,
                "offset_uri": offset_uri,
                "offset_position": offset_position,
            }
        )
        return True

    monkeypatch.setattr(spotify_playback, "_spotify_start_playlist_context", fake_start_context)
    monkeypatch.setattr(
        spotify_playback,
        "_spotify_queue_tracks",
        lambda uris, device_id: queued.extend(uris) or len(uris),
    )

    spotify_playback._post_playback_ok(
        "Seed",
        "Artist",
        "spotify:track:seed",
        "seed",
        track={
            "id": "seed",
            "uri": "spotify:track:seed",
            "name": "Seed",
            "artists": [{"id": "artist1", "name": "Artist"}],
        },
    )

    assert started == {
        "playlist_id": "automix1",
        "device_id": "device1",
        "offset_uri": "spotify:track:seed",
        "offset_position": None,
    }
    assert queued == []


def test_spotify_control_rejects_stop_and_para_aliases(monkeypatch):
    class FakeSpotify:
        def pause_playback(self, *args, **kwargs):
            raise AssertionError("stop/para aliases must not pause playback")

    monkeypatch.setattr(spotify_client, "sp", FakeSpotify())
    monkeypatch.setattr(spotify_playback, "_spotify_dispositivo_objetivo", lambda: None)

    for accion in ("stop", "para"):
        response = spotify_tools.controlar_reproduccion.invoke({"accion": accion})
        assert "not recognized" in response or "no reconocida" in response


def test_auto_mode_uses_desktop_without_cached_api_token(monkeypatch):
    calls = []
    monkeypatch.setattr(spotify_service, "SPOTIFY_PLAYBACK_MODE", "auto")
    monkeypatch.setattr(spotify_service, "_spotify_has_valid_cached_token", lambda: False)
    monkeypatch.setattr(
        spotify_service,
        "_spotify_play_desktop",
        lambda song: calls.append(song) or "desktop-ok",
    )

    assert (
        spotify_tools.reproducir_en_spotify.invoke({"cancion": "Killer Queen"})
        == "desktop-ok"
    )
    assert calls == ["Killer Queen"]


def test_api_mode_keeps_explicit_spotipy_path(monkeypatch):
    monkeypatch.setattr(spotify_service, "SPOTIFY_PLAYBACK_MODE", "api")
    monkeypatch.setattr(
        spotify_service,
        "_spotify_play_api",
        lambda song: spotify_playback.SpotifyAPIPlaybackResult(
            ok=True,
            message=f"api:{song}",
        ),
    )
    monkeypatch.setattr(
        spotify_service,
        "_spotify_play_desktop",
        lambda _song: (_ for _ in ()).throw(
            AssertionError("desktop must not run")
        ),
    )

    assert (
        spotify_tools.reproducir_en_spotify.invoke({"cancion": "Killer Queen"})
        == "api:Killer Queen"
    )


def test_auto_mode_falls_back_after_permanent_api_capability_failure(monkeypatch):
    monkeypatch.setattr(spotify_service, "SPOTIFY_PLAYBACK_MODE", "auto")
    monkeypatch.setattr(spotify_service, "_spotify_has_valid_cached_token", lambda: True)
    monkeypatch.setattr(
        spotify_service,
        "_spotify_play_api",
        lambda _song: spotify_playback.SpotifyAPIPlaybackResult(
            ok=False,
            message="blocked",
            capability_failure=True,
        ),
    )
    monkeypatch.setattr(spotify_service, "_spotify_play_desktop", lambda _song: "desktop-ok")

    assert (
        spotify_tools.reproducir_en_spotify.invoke({"cancion": "Killer Queen"})
        == "desktop-ok"
    )
    assert spotify_service._spotify_api_capability_failed


def test_desktop_ambiguity_is_localized(monkeypatch):
    result = SpotifyDesktopResult(
        status=DesktopResultStatus.AMBIGUOUS,
        message_key="spotify_ambiguous_results",
        choices=(
            SpotifyCandidate("one", "No Te Apartes de M\u00ed", "Vicentico"),
            SpotifyCandidate("two", "No Te Apartes de M\u00ed", "Roberto Carlos"),
        ),
    )
    monkeypatch.setattr(spotify_service, "_spotify_desktop_result", lambda _song: result)

    message = spotify_service._spotify_play_desktop("No te apartes de mi")

    assert "Vicentico" in message
    assert "Roberto Carlos" in message
    pending_spotify_selections.clear("admin")


def test_desktop_search_uses_a_natural_query_not_web_api_syntax():
    request_data = spotify_service._spotify_desktop_request(
        "No te apartes de mi de Vicentico"
    )

    assert request_data.query == "no te apartes de mi vicentico"
    assert "track:" not in request_data.query
    assert request_data.artist == "vicentico"


def test_cached_token_probe_never_starts_oauth(monkeypatch):
    class CacheHandler:
        def get_cached_token(self):
            return {"access_token": "cached"}

    class AuthManager:
        cache_handler = CacheHandler()

        def validate_token(self, token):
            return token

        def get_access_token(self, **_kwargs):
            raise AssertionError("cache probe must not start OAuth")

    class FakeSpotify:
        auth_manager = AuthManager()

    monkeypatch.setattr(spotify_client, "sp", FakeSpotify())

    assert spotify_client._spotify_has_valid_cached_token()


def test_desktop_mode_routes_playback_controls_to_uia(monkeypatch):
    calls = []

    class Controller:
        def control(self, action):
            calls.append(action)
            return SpotifyDesktopResult(
                status=DesktopResultStatus.SUCCESS,
                message_key="spotify_control_complete",
            )

    monkeypatch.setattr(spotify_service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    monkeypatch.setattr(spotify_service, "_get_desktop_controller", lambda: Controller())

    response = spotify_tools.controlar_reproduccion.invoke({"accion": "pausar"})

    assert calls == ["pause"]
    assert "paused" in response.lower() or "pausada" in response.lower()


def test_desktop_control_reports_unverified_state_change(monkeypatch):
    class Controller:
        def control(self, _action):
            return SpotifyDesktopResult(
                status=DesktopResultStatus.FAILED,
                message_key="spotify_control_not_verified",
            )

    monkeypatch.setattr(spotify_service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    monkeypatch.setattr(spotify_service, "_get_desktop_controller", lambda: Controller())

    response = spotify_tools.controlar_reproduccion.invoke({"accion": "pausar"})

    assert "verify" in response.lower() or "verificar" in response.lower()
