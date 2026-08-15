"""Frontend unit tests for LiveVoiceClient and BargeInDetector using Node.js."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
LIVE_VOICE_MODULE = (
    ROOT / "src" / "frontend" / "static" / "js" / "modules" / "live-voice.js"
)


def _run_node_live_voice_assertions(assertions: str) -> None:
    live_voice_url = LIVE_VOICE_MODULE.resolve().as_uri()
    script = (
        f"import * as liveVoice from {json.dumps(live_voice_url)};\n"
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
def test_barge_in_detector_triggers_only_when_assistant_speaking():
    _run_node_live_voice_assertions(
        """
let bargeInTriggered = false;
const detector = new liveVoice.BargeInDetector({
    speechThreshold: 0.05,
    minConsecutiveFrames: 2
});
detector.onBargeIn = () => { bargeInTriggered = true; };

// Silent audio frame while assistant is silent -> no barge-in
const silentFrame = new Float32Array([0.001, 0.002, 0.001, 0.000]);
detector.processAudioFrame(silentFrame);
if (bargeInTriggered) process.exit(1);

// Loud audio frame while assistant is NOT speaking -> no barge-in
const loudFrame = new Float32Array([0.2, 0.3, 0.25, 0.18]);
detector.processAudioFrame(loudFrame);
detector.processAudioFrame(loudFrame);
if (bargeInTriggered) process.exit(2);

// Loud audio frame when assistant IS speaking -> triggers barge-in
detector.setAssistantSpeaking(true);
detector.processAudioFrame(loudFrame);
if (bargeInTriggered) process.exit(3); // only 1 frame, needs 2

detector.processAudioFrame(loudFrame);
if (!bargeInTriggered) process.exit(4); // 2nd frame, must trigger
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_audio_buffer_queue_player_initial_state():
    _run_node_live_voice_assertions(
        """
const player = new liveVoice.AudioBufferQueuePlayer(24000);
if (player.sampleRate !== 24000) process.exit(1);
if (player.isPlaying !== false) process.exit(2);
if (player.activeSources.length !== 0) process.exit(3);

player.interrupt();
if (player.isPlaying !== false) process.exit(4);
"""
    )
