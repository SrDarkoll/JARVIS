class JarvisLivePcmProcessor extends AudioWorkletProcessor {
    process(inputs) {
        const channel = inputs?.[0]?.[0];
        if (channel?.length) {
            this.port.postMessage(new Float32Array(channel));
        }
        return true;
    }
}

registerProcessor('jarvis-live-pcm-processor', JarvisLivePcmProcessor);
