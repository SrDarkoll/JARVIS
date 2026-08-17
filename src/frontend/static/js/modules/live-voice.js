/**
 * Live Full-Duplex Voice Streaming Client & Gapless PCM Player for J.A.R.V.I.S.
 *
 * Implements bidirectional WebSocket audio streaming, gapless playback queue,
 * and zero-latency barge-in (interruption) detection.
 */

export class AudioBufferQueuePlayer {
    constructor(sampleRate = 24000) {
        this.sampleRate = sampleRate;
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
            int16Array = new Int16Array(rawPcmData);
        } else if (rawPcmData.buffer) {
            int16Array = new Int16Array(rawPcmData.buffer, rawPcmData.byteOffset, rawPcmData.byteLength / 2);
        } else {
            return;
        }

        const numSamples = int16Array.length;
        if (numSamples === 0) return;

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
            if (this.activeSources.length === 0 && ctx.currentTime >= this.nextPlayTime - 0.05) {
                this.isPlaying = false;
                this.nextPlayTime = 0;
                this.onPlaybackStateChange?.(false);
            }
        };
    }

    /**
     * Instantly halts all active audio playback and clears queue (barge-in).
     */
    interrupt() {
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
        this.player = new AudioBufferQueuePlayer(24000);
        this.bargeInDetector = new BargeInDetector();
        this.audioCtx = null;
        this.mediaStream = null;
        this.processorNode = null;
        this.isConnected = false;
        this.isStreaming = false;

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

        this.player.onPlaybackStateChange = (speaking) => {
            this.bargeInDetector.setAssistantSpeaking(speaking);
            this.onStateChange?.(speaking ? 'speaking' : 'listening');
        };

        this.bargeInDetector.onBargeIn = () => {
            this.handleLocalBargeIn();
        };
    }

    async connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host;
        const url = `${protocol}//${host}/api/voice/stream?lang=${encodeURIComponent(this.language)}&profile_id=${encodeURIComponent(this.profileId)}&mode=${encodeURIComponent(this.mode)}`;

        return new Promise((resolve, reject) => {
            try {
                this.ws = new WebSocket(url);
                this.ws.binaryType = 'arraybuffer';

                this.ws.onopen = () => {
                    this.isConnected = true;
                    resolve(true);
                };

                this.ws.onmessage = (event) => {
                    this.handleWsMessage(event);
                };

                this.ws.onerror = (err) => {
                    this.onError?.(err);
                };

                this.ws.onclose = () => {
                    this.isConnected = false;
                    this.stopStreaming();
                    this.onStateChange?.('closed');
                };
            } catch (e) {
                reject(e);
            }
        });
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
                    this.onSessionReady?.(data);
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
                    this.onReconnecting?.(data);
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
                    this.onError?.(data);
                    break;
            }
        } catch (e) {
            console.warn('[LIVE_VOICE] Error parsing JSON message:', e);
        }
    }

    handleLocalBargeIn() {
        this.player.interrupt();
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'interrupt' }));
        }
        this.onInterrupted?.({ source: 'local_barge_in' });
    }

    async startStreaming() {
        if (this.isStreaming) return;
        if (!this.isConnected || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            await this.connect();
        }

        const stream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                sampleRate: 16000,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            }
        });

        this.mediaStream = stream;
        this.audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        const source = this.audioCtx.createMediaStreamSource(stream);

        // Capture chunks via ScriptProcessor or AudioWorklet
        const bufferSize = 2048;
        this.processorNode = this.audioCtx.createScriptProcessor(bufferSize, 1, 1);

        this.processorNode.onaudioprocess = (e) => {
            if (!this.isStreaming) return;
            const inputData = e.inputBuffer.getChannelData(0);

            // Feed barge-in detector
            this.bargeInDetector.processAudioFrame(inputData);

            // Convert Float32Array to 16-bit PCM Int16Array
            const pcmBuffer = new ArrayBuffer(inputData.length * 2);
            const pcmView = new DataView(pcmBuffer);
            for (let i = 0; i < inputData.length; i++) {
                const s = Math.max(-1, Math.min(1, inputData[i]));
                pcmView.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
            }

            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(pcmBuffer);
            }
        };

        source.connect(this.processorNode);
        this.processorNode.connect(this.audioCtx.destination);
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
    }

    sendUserTranscript(text) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'user_transcript', text }));
        }
    }

    sendUserText(text) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'user_text', text }));
        }
    }

    stopStreaming() {
        this.isStreaming = false;
        if (this.rec) {
            try { this.rec.stop(); } catch (e) {}
            this.rec = null;
        }
        if (this.processorNode) {
            try { this.processorNode.disconnect(); } catch (e) {}
            this.processorNode = null;
        }
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(t => t.stop());
            this.mediaStream = null;
        }
        if (this.audioCtx && this.audioCtx.state !== 'closed') {
            this.audioCtx.close();
            this.audioCtx = null;
        }
        this.player.interrupt();
    }

    disconnect() {
        this.stopStreaming();
        if (this.ws) {
            try { this.ws.close(); } catch (e) {}
            this.ws = null;
        }
        this.isConnected = false;
    }
}
