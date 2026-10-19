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
MEMORY_GRAPH_MODULE = ROOT / "src" / "frontend" / "static" / "js" / "modules" / "memory-graph.js"


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


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_core_temperature_view_clears_stale_sensor_values():
    _run_node_hud_assertions(
        """
const missing = hud.coreTemperatureView(null, 'es-MX');
if (missing.available !== false || missing.label !== '—' || missing.barWidth !== 0) process.exit(1);
const celsius = hud.coreTemperatureView(50, 'es-MX');
if (!celsius.available || celsius.label !== '50.0°C') process.exit(2);
const fahrenheit = hud.coreTemperatureView(50, 'en-US');
if (fahrenheit.label !== '122.0°F') process.exit(3);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_empty_memory_graph_stays_empty_and_preview_uses_safe_text():
    script = f"""
import {{ MemoryGraphRenderer }} from {json.dumps(MEMORY_GRAPH_MODULE.resolve().as_uri())};
globalThis.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = () => undefined;
const graph = new MemoryGraphRenderer({{ canvas: null, countEl: null, infoEl: null, refreshBtn: null }});
graph._buildGraph([], []);
if (graph.nodes.length !== 0 || graph.links.length !== 0 || graph.running) process.exit(1);
"""
    subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    source = MEMORY_GRAPH_MODULE.read_text(encoding="utf-8")
    assert "Placeholder demonstration nodes" not in source
    assert ".innerHTML" not in source


def test_memory_graph_has_a_screen_reader_equivalent():
    html = (ROOT / "src/frontend/templates/index.html").read_text(encoding="utf-8")
    hud_source = HUD_MODULE.read_text(encoding="utf-8")
    graph_source = MEMORY_GRAPH_MODULE.read_text(encoding="utf-8")

    assert 'id="memory-graph-canvas" class="memory-canvas" aria-hidden="true"' in html
    assert 'id="memory-graph-list"' in html
    assert 'data-i18n-aria-label="memory_graph_list_label"' in html
    assert "listEl: this.elements.memoryGraphList" in hud_source
    assert "this.listEl.replaceChildren()" in graph_source
    assert "item.textContent" in graph_source


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
