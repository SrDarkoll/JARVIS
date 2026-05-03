/**
 * Módulo de Reconocimiento y Procesamiento de Voz.
 */

export class VoiceManager {
    constructor() {
        this.audioCtx = null;
        this.analyser = null;
        this.silenceDetectorInterval = null;
        this.userStartedSpeaking = false;
        this.activeTimeout = null;
        this.SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    }

    getGlobalAudioContext() {
        if (!this.audioCtx || this.audioCtx.state === 'closed') {
            this.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        return this.audioCtx;
    }

    stopSilenceDetector() {
        if (this.silenceDetectorInterval) {
            clearInterval(this.silenceDetectorInterval);
            this.silenceDetectorInterval = null;
        }
    }

    startSilenceDetector(stream, onSilence, options = {}) {
        this.stopSilenceDetector();
        this.userStartedSpeaking = false;
        try {
            const config = typeof options === 'number' ? { silenceMs: options } : (options || {});
            const ctx = this.getGlobalAudioContext();
            this.analyser = ctx.createAnalyser();
            this.analyser.fftSize = 1024;
            const source = ctx.createMediaStreamSource(stream);
            source.connect(this.analyser);
            const freqDataArray = new Uint8Array(this.analyser.frequencyBinCount);
            const timeDataArray = new Uint8Array(this.analyser.fftSize);

            let silenceStartTime = null;
            let speechStartTime = null;
            const FREQ_START_THRESHOLD = Number(config.freqStartThreshold ?? 3);
            const FREQ_CONTINUE_THRESHOLD = Number(config.freqContinueThreshold ?? 6);
            const RMS_START_THRESHOLD = Number(config.rmsStartThreshold ?? 0.0085);
            const RMS_CONTINUE_THRESHOLD = Number(config.rmsContinueThreshold ?? 0.013);
            const SILENCE_DURATION_MS = Number(config.silenceMs ?? 1300);
            const MIN_SPEECH_MS = Number(config.minSpeechMs ?? 220);
            const CHECK_INTERVAL_MS = Number(config.checkIntervalMs ?? 60);

            this.silenceDetectorInterval = setInterval(() => {
                this.analyser.getByteFrequencyData(freqDataArray);
                this.analyser.getByteTimeDomainData(timeDataArray);
                const avg = freqDataArray.reduce((a, b) => a + b, 0) / freqDataArray.length;

                let sumSq = 0;
                for (let i = 0; i < timeDataArray.length; i++) {
                    const normalized = (timeDataArray[i] - 128) / 128;
                    sumSq += normalized * normalized;
                }
                const rms = Math.sqrt(sumSq / timeDataArray.length);

                const freqThreshold = this.userStartedSpeaking ? FREQ_CONTINUE_THRESHOLD : FREQ_START_THRESHOLD;
                const rmsThreshold = this.userStartedSpeaking ? RMS_CONTINUE_THRESHOLD : RMS_START_THRESHOLD;
                const speechDetected = avg >= freqThreshold || rms >= rmsThreshold;

                if (speechDetected) {
                    if (!this.userStartedSpeaking) {
                        this.userStartedSpeaking = true;
                        speechStartTime = Date.now();
                    }
                    silenceStartTime = null;
                } else {
                    if (this.userStartedSpeaking) {
                        if (!silenceStartTime) silenceStartTime = Date.now();
                        const speechDuration = speechStartTime ? Date.now() - speechStartTime : 0;
                        const silenceDuration = Date.now() - silenceStartTime;
                        if (silenceDuration >= SILENCE_DURATION_MS && speechDuration >= MIN_SPEECH_MS) {
                            this.stopSilenceDetector();
                            onSilence();
                        }
                    }
                }
            }, CHECK_INTERVAL_MS);
        } catch (e) {
            console.warn('[SILENCE] AudioContext no disponible:', e);
        }
    }
}
