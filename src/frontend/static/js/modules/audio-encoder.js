/**
 * Módulo de encodeo de audio de alta fidelidad para JARVIS.
 * Convierte Blobs de navegador (WebM/Opus) a WAV de 16kHz mono para biometría.
 */

function _writeWavStr(view, offset, str) {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
}

function _encodeWavFromBuffer(audioBuffer) {
    const numChannels = 1;
    const sampleRate = audioBuffer.sampleRate;
    const samples = audioBuffer.getChannelData(0);
    const numSamples = samples.length;
    const buffer = new ArrayBuffer(44 + numSamples * 2);
    const view = new DataView(buffer);

    _writeWavStr(view, 0, 'RIFF');
    view.setUint32(4, 36 + numSamples * 2, true);
    _writeWavStr(view, 8, 'WAVE');
    _writeWavStr(view, 12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);    // PCM
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * numChannels * 2, true);
    view.setUint16(32, numChannels * 2, true);
    view.setUint16(34, 16, true);   // 16-bit
    _writeWavStr(view, 36, 'data');
    view.setUint32(40, numSamples * 2, true);

    let offset = 44;
    for (let i = 0; i < numSamples; i++) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
        offset += 2;
    }
    return new Blob([buffer], { type: 'audio/wav' });
}

function _boostSpeechBuffer(audioBuffer) {
    try {
        const samples = audioBuffer.getChannelData(0);
        if (!samples || !samples.length) return audioBuffer;

        let peak = 0;
        let sumSq = 0;
        for (let i = 0; i < samples.length; i++) {
            const v = samples[i];
            const abs = Math.abs(v);
            if (abs > peak) peak = abs;
            sumSq += v * v;
        }

        const safePeak = Math.max(peak, 0.0001);
        const rms = Math.sqrt(sumSq / samples.length);
        const safeRms = Math.max(rms, 0.0001);

        let gain = 1.0;
        if (safePeak < 0.32) {
            gain = Math.max(gain, Math.min(4.5, 0.74 / safePeak));
        }
        if (safeRms < 0.045) {
            gain = Math.max(gain, Math.min(3.5, 0.085 / safeRms));
        }
        if (gain <= 1.05) return audioBuffer;

        for (let i = 0; i < samples.length; i++) {
            const boosted = samples[i] * gain;
            samples[i] = Math.tanh(boosted * 0.9);
        }
    } catch (e) {
        console.warn('[AUDIO ENCODER] No pude aplicar boost suave:', e);
    }
    return audioBuffer;
}

/**
 * Convierte un Blob de audio (cualquier formato soportado por el navegador) 
 * a un WAV de 16kHz mono.
 */
export async function convertBlobToWav(blob, audioCtx, options = {}) {
    try {
        if (!audioCtx) return null;
        const enhanceSpeech = Boolean(options?.enhanceSpeech);
        const arrayBuf = await blob.arrayBuffer();
        const decoded = await audioCtx.decodeAudioData(arrayBuf);
        
        const targetRate = 16000;
        const offlineLen = Math.ceil(decoded.duration * targetRate);
        if (offlineLen < 100) {
            console.warn('[AUDIO ENCODER] Audio demasiado corto para convertir');
            return null;
        }
        
        const offCtx = new OfflineAudioContext(1, offlineLen, targetRate);
        const src = offCtx.createBufferSource();
        src.buffer = decoded;
        src.connect(offCtx.destination);
        src.start();
        
        const renderedBase = await offCtx.startRendering();
        const rendered = enhanceSpeech ? _boostSpeechBuffer(renderedBase) : renderedBase;
        const wavBlob = _encodeWavFromBuffer(rendered);
        console.log(`[AUDIO ENCODER] Convertido: ${blob.size} bytes → ${wavBlob.size} bytes WAV (16kHz mono)`);
        return wavBlob;
    } catch (e) {
        console.error('[AUDIO ENCODER] Error convirtiendo a WAV:', e);
        return null;
    }
}
