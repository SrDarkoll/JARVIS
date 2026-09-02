/**
 * Live Full-Duplex Voice Streaming Client & Gapless PCM Player for J.A.R.V.I.S.
 *
 * Implements bidirectional WebSocket audio streaming, gapless playback queue,
 * and best-effort low-latency barge-in (interruption) detection.
 */

import {
    LivePcmCapture,
    isValidPcm16Chunk,
} from './live-pcm-capture.js';

const LIVE_ERROR_CODES = new Set([
    'gemini_live_unconfigured',
    'gemini_live_dependency_missing',
    'gemini_live_connect_failed',
    'gemini_live_setup_timeout',
    'gemini_live_setup_rejected',
    'gemini_live_reconnect_exhausted',
    'gemini_live_protocol_error',
    'live_audio_capture_unsupported',
    'live_audio_transport_degraded',
    'live_voice_unavailable',
]);

export function normalizeLiveVoiceError(error) {
    const candidates = [error?.code, error?.message, error];
    for (const candidate of candidates) {
        const value = String(candidate || '').trim().toLowerCase();
        if (LIVE_ERROR_CODES.has(value)) return value;
        const match = value.match(/[a-z0-9_]+/g) || [];
        const known = match.find(token => LIVE_ERROR_CODES.has(token));
        if (known) return known;
    }
    return 'live_voice_unavailable';
}

export function liveVoiceErrorView(error, translate) {
    const code = normalizeLiveVoiceError(error);
    const key = `live_error_${code}`;
    return {
        code,
        key,
        message: typeof translate === 'function' ? translate(key) : key,
        terminal: code !== 'live_audio_transport_degraded',
    };
}

export class AudioBufferQueuePlayer {
    constructor(sampleRate = 24000, maxQueuedMs = 2000) {
        this.sampleRate = sampleRate;
        this.maxQueuedMs = Math.max(250, Number(maxQueuedMs) || 2000);
        this.audioCtx = null;
        this.nextPlayTime = 0;
        this.activeSources = [];
        this.isPlaying = false;
        this.onPlaybackStateChange = null;
    }

    getAudioContext() {
        if (!this.audioCtx || this.audioCtx.state === 'closed') {
            this.audioCtx = new (window.AudioContext || window.webkitAudioContext)({
                sampleRate: this.sampleRate
            });
        }
        if (this.audioCtx.state === 'suspended') {
            this.audioCtx.resume();
        }
        return this.audioCtx;
    }

    /**
     * Enqueue and play a raw 16-bit PCM chunk seamlessly.
     * @param {ArrayBuffer|Uint8Array|Int16Array} rawPcmData
     */
    enqueuePcmChunk(rawPcmData) {
        const ctx = this.getAudioContext();
        let int16Array;
        if (rawPcmData instanceof Int16Array) {
            int16Array = rawPcmData;
        } else if (rawPcmData instanceof ArrayBuffer) {
            if (!isValidPcm16Chunk(rawPcmData)) return false;
            int16Array = new Int16Array(rawPcmData);
        } else if (rawPcmData.buffer) {
            if (rawPcmData.byteLength <= 0 || rawPcmData.byteLength % 2 !== 0) {
                return false;
            }
            int16Array = new Int16Array(rawPcmData.buffer, rawPcmData.byteOffset, rawPcmData.byteLength / 2);
        } else {
            return false;
        }

        const numSamples = int16Array.length;
        if (numSamples === 0) return false;

        const floatArray = new Float32Array(numSamples);
        for (let i = 0; i < numSamples; i++) {
            floatArray[i] = int16Array[i] / 32768.0;
        }

        const audioBuffer = ctx.createBuffer(1, numSamples, this.sampleRate);
        audioBuffer.getChannelData(0).set(floatArray);

        const source = ctx.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(ctx.destination);

        const currentTime = ctx.currentTime;
        const queuedMs = Math.max(0, this.nextPlayTime - currentTime) * 1000;
        if (queuedMs + (audioBuffer.duration * 1000) > this.maxQueuedMs) {
            this.interrupt();
        }
        const startTime = Math.max(currentTime, this.nextPlayTime);
        source.start(startTime);

        this.nextPlayTime = startTime + audioBuffer.duration;
        this.activeSources.push(source);

        if (!this.isPlaying) {
            this.isPlaying = true;
            this.onPlaybackStateChange?.(true);
        }

        source.onended = () => {
            const index = this.activeSources.indexOf(source);
            if (index !== -1) {
                this.activeSources.splice(index, 1);
            }
            if (this.activeSources.length === 0) {
                if (this._stopCheckTimer) clearTimeout(this._stopCheckTimer);
                this._stopCheckTimer = setTimeout(() => {
                    if (this.activeSources.length === 0 && ctx.currentTime >= this.nextPlayTime - 0.05) {
                        this.isPlaying = false;
                        this.nextPlayTime = 0;
                        this.onPlaybackStateChange?.(false);
                    }
                }, 120);
            }
        };
        return true;
    }

