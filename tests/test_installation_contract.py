from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _requirement_names(path: Path) -> set[str]:
    names = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "-r ")):
            continue
        package = line.split(";", 1)[0].strip()
        for separator in ("==", ">=", "<=", "~=", "!=", ">", "<", "["):
            package = package.split(separator, 1)[0]
        names.add(package.strip().lower().replace("_", "-"))
    return names


def test_core_requirements_exclude_heavy_optional_features():
    core = _requirement_names(ROOT / "requirements.txt")
    forbidden = {
        "speechbrain",
        "faiss-cpu",
        "sentence-transformers",
        "python-telegram-bot",
        "playwright",
        "wmi",
        "pycaw",
        "comtypes",
    }

    assert core.isdisjoint(forbidden)


def test_optional_requirements_contain_full_feature_dependencies():
    optional = _requirement_names(ROOT / "requirements-optional.txt")
    expected = {
        "speechbrain",
        "faiss-cpu",
        "sentence-transformers",
        "python-telegram-bot",
        "playwright",
        "wmi",
        "pycaw",
        "comtypes",
    }

    assert expected <= optional


def test_core_requirements_declare_direct_audio_dependencies():
    core = _requirement_names(ROOT / "requirements.txt")

    assert {"numpy", "soundfile", "piper-tts", "faster-whisper"} <= core


def test_setup_scripts_require_explicit_full_mode_for_optional_dependencies():
    powershell = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    shell = (ROOT / "setup.sh").read_text(encoding="utf-8")

    assert "[switch]$Full" in powershell
    assert 'pip install -r requirements-optional.txt' in powershell
    assert 'python -m playwright install chromium' in powershell
    assert '--full' in shell
    assert 'pip install -r requirements-optional.txt' in shell
