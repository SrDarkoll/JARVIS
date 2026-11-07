"""Music tool calls are allowed when the model's arguments are grounded in what the user said.

Utterances below come from real Gemini Live sessions where the previous
start-anchored regex guard rejected clear requests and every correction.
"""

from __future__ import annotations

import pytest
from modules.spotify import intent


@pytest.fixture(autouse=True)
def _clean_attempts():
    intent.clear_music_attempts()
    yield
    intent.clear_music_attempts()


@pytest.mark.parametrize(
    ("text", "args"),
    [
        ("Si puedes reproducir en Spotify Victory de Two Steps from Hell.", {"cancion": "Victory", "artista": "Two Steps From Hell"}),
        ("¿Puedes poner Mi primer día sin ti?", {"cancion": "El primer día sin ti"}),
        ("¿Me pones Despacito?", {"cancion": "Despacito", "artista": "Luis Fonsi"}),
        ("oye jarvis quiero escuchar Bohemian Rhapsody de Queen", {"cancion": "Bohemian Rhapsody", "artista": "Queen"}),
        ("ponme algo de Bad Bunny", {"cancion": "Bad Bunny"}),
        # STT mishearing: the model corrects the title, the transcript still overlaps.
        ("pon tití me preguntó", {"cancion": "Tití Me Preguntó", "artista": "Bad Bunny"}),
    ],
)
def test_grounded_requests_are_allowed(text, args):
    assert intent.music_request_grounded(text, args, profile_id="p1")


@pytest.mark.parametrize(
    ("text", "args"),
    [
        ("es que tengo una duda", {"cancion": "Duda"}),
        ("solo quiero hablar contigo", {"cancion": "Hablar contigo"}),
        ("no quiero que pongas música", {"cancion": "música"}),
        ("no reproduzcas nada en Spotify", {"cancion": "nada"}),
        ("Jarvis, no quiero que pongas Despacito", {"cancion": "Despacito"}),
        ("pon atención a lo que te digo", {"cancion": "atención"}),
        ("pon un ejemplo en español", {"cancion": "un ejemplo"}),
        ("reproduce el error del programa", {"cancion": "el error del programa"}),
        ("play chess with me", {"cancion": "chess"}),
        ("te explico que puedes poner música", {"cancion": "música"}),
        ("el mensaje dice can you play a song", {"cancion": "a song"}),
        # The model invents a title the user never said.
        ("pon algo", {"cancion": "Blinding Lights", "artista": "The Weeknd"}),
        # Song lyrics captured from the speakers, with a hallucinated play call.
        ("Jeres estar en esta misma mesa tu sentado justo enfrente", {"cancion": "El primer día sin ti", "artista": "Danna Paola"}),
    ],
)
def test_ungrounded_or_negated_requests_are_blocked(text, args):
    assert not intent.music_request_grounded(text, args, profile_id="p1")


def test_explicit_song_named_like_an_excluded_word_is_allowed():
    assert intent.music_request_grounded(
        "pon la canción Error de The Warning", {"cancion": "Error", "artista": "The Warning"}, profile_id="p1"
    )


@pytest.mark.parametrize(
    ("text", "args"),
    [
        ("Pues ese es el que te dije y la cantante es Dana.", {"cancion": "El primer día sin ti", "artista": "Danna Paola"}),
        ("primer día sin ti de Dana", {"cancion": "El primer día sin ti", "artista": "Danna Paola"}),
        ("la de Danna Paola", {"cancion": "El primer día sin ti", "artista": "Danna Paola"}),
        ("la canción se llama Victory de y el cantante", {"cancion": "Victory", "artista": "Two Steps From Hell"}),
    ],
)
def test_corrections_after_a_failed_attempt_are_allowed(text, args):
    intent.remember_music_attempt("p1", args["cancion"], "", succeeded=False)
    assert intent.music_request_grounded(text, args, profile_id="p1")


def test_corrections_without_a_recent_attempt_need_a_play_verb():
    assert not intent.music_request_grounded(
        "la de Danna Paola", {"cancion": "El primer día sin ti", "artista": "Danna Paola"}, profile_id="p1"
    )


def test_followup_window_is_per_profile_and_expires(monkeypatch):
    intent.remember_music_attempt("p1", "Victory", "", succeeded=False)
    args = {"cancion": "Victory", "artista": "Two Steps From Hell"}
    assert not intent.music_request_grounded("la de Two Steps from Hell", args, profile_id="other")

    real_monotonic = intent.time.monotonic
    monkeypatch.setattr(intent.time, "monotonic", lambda: real_monotonic() + intent.MUSIC_FOLLOWUP_SECONDS + 1)
    assert not intent.music_request_grounded("la de Two Steps from Hell", args, profile_id="p1")


def test_successful_attempt_does_not_open_a_followup_window():
    intent.remember_music_attempt("p1", "Victory", "Two Steps From Hell", succeeded=True)
    assert not intent.music_request_grounded(
        "la de Two Steps from Hell", {"cancion": "Victory", "artista": "Two Steps From Hell"}, profile_id="p1"
    )
