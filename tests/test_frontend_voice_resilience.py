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
CAPABILITIES_MODULE = (
    ROOT / "src" / "frontend" / "static" / "js" / "modules" / "voice-capabilities.js"
)


def _run_node_module_assertions(assertions: str) -> None:
    policy_url = POLICY_MODULE.resolve().as_uri()
    capabilities_url = CAPABILITIES_MODULE.resolve().as_uri()
    script = (
        f"import * as policy from {json.dumps(policy_url)};\n"
        f"import * as capabilities from {json.dumps(capabilities_url)};\n"
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
if (!policy.shouldRestartPassiveRecognition('passive', false, false, false)) process.exit(1);
if (policy.shouldRestartPassiveRecognition('passive', false, true, false)) process.exit(1);
if (policy.shouldRestartPassiveRecognition('passive', false, false, true)) process.exit(1);
if (policy.shouldRestartPassiveRecognition('idle', false, false, false)) process.exit(1);
if (policy.shouldRestartPassiveRecognition('passive', true, false, false)) process.exit(1);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_browser_voice_capabilities_are_detected_independently():
    _run_node_module_assertions(
        """
const complete = capabilities.detectVoiceCapabilities({
  isSecureContext: true,
  navigator: { mediaDevices: { getUserMedia() {} } },
  MediaRecorder: function MediaRecorder() {},
  SpeechRecognition: function SpeechRecognition() {},
});
if (!complete.canCaptureAudio || !complete.hasBrowserRecognition) process.exit(1);

const firefoxLike = capabilities.detectVoiceCapabilities({
  isSecureContext: true,
  navigator: { mediaDevices: { getUserMedia() {} } },
  MediaRecorder: function MediaRecorder() {},
});
if (!firefoxLike.canCaptureAudio || firefoxLike.hasBrowserRecognition) process.exit(1);

const cases = new Map([
  ['NotAllowedError', 'permission_denied'],
  ['NotFoundError', 'device_missing'],
  ['NotReadableError', 'device_busy'],
  ['SecurityError', 'insecure_context'],
  ['network', 'recognition_network'],
]);
for (const [error, expected] of cases) {
  if (capabilities.classifyVoiceError(error) !== expected) process.exit(1);
}
"""
    )


def test_main_uses_microphone_block_state_to_stop_automatic_retries():
    source = (
        ROOT / "src" / "frontend" / "static" / "js" / "main.js"
    ).read_text(encoding="utf-8")

    assert "microphonePermissionBlocked" in source
    assert "markMicrophonePermissionBlocked" in source
    assert "shouldRestartPassiveRecognition" in source