    /**
     * Instantly halts all active audio playback and clears queue (barge-in).
     */
    interrupt() {
        if (this._stopCheckTimer) {
            clearTimeout(this._stopCheckTimer);
            this._stopCheckTimer = null;
        }
        for (const source of this.activeSources) {
            try {
                source.stop();
                source.disconnect();
            } catch (e) {
                // Ignore already stopped sources
            }
        }
        this.activeSources = [];
        this.nextPlayTime = 0;
        if (this.isPlaying) {
            this.isPlaying = false;
            this.onPlaybackStateChange?.(false);
        }
    }
}

export class BargeInDetector {
    constructor(options = {}) {
        this.speechThreshold = options.speechThreshold ?? 0.025;
        this.minConsecutiveFrames = options.minConsecutiveFrames ?? 3;
        this.consecutiveFrames = 0;
        this.onBargeIn = null;
        this.isAssistantSpeaking = false;
    }

    setAssistantSpeaking(speaking) {
        this.isAssistantSpeaking = Boolean(speaking);
        if (!speaking) {
            this.consecutiveFrames = 0;
        }
    }

    processAudioFrame(samples) {
        if (!this.isAssistantSpeaking || !samples || samples.length === 0) {
            this.consecutiveFrames = 0;
            return;
        }

        let sumSq = 0;
        for (let i = 0; i < samples.length; i++) {
            sumSq += samples[i] * samples[i];
        }
        const rms = Math.sqrt(sumSq / samples.length);

        if (rms >= this.speechThreshold) {
            this.consecutiveFrames += 1;
            if (this.consecutiveFrames >= this.minConsecutiveFrames) {
                this.consecutiveFrames = 0;
                this.onBargeIn?.();
            }
        } else {
            this.consecutiveFrames = 0;
        }
    }
}

export class LiveVoiceClient {
    constructor(options = {}) {
        this.language = options.language || 'es';
        this.profileId = options.profileId || 'default';
        this.mode = options.mode || 'auto';
        this.ws = null;
        this.player = options.player || new AudioBufferQueuePlayer(
            24000,
            options.maxPlaybackQueueMs ?? 2000
        );
        this.bargeInDetector = new BargeInDetector();
        this.capture = options.capture || new LivePcmCapture({
            windowRef: globalThis.window,
            targetRate: 16000,
            chunkMs: options.inputChunkMs ?? 20,
        });
        this.audioCtx = null;
        this.mediaStream = null;
        this.processorNode = null;
        this.isConnected = false;
        this.isSocketOpen = false;
        this.isProviderReady = false;
        this.activeMode = '';
        this.isStreaming = false;
        this.connectTimeoutMs = Math.max(100, Number(options.connectTimeoutMs ?? 10000));
        this.readyTimeoutMs = Math.max(100, Number(options.readyTimeoutMs ?? 12000));
        this.maxBufferedAmount = Math.max(1024, Number(options.maxBufferedAmount ?? 256 * 1024));
        this.maxConsecutiveAudioDrops = Math.max(
            3,
            Number(options.maxConsecutiveAudioDrops ?? 25)
        );
        this.consecutiveAudioDrops = 0;
        this.audioTransportDegraded = false;
        this.audioSendPaused = false;
        this.droppedAudioFrames = 0;
        this.droppedControlMessages = 0;
        this.droppedScreenFrames = 0;
        this._connectPromise = null;
        this._readyResolve = null;
        this._readyReject = null;
        this.isScreenSharing = false;
        this.screenStream = null;
        this.screenVideo = null;
        this.screenCanvas = null;
        this.screenInterval = null;

        // Event callbacks
        this.onStateChange = null;
        this.onTranscript = null;
        this.onInterrupted = null;
        this.onError = null;
        this.onSessionReady = null;
        this.onToolExecuting = null;
        this.onActionPlanUpdated = null;
        this.onActionCancelled = null;
        this.onDiagnostics = null;
        this.onReconnecting = null;
        this.onScreenShareStateChange = null;

        this.player.onPlaybackStateChange = (speaking) => {
            this.bargeInDetector.setAssistantSpeaking(speaking);
            this.onStateChange?.(speaking ? 'speaking' : 'listening');
        };

        this.bargeInDetector.onBargeIn = () => {
            this.handleLocalBargeIn();
        };
    }

