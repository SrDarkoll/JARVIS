"""Tests for core/brain/router.py - improved coverage."""

from __future__ import annotations

import os
import re
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "src", "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


class TestRouterConstants:
    def test_router_web_directo_has_standard_sites(self):
        from core.brain import router

        assert "facebook" in router._ROUTER_WEB_DIRECTO
        assert "youtube" in router._ROUTER_WEB_DIRECTO
        assert "google" in router._ROUTER_WEB_DIRECTO

    def test_router_app_candidatos_has_common_apps(self):
        from core.brain import router

        assert "vscode" in router._ROUTER_APP_CANDIDATOS
        assert "chrome" in router._ROUTER_APP_CANDIDATOS
        assert "notepad" in router._ROUTER_APP_CANDIDATOS

    def test_max_compound_steps_is_5(self):
        from core.brain import router

        assert router._MAX_COMPOUND_STEPS == 5

    def test_action_start_pattern_is_valid_regex(self):
        from core.brain import router

        assert re.compile(router._ACTION_START_PATTERN) is not None

    def test_trailing_request_re_is_valid_regex(self):
        from core.brain import router

        assert re.compile(router._TRAILING_REQUEST_RE) is not None


class TestRouterWebDirectoLookup:
    def test_facebook_maps_to_url(self):
        from core.brain import router

        assert "facebook" in router._ROUTER_WEB_DIRECTO
        assert "https://" in router._ROUTER_WEB_DIRECTO["facebook"]

    def test_youtube_maps_to_url(self):
        from core.brain import router

        assert "youtube" in router._ROUTER_WEB_DIRECTO

    def test_open_spotify_alias(self):
        from core.brain import router

        assert "open spotify" in router._ROUTER_WEB_DIRECTO


class TestRouterAppCandidatosSets:
    def test_vscode_in_candidates(self):
        from core.brain import router

        assert "vscode" in router._ROUTER_APP_CANDIDATOS

    def test_chrome_in_candidates(self):
        from core.brain import router

        assert "chrome" in router._ROUTER_APP_CANDIDATOS

    def test_discord_in_candidates(self):
        from core.brain import router

        assert "discord" in router._ROUTER_APP_CANDIDATOS

    def test_notepad_variations(self):
        from core.brain import router

        assert "notepad" in router._ROUTER_APP_CANDIDATOS
        assert "el bloc de notas" in router._ROUTER_APP_CANDIDATOS
        assert "bloc de notas" in router._ROUTER_APP_CANDIDATOS


class TestRouterNormalization:
    def test_extraer_objetivo_apertura_returns_tuple(self):
        from core.brain import router

        result = router._extraer_objetivo_apertura("abre chrome")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_extraer_objetivo_handles_empty_string(self):
        from core.brain import router

        result = router._extraer_objetivo_apertura("")
        assert result[0] == ""

    def test_router_dias_semana_english(self):
        from core.brain import router

        assert len(router._DAYS_EN) == 7
        assert "Monday" in router._DAYS_EN
        assert "Friday" in router._DAYS_EN

    def test_router_meses_english(self):
        from core.brain import router

        assert len(router._MONTHS_EN) == 12
        assert "January" in router._MONTHS_EN
        assert "December" in router._MONTHS_EN


class TestRouterDaysAndMonths:
    def test_days_english_all_present(self):
        from core.brain import router

        for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
            assert day in router._DAYS_EN

    def test_days_spanish_all_present(self):
        from core.brain import router

        assert len(router._DAYS_ES) == 7

    def test_months_english_all_present(self):
        from core.brain import router

        for month in ["January", "February", "March", "April", "May", "June",
                      "July", "August", "September", "October", "November", "December"]:
            assert month in router._MONTHS_EN

    def test_months_spanish_all_present(self):
        from core.brain import router

        assert len(router._MONTHS_ES) == 12


