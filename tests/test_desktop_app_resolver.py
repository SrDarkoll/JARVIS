import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from tools import desktop_app_resolver as resolver


@pytest.fixture
def app_roots(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    menu = tmp_path / "Programs"
    desktop.mkdir()
    menu.mkdir()
    monkeypatch.setattr(resolver, "application_search_roots", lambda: ((desktop,), (menu,)))
    return desktop, menu


@pytest.mark.parametrize("query", ["MCPGodot", "mcp_godot", "MCP-Godot", "MCP\u2013Godot", "MCP  Godot"])
def test_unique_normalized_folder_name_resolves(app_roots, query):
    desktop, _ = app_roots
    target = desktop / "MCP Godot"
    target.mkdir()

    assert resolver.resolve_local_application(query) == target.resolve()


def test_exact_name_precedes_normalized_name_in_earlier_root(app_roots):
    desktop, menu = app_roots
    (desktop / "MCP Godot.exe").touch()
    target = menu / "MCPGodot.lnk"
    target.touch()

    assert resolver.resolve_local_application("MCPGodot") == target.resolve()


def test_exact_filename_precedes_exact_stem(app_roots):
    desktop, _ = app_roots
    target = desktop / "MCPGodot.exe"
    target.touch()
    (desktop / "MCP Godot.exe").touch()

    assert resolver.resolve_local_application("MCPGodot.exe") == target.resolve()


@pytest.mark.parametrize("reverse_roots", [False, True])
def test_normalized_collisions_are_ambiguous_across_roots(app_roots, monkeypatch, reverse_roots):
    desktop, menu = app_roots
    (desktop / "MCP Godot.lnk").touch()
    (menu / "MCP-Godot.lnk").touch()
    if reverse_roots:
        monkeypatch.setattr(resolver, "application_search_roots", lambda: ((menu,), (desktop,)))

    with pytest.raises(resolver.ApplicationResolutionError, match="^app_ambiguous$"):
        resolver.resolve_local_application("MCPGodot")


def test_normalized_folder_and_executable_collision_does_not_prefer_type(app_roots):
    desktop, _ = app_roots
    (desktop / "MCP Godot").mkdir()
    (desktop / "MCP-Godot.exe").touch()

    with pytest.raises(resolver.ApplicationResolutionError, match="^app_ambiguous$"):
        resolver.resolve_local_application("MCPGodot")


def test_normalization_does_not_remove_significant_punctuation(app_roots):
    desktop, _ = app_roots
    (desktop / "C++ Editor").mkdir()

    assert resolver.resolve_local_application("CEditor") is None


def test_normalization_supports_canonical_unicode_without_dropping_accents(app_roots):
    desktop, _ = app_roots
    target = desktop / "Caf\u00e9 Studio"
    target.mkdir()

    assert resolver.resolve_local_application("Cafe\u0301Studio") == target.resolve()
    assert resolver.resolve_local_application("CafeStudio") is None


def test_normalized_portable_directory_name_resolves(app_roots):
    desktop, _ = app_roots
    directory = desktop / "MCP Godot"
    directory.mkdir()
    target = directory / "Editor.exe"
    target.touch()

    assert resolver.resolve_local_application("MCPGodot/Editor.exe") == target.resolve()


def test_normalized_parent_collisions_remain_ambiguous_across_desktops(app_roots, tmp_path, monkeypatch):
    desktop, _ = app_roots
    second_desktop = tmp_path / "PublicDesktop"
    second_desktop.mkdir()
    for root, folder in [(desktop, "MCP Godot"), (second_desktop, "MCP-Godot")]:
        directory = root / folder
        directory.mkdir()
        (directory / "Editor.exe").touch()
    monkeypatch.setattr(resolver, "application_search_roots", lambda: ((desktop, second_desktop), ()))

    with pytest.raises(resolver.ApplicationResolutionError, match="^app_ambiguous$"):
        resolver.resolve_local_application("MCPGodot/Editor.exe")


def test_exact_parent_name_precedes_normalized_parent(app_roots):
    desktop, _ = app_roots
    for folder in ["MCP Godot", "MCPGodot"]:
        directory = desktop / folder
        directory.mkdir()
        (directory / "Editor.exe").touch()

    assert resolver.resolve_local_application("MCPGodot/Editor.exe") == desktop / "MCPGodot" / "Editor.exe"


def test_normalized_name_precedes_fuzzy_application(app_roots):
    desktop, _ = app_roots
    target = desktop / "MCP Godot"
    target.mkdir()
    (desktop / "MCPGodot Preview.exe").touch()

    assert resolver.resolve_local_application("MCPGodot") == target.resolve()


def test_incomplete_normalized_search_fails_closed(app_roots, monkeypatch):
    desktop, menu = app_roots
    (desktop / "MCP Godot.exe").touch()
    (menu / "MCP-Godot.lnk").touch()
    monkeypatch.setattr(resolver, "MAX_SEARCH_ENTRIES", 1)

    with pytest.raises(resolver.ApplicationResolutionError, match="^app_search_limit$"):
        resolver.resolve_local_application("MCPGodot")


@pytest.mark.parametrize("query", ["MCPGodot & calc", "MCPGodot/../Editor.exe", r"\\server\MCPGodot.exe"])
def test_normalization_does_not_bypass_validation(app_roots, query):
    with pytest.raises(resolver.ApplicationResolutionError, match="^app_invalid_name$"):
        resolver.resolve_local_application(query)


def test_registered_normalized_name_is_unique_and_not_shell_interpolated(monkeypatch):
    target_id = "Vendor.MCP_abc!App"
    run = Mock(return_value=SimpleNamespace(stdout=json.dumps([{"Name": "MCP Godot", "AppID": target_id}])))
    monkeypatch.setattr(resolver.subprocess, "run", run)

    assert resolver.resolve_registered_application("MCPGodot") == f"shell:AppsFolder\\{target_id}"
    assert "MCPGodot" not in run.call_args.args[0][-1]
    assert run.call_args.kwargs.get("shell", False) is False


def test_registered_exact_name_precedes_normalized_collision(monkeypatch):
    records = [
        {"Name": "MCP Godot", "AppID": "Vendor.Other_abc!App"},
        {"Name": "MCPGodot", "AppID": "Vendor.Exact_abc!App"},
    ]
    monkeypatch.setattr(resolver.subprocess, "run", Mock(return_value=SimpleNamespace(stdout=json.dumps(records))))

    assert resolver.resolve_registered_application("MCPGodot") == r"shell:AppsFolder\Vendor.Exact_abc!App"


def test_registered_normalized_collision_is_ambiguous(monkeypatch):
    records = [
        {"Name": "MCP Godot", "AppID": "Vendor.First_abc!App"},
        {"Name": "MCP-Godot", "AppID": "Vendor.Second_abc!App"},
    ]
    monkeypatch.setattr(resolver.subprocess, "run", Mock(return_value=SimpleNamespace(stdout=json.dumps(records))))

    with pytest.raises(resolver.ApplicationResolutionError, match="^app_ambiguous$"):
        resolver.resolve_registered_application("MCPGodot")