    async connect() {
        if (this.isConnected && this.ws?.readyState === WebSocket.OPEN) return true;
        if (this._connectPromise) return this._connectPromise;

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host;
        const url = `${protocol}//${host}/api/voice/stream?lang=${encodeURIComponent(this.language)}&profile_id=${encodeURIComponent(this.profileId)}&mode=${encodeURIComponent(this.mode)}`;

        const connectionPromise = new Promise((resolve, reject) => {
            let settled = false;
            let timeoutId = null;

            const settle = (callback, value) => {
                if (settled) return;
                settled = true;
                if (timeoutId) clearTimeout(timeoutId);
                this._readyResolve = null;
                this._readyReject = null;
                callback(value);
            };

            const rejectConnection = (error) => {
                const socket = this.ws;
                this.ws = null;
                this.isConnected = false;
                this.isProviderReady = false;
                this.isSocketOpen = false;
                if (socket && socket.readyState !== WebSocket.CLOSED) {
                    try { socket.close(); } catch (closeError) {}
                }
                settle(reject, error instanceof Error ? error : new Error('live_websocket_connection_failed'));
            };

            try {
                const socket = new WebSocket(url);
                this.ws = socket;
                socket.binaryType = 'arraybuffer';
                this._readyResolve = value => settle(resolve, value);
                this._readyReject = error => rejectConnection(error);

                socket.onopen = () => {
                    if (this.ws !== socket) return;
                    this.isSocketOpen = true;
                };

                socket.onmessage = (event) => {
                    this.handleWsMessage(event);
                };

                socket.onerror = (err) => {
                    this.onError?.(err);
                    if (!settled) {
                        rejectConnection(new Error('live_websocket_connection_failed'));
                    }
                };

                socket.onclose = (event = {}) => {
                    const opened = this.isSocketOpen;
                    if (this.ws === socket) this.ws = null;
                    this.isConnected = false;
                    this.isProviderReady = false;
                    this.isSocketOpen = false;
                    void this.stopStreaming();
                    this.onStateChange?.('closed');
                    if (!settled) {
                        const code = Number(event.code || 0);
                        const reason = opened
                            ? `live_provider_closed_before_ready:${code}`
                            : `live_websocket_closed_before_open:${code}`;
                        rejectConnection(new Error(reason));
                    }
                };

                timeoutId = setTimeout(() => {
                    rejectConnection(new Error('live_provider_ready_timeout'));
                }, Math.max(this.connectTimeoutMs, this.readyTimeoutMs));
            } catch (e) {
                rejectConnection(e);
            }
        });

        this._connectPromise = connectionPromise;
        try {
            return await connectionPromise;
        } finally {
            if (this._connectPromise === connectionPromise) {
                this._connectPromise = null;
            }
        }
    }

