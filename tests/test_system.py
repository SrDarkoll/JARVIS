from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "src", "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


@pytest.fixture
def mock_jarvis_state(monkeypatch):
    from core import jarvis_state
    return jarvis_state


class TestAjustarVolumen:
    def test_ajustar_volumen_accepts_absolute_number(self, monkeypatch):
        from tools import system

        monkeypatch.setattr(system, "_ajustar_volumen_absoluto", lambda v: f"vol={v}")
        out = system.ajustar_volumen.invoke({"nivel": 50})
        assert "50" in out or "vol=50" in out

    def test_ajustar_volumen_rejects_non_numeric(self, monkeypatch):
        from tools import system

        monkeypatch.setattr(system, "_ajustar_volumen_absoluto", lambda v: f"vol={v}")
        out = system.ajustar_volumen.invoke({"nivel": "abc"})
        assert "numérico" in out.lower() or "error" in out.lower()

    def test_ajustar_volumen_relative_plus(self, monkeypatch):
        from tools import system

        calls = []
        monkeypatch.setattr(system, "_leer_volumen_actual", lambda: 30.0)
        monkeypatch.setattr(system, "_ajustar_volumen_absoluto", lambda v: calls.append(v) or f"vol={v}")
        system.ajustar_volumen.invoke({"nivel": "+10"})
        assert 40.0 in calls

    def test_ajustar_volumen_relative_minus(self, monkeypatch):
        from tools import system

        calls = []
        monkeypatch.setattr(system, "_leer_volumen_actual", lambda: 50.0)
        monkeypatch.setattr(system, "_ajustar_volumen_absoluto", lambda v: calls.append(v) or f"vol={v}")
        system.ajustar_volumen.invoke({"nivel": "-20"})
        assert 30.0 in calls


class TestModoNoMolestar:
    def test_modo_no_molestar_activar(self, monkeypatch):
        from tools import system

        mock_vol = MagicMock()
        monkeypatch.setattr(system, "_obtener_control_volumen", lambda: mock_vol)
        out = system.modo_no_molestar.invoke({"activar": True})
        mock_vol.SetMute.assert_called_once()
        assert "silenciado" in out.lower()

    def test_modo_no_molestar_desactivar(self, monkeypatch):
        from tools import system

        mock_vol = MagicMock()
        monkeypatch.setattr(system, "_obtener_control_volumen", lambda: mock_vol)
        out = system.modo_no_molestar.invoke({"activar": False})
        mock_vol.SetMute.assert_called_once()
        assert "activado" in out.lower() or "sonido" in out.lower()


class TestControlarPC:
    def test_controlar_pc_denied_without_confirmation(self, monkeypatch):
        from tools import system

        monkeypatch.setattr(system, "verificar_autorizacion", lambda pid: True)
        out = system.controlar_pc.invoke({"accion": "apagar", "el_usuario_ya_confirmo": False})
        assert "denegada" in out.lower() or "preguntale" in out.lower()

    def test_controlar_pc_denied_without_auth(self, monkeypatch):
        from tools import system

        monkeypatch.setattr(system, "verificar_autorizacion", lambda pid: False)
        out = system.controlar_pc.invoke({"accion": "apagar", "el_usuario_ya_confirmo": True})
        assert "acceso_denegado" in out.lower() or "autoriz" in out.lower()

    def test_controlar_pc_blocked_if_contains_apagar_but_is_reminder(self, monkeypatch):
        from tools import system

        monkeypatch.setattr(system, "verificar_autorizacion", lambda pid: True)
        out = system.controlar_pc.invoke({"accion": "apagar", "el_usuario_ya_confirmo": False})
        assert "denegada" in out.lower()


class TestMatarProceso:
    def test_matar_proceso_denied_without_auth(self, monkeypatch):
        from tools import system

        monkeypatch.setattr(system, "verificar_autorizacion", lambda pid: False)
        out = system.matar_proceso.invoke({"nombre": "notepad"})
        assert "denegado" in out.lower() or "acceso_denegado" in out.lower()

    def test_matar_proceso_reports_zero_when_not_found(self, monkeypatch):
        from tools import system

        monkeypatch.setattr(system, "verificar_autorizacion", lambda pid: True)
        mock_iter = MagicMock()
        mock_iter.__iter__ = MagicMock(return_value=iter([]))
        monkeypatch.setattr(system.psutil, "process_iter", lambda *args, **kwargs: mock_iter)
        out = system.matar_proceso.invoke({"nombre": "notepad"})
        assert "no se detectó" in out.lower()


class TestBorrarMemoria:
    def test_borrar_memoria_denied_without_auth(self, monkeypatch):
        from tools import system

        monkeypatch.setattr(system, "verificar_autorizacion", lambda pid: False)
        out = system.borrar_memoria.invoke({})
        assert "acceso_denegado" in out.lower()

    def test_borrar_memoria_calls_security_audit_on_success(self, monkeypatch):
        from tools import system
        from services import security_manager

        audit_calls = []
        monkeypatch.setattr(security_manager, "_security_audit", lambda *args, **kwargs: audit_calls.append((args, kwargs)))
        monkeypatch.setattr(system, "verificar_autorizacion", lambda pid: True)
        monkeypatch.setattr(system.jarvis_state, "chat_history", MagicMock())
        monkeypatch.setattr(system.jarvis_state, "DATOS_CURIOSOS", "")
        monkeypatch.setattr(system, "_perfiles_memoria", {})
        monkeypatch.setattr(system.jarvis_state, "_msg_counter_by_profile", {})

        with patch.object(system.os.path, "exists", return_value=False):
            with patch.object(system, "MEMORIA_FILE", "/fake/memoria.txt"):
                with patch.object(system, "MEMORIA_PROFILES_FILE", "/fake/profiles.txt"):
                    out = system.borrar_memoria.invoke({})

        assert len(audit_calls) == 1
        args, kwargs = audit_calls[0]
        assert args[0] == "memory_wipe"
        assert kwargs["level"] == "critical"
        assert kwargs["tool"] == "borrar_memoria"


class TestAbrirAplicacion:
    def test_abrir_aplicacion_denied_without_auth(self, monkeypatch):
        from tools import system

        monkeypatch.setattr(system, "verificar_autorizacion", lambda pid: False)
        out = system.abrir_aplicacion.invoke({"nombre_app": "notepad"})
        assert "acceso_denegado" in out.lower()


class TestVerProcesosPesados:
    def test_ver_procesos_pesados_returns_process_list(self, monkeypatch):
        from tools import system

        mock_proc = MagicMock()
        mock_proc.info = {"pid": 1234, "name": "python.exe", "cpu_percent": 15.5, "memory_percent": 2.1}
        mock_iter = MagicMock()
        mock_iter.__iter__ = MagicMock(return_value=iter([mock_proc]))
        monkeypatch.setattr(system.psutil, "process_iter", lambda *args, **kwargs: mock_iter)

        out = system.ver_procesos_pesados.invoke({})
        assert "Sensores" in out or "PID 1234" in out