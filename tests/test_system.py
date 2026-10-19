from __future__ import annotations

import os
import sys
from types import SimpleNamespace
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
        assert out.status.value == "failed"
        assert out.diagnostic_code == "audio_level_invalid"

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

    def test_ajustar_volumen_natural_words_subir_bajar(self, monkeypatch):
        from tools import system

        calls = []
        monkeypatch.setattr(system, "_leer_volumen_actual", lambda: 50.0)
        monkeypatch.setattr(system, "_ajustar_volumen_absoluto", lambda v: calls.append(v) or f"vol={v}")
        system.ajustar_volumen.invoke({"nivel": "subir"})
        assert 60.0 in calls

        calls.clear()
        system.ajustar_volumen.invoke({"nivel": "bajar"})
        assert 40.0 in calls

    def test_ajustar_volumen_target_percentage_phrases(self, monkeypatch):
        from tools import system

        calls = []
        monkeypatch.setattr(system, "_ajustar_volumen_absoluto", lambda v: calls.append(v) or f"vol={v}")

        system.ajustar_volumen.invoke({"nivel": "bajalo al 50"})
        assert 50.0 in calls

        calls.clear()
        system.ajustar_volumen.invoke({"nivel": "subelo al 100"})
        assert 100.0 in calls

        calls.clear()
        system.ajustar_volumen.invoke({"nivel": "bájalo al 50%"})
        assert 50.0 in calls

        calls.clear()
        system.ajustar_volumen.invoke({"nivel": "súbele al 80"})
        assert 80.0 in calls

        calls.clear()
        system.ajustar_volumen.invoke({"nivel": "ponlo en 30"})
        assert 30.0 in calls

        calls.clear()
        system.ajustar_volumen.invoke({"nivel": "50%"})
        assert 50.0 in calls


class TestModoNoMolestar:
    def test_modo_no_molestar_activar(self, monkeypatch, tmp_path):
        from services.notification_policy import NotificationPolicy
        from tools import system

        policy = NotificationPolicy(tmp_path / "quiet.db")
        monkeypatch.setattr(system, "notification_policy", policy)
        getter = MagicMock()
        monkeypatch.setattr(system, "_obtener_control_volumen", getter)
        out = system.modo_no_molestar.invoke({"activar": True})
        getter.assert_not_called()
        assert out.verified is True
        assert out.result["quiet"] is True

    def test_modo_no_molestar_desactivar(self, monkeypatch, tmp_path):
        from services.notification_policy import NotificationPolicy
        from tools import system

        policy = NotificationPolicy(tmp_path / "quiet.db")
        monkeypatch.setattr(system, "notification_policy", policy)
        getter = MagicMock()
        monkeypatch.setattr(system, "_obtener_control_volumen", getter)
        out = system.modo_no_molestar.invoke({"activar": False})
        getter.assert_not_called()
        assert out.verified is True
        assert out.result["quiet"] is False


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
        from services import security_manager
        from tools import system

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
                    system.borrar_memoria.invoke({})

        assert len(audit_calls) == 1
        args, kwargs = audit_calls[0]
        assert args[0] == "memory_wipe"
        assert kwargs["level"] == "critical"
        assert kwargs["tool"] == "borrar_memoria"


