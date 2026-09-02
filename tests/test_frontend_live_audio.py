from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
MODULE = ROOT / "src/frontend/static/js/modules/live-pcm-capture.js"


def _run_node(assertions: str) -> None:
    script = f"import * as audio from {json.dumps(MODULE.resolve().as_uri())};\n{assertions}\n"
    subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required")
def test_live_audio_resamples_48khz_to_exact_16khz_chunks():
    _run_node(
        """
const source = new Float32Array(4800);
for (let i = 0; i < source.length; i += 1) {
  source[i] = Math.sin((2 * Math.PI * 440 * i) / 48000);
}
const resampled = audio.resampleMono(source, 48000, 16000);
if (resampled.length !== 1600) process.exit(1);
const pcm = audio.floatToPcm16Le(resampled);
if (!(pcm instanceof ArrayBuffer) || pcm.byteLength !== 3200) process.exit(2);
const chunks = audio.chunkPcm16(pcm, 16000, 20);
if (chunks.length !== 5) process.exit(3);
if (chunks.some(chunk => chunk.byteLength !== 640)) process.exit(4);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required")
def test_streaming_chunker_handles_worklet_frames_without_rate_drift():
    _run_node(
        """
for (const sourceRate of [44100, 48000]) {
  const chunker = new audio.LivePcmChunker({ sourceRate, targetRate: 16000, chunkMs: 20 });
  const total = Math.round(sourceRate * 0.1);
  let offset = 0;
  const chunks = [];
  while (offset < total) {
    const size = Math.min(128, total - offset);
    chunks.push(...chunker.push(new Float32Array(size).fill(0.2)));
    offset += size;
  }
  if (chunks.length !== 5) process.exit(1);
  if (chunks.some(chunk => chunk.byteLength !== 640)) process.exit(2);
  if (chunker.pendingSamples !== 0) process.exit(3);
}
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required")
def test_pcm_conversion_is_little_endian_and_bounded():
    _run_node(
        """
const pcm = audio.floatToPcm16Le(new Float32Array([-2, -1, 0, 1, 2]));
const view = new DataView(pcm);
if (view.getInt16(0, true) !== -32768) process.exit(1);
if (view.getInt16(2, true) !== -32768) process.exit(2);
if (view.getInt16(4, true) !== 0) process.exit(3);
if (view.getInt16(6, true) !== 32767) process.exit(4);
if (view.getInt16(8, true) !== 32767) process.exit(5);
if (audio.isValidPcm16Chunk(new ArrayBuffer(3))) process.exit(6);
if (!audio.isValidPcm16Chunk(new ArrayBuffer(640))) process.exit(7);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required")
def test_capture_prime_does_not_request_microphone_permission():
    _run_node(
        """
let permissionRequests = 0;
class FakeAudioContext {
  constructor() {
    this.state = 'suspended';
    this.sampleRate = 48000;
    this.destination = {};
    this.audioWorklet = { async addModule() {} };
  }
  async resume() { this.state = 'running'; }
  async close() { this.state = 'closed'; }
}
const windowRef = { AudioContext: FakeAudioContext };
Object.defineProperty(globalThis, 'navigator', {
  configurable: true,
  value: { mediaDevices: { async getUserMedia() { permissionRequests += 1; } } }
});
const capture = new audio.LivePcmCapture({ windowRef });
await capture.prime();
if (permissionRequests !== 0) process.exit(1);
if (capture.audioContext.state !== 'running') process.exit(2);
await capture.stop();
if (capture.audioContext !== null) process.exit(3);
"""
    )
