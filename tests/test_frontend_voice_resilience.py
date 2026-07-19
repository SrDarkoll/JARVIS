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


def test_main_submits_audio_when_browser_transcript_is_empty():
    source = (ROOT / "src/frontend/static/js/main.js").read_text(encoding="utf-8")

    assert "detectVoiceCapabilities" in source
    assert "classifyVoiceError" in source
    assert "browserRecognitionDegraded" in source
    assert "const audioBlob = await stopBiometricRecording();" in source
    assert source.index(
        "const audioBlob = await stopBiometricRecording();"
    ) < source.index("if (!transcript && !hasAudio)")
    assert "if (audioBlob && audioBlob.size > 1000)" in source
    assert "'X-Transcript': encodeURIComponent(transcript)" in source
    assert "recordedAudioMimeType" in source
    assert "e.data.type" in source


def test_voice_diagnostics_have_english_and_spanish_translations():
    source = (ROOT / "src/frontend/static/js/i18n.js").read_text(encoding="utf-8")
    keys = {
        "voice_permission_denied",
        "voice_device_missing",
        "voice_device_busy",
        "voice_insecure_context",
        "voice_capture_unsupported",
        "voice_recognition_backend_fallback",
        "voice_recognition_network",
        "voice_transcribing_backend",
        "voice_no_input",
        "voice_transcription_unavailable",
    }

    for key in keys:
        assert source.count(f'"{key}"') == 2


def test_main_does_not_add_a_new_push_to_talk_mode():
    source = (ROOT / "src/frontend/static/js/main.js").read_text(
        encoding="utf-8"
    ).lower()
    template = (ROOT / "src/frontend/templates/index.html").read_text(
        encoding="utf-8"
    ).lower()

    assert "push-to-talk" not in source
    assert "push-to-talk" not in template
    assert 'id="ptt"' not in template
    assert 'data-mode="push-to-talk"' not in template
