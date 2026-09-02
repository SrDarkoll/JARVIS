/** Deterministic microphone resampling and bounded PCM capture for Live voice. */

export function resampleMono(samples, sourceRate, targetRate = 16000) {
    const input = samples instanceof Float32Array
        ? samples
        : new Float32Array(samples || []);
    const from = Math.max(1, Number(sourceRate) || targetRate);
    const to = Math.max(1, Number(targetRate) || 16000);
    if (input.length === 0) return new Float32Array();
    if (from === to) return new Float32Array(input);

    const outputLength = Math.max(1, Math.round((input.length * to) / from));
    const output = new Float32Array(outputLength);
    const ratio = from / to;
    for (let index = 0; index < outputLength; index += 1) {
        const position = index * ratio;
        const left = Math.min(input.length - 1, Math.floor(position));
        const right = Math.min(input.length - 1, left + 1);
        const weight = position - left;
        output[index] = (input[left] * (1 - weight)) + (input[right] * weight);
    }
    return output;
}

export function floatToPcm16Le(samples) {
    const input = samples instanceof Float32Array
        ? samples
        : new Float32Array(samples || []);
    const buffer = new ArrayBuffer(input.length * 2);
    const view = new DataView(buffer);
    for (let index = 0; index < input.length; index += 1) {
        const sample = Math.max(-1, Math.min(1, input[index]));
        view.setInt16(
            index * 2,
            sample < 0 ? sample * 0x8000 : sample * 0x7fff,
            true
        );
    }
    return buffer;
}

export function isValidPcm16Chunk(buffer) {
    return buffer instanceof ArrayBuffer
        && buffer.byteLength > 0
        && buffer.byteLength % 2 === 0;
}

export function chunkPcm16(buffer, sampleRate = 16000, chunkMs = 20) {
    if (!isValidPcm16Chunk(buffer)) return [];
    const samplesPerChunk = Math.max(
        1,
        Math.round(Math.max(1, Number(sampleRate) || 16000) * (chunkMs / 1000))
    );
    const bytesPerChunk = samplesPerChunk * 2;
    const chunks = [];
    for (let offset = 0; offset < buffer.byteLength; offset += bytesPerChunk) {
        chunks.push(buffer.slice(offset, Math.min(buffer.byteLength, offset + bytesPerChunk)));
    }
    return chunks;
}

export class LivePcmChunker {
    constructor({ sourceRate, targetRate = 16000, chunkMs = 20 } = {}) {
        this.sourceRate = Math.max(1, Number(sourceRate) || targetRate);
        this.targetRate = Math.max(1, Number(targetRate) || 16000);
        this.chunkMs = Math.max(20, Math.min(40, Number(chunkMs) || 20));
        this.sourceSamplesPerChunk = Math.max(
            1,
            Math.round(this.sourceRate * (this.chunkMs / 1000))
        );
        this.pending = new Float32Array();
    }

    get pendingSamples() {
        return this.pending.length;
    }

    push(samples) {
        const incoming = samples instanceof Float32Array
            ? samples
            : new Float32Array(samples || []);
        if (incoming.length === 0) return [];

        const combined = new Float32Array(this.pending.length + incoming.length);
        combined.set(this.pending, 0);
        combined.set(incoming, this.pending.length);
        this.pending = combined;

        const chunks = [];
        while (this.pending.length >= this.sourceSamplesPerChunk) {
            const sourceChunk = this.pending.slice(0, this.sourceSamplesPerChunk);
            this.pending = this.pending.slice(this.sourceSamplesPerChunk);
            const resampled = resampleMono(
                sourceChunk,
                this.sourceRate,
                this.targetRate
            );
            chunks.push(floatToPcm16Le(resampled));
        }
        return chunks;
    }

    reset() {
        this.pending = new Float32Array();
    }
}

export class LivePcmCapture {
    constructor(options = {}) {
        this.window = options.windowRef || globalThis.window;
        this.targetRate = Math.max(1, Number(options.targetRate) || 16000);
        this.chunkMs = Math.max(20, Math.min(40, Number(options.chunkMs) || 20));
        this.workletUrl = options.workletUrl
            || '/static/js/worklets/live-pcm-processor.js';
        this.audioContext = null;
        this.mediaStream = null;
        this.sourceNode = null;
        this.captureNode = null;
        this.silentGain = null;
        this.chunker = null;
        this.mode = '';
        this.started = false;
    }

    async prime() {
        if (this.audioContext && this.audioContext.state !== 'closed') {
            if (this.audioContext.state === 'suspended') {
                await this.audioContext.resume();
            }
            return this.audioContext;
        }
        const AudioContextClass = this.window?.AudioContext
            || this.window?.webkitAudioContext;
        if (!AudioContextClass) throw new Error('live_audio_capture_unsupported');
        this.audioContext = new AudioContextClass();
        if (this.audioContext.state === 'suspended') {
            await this.audioContext.resume();
        }
        if (this.audioContext.audioWorklet?.addModule) {
            try {
                await this.audioContext.audioWorklet.addModule(this.workletUrl);
                this.mode = 'worklet';
            } catch {
                this.mode = 'script_processor';
            }
        } else {
            this.mode = 'script_processor';
        }
        return this.audioContext;
    }

    async start({ onChunk, onFrame, onDegraded } = {}) {
        if (this.started) return;
        const context = await this.prime();
        const mediaDevices = globalThis.navigator?.mediaDevices;
        if (!mediaDevices?.getUserMedia) {
            throw new Error('live_audio_capture_unsupported');
        }
        this.mediaStream = await mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            }
        });
        this.sourceNode = context.createMediaStreamSource(this.mediaStream);
        this.chunker = new LivePcmChunker({
            sourceRate: context.sampleRate,
            targetRate: this.targetRate,
            chunkMs: this.chunkMs,
        });
        const processFrame = frame => {
            const samples = frame instanceof Float32Array
                ? frame
                : new Float32Array(frame || []);
            onFrame?.(samples);
            for (const chunk of this.chunker.push(samples)) onChunk?.(chunk);
        };

        if (this.mode === 'worklet') {
            const WorkletNode = this.window?.AudioWorkletNode
                || globalThis.AudioWorkletNode;
            this.captureNode = new WorkletNode(
                context,
                'jarvis-live-pcm-processor'
            );
            this.captureNode.port.onmessage = event => processFrame(event.data);
        } else {
            onDegraded?.('live_audio_script_processor_fallback');
            this.captureNode = context.createScriptProcessor(2048, 1, 1);
            this.captureNode.onaudioprocess = event => {
                processFrame(event.inputBuffer.getChannelData(0));
            };
        }

        this.silentGain = context.createGain();
        this.silentGain.gain.value = 0;
        this.sourceNode.connect(this.captureNode);
        this.captureNode.connect(this.silentGain);
        this.silentGain.connect(context.destination);
        this.started = true;
    }

    async stop() {
        this.started = false;
        if (this.captureNode) {
            try { this.captureNode.disconnect(); } catch {}
            if (this.captureNode.port) this.captureNode.port.onmessage = null;
            this.captureNode.onaudioprocess = null;
            this.captureNode = null;
        }
        if (this.sourceNode) {
            try { this.sourceNode.disconnect(); } catch {}
            this.sourceNode = null;
        }
        if (this.silentGain) {
            try { this.silentGain.disconnect(); } catch {}
            this.silentGain = null;
        }
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
        }
        this.chunker?.reset();
        this.chunker = null;
        if (this.audioContext && this.audioContext.state !== 'closed') {
            await this.audioContext.close();
        }
        this.audioContext = null;
        this.mode = '';
    }
}
