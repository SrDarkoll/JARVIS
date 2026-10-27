/**
 * Pure command center state, command normalization, and recovery view models.
 */

const MAX_COMMAND_LENGTH = 4000;

const STATE_KEYS = Object.freeze({
    idle: 'command_state_idle',
    listening: 'command_state_listening',
    transcribing: 'command_state_transcribing',
    processing: 'command_state_processing',
    confirmation: 'command_state_confirmation',
    success: 'command_state_success',
    error: 'command_state_error',
    offline: 'command_state_offline',
});

export function normalizeCommandText(value, maxLength = MAX_COMMAND_LENGTH) {
    const limit = Math.max(1, Number(maxLength) || MAX_COMMAND_LENGTH);
    return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, limit);
}

export function commandStateView(state, translate) {
    const normalized = Object.hasOwn(STATE_KEYS, state) ? state : 'idle';
    const key = STATE_KEYS[normalized];
    return {
        state: normalized,
        key,
        label: typeof translate === 'function' ? translate(key) : key,
        busy: normalized === 'transcribing' || normalized === 'processing',
    };
}

export function microphoneRecoveryView(errorKind, translate) {
    const supported = new Set([
        'permission_denied',
        'device_missing',
        'device_busy',
        'insecure_context',
        'capture_unsupported',
    ]);
    const kind = String(errorKind || '').trim();
    if (!supported.has(kind)) {
        return { hidden: true, key: '', message: '' };
    }
    const key = kind === 'permission_denied'
        ? 'voice_permission_recovery'
        : `voice_recovery_${kind}`;
    const message = typeof translate === 'function' ? translate(key) : key;
    return { hidden: false, key, message };
}

export function isSuccessfulCommandOutcome(outcome) {
    const normalized = String(outcome || '').trim().toLowerCase();
    return new Set([
        'responded',
        'accepted',
        'executed',
        'verified',
        'duplicate',
        'succeeded', // Compatibility with older backends.
    ]).has(normalized);
}

export function commandDoneText(event, accumulatedText = '') {
    if (event?.type !== 'done') return String(accumulatedText ?? '');
    for (const candidate of [event.text, event.response, accumulatedText]) {
        const text = String(candidate ?? '');
        if (text.trim()) return text;
    }
    return '';
}
