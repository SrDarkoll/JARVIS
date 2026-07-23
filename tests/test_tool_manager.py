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

    def test_detects_spanish_spotify_not_found_result(self):
        from core.brain import tool_manager

        assert (
            tool_manager._resultado_parece_error(
                "No encontre ese contenido en Spotify Desktop."
            )
            is True
        )

    def test_ignores_access_denied(self):
        from core.brain import tool_manager

        assert tool_manager._resultado_parece_error("ACCESS_DENIED") is False

    def test_ignores_normal_response(self):
        from core.brain import tool_manager

        assert tool_manager._resultado_parece_error("playing music now") is False

    def test_empty_returns_false(self):
        from core.brain import tool_manager

        assert tool_manager._resultado_parece_error("") is False


class TestToolExecutionBoundary:
    @staticmethod
    def _allow_tool(monkeypatch):
        from core.brain import security_engine, tool_manager

        monkeypatch.setattr(
            tool_manager,
            "_tool_permitida_por_contexto",
            lambda *_args, **_kwargs: True,
        )
        monkeypatch.setattr(
            tool_manager.security_manager,
            "_security_guard",
            lambda *_args, **_kwargs: (True, ""),
        )
        monkeypatch.setattr(
            security_engine,
            "_tool_requiere_autorizacion",
            lambda _tool_name: False,
        )

    def test_entry_deduplicates_same_request_and_step(self, monkeypatch):
        from core.brain import brain_state, tool_manager

        calls = []

        class Tool:
            def invoke(self, args):
                calls.append(dict(args))
                return "ok"

        self._allow_tool(monkeypatch)
        monkeypatch.setitem(brain_state.tool_map, "test_tool", Tool())

        first = tool_manager._invocar_tool_entry(
            "test_tool",
            {"value": 1},
            "run it",
            "test",
            "admin",
            request_id="dedupe-request",
            step_id="dedupe-step",
        )
        second = tool_manager._invocar_tool_entry(
            "test_tool",
            {"value": 1},
            "run it",
            "test",
            "admin",
            request_id="dedupe-request",
            step_id="dedupe-step",
        )

        assert calls == [{"value": 1}]
        assert str(first) == "ok"
        assert str(second) == "ok"

    def test_legacy_entry_calls_execute_independently(self, monkeypatch):
        from core.brain import brain_state, tool_manager

        calls = []

        class Tool:
            def invoke(self, args):
                calls.append(dict(args))
                return "ok"

        self._allow_tool(monkeypatch)
        monkeypatch.setitem(brain_state.tool_map, "legacy_test_tool", Tool())

        tool_manager._invocar_tool_entry(
            "legacy_test_tool", {"value": 1}, "run it", "test", "admin"
        )
        tool_manager._invocar_tool_entry(
            "legacy_test_tool", {"value": 1}, "run it", "test", "admin"
        )

        assert calls == [{"value": 1}, {"value": 1}]

    def test_entry_preserves_explicit_confirmation_guard(self, monkeypatch):
        from core.brain import brain_state, security_engine, tool_manager

        guard_calls = []
        tool_calls = []

        class Tool:
            def invoke(self, args):
                tool_calls.append(dict(args))
                return "should not run"

        monkeypatch.setattr(
            tool_manager,
            "_tool_permitida_por_contexto",
            lambda *_args, **_kwargs: True,
        )

        def reject_without_confirmation(
            tool_name,
            args,
            user_input,
            source,
            *,
            profile_id,
        ):
            guard_calls.append(
                (tool_name, dict(args), user_input, source, profile_id)
            )
            return False, "Explicit confirmation required."

        monkeypatch.setattr(
            tool_manager.security_manager,
            "_security_guard",
            reject_without_confirmation,
        )
        monkeypatch.setattr(
            security_engine,
            "_tool_requiere_autorizacion",
            lambda _tool_name: False,
        )
        monkeypatch.setitem(brain_state.tool_map, "dangerous_test_tool", Tool())

        result = tool_manager._invocar_tool_entry(
            "dangerous_test_tool",
            {"target": "test"},
            "do it",
            "test",
            "admin",
            request_id="confirmation-request",
            step_id="confirmation-step",
        )

        assert guard_calls == [
            (
                "dangerous_test_tool",
                {"target": "test"},
                "do it",
                "test",
                "admin",
            )
        ]
        assert tool_calls == []
        assert "confirm" in str(result).lower()

    def test_entry_preserves_profile_authorization(self, monkeypatch):
        from core.brain import (
            brain_state,
            history_manager,
            security_engine,
            tool_manager,
        )
        from utils import jarvis_auth

        pending_actions = []
        tool_calls = []

        class Tool:
            def invoke(self, args):
                tool_calls.append(dict(args))
                return "should not run"

        monkeypatch.setattr(
            tool_manager,
            "_tool_permitida_por_contexto",
            lambda *_args, **_kwargs: True,
        )
        monkeypatch.setattr(
            tool_manager.security_manager,
            "_security_guard",
            lambda *_args, **_kwargs: (True, ""),
        )
        monkeypatch.setattr(
            security_engine,
            "_tool_requiere_autorizacion",
            lambda _tool_name: True,
        )
        monkeypatch.setattr(
            jarvis_auth,
            "verificar_autorizacion",
            lambda _profile_id: False,
        )
        monkeypatch.setattr(
            history_manager,
            "_registrar_accion_pendiente_auth",
            lambda *args: pending_actions.append(args),
        )
        monkeypatch.setitem(brain_state.tool_map, "authorized_test_tool", Tool())

        result = tool_manager._invocar_tool_entry(
            "authorized_test_tool",
            {"target": "test"},
            "do it",
            "test",
            "guest",
            request_id="authorization-request",
            step_id="authorization-step",
        )

        assert pending_actions == [
            (
                "guest",
                "authorized_test_tool",
                {"target": "test"},
                "do it",
            )
        ]
        assert tool_calls == []
        assert "authorization" in str(result).lower()

    def test_entry_hides_raw_tool_exception(self, monkeypatch):
        from core.brain import brain_state, tool_manager

        class Tool:
            def invoke(self, _args):
                raise RuntimeError("secret-internal-detail")

        self._allow_tool(monkeypatch)
        monkeypatch.setitem(brain_state.tool_map, "failing_test_tool", Tool())

        result = tool_manager._invocar_tool_entry(
            "failing_test_tool",
            {},
            "run it",
            "test",
            "admin",
            request_id="failure-request",
            step_id="failure-step",
        )

        assert "secret-internal-detail" not in str(result)
        assert "failed" in str(result).lower()

    def test_security_reason_classifies_authorization_separately(self):
        from core.brain import tool_manager
        from core.command_pipeline.execution import (
            ToolAuthorizationRequiredError,
        )

        error = tool_manager._blocked_error(
            "Administrator authorization required."
        )

        assert isinstance(error, ToolAuthorizationRequiredError)
