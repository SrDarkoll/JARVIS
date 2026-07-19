"""Pruebas para tool_manager - cobertura de _tool_permitida_por_contexto."""

from __future__ import annotations

import os
import sys


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "src", "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


class TestToolPermitidaPorContexto:
    """Tests for _tool_permitida_por_contexto anti-hallucination guard."""

    def test_passes_for_control_panel_source(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "borrar_memoria", "clear memory", source="control_panel"
        )
        assert result is True

    def test_passes_for_router_source(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "controlar_pc", "turn off computer", source="router"
        )
        assert result is True

    def test_passes_for_routine_source(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "abrir_aplicacion", "open vscode", source="routine"
        )
        assert result is True

    def test_blocks_borrar_memoria_without_clear_memory_kw(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "borrar_memoria", "hello jarvis", source="unknown"
        )
        assert result is False

    def test_allows_borrar_memoria_with_clear_memory(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "borrar_memoria", "clear memory please", source="unknown"
        )
        assert result is True

    def test_allows_borrar_memoria_with_forget_everything(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "borrar_memoria", "forget everything about me", source="unknown"
        )
        assert result is True

    def test_blocks_matar_proceso_without_kill_keywords(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "matar_proceso", "show my processes", source="unknown"
        )
        assert result is False

    def test_allows_matar_proceso_with_kill_keyword(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "matar_proceso", "kill process chrome", source="unknown"
        )
        assert result is True

    def test_blocks_controlar_pc_without_shutdown_keywords(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "controlar_pc", "what time is it", source="unknown"
        )
        assert result is False

    def test_allows_controlar_pc_with_turn_off(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "controlar_pc", "turn off the lights", source="unknown"
        )
        assert result is True

    def test_allows_controlar_pc_with_restart(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "controlar_pc", "restart the server", source="unknown"
        )
        assert result is True

    def test_blocks_abrir_aplicacion_without_open_keywords(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "abrir_aplicacion", "tell me a joke", source="unknown"
        )
        assert result is False

    def test_allows_abrir_aplicacion_with_open(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "abrir_aplicacion", "open notepad", source="unknown"
        )
        assert result is True

    def test_allows_abrir_aplicacion_with_launch(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "abrir_aplicacion", "launch spotify", source="unknown"
        )
        assert result is True

    def test_empty_input_returns_false(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "ajustar_volumen", "", source="unknown"
        )
        assert result is False

    def test_routine_keyword_always_passes(self):
        from core.brain import tool_manager

        result = tool_manager._tool_permitida_por_contexto(
            "borrar_memoria", "run my evening routine", source="unknown"
        )
        assert result is True


class TestResultadoPareceError:
    def test_detects_error_in_result(self):
        from core.brain import tool_manager

        assert tool_manager._resultado_parece_error("error occurred") is True

    def test_detects_exception_in_result(self):
        from core.brain import tool_manager

        assert tool_manager._resultado_parece_error("exception thrown") is True

    def test_detects_timeout_in_result(self):
        from core.brain import tool_manager

        assert tool_manager._resultado_parece_error("request timeout") is True

    def test_ignores_access_denied(self):
        from core.brain import tool_manager

        assert tool_manager._resultado_parece_error("ACCESS_DENIED") is False

    def test_ignores_normal_response(self):
        from core.brain import tool_manager

        assert tool_manager._resultado_parece_error("playing music now") is False

    def test_empty_returns_false(self):
        from core.brain import tool_manager

        assert tool_manager._resultado_parece_error("") is False