    _canSend(payloadBytes = 0, force = false) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
        if (force) return true;
        const buffered = Number(this.ws.bufferedAmount || 0);
        return buffered + Math.max(0, Number(payloadBytes || 0)) <= this.maxBufferedAmount;
    }

    _sendBinary(payload, { force = false } = {}) {
        if (this.audioSendPaused && !force) return false;
        const size = Number(payload?.byteLength ?? payload?.length ?? 0);
        if (!this._canSend(size, force)) {
            this.droppedAudioFrames += 1;
            this.consecutiveAudioDrops += 1;
            if (
                !this.audioTransportDegraded
                && this.consecutiveAudioDrops >= this.maxConsecutiveAudioDrops
            ) {
                this.audioTransportDegraded = true;
                this.onDiagnostics?.({
                    code: 'live_audio_transport_degraded',
                    dropped_audio_frames: this.droppedAudioFrames,
                });
            }
            return false;
        }
        try {
            this.ws.send(payload);
            this.consecutiveAudioDrops = 0;
            this.audioTransportDegraded = false;
            return true;
        } catch (error) {
            this.onError?.(error);
            return false;
        }
    }

    _sendJson(data, { force = false } = {}) {
        const payload = JSON.stringify(data);
        if (!this._canSend(payload.length, force)) {
            this.droppedControlMessages += 1;
            if (data?.type === 'screen_frame' || data?.type === 'image_frame') {
                this.droppedScreenFrames += 1;
            }
            return false;
        }
        try {
            this.ws.send(payload);
            return true;
        } catch (error) {
            this.onError?.(error);
            return false;
        }
    }

    handleWsMessage(event) {
        if (event.data instanceof ArrayBuffer) {
            this.player.enqueuePcmChunk(event.data);
            return;
        }

        try {
            const data = JSON.parse(event.data);
            switch (data.type) {
                case 'session_ready':
                    this.isConnected = true;
                    this.isProviderReady = true;
                    this.audioSendPaused = false;
                    this.activeMode = data.mode || '';
                    this._readyResolve?.(true);
                    this.onSessionReady?.(data);
                    break;
                case 'provider_state':
                    this.audioSendPaused = Boolean(data.input_paused);
                    if (data.active_mode) this.activeMode = data.active_mode;
                    this.onStateChange?.(data.state);
                    break;
                case 'state_change':
                    this.onStateChange?.(data.state);
                    break;
                case 'tool_executing':
                case 'tool_call':
                    this.onToolExecuting?.(data);
                    break;
                case 'action_plan_updated':
                    this.onActionPlanUpdated?.(data.plan);
                    break;
                case 'action_cancelled':
                    this.onActionCancelled?.(data);
                    break;
                case 'diagnostics':
                    this.onDiagnostics?.(data.metrics);
                    break;
                case 'session_reconnecting':
                    this.audioSendPaused = true;
                    this.onReconnecting?.(data);
                    break;
                case 'live_fallback_active':
                    this.activeMode = data.mode || 'hybrid_stream';
                    this.audioSendPaused = false;
                    this.onSessionReady?.(data);
                    break;
                case 'transcript':
                    this.onTranscript?.(data);
                    break;
                case 'interrupted':
                    this.player.interrupt();
                    this.onInterrupted?.(data);
                    break;
                case 'turn_complete':
                    this.onStateChange?.('listening');
                    if (data.diagnostics) {
                        this.onDiagnostics?.(data.diagnostics);
                    }
                    break;
                case 'error':
                    if (!this.isProviderReady) {
                        this._readyReject?.(new Error(data.code || 'live_voice_unavailable'));
                    }
                    this.onError?.(data);
                    break;
            }
        } catch (e) {
            console.warn('[LIVE_VOICE] Error parsing JSON message:', e);
        }
    }

    handleLocalBargeIn() {
        this.player.interrupt();
        this._sendJson({ type: 'interrupt' }, { force: true });
        this.onInterrupted?.({ source: 'local_barge_in' });
    }

    async startStreaming() {
        if (this.isStreaming) return;
        try {
            await this.capture.prime();
            if (!this.isConnected || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
                await this.connect();
            }
            await this.capture.start({
                onChunk: chunk => this._sendBinary(chunk),
                onFrame: frame => this.bargeInDetector.processAudioFrame(frame),
                onDegraded: code => this.onDiagnostics?.({ code }),
            });
            this.audioCtx = this.capture.audioContext;
            this.mediaStream = this.capture.mediaStream;
            this.processorNode = this.capture.captureNode;
            this.isStreaming = true;
            this.onStateChange?.('listening');

            // Optional parallel browser speech recognition for local user transcription
            const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (SpeechRec) {
                try {
                    this.rec = new SpeechRec();
                    this.rec.continuous = true;
                    this.rec.interimResults = false;
                    this.rec.lang = this.language === 'en' ? 'en-US' : 'es-MX';
                    this.rec.onresult = (event) => {
                        for (let i = event.resultIndex; i < event.results.length; ++i) {
                            if (event.results[i].isFinal) {
                                const userTranscript = event.results[i][0].transcript.trim();
                                if (userTranscript) {
                                    this.sendUserTranscript(userTranscript);
                                    this.onTranscript?.({ role: 'user', text: userTranscript, is_final: true });
                                }
                            }
                        }
                    };
                    this.rec.onerror = () => {};
                    this.rec.onend = () => {
                        if (this.isStreaming && this.rec) {
                            try { this.rec.start(); } catch (e) {}
                        }
                    };
                    try { this.rec.start(); } catch (e) {}
                } catch (e) {
                    console.debug('[LIVE_VOICE] Browser speech recognition init:', e);
                }
            }
        } catch (error) {
            await this.disconnect();
            throw error;
        }
    }

    sendUserTranscript(text) {
        this._sendJson({ type: 'user_transcript', text });
    }

    sendUserText(text) {
        this._sendJson({ type: 'user_text', text });
    }

    async startScreenShare() {
        if (this.isScreenSharing) return true;
        if (!navigator.mediaDevices?.getDisplayMedia) {
            console.warn('[LIVE_VOICE] getDisplayMedia is not supported in this browser.');
            return false;
        }

        try {
            const stream = await navigator.mediaDevices.getDisplayMedia({
                video: {
                    cursor: 'always',
                    frameRate: { ideal: 1, max: 2 },
                },
                audio: false,
            });

            this.screenStream = stream;
            this.screenVideo = document.createElement('video');
            this.screenVideo.srcObject = stream;
            this.screenVideo.muted = true;
            this.screenVideo.playsInline = true;
            await this.screenVideo.play();

            this.screenCanvas = document.createElement('canvas');
            this.isScreenSharing = true;
            this.onScreenShareStateChange?.(true);

            // Stream frames at ~1 FPS
            const captureAndSendFrame = () => {
                if (!this.isScreenSharing || !this.screenVideo || !this.screenCanvas) return;
                const video = this.screenVideo;
                if (video.videoWidth === 0 || video.videoHeight === 0) return;

                // Scale down maintaining aspect ratio (max dimension 1280)
                let width = video.videoWidth;
                let height = video.videoHeight;
                const maxDim = 1280;
                if (width > maxDim || height > maxDim) {
                    if (width > height) {
                        height = Math.round((height * maxDim) / width);
                        width = maxDim;
                    } else {
                        width = Math.round((width * maxDim) / height);
                        height = maxDim;
                    }
                }

                this.screenCanvas.width = width;
                this.screenCanvas.height = height;
                const ctx = this.screenCanvas.getContext('2d');
                if (!ctx) return;
                ctx.drawImage(video, 0, 0, width, height);

                const dataUrl = this.screenCanvas.toDataURL('image/jpeg', 0.6);
                const b64Data = dataUrl.split(',')[1];

                if (b64Data) {
                    this._sendJson({
                        type: 'screen_frame',
                        mime_type: 'image/jpeg',
                        data: b64Data,
                    });
                }
            };

            // Send first frame shortly after stream starts
            setTimeout(captureAndSendFrame, 300);
            this.screenInterval = setInterval(captureAndSendFrame, 1000);

            // Handle when user stops sharing via browser UI bar
            const track = stream.getVideoTracks()[0];
            if (track) {
                track.onended = () => {
                    this.stopScreenShare();
                };
            }

            return true;
        } catch (err) {
            console.warn('[LIVE_VOICE] Failed to start screen share:', err);
            this.stopScreenShare();
            return false;
        }
    }

    stopScreenShare() {
        if (!this.isScreenSharing && !this.screenStream) return;
        this.isScreenSharing = false;

        if (this.screenInterval) {
            clearInterval(this.screenInterval);
            this.screenInterval = null;
        }
        if (this.screenStream) {
            this.screenStream.getTracks().forEach(t => t.stop());
            this.screenStream = null;
        }
        if (this.screenVideo) {
            this.screenVideo.srcObject = null;
            this.screenVideo = null;
        }
        this.screenCanvas = null;
        this.onScreenShareStateChange?.(false);
    }

    async stopStreaming() {
        this.isStreaming = false;
        this.stopScreenShare();
        if (this.rec) {
            try { this.rec.stop(); } catch (e) {}
            this.rec = null;
        }
        await this.capture.stop();
        this.processorNode = null;
        this.mediaStream = null;
        this.audioCtx = null;
        this.player.interrupt();
    }

    async disconnect() {
        await this.stopStreaming();
        const socket = this.ws;
        this.ws = null;
        if (socket) {
            try { socket.close(); } catch (e) {}
        }
        this.isConnected = false;
        this.isProviderReady = false;
        this.isSocketOpen = false;
        this.audioSendPaused = false;
        this._readyReject?.(new Error('live_client_disconnected'));
        this._readyResolve = null;
        this._readyReject = null;
    }
}