class TestRouterHelpers:
    def test_normalizar_ascii_function_exists(self):
        from core.brain import router

        assert callable(router._normalizar_ascii)

    def test_normalizar_ascii_handles_unicode(self):
        from core.brain import router

        result = router._normalizar_ascii("café")
        assert "cafe" in result

    def test_compactar_resumen_busqueda_function_exists(self):
        from core.brain import router

        assert callable(router._compactar_resumen_busqueda)

    def test_lang_is_english_function(self):
        from core.brain import router

        assert callable(router._lang_is_english)

    def test_clean_music_query_function(self):
        from core.brain import router

        assert callable(router._clean_music_query)

    def test_extract_music_request_function(self):
        from core.brain import router

        assert callable(router._extract_music_request)

    def test_extract_weather_city_function(self):
        from core.brain import router

        assert callable(router._extract_weather_city)

    def test_extract_weather_city_with_time_word_and_question_mark(self):
        from core.brain import router

        assert (
            router._extract_weather_city("How's the weather today in Malibu?")
            == "Malibu"
        )
        assert router._extract_weather_city("¿Cuál es el clima hoy en Reynosa?") == "Reynosa"

    def test_incomplete_weather_location_requests_city(self, monkeypatch):
        from core.command_pipeline.deterministic import DeterministicPlanner
        from core.command_pipeline.models import CommandRequest

        monkeypatch.setattr(
            "core.brain.tool_manager._invocar_tool_entry",
            lambda *_args, **_kwargs: pytest.fail("weather tool must not run"),
        )
        request = CommandRequest.create(
            text="What's the weather in?",
            profile_id="admin",
            channel="chat",
            language="en",
            request_id="weather-clarification",
        )

        plan = DeterministicPlanner().plan(request)

        assert plan is not None
        assert plan.steps == ()
        assert plan.direct_response == "Which city should I check?"
        assert plan.requires_follow_up is True

    def test_compound_result_labels_follow_active_language(self):
        from core.brain import router
        from utils.jarvis_i18n import get_current_language, set_current_language

        previous_language = get_current_language()
        try:
            set_current_language("en")
            assert "Step 1:" in router._format_compound_results([("one", "ok")])
            set_current_language("es")
            assert "Paso 1:" in router._format_compound_results([("uno", "ok")])
        finally:
            set_current_language(previous_language)


class TestRouterCompound:
    def test_router_compuesto_function_exists(self):
        from core.brain import router

        assert callable(router._router_compuesto)

    def test_split_compound_intents_function_exists(self):
        from core.brain import router

        assert callable(router._split_compound_intents)

    def test_format_compound_results_function_exists(self):
        from core.brain import router

        assert callable(router._format_compound_results)


class TestRouterRouterHibrido:
    def test_router_hibrido_function_exists(self):
        from core.brain import router

        assert callable(router._router_hibrido)


class TestRouterHasActionableMarker:
    def test_has_actionable_marker_function_exists(self):
        from core.brain import router

        assert callable(router._has_actionable_marker)