class TestAbrirAplicacion:
    @pytest.fixture(autouse=True)
    def app_environment(self, monkeypatch, tmp_path):
        import webbrowser

        from tools import system

        desktop = tmp_path / "Desktop"
        programs = tmp_path / "AppData" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        desktop.mkdir()
        programs.mkdir(parents=True)
        monkeypatch.setenv("JARVIS_DESKTOP_HOME", str(desktop))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
        monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
        monkeypatch.setenv("PUBLIC", str(tmp_path / "Public"))
        monkeypatch.setattr(system.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(system, "IS_WINDOWS", True)
        monkeypatch.setattr(system, "verificar_autorizacion", lambda pid: True)
        launch = MagicMock(side_effect=FileNotFoundError)
        popen = MagicMock()
        browser = MagicMock(return_value=True)
        monkeypatch.setattr(system.os, "startfile", launch, raising=False)
        monkeypatch.setattr(system.subprocess, "Popen", popen)
        monkeypatch.setattr(webbrowser, "open", browser)
        registered = MagicMock(return_value=None)
        monkeypatch.setattr(system, "resolve_registered_application", registered, raising=False)
        # The old fallback must not inspect the machine's real Start Menu.
        monkeypatch.setattr(system.os, "walk", MagicMock(return_value=iter(())))

        def accept(*targets):
            def startfile(target):
                if target not in {str(item) for item in targets}:
                    raise FileNotFoundError
            launch.side_effect = startfile

        return SimpleNamespace(
            system=system, desktop=desktop, programs=programs,
            launch=launch, popen=popen, browser=browser, accept=accept, registered=registered,
        )

    def test_abrir_aplicacion_denied_without_auth(self, monkeypatch, app_environment):
        from core.command_pipeline.models import ReceiptStatus, ToolExecutionResult

        system = app_environment.system
        monkeypatch.setattr(system, "verificar_autorizacion", lambda pid: False)
        out = system.abrir_aplicacion.invoke({"nombre_app": "notepad"})
        assert isinstance(out, ToolExecutionResult)
        assert out.status is ReceiptStatus.BLOCKED
        assert "acceso_denegado" in out.user_message.lower()
        assert out.verified is False
        app_environment.launch.assert_not_called()
        app_environment.popen.assert_not_called()
        app_environment.browser.assert_not_called()

    def test_not_found_returns_typed_failure(self, app_environment):
        from core.command_pipeline.models import ReceiptStatus, ToolExecutionResult

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "ZetaUnknownApp"})

        assert isinstance(out, ToolExecutionResult)
        assert out.status is ReceiptStatus.FAILED
        assert out.diagnostic_code == "app_not_found"
        assert out.verified is False
        assert out.accepted is False
        assert "No se encontró el ejecutable o acceso directo" in out.user_message

    def test_empty_name_returns_typed_failure(self, app_environment):
        from core.command_pipeline.models import ReceiptStatus

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "  "})

        assert out.status is ReceiptStatus.FAILED
        assert out.user_message == "Nombre de aplicación no especificado."
        app_environment.launch.assert_not_called()

    def test_known_executable_is_accepted_without_ui_verification(self, app_environment):
        from core.command_pipeline.models import ReceiptStatus

        app_environment.accept("notepad.exe")
        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "el bloc de notas"})

        app_environment.launch.assert_called_once_with("notepad.exe")
        assert out.status is ReceiptStatus.ACCEPTED
        assert out.accepted is True
        assert out.verified is False
        assert out.user_message == "Iniciando el bloc de notas..."

    def test_exact_desktop_folder_wins_over_partial_match(self, app_environment):
        from core.command_pipeline.models import ReceiptStatus

        folder = app_environment.desktop / "Projects"
        folder.mkdir()
        (app_environment.desktop / "Projects Archive").mkdir()
        app_environment.accept(folder)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "projects"})

        app_environment.launch.assert_called_once_with(str(folder))
        assert out.status is ReceiptStatus.ACCEPTED
        assert out.verified is False

    def test_exact_folder_does_not_require_reading_its_contents(self, monkeypatch, app_environment):
        from pathlib import Path

        folder = app_environment.desktop / "Projects"
        folder.mkdir()
        app_environment.accept(folder)
        original_iterdir = Path.iterdir

        def iterdir(path):
            if path == folder:
                raise PermissionError("folder contents unavailable")
            return original_iterdir(path)

        monkeypatch.setattr(Path, "iterdir", iterdir)
        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Projects"})

        assert out.accepted is True
        app_environment.launch.assert_called_once_with(str(folder))

    def test_exact_folder_with_leading_article_keeps_its_literal_name(self, app_environment):
        folder = app_environment.desktop / "The Project"
        folder.mkdir()
        (app_environment.desktop / "Project").mkdir()
        app_environment.accept(folder)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "The Project"})

        assert out.accepted is True
        app_environment.launch.assert_called_once_with(str(folder))

    def test_exact_local_name_wins_over_builtin_substring(self, app_environment):
        folder = app_environment.desktop / "Rust Documents"
        folder.mkdir()
        app_environment.accept(folder)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Rust Documents"})

        assert out.accepted is True
        app_environment.launch.assert_called_once_with(str(folder))
        app_environment.browser.assert_not_called()

    def test_portable_executable_on_desktop(self, app_environment):
        executable = app_environment.desktop / "Aurora.exe"
        executable.touch()
        (app_environment.desktop / "Aurora Studio.lnk").touch()
        app_environment.accept(executable)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Aurora"})

        assert out.accepted is True
        assert out.verified is False
        app_environment.launch.assert_called_once_with(str(executable))

    @pytest.mark.parametrize("name", ["Aurora.exe", "Aurora Portable/Aurora.exe", r"Aurora Portable\Aurora.exe"])
    def test_portable_executable_in_named_desktop_directory(self, app_environment, name):
        folder = app_environment.desktop / "Aurora Portable"
        folder.mkdir()
        executable = folder / "Aurora.exe"
        executable.touch()
        app_environment.accept(executable)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": name})

        assert out.accepted is True
        app_environment.launch.assert_called_once_with(str(executable))

    def test_portable_search_does_not_descend_into_unrelated_folders(self, app_environment):
        from core.command_pipeline.models import ReceiptStatus

        folder = app_environment.desktop / "Unrelated"
        folder.mkdir()
        executable = folder / "Aurora.exe"
        executable.touch()
        app_environment.accept(executable)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Aurora.exe"})

        assert out.status is ReceiptStatus.FAILED
        assert all(call.args[0] != str(executable) for call in app_environment.launch.call_args_list)

    def test_portable_search_is_only_one_directory_deep(self, app_environment):
        from core.command_pipeline.models import ReceiptStatus

        folder = app_environment.desktop / "Aurora Portable" / "bin"
        folder.mkdir(parents=True)
        executable = folder / "Aurora.exe"
        executable.touch()
        app_environment.accept(executable)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Aurora.exe"})

        assert out.status is ReceiptStatus.FAILED
        assert all(call.args[0] != str(executable) for call in app_environment.launch.call_args_list)

    def test_missing_shallow_path_does_not_become_a_builtin_alias(self, app_environment):
        from core.command_pipeline.models import ReceiptStatus

        app_environment.accept("notepad.exe")
        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Missing/notepad.exe"})

        assert out.status is ReceiptStatus.FAILED
        assert out.diagnostic_code == "app_not_found"
        app_environment.launch.assert_not_called()

    @pytest.mark.parametrize("suffix", [".lnk", ".url"])
    def test_start_menu_shortcuts_remain_supported(self, app_environment, suffix):
        folder = app_environment.programs / "Vendor"
        folder.mkdir()
        shortcut = folder / f"Aurora{suffix}"
        shortcut.touch()
        app_environment.accept(shortcut)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Aurora"})

        assert out.accepted is True
        assert out.verified is False
        app_environment.launch.assert_called_once_with(str(shortcut))

    def test_ambiguous_name_does_not_launch_any_candidate_or_raw_name(self, app_environment):
        from core.command_pipeline.models import ReceiptStatus

        first = app_environment.desktop / "Aurora Editor.exe"
        second = app_environment.desktop / "Aurora Studio.exe"
        first.touch()
        second.touch()
        app_environment.accept(first, second, "Aurora")

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Aurora"})

        assert out.status is ReceiptStatus.FAILED
        assert out.diagnostic_code == "app_ambiguous"
        app_environment.launch.assert_not_called()

    def test_desktop_executable_takes_priority_over_menu_duplicate(self, app_environment):
        from core.command_pipeline.models import ReceiptStatus

        executable = app_environment.desktop / "Aurora.exe"
        executable.touch()
        (app_environment.programs / "Aurora.lnk").touch()
        app_environment.accept(executable)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Aurora"})

        assert out.status is ReceiptStatus.ACCEPTED
        app_environment.launch.assert_called_once_with(str(executable))

    def test_jan_desktop_shortcut_wins_over_start_menu_duplicate(self, app_environment):
        shortcut = app_environment.desktop / "Jan.lnk"
        shortcut.touch()
        (app_environment.programs / "Jan.lnk").touch()
        app_environment.accept(shortcut)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Jan"})

        assert out.accepted is True
        assert out.verified is False
        app_environment.launch.assert_called_once_with(str(shortcut))

    def test_user_desktop_precedes_public_desktop_duplicate(self, app_environment):
        shortcut = app_environment.desktop / "Jan.lnk"
        shortcut.touch()
        public_desktop = app_environment.desktop.parent / "Public" / "Desktop"
        public_desktop.mkdir(parents=True)
        (public_desktop / "Jan.lnk").touch()
        app_environment.accept(shortcut)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Jan"})

        assert out.accepted is True
        app_environment.launch.assert_called_once_with(str(shortcut))

    def test_exact_menu_app_precedes_fuzzy_desktop_match(self, app_environment):
        shortcut = app_environment.programs / "Jan.lnk"
        shortcut.touch()
        (app_environment.desktop / "Jan Experimental.exe").touch()
        app_environment.accept(shortcut)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Jan"})

        assert out.accepted is True
        app_environment.launch.assert_called_once_with(str(shortcut))

    def test_godot_executable_precedes_fuzzy_project_folders(self, app_environment):
        executable = app_environment.desktop / "Godot_v4.6.3-stable_win64.exe"
        executable.touch()
        for name in ("MCP Godot", "GodotMcpSmoke", "GodotTemplateSources"):
            (app_environment.desktop / name).mkdir()
        app_environment.accept(executable)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Godot"})

        assert out.accepted is True
        app_environment.launch.assert_called_once_with(str(executable))

    def test_fuzzy_menu_shortcut_precedes_only_fuzzy_desktop_folder(self, app_environment):
        shortcut = app_environment.programs / "Godot Engine.lnk"
        shortcut.touch()
        (app_environment.desktop / "MCP Godot").mkdir()
        app_environment.accept(shortcut)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Godot"})

        assert out.accepted is True
        app_environment.launch.assert_called_once_with(str(shortcut))

    @pytest.mark.parametrize("name", ["MCP Godot", "artemis"])
    def test_requested_exact_project_folder_remains_supported(self, app_environment, name):
        folder = app_environment.desktop / name
        folder.mkdir()
        (app_environment.desktop / "Godot_v4.6.3-stable_win64.exe").touch()
        app_environment.accept(folder)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": name})

        assert out.accepted is True
        app_environment.launch.assert_called_once_with(str(folder))

    def test_godot_gui_console_pair_remains_ambiguous(self, app_environment):
        from core.command_pipeline.models import ReceiptStatus

        (app_environment.desktop / "Godot_v4.6.3-stable_win64.exe").touch()
        (app_environment.desktop / "Godot_v4.6.3-stable_win64_console.exe").touch()

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Godot"})

        assert out.status is ReceiptStatus.FAILED
        assert out.diagnostic_code == "app_ambiguous"
        app_environment.launch.assert_not_called()

    def test_distinct_same_scope_executable_and_shortcut_remain_ambiguous(self, app_environment):
        from core.command_pipeline.models import ReceiptStatus

        (app_environment.desktop / "Jan.exe").touch()
        (app_environment.desktop / "Jan.lnk").touch()

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Jan"})

        assert out.status is ReceiptStatus.FAILED
        assert out.diagnostic_code == "app_ambiguous"
        app_environment.launch.assert_not_called()

    def test_search_budget_exhaustion_does_not_select_an_incomplete_match(self, monkeypatch, app_environment):
        from core.command_pipeline.models import ReceiptStatus
        from tools import desktop_app_resolver

        monkeypatch.setattr(desktop_app_resolver, "MAX_SEARCH_ENTRIES", 1)
        (app_environment.desktop / "Aurora.exe").touch()
        (app_environment.desktop / "Aurora Studio.exe").touch()
        app_environment.accept("Aurora")

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Aurora"})

        assert out.status is ReceiptStatus.FAILED
        assert out.diagnostic_code == "app_search_limit"
        app_environment.launch.assert_not_called()

    def test_resolved_link_outside_desktop_is_not_launched(self, monkeypatch, app_environment, tmp_path):
        from pathlib import Path

        from core.command_pipeline.models import ReceiptStatus

        link = app_environment.desktop / "Aurora.exe"
        link.touch()
        outside = tmp_path / "outside.exe"
        outside.touch()
        original_resolve = Path.resolve
        monkeypatch.setattr(Path, "resolve", lambda path, *args, **kwargs:
                            outside if path == link else original_resolve(path, *args, **kwargs))
        app_environment.accept(link, outside)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Aurora"})

        assert out.status is ReceiptStatus.FAILED
        assert all(call.args[0] not in {str(link), str(outside)} for call in app_environment.launch.call_args_list)

    @pytest.mark.parametrize("name", [
        "../notepad", r"..\notepad", "Folder/../notepad", "/notepad",
        r"C:Windows\notepad.exe", r"\\server\share\notepad.exe",
        "notepad & calc", "notepad | calc", "notepad;calc", "notepad\ncalc",
        "powershell -Command calc", "cmd /c calc", "$(calc)", "%COMSPEC%",
        "Folder/Child/notepad.exe", "payload.cmd", '"notepad"',
        "Aurora.exe calc", "Aurora.exe\tcalc", "`calc`", "unknown-scheme:payload",
    ])
    def test_invalid_names_do_not_reach_any_launcher(self, app_environment, name):
        from core.command_pipeline.models import ReceiptStatus

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": name})

        app_environment.launch.assert_not_called()
        app_environment.popen.assert_not_called()
        app_environment.browser.assert_not_called()
        assert out.status is ReceiptStatus.FAILED
        assert out.diagnostic_code == "app_invalid_name"

    @pytest.mark.parametrize("kind", [".exe", ".lnk", ".url", "folder"])
    def test_existing_windows_absolute_local_paths_remain_supported(self, app_environment, tmp_path, kind):
        from core.command_pipeline.models import ReceiptStatus

        directory = tmp_path / "Program Files" / "Aurora"
        directory.mkdir(parents=True)
        target = directory if kind == "folder" else directory / f"Aurora{kind}"
        if kind != "folder":
            target.touch()
        app_environment.accept(target)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": str(target)})

        assert out.status is ReceiptStatus.ACCEPTED
        assert out.verified is False
        app_environment.launch.assert_called_once_with(str(target))

    def test_missing_windows_absolute_path_has_no_launcher_fallback(self, app_environment, tmp_path):
        from core.command_pipeline.models import ReceiptStatus

        target = tmp_path / "Missing" / "notepad.exe"
        app_environment.accept("notepad.exe")

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": str(target)})

        assert out.status is ReceiptStatus.FAILED
        assert out.diagnostic_code == "app_not_found"
        app_environment.launch.assert_not_called()

    def test_windows_absolute_path_is_not_rewritten_as_a_game_alias(self, app_environment, tmp_path):
        from core.command_pipeline.models import ReceiptStatus

        target = tmp_path / "csgo" / "Aurora.exe"
        target.parent.mkdir()
        target.touch()
        app_environment.accept(target)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": str(target).replace("csgo", "cs:go")})

        assert out.status is ReceiptStatus.FAILED
        assert out.diagnostic_code == "app_invalid_name"
        app_environment.launch.assert_not_called()

    def test_windows_absolute_path_on_remote_drive_is_not_launched(self, monkeypatch, app_environment, tmp_path):
        from core.command_pipeline.models import ReceiptStatus
        from tools import desktop_app_resolver

        target = tmp_path / "Aurora.exe"
        target.touch()
        app_environment.accept(target)
        monkeypatch.setattr(desktop_app_resolver, "_is_local_windows_drive", lambda anchor: False)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": str(target)})

        assert out.status is ReceiptStatus.FAILED
        assert out.diagnostic_code == "app_invalid_name"
        app_environment.launch.assert_not_called()

    @pytest.mark.parametrize("form", ["arguments", "traversal", "script", "text", "ads", "expansion"])
    def test_windows_absolute_paths_do_not_bypass_validation(self, app_environment, tmp_path, form):
        from core.command_pipeline.models import ReceiptStatus

        target = tmp_path / "Aurora.exe"
        target.touch()
        names = {
            "arguments": f"{target} --help",
            "traversal": str(tmp_path / "Nested" / ".." / "Aurora.exe"),
            "script": str(tmp_path / "payload.cmd"),
            "text": str(tmp_path / "notes.txt"),
            "ads": f"{target}:stream",
            "expansion": str(tmp_path / "%APPDATA%" / "Aurora.exe"),
        }
        if form in {"script", "text"}:
            (tmp_path / ("payload.cmd" if form == "script" else "notes.txt")).touch()

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": names[form]})

        assert out.status is ReceiptStatus.FAILED
        assert out.diagnostic_code == "app_invalid_name"
        app_environment.launch.assert_not_called()
        app_environment.popen.assert_not_called()

    def test_exact_registered_store_app_can_be_requested_without_a_shortcut(self, app_environment):
        from core.command_pipeline.models import ReceiptStatus

        target = r"shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App"
        app_environment.registered.return_value = target
        app_environment.accept(target)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "ChatGPT"})

        assert out.status is ReceiptStatus.ACCEPTED
        assert out.verified is False
        app_environment.registered.assert_called_once_with("chatgpt")
        assert app_environment.launch.call_args.args == (target,)

    @pytest.mark.parametrize("names,expected", [
        ([{"Name": "ChatGPT", "AppID": "OpenAI.Codex_2p2nqsd0c76g0!App"}],
         r"shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App"),
        ([{"Name": "ChatGPT Preview", "AppID": "OpenAI.Preview_abc!App"}], None),
        ([{"Name": "ChatGPT", "AppID": "malformed;payload!App"}], None),
        ([{"Name": "Jan", "AppID": "jan.ai.app"}], None),
    ])
    def test_registered_lookup_uses_exact_names_and_valid_packaged_ids(self, monkeypatch, names, expected):
        import json

        from tools import desktop_app_resolver

        query = MagicMock(return_value=SimpleNamespace(stdout=json.dumps(names)))
        monkeypatch.setattr(desktop_app_resolver.subprocess, "run", query)

        target = desktop_app_resolver.resolve_registered_application("ChatGPT")

        assert target == expected
        assert query.call_args.kwargs.get("shell", False) is False
        assert 0 < query.call_args.kwargs["timeout"] <= 5
        assert "ChatGPT" not in query.call_args.args[0]

    def test_registered_lookup_does_not_choose_between_distinct_exact_ids(self, monkeypatch):
        import json

        from tools import desktop_app_resolver

        names = [
            {"Name": "ChatGPT", "AppID": "OpenAI.Codex_one!App"},
            {"Name": "ChatGPT", "AppID": "OpenAI.Codex_two!App"},
        ]
        monkeypatch.setattr(desktop_app_resolver.subprocess, "run",
                            MagicMock(return_value=SimpleNamespace(stdout=json.dumps(names))))

        with pytest.raises(desktop_app_resolver.ApplicationResolutionError, match="app_ambiguous"):
            desktop_app_resolver.resolve_registered_application("ChatGPT")

    @pytest.mark.parametrize("name,target", [
        ("cs2", "steam://rungameid/730"), ("spotify", "spotify:"),
        ("cs:go", "steam://rungameid/730"), ("el cs:go", "steam://rungameid/730"),
        ("juego Counter Strike 2", "steam://rungameid/730"),
        ("discord:", "discord:"), ("steam://rungameid/730", "steam://rungameid/730"),
    ])
    def test_uri_launches_remain_accepted_not_verified(self, app_environment, name, target):
        from core.command_pipeline.models import ReceiptStatus

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": name})

        app_environment.browser.assert_called_once_with(target)
        assert out.status is ReceiptStatus.ACCEPTED
        assert out.verified is False

    def test_rejected_uri_request_is_not_reported_as_accepted(self, app_environment):
        from core.command_pipeline.models import ReceiptStatus

        app_environment.browser.return_value = False
        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "spotify"})

        assert out.status is ReceiptStatus.FAILED
        assert out.accepted is False
        app_environment.launch.assert_not_called()

    @pytest.mark.parametrize("platform,opener", [("darwin", "open"), ("linux", "xdg-open")])
    def test_cross_platform_launch_is_accepted_without_shell(self, monkeypatch, app_environment, platform, opener):
        from core.command_pipeline.models import ReceiptStatus

        monkeypatch.setattr(app_environment.system, "IS_WINDOWS", False)
        monkeypatch.setattr(app_environment.system.sys, "platform", platform)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Aurora"})

        app_environment.popen.assert_called_once_with([opener, "Aurora"])
        assert out.status is ReceiptStatus.ACCEPTED
        assert out.verified is False

    @pytest.mark.parametrize("platform,path,opener", [
        ("darwin", "/Applications/Aurora.app", "open"),
        ("linux", "/opt/aurora/aurora.desktop", "xdg-open"),
    ])
    def test_cross_platform_absolute_paths_remain_supported(self, monkeypatch, app_environment, platform, path, opener):
        from core.command_pipeline.models import ReceiptStatus

        monkeypatch.setattr(app_environment.system, "IS_WINDOWS", False)
        monkeypatch.setattr(app_environment.system.sys, "platform", platform)

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": path})

        app_environment.popen.assert_called_once_with([opener, path])
        assert out.status is ReceiptStatus.ACCEPTED
        assert out.verified is False

    def test_platform_launch_error_is_typed_and_does_not_expose_exception(self, monkeypatch, app_environment):
        from core.command_pipeline.models import ReceiptStatus

        monkeypatch.setattr(app_environment.system, "IS_WINDOWS", False)
        app_environment.popen.side_effect = OSError("private process details")

        out = app_environment.system.abrir_aplicacion.invoke({"nombre_app": "Aurora"})

        assert out.status is ReceiptStatus.FAILED
        assert "private process details" not in out.user_message

    @pytest.mark.parametrize("authorized,name,expected", [
        (False, "notepad", "blocked"), (True, "ZetaUnknownApp", "failed"),
        (True, "notepad", "accepted"),
    ])
    def test_central_receipt_preserves_app_outcome_and_executes_once(
        self, monkeypatch, app_environment, authorized, name, expected,
    ):
        from core.command_pipeline.execution import ToolExecutionService
        from core.command_pipeline.models import ActionStep, CommandRequest

        system = app_environment.system
        monkeypatch.setattr(system, "verificar_autorizacion", lambda pid: authorized)
        app_environment.accept("notepad.exe")
        invoke = MagicMock(side_effect=lambda _request, step: system.abrir_aplicacion.invoke(dict(step.arguments)))
        executor = ToolExecutionService(invoke)
        request = CommandRequest.create(text="abre la aplicación", profile_id="admin", channel="chat", language="es")
        step = ActionStep("open-app", "abrir_aplicacion", {"nombre_app": name})

        receipt = executor.execute(request, step)
        replay = executor.execute(request, step)

        assert receipt.status.value == expected
        assert replay.status.value == expected
        assert receipt.verified is False
        assert replay.verified is False
        invoke.assert_called_once()


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
