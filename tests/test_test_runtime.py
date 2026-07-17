import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_pytest_tmp_path_uses_isolated_repo_runtime(tmp_path):
    resolved_tmp = Path(tmp_path).resolve()
    runtime_tmp = (Path(os.environ["JARVIS_RUNTIME_DIR"]) / "tmp").resolve()

    assert runtime_tmp == resolved_tmp or runtime_tmp in resolved_tmp.parents
    assert (ROOT / "scratch").resolve() in resolved_tmp.parents