def test_router_evaluates_arithmetic_without_web_tool(monkeypatch):
    from core.command_pipeline.deterministic import DeterministicPlanner
    from core.command_pipeline.models import CommandRequest

    monkeypatch.setattr(
        "core.brain.tool_manager._invocar_tool_entry",
        lambda *_args, **_kwargs: pytest.fail("arithmetic must stay local"),
    )
    request = CommandRequest.create(
        text="Could you tell me what is 99,000 / 8?",
        profile_id="admin",
        channel="chat",
        language="en",
        request_id="arithmetic-1",
    )

    plan = DeterministicPlanner().plan(request)

    assert plan is not None
    assert plan.steps == ()
    assert plan.direct_response == "99,000 / 8 = 12,375."


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        (
            "Cual es la raiz cuadrada de 28?",
            "es",
            "La raiz cuadrada de 28 es aproximadamente 5.2915026221.",
        ),
        (
            "What is the square root of 81?",
            "en",
            "The square root of 81 is 9.",
        ),
    ],
)
def test_router_evaluates_square_root_locally(
    monkeypatch,
    text,
    language,
    expected,
):
    from core.command_pipeline.deterministic import DeterministicPlanner
    from core.command_pipeline.models import CommandRequest

    monkeypatch.setattr(
        "core.brain.tool_manager._invocar_tool_entry",
        lambda *_args, **_kwargs: pytest.fail("square root must stay local"),
    )
    request = CommandRequest.create(
        text=text,
        profile_id="admin",
        channel="chat",
        language=language,
        request_id=f"square-root-{language}",
    )

    plan = DeterministicPlanner().plan(request)

    assert plan is not None
    assert plan.steps == ()
    assert plan.direct_response == expected


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        (
            "Como esta el clima en Africa?",
            "es",
            "De que ciudad desea consultar el clima?",
        ),
        (
            "Dime el clima del Amazonas",
            "es",
            "De que ciudad desea consultar el clima?",
        ),
        (
            "What is the weather in Europe?",
            "en",
            "Which city should I check?",
        ),
    ],
)
def test_broad_weather_region_requests_a_precise_location(
    text,
    language,
    expected,
):
    from core.command_pipeline.deterministic import DeterministicPlanner
    from core.command_pipeline.models import CommandRequest

    request = CommandRequest.create(
        text=text,
        profile_id="admin",
        channel="chat",
        language=language,
        request_id=f"broad-weather-{language}-{len(text)}",
    )

    plan = DeterministicPlanner().plan(request)

    assert plan is not None
    assert plan.steps == ()
    assert plan.direct_response == expected
    assert plan.requires_follow_up is True


def test_preflight_reply_does_not_trigger_strict_web_retry(monkeypatch):
    from core.brain import brain_utils, processor

    monkeypatch.setattr(
        brain_utils,
        "_respuesta_necesita_web_forzarla",
        lambda *_args, **_kwargs: pytest.fail(
            "a routed preflight reply must not be replaced"
        ),
    )

    assert (
        processor._reply_needs_strict_web_retry(
            "How's the weather today?",
            "The weather in Malibu is sunny.",
            [],
            path="preflight",
        )
        is False
    )


def test_strict_web_detects_existing_spanish_web_tool_call(monkeypatch):
    from core import jarvis_config
    from core.brain import brain_utils, social_engine

    class ExistingWebCall:
        tool_calls = [
            {
                "name": "buscar_en_internet",
                "args": {"query": "latest news"},
                "id": "web-call",
            }
        ]

    monkeypatch.setattr(jarvis_config, "STRICT_WEB_SEARCH", True)
    monkeypatch.setattr(social_engine, "_debe_buscar_en_web", lambda _text: True)

    assert (
        brain_utils._respuesta_necesita_web_forzarla(
            "latest news",
            "Current results.",
            [ExistingWebCall()],
        )
        is False
    )

def test_router_does_not_call_guest_administrator(monkeypatch):
    from core.command_pipeline.deterministic import DeterministicPlanner
    from core.command_pipeline.models import CommandRequest

    request = CommandRequest.create(
        text="how are you today",
        profile_id="guest_unverified",
        channel="chat",
        language="en",
        request_id="guest-status",
    )

    plan = DeterministicPlanner().plan(request)

    assert plan is not None
    assert "Administrator" not in plan.direct_response
    assert "Guest" in plan.direct_response


def test_weather_planning_has_no_side_effects(monkeypatch):
    from core.command_pipeline.deterministic import DeterministicPlanner
    from core.command_pipeline.models import CommandRequest, PlanSource

    monkeypatch.setattr(
        "core.brain.tool_manager._invocar_tool_entry",
        lambda *_args, **_kwargs: pytest.fail("planner must not execute tools"),
    )
    request = CommandRequest.create(
        text="clima en Monterrey",
        profile_id="admin",
        channel="chat",
        language="es",
        request_id="weather-1",
    )

    plan = DeterministicPlanner().plan(request)

    assert plan is not None
    assert plan.source is PlanSource.DETERMINISTIC
    assert [(step.tool_name, dict(step.arguments)) for step in plan.steps] == [
        ("obtener_clima", {"ciudad": "Monterrey"})
    ]


