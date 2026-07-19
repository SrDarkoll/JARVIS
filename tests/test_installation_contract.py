from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _requirement_names(path: Path) -> set[str]:
    names = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "-r ", "-c ")):
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
        "langchain-huggingface",
        "sentence-transformers",
        "torch",
        "torchaudio",
        "python-telegram-bot",
        "playwright",
        "wmi",
        "pycaw",
        "comtypes",
    }

    assert expected <= optional


def test_optional_ml_dependencies_use_compatible_major_versions():
    optional = (ROOT / "requirements-optional.txt").read_text(encoding="utf-8")

    assert "-c requirements.txt" in optional
    assert "langchain-huggingface==1.2.2" in optional
    assert "sentence-transformers>=5.2,<6" in optional
    assert "torch==2.11.0" in optional
    assert "torchaudio==2.11.0" in optional

    rag_source = (ROOT / "src/backend/engines/memory_rag.py").read_text(
        encoding="utf-8"
    )
    assert "from langchain_huggingface import HuggingFaceEmbeddings" in rag_source
    assert "langchain_community.embeddings" not in rag_source


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


def test_spotipy_version_and_spotify_environment_contract():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    config = (ROOT / "src/backend/core/jarvis_config.py").read_text(encoding="utf-8")

    assert re.search(r"(?m)^spotipy>=2\.26,<3$", requirements)
    assert 'SPOTIPY_REDIRECT_URI="http://127.0.0.1:8888/callback"' in env_example
    assert 'SPOTIFY_AUTO_SHUFFLE="false"' in env_example
    assert 'SPOTIFY_EXTENDED_QUOTA_MODE="false"' in env_example
    assert (
        'SPOTIFY_AUTO_SHUFFLE = _read_bool(os.environ, "SPOTIFY_AUTO_SHUFFLE", False)'
        in config
    )
    assert 'SPOTIFY_EXTENDED_QUOTA_MODE = _read_bool(' in config


def test_spotify_desktop_mode_and_dependency_contract():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    config = (ROOT / "src/backend/core/jarvis_config.py").read_text(encoding="utf-8")

    assert 'SPOTIFY_PLAYBACK_MODE="auto"' in env_example
    assert 'SPOTIFY_DESKTOP_START_TIMEOUT="20"' in env_example
    assert 'SPOTIFY_DESKTOP_ACTION_TIMEOUT="8"' in env_example
    assert 'pywinauto>=0.6.9,<0.7; sys_platform == "win32"' in requirements
    assert "SPOTIFY_PLAYBACK_MODE = resolve_spotify_playback_mode()" in config
    assert "SPOTIFY_DESKTOP_START_TIMEOUT = _read_float(" in config
    assert "SPOTIFY_DESKTOP_ACTION_TIMEOUT = _read_float(" in config

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "SPOTIFY_PLAYBACK_MODE=desktop" in readme
    assert "does not bypass Spotify Free restrictions" in readme
    assert "test_spotify_desktop_controller.py" in agents


def test_spotify_playback_mode_rejects_unknown_values():
    from core.jarvis_config import resolve_spotify_playback_mode

    assert resolve_spotify_playback_mode({}) == "auto"
    assert resolve_spotify_playback_mode({"SPOTIFY_PLAYBACK_MODE": "desktop"}) == "desktop"
    assert resolve_spotify_playback_mode({"SPOTIFY_PLAYBACK_MODE": "api"}) == "api"
    assert resolve_spotify_playback_mode({"SPOTIFY_PLAYBACK_MODE": "invalid"}) == "auto"


def test_public_llm_configuration_is_groq_only():
    public_files = (
        ROOT / ".env.example",
        ROOT / "src/backend/core/jarvis_config.py",
        ROOT / "jarvis_settings.py",
    )
    public_config = "\n".join(
        path.read_text(encoding="utf-8") for path in public_files
    )

    assert "GROQ_API_KEY" in public_config
    assert "MINIMAX" not in public_config.upper()


def test_frontend_uses_the_supported_threejs_module_build():
    template = (ROOT / "src/frontend/templates/index.html").read_text(encoding="utf-8")
    reactor = (ROOT / "src/frontend/static/js/modules/reactor.js").read_text(
        encoding="utf-8"
    )
    vendor = ROOT / "src/frontend/static/vendor"

    assert "vendor/three.min.js" not in template
    assert "window.THREE" not in reactor
    assert "../../vendor/three.module.min.js" in reactor
    assert (vendor / "three.module.min.js").is_file()
    assert (vendor / "three.core.min.js").is_file()
    assert (vendor / "three.LICENSE.txt").is_file()


def test_voice_support_contract_documents_adaptive_transcription():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "JARVIS_STT_PROVIDER" in env_example
    assert "whisper-large-v3-turbo" in env_example
    assert "SpeechRecognition" in readme
    assert "Firefox" in readme
    assert "Safari" in readme
    assert "non-loopback" in readme.lower()
    assert "HTTPS" in readme
    assert "voice-capabilities.js" in agents
