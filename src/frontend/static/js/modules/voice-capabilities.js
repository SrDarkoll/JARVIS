const ERROR_KIND = new Map([
    ['not-allowed', 'permission_denied'],
    ['service-not-allowed', 'permission_denied'],
    ['notallowederror', 'permission_denied'],
    ['notfounderror', 'device_missing'],
    ['audio-capture', 'device_busy'],
    ['notreadableerror', 'device_busy'],
    ['securityerror', 'insecure_context'],
    ['network', 'recognition_network'],
]);

export function classifyVoiceError(errorType) {
    return ERROR_KIND.get(String(errorType || '').trim().toLowerCase()) || 'unknown';
}

export function detectVoiceCapabilities(scope = globalThis) {
    const mediaDevices = scope?.navigator?.mediaDevices;
    const hasGetUserMedia = typeof mediaDevices?.getUserMedia === 'function';
    const hasMediaRecorder = typeof scope?.MediaRecorder === 'function';
    const hasBrowserRecognition = typeof (
        scope?.SpeechRecognition || scope?.webkitSpeechRecognition
    ) === 'function';
    const secureContext = scope?.isSecureContext !== false;
    return {
        secureContext,
        hasGetUserMedia,
        hasMediaRecorder,
        hasBrowserRecognition,
        canCaptureAudio: secureContext && hasGetUserMedia && hasMediaRecorder,
    };
}
