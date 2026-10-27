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
        timeout=10,
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_failed_typed_delivery_is_visible_without_terminating_the_voice_session():
    _run_node_live_voice_assertions("""
const view = liveVoice.liveVoiceErrorView({code: 'live_text_not_delivered'}, key => key);
if (view.terminal || view.code !== 'live_text_not_delivered') process.exit(1);
globalThis.window = {location: {protocol: 'http:', host: 'localhost:5002'}};
globalThis.WebSocket = {OPEN: 1};
const client = new liveVoice.LiveVoiceClient();
if (client.sendUserText('otra vez') !== false) process.exit(2);
let calls = 0;
client.ws = {readyState: 1, bufferedAmount: 0, send() { calls++; }};
client.inputPaused = false;
if (client.sendUserText('Iamramidev') !== true || calls !== 1) process.exit(3);
""")


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_live_voice_ticket_url_never_contains_device_token_or_profile_override():
    _run_node_live_voice_assertions(
        """
const url = new URL(liveVoice.buildLiveVoiceSocketUrl(
    { protocol: 'https:', host: 'jarvis.tail123.ts.net' },
    { language: 'es', profileId: 'telegram_user', mode: 'gemini_live', websocketTicket: 'one-use-ticket' }
));
if (url.protocol !== 'wss:') process.exit(1);
if (url.searchParams.get('ws_ticket') !== 'one-use-ticket') process.exit(2);
if (url.searchParams.has('profile_id')) process.exit(3);
if (url.searchParams.has('device_token')) process.exit(4);
if (url.searchParams.get('lang') !== 'es') process.exit(5);
"""
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


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_live_voice_connect_rejects_when_socket_closes_before_open():
    _run_node_live_voice_assertions(
        """
globalThis.window = {
    location: { protocol: 'http:', host: 'localhost:5002' }
};

class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 3;

    constructor() {
        this.readyState = FakeWebSocket.CONNECTING;
        this.bufferedAmount = 0;
        queueMicrotask(() => {
            this.readyState = FakeWebSocket.CLOSED;
            this.onerror?.({ message: 'handshake failed' });
            this.onclose?.({ code: 1006, reason: 'denied' });
        });
    }

    close() {
        this.readyState = FakeWebSocket.CLOSED;
    }
}
globalThis.WebSocket = FakeWebSocket;

const timeout = setTimeout(() => process.exit(9), 250);
const client = new liveVoice.LiveVoiceClient({ connectTimeoutMs: 100 });
let rejected = false;
try {
    await client.connect();
} catch (error) {
    rejected = true;
}
clearTimeout(timeout);

if (!rejected) process.exit(1);
if (client.isConnected) process.exit(2);
if (client.ws !== null) process.exit(3);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_live_voice_backpressure_drops_media_but_forces_control_messages():
    _run_node_live_voice_assertions(
        """
globalThis.window = {
    location: { protocol: 'http:', host: 'localhost:5002' }
};
globalThis.WebSocket = { OPEN: 1 };

const sent = [];
const client = new liveVoice.LiveVoiceClient({ maxBufferedAmount: 1024 });
client.ws = {
    readyState: 1,
    bufferedAmount: 2048,
    send(payload) { sent.push(payload); }
};

if (client._sendBinary(new ArrayBuffer(16)) !== false) process.exit(1);
if (client.droppedAudioFrames !== 1) process.exit(2);
if (sent.length !== 0) process.exit(3);

if (client._sendJson({ type: 'screen_frame', data: 'abc' }) !== false) process.exit(4);
if (client.droppedControlMessages !== 1) process.exit(5);

if (client._sendJson({ type: 'interrupt' }, { force: true }) !== true) process.exit(6);
if (sent.length !== 1) process.exit(7);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_live_voice_partial_microphone_failure_disconnects_socket():
    _run_node_live_voice_assertions(
        """
globalThis.window = {
    location: { protocol: 'http:', host: 'localhost:5002' }
};

let closeCalls = 0;
class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 3;

    constructor() {
        this.readyState = FakeWebSocket.CONNECTING;
        this.bufferedAmount = 0;
        queueMicrotask(() => {
            this.readyState = FakeWebSocket.OPEN;
            this.onopen?.();
            this.onmessage?.({
                data: JSON.stringify({ type: 'session_ready', mode: 'gemini_live' })
            });
        });
    }

    close() {
        closeCalls += 1;
        this.readyState = FakeWebSocket.CLOSED;
    }
}
globalThis.WebSocket = FakeWebSocket;
const capture = {
    audioContext: null,
    mediaStream: null,
    captureNode: null,
    async prime() {},
    async start() { throw new Error('permission denied'); },
    async stop() {},
};
const client = new liveVoice.LiveVoiceClient({ capture });
let rejected = false;
try {
    await client.startStreaming();
} catch (error) {
    rejected = true;
}

if (!rejected) process.exit(1);
if (closeCalls !== 1) process.exit(2);
if (client.ws !== null) process.exit(3);
if (client.isConnected) process.exit(4);
if (client.mediaStream !== null) process.exit(5);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_live_voice_waits_for_provider_ready_not_socket_open():
    _run_node_live_voice_assertions(
        """
globalThis.window = { location: { protocol: 'http:', host: 'localhost:5002' } };
let socket = null;
class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 3;
    constructor() {
        socket = this;
        this.readyState = FakeWebSocket.CONNECTING;
        this.bufferedAmount = 0;
        queueMicrotask(() => {
            this.readyState = FakeWebSocket.OPEN;
            this.onopen?.();
        });
    }
    close() { this.readyState = FakeWebSocket.CLOSED; }
}
globalThis.WebSocket = FakeWebSocket;
const client = new liveVoice.LiveVoiceClient({ readyTimeoutMs: 250 });
let resolved = false;
const connecting = client.connect().then(() => { resolved = true; });
await new Promise(resolve => setTimeout(resolve, 20));
if (resolved) process.exit(1);
if (!client.isSocketOpen || client.isProviderReady) process.exit(2);
socket.onmessage?.({ data: JSON.stringify({ type: 'session_ready', mode: 'gemini_live' }) });
await connecting;
if (!resolved || !client.isProviderReady || !client.isConnected) process.exit(3);
await client.disconnect();
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_provider_failure_before_ready_never_requests_microphone():
    _run_node_live_voice_assertions(
        """
globalThis.window = { location: { protocol: 'http:', host: 'localhost:5002' } };
class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 3;
    constructor() {
        this.readyState = FakeWebSocket.CONNECTING;
        this.bufferedAmount = 0;
        queueMicrotask(() => {
            this.readyState = FakeWebSocket.OPEN;
            this.onopen?.();
            this.onmessage?.({ data: JSON.stringify({ type: 'error', code: 'gemini_live_setup_rejected' }) });
        });
    }
    close() { this.readyState = FakeWebSocket.CLOSED; }
}
globalThis.WebSocket = FakeWebSocket;
let microphoneStarts = 0;
const capture = {
    async prime() {},
    async start() { microphoneStarts += 1; },
    async stop() {},
};
const client = new liveVoice.LiveVoiceClient({ capture, readyTimeoutMs: 250 });
let rejected = false;
try { await client.startStreaming(); } catch { rejected = true; }
if (!rejected) process.exit(1);
if (microphoneStarts !== 0) process.exit(2);
if (client.ws !== null || client.isConnected) process.exit(3);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_provider_reconnect_pauses_audio_without_counting_backpressure():
    _run_node_live_voice_assertions(
        """
globalThis.window = { location: { protocol: 'http:', host: 'localhost:5002' } };
globalThis.WebSocket = { OPEN: 1 };
let sends = 0;
const client = new liveVoice.LiveVoiceClient();
client.ws = { readyState: 1, bufferedAmount: 0, send() { sends += 1; } };
client.handleWsMessage({ data: JSON.stringify({
    type: 'provider_state', state: 'reconnecting', input_paused: true,
    active_mode: 'gemini_live'
}) });
if (client._sendBinary(new ArrayBuffer(640)) !== false) process.exit(1);
if (sends !== 0 || client.droppedAudioFrames !== 0) process.exit(2);
client.handleWsMessage({ data: JSON.stringify({
    type: 'provider_state', state: 'ready', input_paused: false,
    active_mode: 'gemini_live'
}) });
if (client._sendBinary(new ArrayBuffer(640)) !== true) process.exit(3);
if (sends !== 1) process.exit(4);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_sustained_audio_backpressure_emits_one_degraded_diagnostic():
    _run_node_live_voice_assertions(
        """
globalThis.window = { location: { protocol: 'http:', host: 'localhost:5002' } };
globalThis.WebSocket = { OPEN: 1 };
const diagnostics = [];
const client = new liveVoice.LiveVoiceClient({
    maxBufferedAmount: 1024,
    maxConsecutiveAudioDrops: 3,
});
client.onDiagnostics = value => diagnostics.push(value);
client.ws = { readyState: 1, bufferedAmount: 2048, send() {} };
for (let index = 0; index < 6; index += 1) client._sendBinary(new ArrayBuffer(640));
if (diagnostics.length !== 1) process.exit(1);
if (diagnostics[0].code !== 'live_audio_transport_degraded') process.exit(2);
client.ws.bufferedAmount = 0;
if (!client._sendBinary(new ArrayBuffer(640))) process.exit(3);
if (client.consecutiveAudioDrops !== 0 || client.audioTransportDegraded) process.exit(4);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_audio_player_bounds_queued_duration_and_rejects_odd_pcm():
    _run_node_live_voice_assertions(
        """
const sources = [];
class FakeAudioContext {
    constructor() { this.state = 'running'; this.currentTime = 0; this.destination = {}; }
    createBuffer(_channels, samples, sampleRate) {
        return {
            duration: samples / sampleRate,
            getChannelData() { return { set() {} }; }
        };
    }
    createBufferSource() {
        const source = {
            stopped: false,
            connect() {}, disconnect() {}, start() {},
            stop() { this.stopped = true; this.onended?.(); }
        };
        sources.push(source);
        return source;
    }
}
globalThis.window = { AudioContext: FakeAudioContext };
const player = new liveVoice.AudioBufferQueuePlayer(24000, 1500);
if (player.enqueuePcmChunk(new ArrayBuffer(3)) !== false) process.exit(1);
if (!player.enqueuePcmChunk(new ArrayBuffer(48000))) process.exit(2);
const first = sources[0];
if (!player.enqueuePcmChunk(new ArrayBuffer(48000))) process.exit(3);
if (!first.stopped) process.exit(4);
if (player.activeSources.length !== 1) process.exit(5);
if (player.nextPlayTime > 1.01) process.exit(6);
player.interrupt();
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_provider_completion_does_not_override_audio_still_playing():
    _run_node_live_voice_assertions(
        """
const player = { isPlaying: true, interrupt() {}, enqueuePcmChunk() {} };
const client = new liveVoice.LiveVoiceClient({ player });
const states = [];
client.onStateChange = state => states.push(state);
client.handleWsMessage({data: JSON.stringify({type: 'state_change', state: 'listening'})});
client.handleWsMessage({data: JSON.stringify({type: 'turn_complete'})});
if (states.some(state => state !== 'speaking')) throw new Error(JSON.stringify(states));
player.isPlaying = false;
client.handleWsMessage({data: JSON.stringify({type: 'turn_complete'})});
if (states.at(-1) !== 'listening') throw new Error('Playback completion was ignored');
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_reconnect_discards_stale_playback_and_preserves_input_pause():
    _run_node_live_voice_assertions(
        """
let interruptions = 0;
const player = { isPlaying: true, interrupt() { this.isPlaying = false; interruptions++; } };
const client = new liveVoice.LiveVoiceClient({ player });
client.handleWsMessage({data: JSON.stringify({type: 'session_reconnecting'})});
if (interruptions !== 1 || !client.audioSendPaused) throw new Error('Stale audio survived reconnect');
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_cancelled_audio_end_event_cannot_hide_reconnecting_state():
    _run_node_live_voice_assertions(
        """
class FakeAudioContext {
    constructor() { this.state = 'running'; this.currentTime = 0; this.destination = {}; }
    createBuffer(_channels, samples, rate) { return { duration: samples / rate, getChannelData() { return { set() {} }; } }; }
    createBufferSource() {
        return { connect() {}, disconnect() {}, start() {},
            stop() { queueMicrotask(() => this.onended?.()); } };
    }
}
globalThis.window = { AudioContext: FakeAudioContext };
const client = new liveVoice.LiveVoiceClient();
const states = [];
client.onStateChange = state => states.push(state);
client.player.enqueuePcmChunk(new ArrayBuffer(48000));
client.handleWsMessage({data: JSON.stringify({type: 'session_reconnecting'})});
await new Promise(resolve => setTimeout(resolve, 180));
if (states.at(-1) !== 'reconnecting') throw new Error(JSON.stringify(states));
if (!client.audioSendPaused) throw new Error('Input must stay paused');
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_default_playback_buffer_keeps_a_normal_burst_without_cutting_sentences():
    _run_node_live_voice_assertions(
        """
const sources = [];
class FakeAudioContext {
    constructor() { this.state = 'running'; this.currentTime = 0; this.destination = {}; }
    createBuffer(_channels, samples, rate) { return { duration: samples / rate, getChannelData() { return { set() {} }; } }; }
    createBufferSource() {
        const source = { stopped: false, connect() {}, disconnect() {}, start() {}, stop() { this.stopped = true; } };
        sources.push(source); return source;
    }
}
globalThis.window = { AudioContext: FakeAudioContext };
const player = new liveVoice.AudioBufferQueuePlayer();
for (let index = 0; index < 8; index++) player.enqueuePcmChunk(new ArrayBuffer(48000));
if (sources.some(source => source.stopped)) throw new Error('A normal generated response was truncated');
if (player.nextPlayTime !== 8) throw new Error('Speech was not scheduled continuously');
player.interrupt();
if (player.activeSources.length !== 0) throw new Error('Barge-in did not clear lookahead audio');
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_live_voice_errors_map_to_stable_copy_without_raw_details():
    _run_node_live_voice_assertions(
        """
const translate = key => key;
const timeout = liveVoice.liveVoiceErrorView({
    code: 'gemini_live_setup_timeout',
    message: 'proxy=10.0.0.2 key=AIza-secret'
}, translate);
if (timeout.code !== 'gemini_live_setup_timeout') process.exit(1);
if (timeout.message !== 'live_error_gemini_live_setup_timeout') process.exit(2);
if (timeout.message.includes('AIza-secret')) process.exit(3);
const unknown = liveVoice.liveVoiceErrorView(new Error('private provider body'), translate);
if (unknown.code !== 'live_voice_unavailable') process.exit(4);
const degraded = liveVoice.liveVoiceErrorView({ code: 'live_audio_transport_degraded' }, translate);
if (degraded.terminal) process.exit(5);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_provider_transcription_prevents_duplicate_browser_recognition():
    _run_node_live_voice_assertions(
        """
let recognitionStarts = 0;
class FakeRecognition {
    start() { recognitionStarts += 1; }
    stop() {}
}
globalThis.window = {
    location: { protocol: 'http:', host: 'localhost:5002' },
    SpeechRecognition: FakeRecognition,
};
globalThis.WebSocket = { OPEN: 1 };
const capture = {
    audioContext: null, mediaStream: null, captureNode: null,
    async prime() {}, async start() {}, async stop() {},
};
const client = new liveVoice.LiveVoiceClient({ capture });
client.ws = { readyState: 1, bufferedAmount: 0, send() {} };
client.isConnected = true;
client.activeMode = 'gemini_live';
client.serverInputTranscriptionEnabled = true;
await client.startStreaming();
if (recognitionStarts !== 0 || client.rec) process.exit(1);
await client.disconnect();
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_hybrid_mode_keeps_browser_transcription_fallback():
    _run_node_live_voice_assertions(
        """
let recognitionStarts = 0;
class FakeRecognition {
    start() { recognitionStarts += 1; }
    stop() {}
}
globalThis.window = {
    location: { protocol: 'http:', host: 'localhost:5002' },
    SpeechRecognition: FakeRecognition,
};
globalThis.WebSocket = { OPEN: 1 };
const capture = {
    audioContext: null, mediaStream: null, captureNode: null,
    async prime() {}, async start() {}, async stop() {},
};
const client = new liveVoice.LiveVoiceClient({ capture });
client.ws = { readyState: 1, bufferedAmount: 0, send() {} };
client.isConnected = true;
client.activeMode = 'hybrid_stream';
await client.startStreaming();
if (recognitionStarts !== 1 || !client.rec) process.exit(1);
await client.disconnect();
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_live_transcript_commit_and_provider_config_contracts():
    _run_node_live_voice_assertions(
        """
if (liveVoice.shouldCommitLiveTranscript({ is_final: false })) process.exit(1);
if (!liveVoice.shouldCommitLiveTranscript({ is_final: true })) process.exit(2);
if (liveVoice.shouldCommitLiveTranscript({})) process.exit(3);

globalThis.window = { location: { protocol: 'http:', host: 'localhost:5002' } };
const client = new liveVoice.LiveVoiceClient();
client.handleWsMessage({ data: JSON.stringify({
    type: 'session_ready',
    mode: 'gemini_live',
    input_transcription_enabled: false,
    output_transcription_enabled: true,
    browser_transcription_required: true,
}) });
if (client.serverInputTranscriptionEnabled !== false) process.exit(4);
if (client.serverOutputTranscriptionEnabled !== true) process.exit(5);
if (client.browserTranscriptionRequired !== true) process.exit(6);
"""
    )


def test_live_voice_frontend_loads_backend_runtime_limits():
    api_source = (ROOT / "src/frontend/static/js/modules/api.js").read_text(
        encoding="utf-8"
    )
    main_source = (ROOT / "src/frontend/static/js/main.js").read_text(
        encoding="utf-8"
    )

    assert "async fetchLiveVoiceStatus()" in api_source
    assert "await API.fetchLiveVoiceStatus()" in main_source
    assert "inputChunkMs: liveStatus.input_chunk_ms" in main_source
    assert "maxPlaybackQueueMs: liveStatus.max_playback_queue_ms" in main_source
    assert "maxConsecutiveAudioDrops: liveStatus.max_consecutive_audio_drops" in main_source
    assert "shouldCommitLiveTranscript(data)" in main_source


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_classic_live_microphone_handoff_runtime_regressions():
    result = subprocess.run(
        [NODE, "--test", "tests/frontend_live_voice_lifecycle.cjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
