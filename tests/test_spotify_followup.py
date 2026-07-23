import pytest
from core import jarvis_state
from core.brain import processor, router, tool_manager
from modules.spotify import service as spotify_service
from modules.spotify import tools as spotify_tools
from modules.spotify.api.playback import SpotifyAPIPlaybackResult
from modules.spotify.desktop.models import (
    DesktopResultStatus,
    SpotifyCandidate,
    SpotifyDesktopResult,
)
from modules.spotify.followup import (
    PendingSpotifySelections,
    SpotifySelectionStatus,
    pending_spotify_selections,
)
from modules.spotify.state import set_last_requested_track


def _choices():
    return (
        SpotifyCandidate(
            "canonical",
            "No Te Apartes de Mí (feat. Valeria Bertuccelli)",
            "Vicentico, Valeria Bertuccelli",
        ),
        SpotifyCandidate(
            "other",
            "Acaríñame",
            "Los Ángeles Azules, Julieta Venegas, Juan Ingaramo, Jay de la Cueva",
        ),
    )


@pytest.fixture(autouse=True)
def _clear_shared_pending_store():
    set_last_requested_track("")
    for profile_id in ("admin", "guest_unverified"):
        pending_spotify_selections.clear(profile_id)
    yield
    set_last_requested_track("")
    for profile_id in ("admin", "guest_unverified"):
        pending_spotify_selections.clear(profile_id)


def test_semantic_followup_resolves_choice_despite_asr_variants():
    store = PendingSpotifySelections(clock=lambda: 100.0)
    store.remember("guest_unverified", _choices())

    resolution = store.resolve(
        "guest_unverified",
        "La de no te partes de mí, de Valeria Vertuseli y Vicentico.",
    )

    assert resolution is not None
    assert resolution.status is SpotifySelectionStatus.SELECTED
    assert resolution.candidate == _choices()[0]
    assert not store.has_pending("guest_unverified")


def test_ordinal_followup_selects_requested_result():
    store = PendingSpotifySelections(clock=lambda: 100.0)
    store.remember("guest_unverified", _choices())

    resolution = store.resolve("guest_unverified", "La segunda")

    assert resolution is not None
    assert resolution.status is SpotifySelectionStatus.SELECTED
    assert resolution.candidate == _choices()[1]


def test_unrelated_request_does_not_consume_pending_selection():
    store = PendingSpotifySelections(clock=lambda: 100.0)
    store.remember("guest_unverified", _choices())

    resolution = store.resolve("guest_unverified", "Qué clima hace hoy")

    assert resolution is not None
    assert resolution.status is SpotifySelectionStatus.UNRELATED
    assert store.has_pending("guest_unverified")


def test_pending_selection_expires():
    now = [100.0]
    store = PendingSpotifySelections(clock=lambda: now[0], timeout=60.0)
    store.remember("guest_unverified", _choices())
    now[0] = 161.0

    assert store.resolve("guest_unverified", "La primera") is None
    assert not store.has_pending("guest_unverified")


def test_preflight_resolves_spotify_followup_before_dynamic_router(monkeypatch):
    calls = []
    pending_spotify_selections.remember("guest_unverified", _choices())

    def invoke(tool_name, args, user_input, source, profile_id):
        calls.append((tool_name, args, user_input, source, profile_id))
        return "Reproduciendo la selección solicitada."

    monkeypatch.setattr(processor, "_cargar_contexto_perfil", lambda pid: pid)
    monkeypatch.setattr(tool_manager, "_invocar_tool_entry", invoke)
    monkeypatch.setattr(
        router,
        "_router_hibrido",
        lambda _text: pytest.fail("the dynamic router must not run"),
    )

    reply, should_listen = processor._preflight(
        "La de no te apartes de mí, de Valeria Bertuccelli y Vicentico.",
        "guest_unverified",
        allow_compound=False,
    )

    assert reply == "Reproduciendo la selección solicitada."
    assert should_listen is False
    assert calls == [
        (
            "reproducir_en_spotify",
            {"cancion": ("No Te Apartes de Mí (feat. Valeria Bertuccelli) de Vicentico, Valeria Bertuccelli")},
            "La de no te apartes de mí, de Valeria Bertuccelli y Vicentico.",
            "spotify_clarification",
            "guest_unverified",
        )
    ]


def test_ambiguous_first_turn_is_stored_for_the_active_voice_profile(monkeypatch):
    result = SpotifyDesktopResult(
        status=DesktopResultStatus.AMBIGUOUS,
        message_key="spotify_ambiguous_results",
        choices=_choices(),
    )
    monkeypatch.setattr(
        spotify_service,
        "_spotify_desktop_result",
        lambda _song: result,
    )

    with jarvis_state.active_profile("guest_unverified"):
        message = spotify_service._spotify_play_desktop("No te apartes de mí")

    assert "matches" in message.lower() or "coincidencias" in message.lower()
    assert pending_spotify_selections.has_pending("guest_unverified")


def test_new_explicit_play_command_clears_an_old_selection(monkeypatch):
    pending_spotify_selections.remember("admin", _choices())
    monkeypatch.setattr(spotify_service, "SPOTIFY_PLAYBACK_MODE", "api")
    monkeypatch.setattr(
        spotify_service,
        "_spotify_play_api",
        lambda song: SpotifyAPIPlaybackResult(True, f"api:{song}"),
    )

    response = spotify_tools.reproducir_en_spotify.invoke({"cancion": "Killer Queen"})

    assert response == "api:Killer Queen"
    assert not pending_spotify_selections.has_pending("admin")


def test_repeat_command_uses_shared_spotify_state(monkeypatch):
    calls = []
    set_last_requested_track("Killer Queen by Queen")

    monkeypatch.setattr(processor, "_cargar_contexto_perfil", lambda pid: pid)
    monkeypatch.setattr(router, "_router_hibrido", lambda _text: None)
    monkeypatch.setattr(
        tool_manager,
        "_invocar_tool_entry",
        lambda tool_name, args, user_input, source, profile_id: calls.append(
            (tool_name, args, user_input, source, profile_id)
        )
        or "Playing it again.",
    )

    reply, should_listen = processor._preflight(
        "otra vez",
        "guest_unverified",
        allow_compound=False,
    )

    assert reply == "Playing it again."
    assert should_listen is False
    assert calls == [
        (
            "reproducir_en_spotify",
            {"cancion": "Killer Queen by Queen"},
            "otra vez",
            "fast_repeat",
            "guest_unverified",
        )
    ]
