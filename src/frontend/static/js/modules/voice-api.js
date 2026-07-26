const REASONING_FAILURE_KEYS = Object.freeze({
    chat_unavailable: 'voice_reasoning_unavailable',
    llm_unconfigured: 'voice_reasoning_unconfigured',
});

export function classifyVoiceApiFailure(status, payload = {}) {
    if (Number(status) < 400) return null;
    const code = String(payload?.error || '').trim().toLowerCase();
    return REASONING_FAILURE_KEYS[code] || null;
}
