from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
POLICY_MODULE = (
    ROOT / "src" / "frontend" / "static" / "js" / "modules" / "recognition-policy.js"
)


def _run_node_module_assertions(assertions: str) -> None:
    module_url = POLICY_MODULE.resolve().as_uri()
    script = (
        f"import * as policy from {json.dumps(module_url)};\n"
        f"{assertions}\n"
    )
    subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_microphone_permission_errors_are_terminal_until_user_retry():
    _run_node_module_assertions(
        """
const terminalErrors = [
  'not-allowed',
  'service-not-allowed',
  'audio-capture',
  'NotAllowedError',
  'NotFoundError',
  'NotReadableError',
];
for (const error of terminalErrors) {
  if (!policy.isMicrophonePermissionError(error)) process.exit(1);
}
if (policy.isMicrophonePermissionError('no-speech')) process.exit(1);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_passive_recognition_does_not_restart_while_microphone_is_blocked():
    _run_node_module_assertions(
        """
if (!policy.shouldRestartPassiveRecognition('passive', false, false)) process.exit(1);
if (policy.shouldRestartPassiveRecognition('passive', false, true)) process.exit(1);
if (policy.shouldRestartPassiveRecognition('idle', false, false)) process.exit(1);
if (policy.shouldRestartPassiveRecognition('passive', true, false)) process.exit(1);
"""
    )


def test_main_uses_microphone_block_state_to_stop_automatic_retries():
    source = (
        ROOT / "src" / "frontend" / "static" / "js" / "main.js"
    ).read_text(encoding="utf-8")

    assert "microphonePermissionBlocked" in source
    assert "markMicrophonePermissionBlocked" in source
    assert "shouldRestartPassiveRecognition" in source
