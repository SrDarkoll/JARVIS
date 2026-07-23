from __future__ import annotations

from pathlib import Path

import pytest
from core.runtime_paths import ensure_runtime_paths, resolve_runtime_paths


@pytest.mark.parametrize(
    ("platform_name", "environment", "expected_parts"),
    [
        (
            "win32",
            {"LOCALAPPDATA": "platform-root"},
            ("platform-root", "Jarvis"),
        ),
        (
            "darwin",
            {"HOME": "platform-root"},
            (
                "platform-root",
                "Library",
                "Application Support",
                "Jarvis",
            ),
        ),
        (
            "linux",
            {"XDG_DATA_HOME": "platform-root"},
            ("platform-root", "jarvis"),
        ),
    ],
)
def test_runtime_home_uses_platform_application_data(
    tmp_path,
    platform_name,
    environment,
    expected_parts,
) -> None:
    env = {
        key: str(tmp_path / value)
        for key, value in environment.items()
    }

    paths = resolve_runtime_paths(env, platform_name=platform_name)

    expected = Path(env[next(iter(environment))])
    for part in expected_parts[1:]:
        expected /= part
    assert paths.home == expected.resolve()


def test_explicit_data_directory_wins(tmp_path) -> None:
    explicit = tmp_path / "custom"

    paths = resolve_runtime_paths(
        {
            "JARVIS_DATA_DIR": str(explicit),
            "LOCALAPPDATA": str(tmp_path / "ignored"),
        },
        platform_name="win32",
    )

    assert paths.home == explicit.resolve()
    assert not explicit.exists()


def test_legacy_runtime_directory_is_supported(tmp_path) -> None:
    explicit = tmp_path / "legacy-runtime"

    paths = resolve_runtime_paths(
        {"JARVIS_RUNTIME_DIR": str(explicit)},
        platform_name="win32",
    )

    assert paths.home == explicit.resolve()


def test_ensure_runtime_paths_creates_and_probes_all_directories(
    tmp_path,
) -> None:
    paths = resolve_runtime_paths(
        {"JARVIS_DATA_DIR": str(tmp_path / "runtime")},
        platform_name="win32",
    )

    ensure_runtime_paths(paths)

    for directory in paths.directories():
        assert directory.is_dir()
        probe = directory / "write-check.txt"
        probe.write_text("ok", encoding="utf-8")
        assert probe.read_text(encoding="utf-8") == "ok"
