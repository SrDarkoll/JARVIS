from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from difflib import SequenceMatcher

from modules.spotify.desktop.models import (
    MatchDecision,
    MatchStatus,
    SpotifyCandidate,
    SpotifyRequest,
)

_VARIANT_TERMS = {
    "acoustic",
    "cover",
    "cuarentena",
    "demo",
    "edit",
    "en vivo",
    "instrumental",
    "karaoke",
    "live",
    "mayo 2020",
    "quarantine",
    "remaster",
    "remix",
    "session",
    "sped up",
    "tribute",
    "version",
    "2020",
}


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_text.lower())).strip()


def _token_overlap(expected: str, actual: str) -> float:
    expected_tokens = set(normalize_text(expected).split())
    actual_tokens = set(normalize_text(actual).split())
    if not expected_tokens:
        return 0.0
    return len(expected_tokens & actual_tokens) / len(expected_tokens)


def _variant_penalty(request: SpotifyRequest, candidate: SpotifyCandidate) -> float:
    requested = normalize_text(request.raw)
    observed = normalize_text(f"{candidate.title} {candidate.subtitle} {candidate.artist}")
    penalty = 0.0
    for term in _VARIANT_TERMS:
        normalized_term = normalize_text(term)
        observed_has_term = f" {normalized_term} " in f" {observed} "
        requested_has_term = f" {normalized_term} " in f" {requested} "
        if observed_has_term and not requested_has_term:
            penalty += 0.12
    return min(penalty, 0.36)


def score_candidate(request: SpotifyRequest, candidate: SpotifyCandidate) -> float:
    expected_title = request.title or request.query
    title = normalize_text(candidate.title)
    title_sequence = SequenceMatcher(None, normalize_text(expected_title), title).ratio()
    title_tokens = _token_overlap(expected_title, candidate.title)
    score = (title_sequence * 0.55) + (title_tokens * 0.25)

    if request.artist:
        expected_artist = normalize_text(request.artist)
        observed_artist = normalize_text(candidate.artist)
        artist_components = {
            normalize_text(component)
            for component in re.split(
                r",|&|\b(?:and|feat\.?|featuring|y)\b",
                candidate.artist,
                flags=re.IGNORECASE,
            )
            if normalize_text(component)
        }
        if expected_artist == observed_artist:
            artist_sequence = 1.0
        elif expected_artist in artist_components:
            artist_sequence = 0.90
        else:
            artist_sequence = SequenceMatcher(
                None,
                expected_artist,
                observed_artist,
            ).ratio()
        score += artist_sequence * 0.20
        if artist_sequence < 0.45:
            score -= 0.18

    combined_candidate = f"{candidate.title} {candidate.artist}".strip()
    raw_query = request.raw or request.query
    combined_sequence = SequenceMatcher(
        None,
        normalize_text(raw_query),
        normalize_text(combined_candidate),
    ).ratio()
    combined_tokens = _token_overlap(raw_query, combined_candidate)
    combined_score = (combined_sequence * 0.70) + (combined_tokens * 0.30)
    score = max(score, combined_score)
    if candidate.kind != "track":
        score -= 0.20

    return max(0.0, min(1.0, score - _variant_penalty(request, candidate)))


def choose_candidate(
    request: SpotifyRequest,
    candidates: list[SpotifyCandidate],
) -> MatchDecision:
    ranked = sorted(
        (replace(item, score=score_candidate(request, item)) for item in candidates),
        key=lambda item: item.score,
        reverse=True,
    )
    if not ranked or ranked[0].score < 0.55:
        return MatchDecision(status=MatchStatus.NOT_FOUND)

    best = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    best_artist_in_query = bool(
        best.artist
        and (
            normalize_text(best.artist) in normalize_text(request.raw)
            or _token_overlap(best.artist, request.raw) >= 0.50
        )
    )
    same_primary_artist = bool(
        runner_up
        and (
            normalize_text(best.artist) == normalize_text(runner_up.artist)
            or (request.artist and normalize_text(request.artist) in normalize_text(best.artist) and normalize_text(request.artist) in normalize_text(runner_up.artist))
            or (best_artist_in_query and normalize_text(best.artist) in normalize_text(runner_up.artist))
            or (normalize_text(best.artist) in normalize_text(runner_up.artist))
            or (normalize_text(runner_up.artist) in normalize_text(best.artist))
            or (_token_overlap(best.artist, runner_up.artist) >= 0.50)
        )
    )
    different_artists = bool(
        runner_up
        and not same_primary_artist
        and normalize_text(best.artist) != normalize_text(runner_up.artist)
    )
    same_title_different_artist = bool(
        not request.artist
        and not best_artist_in_query
        and runner_up
        and normalize_text(best.title) == normalize_text(runner_up.title)
        and different_artists
    )
    margin = best.score - (runner_up.score if runner_up else 0.0)
    should_be_ambiguous = (
        best.score < 0.74
        or same_title_different_artist
        or (margin < 0.07 and not same_primary_artist and not request.artist and not best_artist_in_query)
    )
    if should_be_ambiguous:
        return MatchDecision(
            status=MatchStatus.AMBIGUOUS,
            alternatives=tuple(ranked[:3]),
            confidence=best.score,
        )
    return MatchDecision(
        status=MatchStatus.SELECTED,
        selected=best,
        confidence=best.score,
    )
