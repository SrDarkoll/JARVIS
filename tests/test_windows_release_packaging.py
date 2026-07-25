from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_release_entrypoints_use_project_scripts():
    installer = (ROOT / "Install-JARVIS.bat").read_text(encoding="utf-8")
    launcher = (ROOT / "Start-JARVIS.bat").read_text(encoding="utf-8")
    setup = (ROOT / "setup.ps1").read_text(encoding="utf-8")

    assert "setup.ps1" in installer
    assert "-CreateShortcut" in installer
    assert "venv\\Scripts\\python.exe" in launcher
    assert "start_app.py" in launcher
    assert "[switch]$CreateShortcut" in setup
    assert "WScript.Shell" in setup


def test_release_builder_uses_a_runtime_allowlist_and_generates_checksum():
    builder = (ROOT / "scripts/build_windows_release.ps1").read_text(encoding="utf-8")

    for required_path in (
        ".env.example",
        "Install-JARVIS.bat",
        "Start-JARVIS.bat",
        "requirements.txt",
        "setup.ps1",
        "src",
        "models",
    ):
        assert f'"{required_path}"' in builder

    assert "Compress-Archive" in builder
    assert "Get-FileHash" in builder
    assert '.env"' not in builder
    assert '"venv"' not in builder
    assert '"tests"' not in builder


def test_release_documentation_covers_windows_package_and_piper_voices():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "Install-JARVIS.bat" in readme
    assert "v0.1.0-alpha.1" in readme
    assert "piper.download_voices" in readme
    assert ".onnx.json" in readme
    assert 'JARVIS_TTS_MODEL_EN="' in env_example
    assert 'JARVIS_TTS_MODEL_ES="' in env_example
