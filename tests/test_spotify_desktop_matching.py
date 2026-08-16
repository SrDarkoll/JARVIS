from modules.spotify.desktop.matching import choose_candidate, normalize_text
from modules.spotify.desktop.models import MatchStatus, SpotifyCandidate, SpotifyRequest


def candidate(title: str, artist: str, element_id: str) -> SpotifyCandidate:
    return SpotifyCandidate(
        element_id=element_id,
        title=title,
        artist=artist,
        kind="track",
    )


def test_normalize_text_removes_diacritics_and_noise():
    assert normalize_text("  No te APARTES de m\u00ed! ") == "no te apartes de mi"


def test_artist_match_wins_over_cover_and_live_versions():
    request = SpotifyRequest(
        raw="No te apartes de mi de Vicentico",
        query="No te apartes de mi Vicentico",
        title="No te apartes de mi",
        artist="Vicentico",
    )
    decision = choose_candidate(
        request,
        [
            candidate("No Te Apartes de M\u00ed (En Vivo)", "Tributo a Vicentico", "cover"),
            candidate("No Te Apartes de M\u00ed", "Vicentico", "expected"),
            candidate("No Te Apartes de M\u00ed", "Roberto Carlos", "original"),
        ],
    )

    assert decision.status is MatchStatus.SELECTED
    assert decision.selected is not None
    assert decision.selected.element_id == "expected"


def test_title_only_request_returns_ambiguity_for_close_artists():
    request = SpotifyRequest(
        raw="No te apartes de mi",
        query="No te apartes de mi",
        title="No te apartes de mi",
    )
    decision = choose_candidate(
        request,
        [
            candidate("No Te Apartes de M\u00ed", "Vicentico", "one"),
            candidate("No Te Apartes de M\u00ed", "Roberto Carlos", "two"),
        ],
    )

    assert decision.status is MatchStatus.AMBIGUOUS
    assert [item.element_id for item in decision.alternatives] == ["one", "two"]


def test_requested_live_variant_is_not_penalized():
    request = SpotifyRequest(
        raw="Comfortably Numb live de Pink Floyd",
        query="Comfortably Numb live Pink Floyd",
        title="Comfortably Numb live",
        artist="Pink Floyd",
    )
    decision = choose_candidate(
        request,
        [
            candidate("Comfortably Numb", "Pink Floyd", "studio"),
            candidate("Comfortably Numb - Live", "Pink Floyd", "live"),
        ],
    )

    assert decision.status is MatchStatus.SELECTED
    assert decision.selected is not None
    assert decision.selected.element_id == "live"


def test_empty_candidates_return_not_found():
    request = SpotifyRequest(raw="missing", query="missing", title="missing")
    assert choose_candidate(request, []).status is MatchStatus.NOT_FOUND


def test_title_and_artist_without_connector_matches_combined_candidate_text():
    request = SpotifyRequest(
        raw="What's Up? 4 Non Blondes",
        query="What's Up? 4 Non Blondes",
        title="What's Up? 4 Non Blondes",
    )
    decision = choose_candidate(
        request,
        [candidate("What's Up?", "4 Non Blondes", "canonical")],
    )

    assert decision.status is MatchStatus.SELECTED
    assert decision.selected is not None
    assert decision.selected.element_id == "canonical"


def test_canonical_collaboration_beats_unrequested_quarantine_variant():
    request = SpotifyRequest(
        raw="No te apartes de mi de Vicentico",
        query="No te apartes de mi Vicentico",
        title="No te apartes de mi",
        artist="Vicentico",
    )
    decision = choose_candidate(
        request,
        [
            candidate(
                "No Te Apartes de M\u00ed (feat. Valeria Bertuccelli)",
                "Vicentico, Valeria Bertuccelli",
                "canonical",
            ),
            candidate(
                "No Te Apartes de M\u00ed - Mayo 2020 Cuarentena",
                "Vicentico",
                "quarantine",
            ),
        ],
    )

    assert decision.status is MatchStatus.SELECTED
    assert decision.selected is not None
    assert decision.selected.element_id == "canonical"


def test_artist_in_raw_query_avoids_same_title_ambiguity():
    request = SpotifyRequest(
        raw="Blinding Lights The Weeknd",
        query="Blinding Lights The Weeknd",
        title="Blinding Lights The Weeknd",
        artist="",
    )
    decision = choose_candidate(
        request,
        [
            candidate("Blinding Lights", "The Weeknd", "weeknd"),
            candidate("Blinding Lights", "Saint Asonia", "asonia"),
        ],
    )

    assert decision.status is MatchStatus.SELECTED
    assert decision.selected is not None
    assert decision.selected.element_id == "weeknd"
