from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
MODULE = ROOT / "src" / "frontend" / "static" / "js" / "modules" / "command-center.js"


def _run_node(assertions: str) -> None:
    script = f"import * as command from {json.dumps(MODULE.resolve().as_uri())};\n{assertions}\n"
    subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required")
def test_command_center_pure_contracts():
    _run_node("""
if (command.normalizeCommandText('  hello   world ') !== 'hello world') process.exit(1);
if (command.normalizeCommandText('x'.repeat(5000)).length !== 4000) process.exit(2);
const t = key => key;
if (!command.commandStateView('processing', t).busy) process.exit(3);
if (command.commandStateView('unknown', t).key !== 'command_state_idle') process.exit(4);
if (command.microphoneRecoveryView('permission_denied', t).hidden) process.exit(5);
if (!command.microphoneRecoveryView('', t).hidden) process.exit(6);
if (!command.isSuccessfulCommandOutcome('succeeded')) process.exit(7);
if (!command.isSuccessfulCommandOutcome('duplicate')) process.exit(8);
if (command.isSuccessfulCommandOutcome('failed')) process.exit(9);
if (command.isSuccessfulCommandOutcome('partial')) process.exit(10);
if (!command.isSuccessfulCommandOutcome(undefined)) process.exit(11);
""")


def test_command_center_markup_is_accessible_and_task_first():
    html = (ROOT / "src/frontend/templates/index.html").read_text(encoding="utf-8")
    assert '<html lang="es">' in html
    assert 'id="command-form"' in html
    assert 'id="command-input"' in html
    assert 'id="command-submit"' in html
    assert 'id="command-status"' in html and 'aria-live="polite"' in html
    assert 'id="command-recovery"' in html and 'role="alert"' in html
    assert 'aria-label="Idioma de la interfaz"' in html
    assert 'id="diagnostics-toggle"' in html
    assert 'aria-controls="diagnostics-left diagnostics-right"' in html
    assert 'id="diagnostics-left" class="side-panel left" inert' in html
    assert 'id="diagnostics-right" class="side-panel right" inert' in html
    assert 'id="mode-actions-toggle"' in html
    assert 'id="mode-actions" class="mode-actions hidden"' in html
    assert 'id="conversation-panel"' in html


def test_primary_copy_has_complete_labels():
    source = (ROOT / "src/frontend/static/js/i18n.js").read_text(encoding="utf-8")
    for forbidden in ('"RCGR PLUGINS"', '"TGL ESTRICTO"', '"RTN TRABAJO"'):
        assert forbidden not in source
    for key in (
        "command_placeholder",
        "command_send",
        "diagnostics_open",
        "voice_permission_recovery",
    ):
        assert f'"{key}"' in source


def test_main_connects_composer_to_existing_chat_path():
    source = (ROOT / "src/frontend/static/js/main.js").read_text(encoding="utf-8")
    assert "normalizeCommandText" in source
    assert "async function submitTextCommand" in source
    assert "dom.commandForm.addEventListener('submit'" in source
    assert "await getLlamaResponses(text)" in source
    assert "ui.addConversationSegment('user', text)" in source
    assert "textCommandInFlight" in source
    assert "dom.commandInput.value = ''" in source
    assert "data?.ok === false" in source
    assert source.count("ok: false") >= 2
    assert "isSuccessfulCommandOutcome(data?.outcome)" in source
    assert "finalOutcome = ev.outcome" in source


def test_api_post_chat_preserves_profile_and_json_errors():
    source = (ROOT / "src/frontend/static/js/modules/api.js").read_text(encoding="utf-8")
    assert "async postChat(message, profileId" in source
    assert "body: JSON.stringify({ message, profile_id: profileId })" in source
    assert "api_chat_invalid_response" in source


def test_recovery_language_and_panels_are_visible_and_accessible():
    source = (ROOT / "src/frontend/static/js/main.js").read_text(encoding="utf-8")

    blocked = source.split("function markMicrophonePermissionBlocked", 1)[1].split(
        "function schedulePassiveRestart", 1
    )[0]
    assert "showMicrophoneRecovery(errorType)" in blocked
    assert "dom.commandRetryVoice.addEventListener('click'" in source
    assert "document.documentElement.lang = newLang" in source
    assert "document.querySelectorAll('.side-panel')" in source
    assert "panel.inert = !expanded" in source
    assert "control.setAttribute('aria-expanded'" in source
    assert "setExpanded(dom.diagnosticsToggle" in source
    assert "setExpanded(dom.modeActionsToggle" in source


def test_adaptive_css_has_accessibility_floor():
    css = (ROOT / "src/frontend/static/css/style.css").read_text(encoding="utf-8")
    for contract in (
        ".command-composer",
        ".side-panel[inert]",
        ".diagnostics-open",
        ":focus-visible",
        "min-height: 44px",
        "@media (prefers-reduced-motion: reduce)",
        "@media (max-width: 1200px)",
        "@media (max-width: 600px)",
    ):
        assert contract in css
