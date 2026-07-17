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
    assert '& $venvPython -m playwright install chromium' in powershell
    assert '--full' in shell
    assert 'pip install -r requirements-optional.txt' in shell


def test_setup_scripts_use_the_project_interpreter_for_all_python_tools():
    powershell = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    shell = (ROOT / "setup.sh").read_text(encoding="utf-8")

    assert '$venvPython = Join-Path' in powershell
    assert '& $venvPython -m pip install --upgrade pip' in powershell
    assert '& $venvPython -m pip install -r requirements.txt' in powershell
    assert '& $venvPython -m pip install -r requirements-optional.txt' in powershell
    assert '& $venvPython -m pip install -r requirements-dev.txt' in powershell
    assert '& $venvPython -m playwright install chromium' in powershell
    assert 'Activate.ps1' not in powershell
    assert '\npip install ' not in powershell

    powershell_lines = [line.strip() for line in powershell.splitlines()]
    checked_commands = (
        '$pythonVersionOutput = & $pythonCmd @pythonArgs -c "import sys; print(f\'{sys.version_info.major}.{sys.version_info.minor}\')"',
        '& $pythonCmd @pythonArgs -m venv venv',
        '& $venvPython -m pip install --upgrade pip',
        '& $venvPython -m pip install -r requirements.txt',
        '& $venvPython -m pip install -r requirements-optional.txt',
        '& $venvPython -m pip install -r requirements-dev.txt',
        '& $venvPython -m playwright install chromium',
    )
    for command in checked_commands:
        command_index = powershell_lines.index(command)
        assert powershell_lines[command_index + 1] == 'if ($LASTEXITCODE -ne 0) {'

    assert 'VENV_PYTHON="venv/bin/python"' in shell
    assert '"$VENV_PYTHON" -m pip install --upgrade pip' in shell
    assert '"$VENV_PYTHON" -m pip install -r requirements.txt' in shell
    assert '"$VENV_PYTHON" -m pip install -r requirements-optional.txt' in shell
    assert '"$VENV_PYTHON" -m pip install -r requirements-dev.txt' in shell
    assert '"$VENV_PYTHON" -m playwright install chromium' in shell
    assert 'source venv/bin/activate' not in shell
