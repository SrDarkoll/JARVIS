/**
 * Pure first-run onboarding state and payload contracts.
 * This module intentionally has no browser-storage or DOM access.
 */

export const ONBOARDING_VERSION = 2;
export const STORAGE_KEY = 'jarvis_onboarding_v2';

export const ONBOARDING_STAGES = Object.freeze({
    WELCOME: 'welcome',
    PROVIDER: 'provider',
    COMMAND: 'command',
    SUCCESS: 'success',
    DISMISSED: 'dismissed',
    HIDDEN: 'hidden',
});

export const ONBOARDING_STATUSES = Object.freeze({
    UNSEEN: 'unseen',
    ACTIVE: 'active',
    DISMISSED: 'dismissed',
    COMPLETED: 'completed',
});

const ALLOWED_PROVIDERS = new Set(['gemini', 'groq']);
const ALLOWED_STAGES = new Set(Object.values(ONBOARDING_STAGES));
const ALLOWED_STATUSES = new Set(Object.values(ONBOARDING_STATUSES));
const MIN_KEY_LENGTH = 16;
const MAX_KEY_LENGTH = 512;

function languageIsConfigured(setupStatus) {
    return setupStatus?.items?.language?.configured === true;
}

function runtimeLlmIsAvailable(setupStatus) {
    return String(setupStatus?.capabilities?.llm?.state || '').toLowerCase() === 'available';
}

export function normalizeOnboardingState(value) {
    if (!value || typeof value !== 'object' || Number(value.version) !== ONBOARDING_VERSION) {
        return null;
    }
    const status = String(value.status || '').toLowerCase();
    const stage = String(value.stage || '').toLowerCase();
    if (!ALLOWED_STATUSES.has(status) || !ALLOWED_STAGES.has(stage)) return null;
    return { version: ONBOARDING_VERSION, status, stage };
}

export function parseOnboardingState(serialized) {
    if (typeof serialized !== 'string' || !serialized.trim()) return null;
    try {
        return normalizeOnboardingState(JSON.parse(serialized));
    } catch {
        return null;
    }
}

export function resolveInitialStage(setupStatus, storedState) {
    const saved = normalizeOnboardingState(storedState);
    if (languageIsConfigured(setupStatus) && runtimeLlmIsAvailable(setupStatus)) {
        return ONBOARDING_STAGES.HIDDEN;
    }
    if (saved?.status === ONBOARDING_STATUSES.COMPLETED) {
        return ONBOARDING_STAGES.HIDDEN;
    }
    if (saved?.status === ONBOARDING_STATUSES.DISMISSED) {
        return ONBOARDING_STAGES.DISMISSED;
    }
    if (languageIsConfigured(setupStatus)) {
        return ONBOARDING_STAGES.PROVIDER;
    }
    return ONBOARDING_STAGES.WELCOME;
}

export function transitionOnboarding(stage, action) {
    const current = ALLOWED_STAGES.has(stage) ? stage : ONBOARDING_STAGES.WELCOME;
    const event = String(action || '').toLowerCase();
    if (event === 'skip') return ONBOARDING_STAGES.DISMISSED;
    if (event === 'replay') return ONBOARDING_STAGES.WELCOME;
    if (current === ONBOARDING_STAGES.WELCOME && event === 'continue') {
        return ONBOARDING_STAGES.PROVIDER;
    }
    if (current === ONBOARDING_STAGES.PROVIDER && event === 'back') {
        return ONBOARDING_STAGES.WELCOME;
    }
    if (current === ONBOARDING_STAGES.PROVIDER && event === 'configured') {
        return ONBOARDING_STAGES.COMMAND;
    }
    if (current === ONBOARDING_STAGES.COMMAND && event === 'back') {
        return ONBOARDING_STAGES.PROVIDER;
    }
    if (current === ONBOARDING_STAGES.COMMAND && event === 'command_succeeded') {
        return ONBOARDING_STAGES.SUCCESS;
    }
    return current;
}

export function persistableOnboardingState(status, stage) {
    const safeStatus = ALLOWED_STATUSES.has(status) ? status : ONBOARDING_STATUSES.UNSEEN;
    const safeStage = ALLOWED_STAGES.has(stage) ? stage : ONBOARDING_STAGES.WELCOME;
    return {
        version: ONBOARDING_VERSION,
        status: safeStatus,
        stage: safeStage,
    };
}

export function buildProviderPayload(provider, apiKey, saveLocal) {
    const name = String(provider || '').trim().toLowerCase();
    const secret = String(apiKey || '').trim();
    if (!ALLOWED_PROVIDERS.has(name)) throw new Error('invalid_provider');
    if (
        secret.length < MIN_KEY_LENGTH
        || secret.length > MAX_KEY_LENGTH
        || /[\r\n\0]/.test(secret)
        || !/^[\x20-\x7E]+$/.test(secret)
    ) {
        throw new Error('invalid_key_format');
    }
    if (saveLocal !== true) throw new Error('invalid_request');
    return { provider: name, api_key: secret, save_local: true };
}

export function firstCommandCompletes(outcome) {
    const normalized = String(outcome || '').trim().toLowerCase();
    return normalized === 'responded' || normalized === 'verified';
}