def test_dangerous_action_is_planned_but_not_executed(monkeypatch):
    from core.command_pipeline.deterministic import DeterministicPlanner
    from core.command_pipeline.models import CommandRequest

    monkeypatch.setattr(
        "core.brain.tool_manager._invocar_tool_entry",
        lambda *_args, **_kwargs: pytest.fail("planner must not execute tools"),
    )
    request = CommandRequest.create(
        text="apaga la computadora",
        profile_id="admin",
        channel="chat",
        language="es",
        request_id="shutdown-1",
    )

    plan = DeterministicPlanner().plan(request)

    assert plan is not None
    assert [(step.tool_name, dict(step.arguments)) for step in plan.steps] == [
        ("controlar_pc", {"accion": "apagar"})
    ]


def test_spotify_followup_uses_read_only_request_snapshot(monkeypatch):
    from core.command_pipeline.deterministic import DeterministicPlanner
    from core.command_pipeline.models import CommandRequest

    monkeypatch.setattr(
        "core.brain.tool_manager._invocar_tool_entry",
        lambda *_args, **_kwargs: pytest.fail("planner must not execute tools"),
    )
    request = CommandRequest.create(
        text="la primera",
        profile_id="guest_unverified",
        channel="chat",
        language="es",
        request_id="spotify-followup-1",
        metadata={
            "spotify_pending_choices": (
                {
                    "title": "No Te Apartes de Mi",
                    "artist": "Vicentico, Valeria Bertuccelli",
                },
                {"title": "Acariname", "artist": "Los Angeles Azules"},
            )
        },
    )

    plan = DeterministicPlanner().plan(request)

    assert plan is not None
    assert [(step.tool_name, dict(step.arguments)) for step in plan.steps] == [
        (
            "reproducir_en_spotify",
            {
                "cancion": (
                    "No Te Apartes de Mi de Vicentico, Valeria Bertuccelli"
                )
            },
        )
    ]


def test_legacy_router_rejects_tool_execution():
    from core.brain import router

    with pytest.raises(RuntimeError, match="legacy_router_execution_removed"):
        router._router_hibrido("clima en Monterrey")


def test_router_source_does_not_import_processor_or_invoke_tools():
    from core.brain import router

    source = open(router.__file__, encoding="utf-8").read()

    assert "core.brain.processor" not in source
    assert "_invocar_tool_wrapper" not in source


def test_router_evaluates_complex_radical_math(monkeypatch):
    from core.command_pipeline.deterministic import DeterministicPlanner
    from core.command_pipeline.models import CommandRequest

    monkeypatch.setattr(
        "core.brain.tool_manager._invocar_tool_entry",
        lambda *_args, **_kwargs: pytest.fail("math must stay local"),
    )
    request = CommandRequest.create(
        text="√28 * 4 / 20 * 5 / 5 * 8 * 9 * 6 / 5 - 8 * 828.",
        profile_id="admin",
        channel="chat",
        language="es",
        request_id="math-radical-1",
    )

    plan = DeterministicPlanner().plan(request)

    assert plan is not None
    assert plan.steps == ()
    assert "-6,532.56" in plan.direct_response


def test_evaluar_expresion_matematica_tool():
    from tools.utilities import evaluar_expresion_matematica

    res = evaluar_expresion_matematica.invoke(
        {"expresion": "√28 * 4 / 20 * 5 / 5 * 8 * 9 * 6 / 5 - 8 * 828."}
    )
    assert "-6532.56" in res
