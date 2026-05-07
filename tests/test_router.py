"""Tests for core/brain/router.py - improved coverage."""

from __future__ import annotations

import os
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

        import re
        assert re.compile(router._ACTION_START_PATTERN) is not None

    def test_trailing_request_re_is_valid_regex(self):
        from core.brain import router

        import re
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