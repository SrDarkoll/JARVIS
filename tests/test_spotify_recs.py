"""Pruebas unitarias para helpers de recomendaciones de Spotify."""

from tools import spotify


def test_spotify_ready_contract():
    ready, reason = spotify._spotify_ready()
    assert isinstance(ready, bool)
    if not ready:
        assert isinstance(reason, str)


def test_spotify_scope_supports_dynamic_mix_inputs():
    assert "user-top-read" in spotify.SPOTIFY_SCOPE
    assert "user-read-recently-played" in spotify.SPOTIFY_SCOPE


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
    monkeypatch.setattr(spotify, "sp", fake)
    monkeypatch.setattr(spotify, "_spotify_market_objetivo", lambda: None)

    seed = {
        "id": "seed",
        "uri": "spotify:track:seed",
        "name": "Seed",
        "album": {"id": "album1"},
        "artists": [{"id": "seed_artist", "name": "Seed Artist"}],
    }

    similares = spotify._spotify_obtener_similares(seed, limite=3)

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


def test_spotify_mix_tool_delegates_to_spotify_playback(monkeypatch):
    calls = []

    def fake_play(seed):
        calls.append(seed)
        return "mix-ok"

    monkeypatch.setattr(spotify, "_play_spotify_seed", fake_play)

    assert spotify.reproducir_mix_spotify.invoke({"semilla": "latin pop"}) == "mix-ok"
    assert calls == ["latin pop"]


def test_spotify_obtener_similares_handles_missing_seed_id():
    seed = {
        "name": "Track sin id",
        "artists": [{"name": "Artista"}],
    }
    similares = spotify._spotify_obtener_similares(seed, limite=3)
    assert isinstance(similares, list)


def test_spotify_obtener_similares_returns_bounded_list_for_minimal_track():
    seed = {
        "id": None,
        "name": "Track de prueba",
        "artists": [{"name": "Artista de prueba"}],
    }
    similares = spotify._spotify_obtener_similares(seed, limite=5)
    assert isinstance(similares, list)
    assert len(similares) <= 5


def test_spotify_mix_uses_album_and_artist_context_without_recommendations(monkeypatch):
    class FakeSpotify:
        def __init__(self):
            self.recommendations_called = False

        def recommendations(self, *args, **kwargs):
            self.recommendations_called = True
            return {"tracks": []}

        def album_tracks(self, album_id, limit=50):
            assert album_id == "album1"
            return {
                "items": [
                    {"id": "seed", "uri": "spotify:track:seed", "name": "Seed"},
                    {"id": "album-next", "uri": "spotify:track:album-next", "name": "Album Next"},
                ]
            }

        def artist_top_tracks(self, artist_id):
            assert artist_id in {"artist1", "related1"}
            return {
                "tracks": [
                    {"id": f"{artist_id}-top", "uri": f"spotify:track:{artist_id}-top", "name": "Top"},
                ]
            }

        def artist_related_artists(self, artist_id):
            assert artist_id == "artist1"
            return {"artists": [{"id": "related1", "name": "Related"}]}

    fake = FakeSpotify()
    monkeypatch.setattr(spotify, "sp", fake)
    monkeypatch.setattr(spotify, "_spotify_market_objetivo", lambda: None)

    seed = {
        "id": "seed",
        "uri": "spotify:track:seed",
        "name": "Seed",
        "album": {"id": "album1"},
        "artists": [{"id": "artist1", "name": "Artist"}],
    }

    similares = spotify._spotify_obtener_similares(seed, limite=3)

    assert not fake.recommendations_called
    assert [track["uri"] for track in similares] == [
        "spotify:track:album-next",
        "spotify:track:artist1-top",
        "spotify:track:related1-top",
    ]


def test_post_playback_starts_automix_playlist_context(monkeypatch):
    started = {}
    queued = []

    monkeypatch.setattr(spotify, "_spotify_dispositivo_objetivo", lambda: "device1")
    monkeypatch.setattr(spotify, "_spotify_set_shuffle", lambda state, device_id: True)
    monkeypatch.setattr(
        spotify,
        "_spotify_obtener_similares",
        lambda track, limite: [
            {"id": "sim1", "uri": "spotify:track:sim1"},
            {"id": "sim2", "uri": "spotify:track:sim2"},
        ],
    )
    monkeypatch.setattr(spotify, "_spotify_usuario_actual_id", lambda: "user1")
    monkeypatch.setattr(
        spotify,
        "_spotify_buscar_o_crear_playlist_automix",
        lambda user_id: {"id": "automix1"},
    )
    monkeypatch.setattr(spotify, "_spotify_reemplazar_playlist_con_uris", lambda playlist_id, uris: True)

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

    monkeypatch.setattr(spotify, "_spotify_start_playlist_context", fake_start_context)
    monkeypatch.setattr(spotify, "_spotify_queue_tracks", lambda uris, device_id: queued.extend(uris) or len(uris))

    spotify._post_playback_ok(
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

    monkeypatch.setattr(spotify, "sp", FakeSpotify())
    monkeypatch.setattr(spotify, "_spotify_dispositivo_objetivo", lambda: None)

    for accion in ("stop", "para"):
        response = spotify.controlar_reproduccion.invoke({"accion": accion})
        assert "not recognized" in response or "no reconocida" in response
