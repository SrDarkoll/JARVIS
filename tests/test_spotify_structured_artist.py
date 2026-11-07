"""The model can pass the artist separately so similar titles resolve without guessing."""

from __future__ import annotations

from modules.spotify import service, tools
from modules.spotify.desktop.matching import choose_candidate
from modules.spotify.desktop.models import MatchStatus, SpotifyCandidate


def _covers() -> list[SpotifyCandidate]:
    return [
        SpotifyCandidate("1", "El Primer Día Sin Ti", "Mana Covers Band"),
        SpotifyCandidate("2", "El Primer Día Sin Ti", "Danna Paola"),
        SpotifyCandidate("3", "El Primer Día Sin Ti", "Karaoke Hits"),
    ]


def test_desktop_request_uses_explicit_artist_verbatim():
    request = service._spotify_desktop_request("el primer día sin ti", artist="Danna Paola")

    assert request.title == "el primer día sin ti"
    assert request.artist == "danna paola"
    assert request.query == "el primer día sin ti danna paola"


def test_desktop_request_without_artist_keeps_legacy_parsing():
    request = service._spotify_desktop_request("Killer Queen de Queen")

    assert request.title == "killer queen"
    assert request.artist == "queen"


def test_same_title_is_ambiguous_without_artist_but_resolves_with_it():
    without = choose_candidate(service._spotify_desktop_request("el primer día sin ti"), _covers())
    assert without.status is MatchStatus.AMBIGUOUS

    with_artist = choose_candidate(
        service._spotify_desktop_request("el primer día sin ti", artist="Danna Paola"),
        _covers(),
    )
    assert with_artist.status is MatchStatus.SELECTED
    assert with_artist.selected is not None
    assert with_artist.selected.artist == "Danna Paola"


def test_play_tool_forwards_artist_to_service(monkeypatch):
    seen: dict[str, str] = {}

    def fake_play(song, artist=""):
        seen["song"], seen["artist"] = song, artist
        return service.SpotifyServicePlaybackOutcome("ok", ok=True, verified=True)

    monkeypatch.setattr(service, "_play_outcome", fake_play)
    tools.reproducir_en_spotify.invoke({"cancion": "Victory", "artista": "Two Steps From Hell"})

    assert seen == {"song": "Victory", "artist": "Two Steps From Hell"}


def test_play_tool_schema_exposes_optional_artist():
    schema = tools.reproducir_en_spotify.args
    assert "cancion" in schema
    assert "artista" in schema
    assert "artista" not in (tools.reproducir_en_spotify.tool_call_schema.model_json_schema().get("required") or [])


def test_play_outcome_routes_artist_to_desktop(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_desktop(song, artist=""):
        calls.append((song, artist))
        return service.SpotifyServicePlaybackOutcome("ok", ok=True, verified=True)

    monkeypatch.setattr(service, "SPOTIFY_PLAYBACK_MODE", "desktop")
    monkeypatch.setattr(service, "_spotify_play_desktop_outcome", fake_desktop)

    service._play_outcome("Victory", artist="Two Steps From Hell")

    assert calls == [("Victory", "Two Steps From Hell")]
