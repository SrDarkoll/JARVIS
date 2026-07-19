const MICROPHONE_PERMISSION_ERRORS = new Set([
    'not-allowed',
    'service-not-allowed',
    'audio-capture',
    'notallowederror',
    'notfounderror',
    'notreadableerror',
    'securityerror',
]);

export function isMicrophonePermissionError(errorType) {
    return MICROPHONE_PERMISSION_ERRORS.has(
        String(errorType || '').trim().toLowerCase()
    );
}

export function shouldRestartPassiveRecognition(
    currentMode,
    adminEnrollmentActive,
    microphonePermissionBlocked
) {
    return currentMode === 'passive'
        && !adminEnrollmentActive
        && !microphonePermissionBlocked;
}
