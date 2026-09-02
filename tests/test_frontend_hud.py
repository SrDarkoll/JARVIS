from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
HUD_MODULE = ROOT / "src" / "frontend" / "static" / "js" / "modules" / "hud.js"
REACTOR_MODULE = ROOT / "src" / "frontend" / "static" / "js" / "modules" / "reactor.js"


def _run_node_hud_assertions(assertions: str) -> None:
    script = f"import * as hud from {json.dumps(HUD_MODULE.resolve().as_uri())};\n{assertions}\n"
    subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_proactive_action_result_classifier_requires_evidence_of_success():
    _run_node_hud_assertions(
        """
const classify = hud.classifyProactiveActionResult;

if (classify({ ok: false, status: 500 }, {}).state !== 'error') process.exit(1);
if (classify({ ok: true, status: 200 }, { error: 'failed' }).state !== 'error') process.exit(2);
if (classify({ ok: true, status: 200 }, {
    receipts: [{ status: 'blocked', diagnostic_code: 'tool_confirmation_required' }]
}).state !== 'confirmation') process.exit(3);
if (classify({ ok: true, status: 200 }, {
    response: 'Necesito confirmacion explicita antes de continuar.'
}).state !== 'confirmation') process.exit(4);
if (classify({ ok: true, status: 200 }, {
    receipts: [{ status: 'failed' }]
}).state !== 'error') process.exit(5);
if (classify({ ok: true, status: 200 }, {
    response: 'No se encontro ninguna accion proactiva.'
}).state !== 'error') process.exit(6);
if (classify({ ok: true, status: 200 }, {
    response: 'Accion proactiva ejecutada correctamente.'
}).state !== 'success') process.exit(7);
"""
    )


def test_reactor_exposes_safe_resize_contract():
    source = REACTOR_MODULE.read_text(encoding="utf-8")

    assert "resize()" in source
    assert "this.camera.aspect = 1" in source
    assert "this.renderer.setSize(size, size, false)" in source


def test_reactor_honors_state_and_reduced_motion():
    source = REACTOR_MODULE.read_text(encoding="utf-8")

    assert "setState(state)" in source
    assert "prefers-reduced-motion: reduce" in source
    assert "this.reducedMotion" in source
