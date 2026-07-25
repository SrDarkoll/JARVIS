from __future__ import annotations

import tomllib
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = ROOT_DIR / "pyproject.toml"


def test_pyright_baseline_contains_only_specific_backend_python_files():
    config = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    ignored_paths = config["tool"]["pyright"].get("ignore", [])

    assert ignored_paths
    assert len(ignored_paths) <= 34
    assert len(ignored_paths) == len(set(ignored_paths))
    for path_text in ignored_paths:
        path = Path(path_text)
        assert path_text.startswith("src/backend/")
        assert path.suffix == ".py"
        assert not any(character in path_text for character in "*?[]")
        assert (ROOT_DIR / path).is_file()
