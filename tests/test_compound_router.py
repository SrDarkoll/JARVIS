from __future__ import annotations

from core.command_pipeline.models import CommandRequest


def _plan(text: str, *, request_id: str = "compound-1"):
    from core.command_pipeline.deterministic import DeterministicPlanner

    request = CommandRequest.create(
        text=text,
        profile_id="admin",
        channel="chat",
        language="en",
        request_id=request_id,
        metadata={"default_location": "Malibu, CA"},
    )
    return DeterministicPlanner().plan(request)


def _steps(plan):
    return [(step.tool_name, dict(step.arguments)) for step in plan.steps]


def test_compound_router_plans_music_then_weather():
    plan = _plan(
        "ponme reggaeton lento de cnco y dime el clima para hoy en malibu"
    )

    assert plan is not None
    assert _steps(plan) == [
        ("reproducir_en_spotify", {"cancion": "reggaeton lento de cnco"}),
        ("obtener_clima", {"ciudad": "Malibu"}),
    ]
    assert [step.step_id for step in plan.steps] == ["step-1", "step-2"]


def test_compound_router_handles_verbose_nba_then_spotify():
    plan = _plan(
        "first, you need to search NBA matches for today and then I need you to "
        "put a song on Spotify that his name is Reggaeton Lento of CNCO. "
        "Can you do that for me?"
    )

    assert plan is not None
    assert [step.tool_name for step in plan.steps] == [
        "obtener_deportes_espn",
        "reproducir_en_spotify",
    ]
    assert plan.steps[0].arguments["consulta"] == "hoy"
    assert "reggaeton lento" in plan.steps[1].arguments["cancion"]
    assert "cnco" in plan.steps[1].arguments["cancion"]


def test_router_handles_spanish_reproduzcas_spotify_song():
    plan = _plan(
        "necesito que reproduzcas en spotify una cancion que se llama "
        "1000 miles de vanessa carlton"
    )

    assert plan is not None
    assert _steps(plan) == [
        (
            "reproducir_en_spotify",
            {"cancion": "1000 miles de vanessa carlton"},
        )
    ]


def test_router_routes_spotify_mix_request_to_mix_tool():
    plan = _plan("pon un mix similar a bad bunny en spotify")

    assert plan is not None
    assert _steps(plan) == [
        ("reproducir_mix_spotify", {"semilla": "bad bunny"})
    ]


def test_compound_router_handles_spanish_music_then_search():
    plan = _plan(
        "necesito que reproduzcas en spotify una cancion que se llama "
        "1000 miles de vanessa carlton y despues busques cuanto esta "
        "ahorita el kilo de tortillas"
    )

    assert plan is not None
    assert [step.tool_name for step in plan.steps] == [
        "reproducir_en_spotify",
        "buscar_en_internet",
    ]
    assert plan.steps[0].arguments["cancion"] == "1000 miles de vanessa carlton"
    assert "kilo de tortillas" in plan.steps[1].arguments["query"]


def test_topic_news_search_is_not_intercepted_by_briefing():
    plan = _plan("search the latest Python security news")

    assert plan is not None
    assert [step.tool_name for step in plan.steps] == ["buscar_en_internet"]


def test_explicit_briefing_request_remains_supported():
    from core.brain import processor

    assert processor._is_briefing_request("news")
    assert processor._is_briefing_request("daily news briefing")
    assert processor._is_briefing_request("resumen de noticias")
    assert not processor._is_briefing_request("search the latest Python security news")


def test_router_weather_wins_over_social_status():
    plan = _plan(
        "how are you today how is the weather doing like in malibu"
    )

    assert plan is not None
    assert _steps(plan) == [("obtener_clima", {"ciudad": "Malibu"})]


def test_compound_handles_reminder_then_music():
    plan = _plan(
        "recuerdame revisar el horno en 5 minutos y pon despacito",
    )

    assert plan is not None
    assert _steps(plan) == [
        (
            "poner_recordatorio",
            {"texto": "revisar el horno", "minutos": 5},
        ),
        ("reproducir_en_spotify", {"cancion": "despacito"}),
    ]


def test_compound_handles_music_then_reminder():
    plan = _plan(
        "pon despacito y recuerdame revisar el horno en 5 minutos",
    )

    assert plan is not None
    assert _steps(plan) == [
        ("reproducir_en_spotify", {"cancion": "despacito"}),
        (
            "poner_recordatorio",
            {"texto": "revisar el horno", "minutos": 5},
        ),
    ]


def test_compound_pause_then_sing_plans_each_action_once():
    plan = _plan(
        "Can you pause the music, please? And then you can sing me "
        "Bad Habit by Steve Lacey.",
    )

    assert plan is not None
    assert [step.tool_name for step in plan.steps] == [
        "controlar_reproduccion",
        "reproducir_en_spotify",
    ]
    assert plan.steps[1].arguments["cancion"] == "bad habit by steve lacey"


def test_partial_compound_returns_clarification_without_planned_actions():
    plan = _plan(
        "pause the music and then dance in circles",
    )

    assert plan is not None
    assert plan.steps == ()
    assert "dance in circles" in plan.direct_response.lower()
    assert plan.requires_follow_up is True


def test_router_stop_the_music_is_not_media_control():
    plan = _plan("jarvis can you stop the music please")

    assert plan is not None
    assert plan.steps == ()
    assert plan.direct_response == "No action taken."


def test_router_bare_stop_is_not_media_control():
    plan = _plan("stop")

    assert plan is not None
    assert plan.steps == ()
    assert plan.direct_response == "No action taken."


def test_router_spanish_para_music_is_not_media_control():
    plan = _plan("jarvis para la musica por favor")

    assert plan is not None
    assert plan.steps == ()
