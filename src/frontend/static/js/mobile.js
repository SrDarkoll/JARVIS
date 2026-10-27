/**
 * Mobile PWA controller for JARVIS synchronization and Live voice.
 *
 * Text is always rendered with textContent. The page never injects assistant
 * or peer content as HTML, and the service worker never caches API responses.
 */

import {
    DeviceSyncClient,
    claimPairing,
    clearSession,
    createRequestId,
    normalizeCommandText,
    readStoredSession,
    storeSession,
} from './modules/device-sync.js';
import { LiveVoiceClient } from './modules/live-voice.js';

const LANGUAGE_KEY = 'jarvis_lang';
const STRINGS = {
    es: {
        app_title: 'J.A.R.V.I.S. Móvil',
        brand_mobile: 'Móvil',
        pairing_title: 'Vincular este dispositivo',
        pairing_intro: 'Escanea el QR del panel de escritorio o introduce el PIN de un solo uso.',
        pin_label: 'PIN de emparejamiento',
        name_label: 'Nombre del dispositivo',
        pair_button: 'Vincular dispositivo',
        pairing_hint: 'El PIN caduca en 10 minutos y solo se puede usar una vez.',
        invalid_pin: 'El PIN debe tener 6 caracteres válidos.',
        pairing_failed: 'No se pudo completar el emparejamiento.',
        pairing_origin: 'Abre este enlace desde el host HTTPS remoto configurado.',
        pairing_expired: 'El PIN no es válido o ya caducó. Genera uno nuevo.',
        connecting: 'Conectando…',
        online: 'En línea',
        offline: 'Sin conexión',
        reconnecting: 'Reconectando…',
        revoked: 'Dispositivo desvinculado',
        auth_lost: 'La sesión del dispositivo ya no es válida. Vuelve a vincularlo.',
        empty_conversation: 'Aún no hay mensajes. Escribe o habla para empezar.',
        message_placeholder: 'Escribe un comando…',
        send: 'Enviar',
        voice: 'Voz',
        voice_stop: 'Detener voz',
        voice_active: 'Escuchando…',
        voice_error: 'La voz no está disponible en este momento.',
        send_error: 'No se pudo enviar el comando. Inténtalo de nuevo.',
        connection_error: 'No se pudo conectar con JARVIS.',
        device_label: 'Dispositivo',
        revoke: 'Desvincular',
        revoke_confirm: '¿Desvincular este dispositivo de JARVIS?',
        revoked_hint: 'Este dispositivo ya no tiene acceso. Genera un PIN nuevo en el escritorio.',
        voice_hint: 'La voz usa una sesión Live temporal y no envía audio al historial.',
        install: 'Instalar',
        role_user: 'Tú',
        role_assistant: 'JARVIS',
        role_system: 'Sistema',
        retry_in: 'Reintentando…',
        pair_success: 'Dispositivo vinculado.',
        ws_ticket_failed: 'No se pudo abrir el canal en tiempo real.',
        uncertain: 'El resultado es incierto; no se reenvió automáticamente.',
    },
    en: {
        app_title: 'J.A.R.V.I.S. Mobile',
        brand_mobile: 'Mobile',
        pairing_title: 'Pair this device',
        pairing_intro: 'Scan the QR from the desktop panel or enter the one-use PIN.',
        pin_label: 'Pairing PIN',
        name_label: 'Device name',
        pair_button: 'Pair device',
        pairing_hint: 'The PIN expires after 10 minutes and can be used once.',
        invalid_pin: 'The PIN must contain 6 valid characters.',
        pairing_failed: 'Pairing could not be completed.',
        pairing_origin: 'Open this link from the configured remote HTTPS host.',
        pairing_expired: 'The PIN is invalid or expired. Generate a new one.',
        connecting: 'Connecting…',
        online: 'Online',
        offline: 'Offline',
        reconnecting: 'Reconnecting…',
        revoked: 'Device unlinked',
        auth_lost: 'This device session is no longer valid. Pair it again.',
        empty_conversation: 'No messages yet. Type or speak to start.',
        message_placeholder: 'Type a command…',
        send: 'Send',
        voice: 'Voice',
        voice_stop: 'Stop voice',
        voice_active: 'Listening…',
        voice_error: 'Voice is unavailable right now.',
        send_error: 'The command could not be sent. Try again.',
        connection_error: 'Could not connect to JARVIS.',
        device_label: 'Device',
        revoke: 'Unlink',
        revoke_confirm: 'Unlink this device from JARVIS?',
        revoked_hint: 'This device no longer has access. Generate a new PIN on the desktop.',
        voice_hint: 'Voice uses a temporary Live session and does not send audio to history.',
        install: 'Install',
        role_user: 'You',
        role_assistant: 'JARVIS',
        role_system: 'System',
        retry_in: 'Retrying…',
        pair_success: 'Device paired.',
        ws_ticket_failed: 'The real-time channel could not be opened.',
        uncertain: 'The result is uncertain; it was not retried automatically.',
    },
};

function detectLanguage() {
    try {
        const stored = String(localStorage.getItem(LANGUAGE_KEY) || '').toLowerCase();
        if (stored === 'es' || stored === 'en') return stored;
    } catch {
        // Storage may be unavailable in private mode.
    }
    return String(navigator.language || '').toLowerCase().startsWith('en') ? 'en' : 'es';
}

let currentLanguage = detectLanguage();
let t = key => STRINGS[currentLanguage][key] || STRINGS.en[key] || key;

const dom = {
    pairingView: document.getElementById('pairing-view'),
    appView: document.getElementById('app-view'),
    pairForm: document.getElementById('pair-form'),
    pin: document.getElementById('pair-pin'),
    deviceName: document.getElementById('device-name'),
    pairButton: document.getElementById('pair-button'),
    pairStatus: document.getElementById('pair-status'),
    connection: document.getElementById('connection'),
    connectionText: document.getElementById('connection-text'),
    conversation: document.getElementById('conversation'),
    emptyState: document.getElementById('empty-state'),
    composer: document.getElementById('composer'),
    input: document.getElementById('message-input'),
    sendButton: document.getElementById('send-button'),
    voiceButton: document.getElementById('voice-button'),
    revokeButton: document.getElementById('revoke-button'),
    deviceLabel: document.getElementById('device-label'),
    installButton: document.getElementById('install-button'),
};

const state = {
    token: '',
    deviceName: '',
    client: null,
    voiceClient: null,
    voiceActive: false,
    online: false,
    sending: false,
    renderedUserRequests: new Set(),
    renderedAssistantRequests: new Set(),
    installPrompt: null,
};

function applyLanguage() {
    currentLanguage = detectLanguage();
    t = key => STRINGS[currentLanguage][key] || STRINGS.en[key] || key;
    document.documentElement.lang = currentLanguage;
    document.title = t('app_title');
    document.querySelectorAll('[data-i18n]').forEach(element => {
        const key = element.getAttribute('data-i18n');
        if (key) element.textContent = t(key);
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(element => {
        const key = element.getAttribute('data-i18n-placeholder');
        if (key) element.setAttribute('placeholder', t(key));
    });
    document.querySelectorAll('[data-i18n-title]').forEach(element => {
        const key = element.getAttribute('data-i18n-title');
        if (key) element.setAttribute('title', t(key));
    });
    if (!state.online) setConnection('offline', t('offline'));
    updateVoiceButton();
    updateComposerState();
    if (dom.conversation && !dom.conversation.querySelector('.message')) {
        showEmptyState();
    }
}

function setConnection(kind, text) {
    if (!dom.connection || !dom.connectionText) return;
    dom.connection.dataset.state = kind;
    dom.connectionText.textContent = text;
    dom.connection.setAttribute('aria-label', text);
}

function rememberRequest(set, requestId) {
    const value = String(requestId || '');
    if (!value) return;
    set.add(value);
    while (set.size > 500) {
        const oldest = set.values().next().value;
        if (oldest === undefined) break;
        set.delete(oldest);
    }
}

function setPairStatus(message, kind = '') {
    if (!dom.pairStatus) return;
    dom.pairStatus.textContent = message || '';
    dom.pairStatus.dataset.state = kind;
}

function showPairing(message = '', kind = '') {
    dom.appView?.classList.add('hidden');
    dom.pairingView?.classList.remove('hidden');
    setPairStatus(message, kind);
    setConnection('offline', t('offline'));
    state.online = false;
    updateComposerState();
    window.setTimeout(() => dom.pin?.focus?.(), 50);
}

function showApp() {
    dom.pairingView?.classList.add('hidden');
    dom.appView?.classList.remove('hidden');
    if (dom.deviceLabel) dom.deviceLabel.textContent = `${t('device_label')}: ${state.deviceName || 'JARVIS Mobile'}`;
    updateComposerState();
}

function showEmptyState() {
    if (!dom.conversation) return;
    dom.conversation.replaceChildren();
    if (dom.emptyState) dom.conversation.appendChild(dom.emptyState);
    dom.emptyState?.classList.remove('hidden');
}

function appendMessage(role, text, { requestId = '', voice = false } = {}) {
    const content = String(text ?? '').trim();
    if (!content || !dom.conversation) return;
    dom.emptyState?.classList.add('hidden');
    const safeRole = role === 'user' || role === 'assistant' ? role : 'system';
    const article = document.createElement('article');
    article.className = `message ${safeRole}${voice ? ' voice' : ''}`;
    article.dataset.role = safeRole;
    if (requestId) article.dataset.requestId = requestId;
    const label = document.createElement('div');
    label.className = 'message-label';
    label.textContent = t(`role_${safeRole}`);
    const body = document.createElement('div');
    body.className = 'message-body';
    body.textContent = content;
    article.append(label, body);
    dom.conversation.appendChild(article);
    while (dom.conversation.querySelectorAll('.message').length > 120) {
        dom.conversation.querySelector('.message')?.remove();
    }
    dom.conversation.scrollTop = dom.conversation.scrollHeight;
}

function renderSnapshot(messages) {
    if (!dom.conversation) return;
    dom.conversation.replaceChildren();
    dom.emptyState?.classList.remove('hidden');
    for (const message of Array.isArray(messages) ? messages : []) {
        if (!message || typeof message !== 'object') continue;
        const role = message.role === 'user' ? 'user' : 'assistant';
        appendMessage(role, message.content);
    }
    if (dom.emptyState && !dom.conversation.querySelector('.message')) {
        dom.conversation.appendChild(dom.emptyState);
    }
    dom.conversation.scrollTop = dom.conversation.scrollHeight;
}

function updateComposerState() {
    const disabled = !state.online || state.sending;
    if (dom.input) dom.input.disabled = disabled;
    if (dom.sendButton) dom.sendButton.disabled = disabled;
    if (dom.voiceButton) dom.voiceButton.disabled = !state.online;
}

function updateVoiceButton() {
    if (!dom.voiceButton) return;
    dom.voiceButton.classList.toggle('active', state.voiceActive);
    dom.voiceButton.setAttribute('aria-pressed', String(state.voiceActive));
    const label = state.voiceActive ? t('voice_stop') : t('voice');
    dom.voiceButton.textContent = label;
    dom.voiceButton.setAttribute('aria-label', label);
}

function handleStatus({ status, retryInMs = 0 } = {}) {
    const kind = String(status || 'offline');
    state.online = kind === 'online';
    if (kind === 'online') setConnection('online', t('online'));
    else if (kind === 'connecting') setConnection('connecting', t('connecting'));
    else if (kind === 'reconnecting') setConnection('reconnecting', t('reconnecting'));
    else if (kind === 'revoked') setConnection('revoked', t('revoked'));
    else setConnection('offline', retryInMs ? `${t('offline')} · ${t('retry_in')}` : t('offline'));
    updateComposerState();
}

function handleAuthLost() {
    state.token = '';
    state.online = false;
    clearSession();
    state.client?.stop?.();
    state.client = null;
    stopVoice().catch(() => {});
    showPairing(t('auth_lost'), 'error');
}

function handleRevoked() {
    state.token = '';
    state.online = false;
    clearSession();
    state.client?.stop?.();
    state.client = null;
    stopVoice().catch(() => {});
    showPairing(t('revoked_hint'), 'error');
}

function handleEvent(event) {
    if (!event || event.eventType !== 'chat_message') return;
    const payload = event.payload || {};
    const role = payload.role === 'user' ? 'user' : 'assistant';
    const requestId = String(payload.client_request_id || '');
    if (role === 'user' && requestId && state.renderedUserRequests.has(requestId)) {
        return;
    }
    if (role === 'assistant' && requestId && state.renderedAssistantRequests.has(requestId)) {
        return;
    }
    appendMessage(role, payload.content, { requestId });
    if (requestId) {
        if (role === 'user') rememberRequest(state.renderedUserRequests, requestId);
        else rememberRequest(state.renderedAssistantRequests, requestId);
    }
}

async function startClient() {
    if (!state.token) {
        showPairing();
        return false;
    }
    state.client?.stop?.();
    const client = new DeviceSyncClient({
        token: state.token,
        onEvent: handleEvent,
        onSnapshot: renderSnapshot,
        onStatus: handleStatus,
        onAuthError: () => handleAuthLost(),
        onRevoked: () => handleRevoked(),
        onError: () => {},
        onResync: () => appendMessage('system', t('reconnecting')),
    });
    state.client = client;
    state.online = false;
    setConnection('connecting', t('connecting'));
    try {
        await client.start();
        state.online = true;
        showApp();
        return true;
    } catch (error) {
        state.client = null;
        if (error?.status === 401) {
            handleAuthLost();
        } else {
            showPairing(t('connection_error'), 'error');
        }
        return false;
    }
}

async function submitPairing(event) {
    event.preventDefault();
    if (!dom.pairForm || !dom.pin) return;
    const pin = String(dom.pin.value || '').trim().toUpperCase();
    const deviceName = String(dom.deviceName?.value || 'Móvil').trim().slice(0, 64) || 'Móvil';
    if (!/^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}$/.test(pin)) {
        setPairStatus(t('invalid_pin'), 'error');
        dom.pin.focus();
        return;
    }
    if (dom.pairButton) dom.pairButton.disabled = true;
    setPairStatus(t('connecting'));
    try {
        const data = await claimPairing({ pin, deviceName });
        state.token = String(data.device_token || '');
        state.deviceName = String(data.name || deviceName);
        storeSession(state.token, state.deviceName);
        setPairStatus(t('pair_success'), 'success');
        const started = await startClient();
        if (!started) {
            state.token = '';
            clearSession();
        }
    } catch (error) {
        const code = String(error?.code || '');
        if (error?.status === 403 && code === 'untrusted_pair_origin') {
            setPairStatus(t('pairing_origin'), 'error');
        } else if (error?.status === 401) {
            setPairStatus(t('pairing_expired'), 'error');
        } else {
            setPairStatus(t('pairing_failed'), 'error');
        }
    } finally {
        if (dom.pairButton) dom.pairButton.disabled = false;
    }
}

async function submitMessage(event) {
    event?.preventDefault?.();
    if (!state.client || !state.online || state.sending || !dom.input) return;
    const message = normalizeCommandText(dom.input.value);
    if (!message) return;
    const requestId = createRequestId();
    rememberRequest(state.renderedUserRequests, requestId);
    appendMessage('user', message, { requestId });
    dom.input.value = '';
    resizeInput();
    state.sending = true;
    updateComposerState();
    try {
        const response = await state.client.dispatchCommand(message, requestId);
        const responseRequestId = String(response.request_id || requestId);
        if (response.response && !state.renderedAssistantRequests.has(responseRequestId)) {
            appendMessage('assistant', response.response, { requestId: responseRequestId });
            rememberRequest(state.renderedAssistantRequests, responseRequestId);
        }
    } catch (error) {
        if (error?.status === 401) {
            handleAuthLost();
        } else {
            appendMessage('system', error?.code === 'remote_command_unavailable' ? t('uncertain') : t('send_error'));
        }
    } finally {
        state.sending = false;
        updateComposerState();
        dom.input?.focus?.();
    }
}

function resizeInput() {
    if (!dom.input) return;
    dom.input.style.height = 'auto';
    dom.input.style.height = `${Math.min(120, dom.input.scrollHeight)}px`;
}

async function toggleVoice() {
    if (state.voiceClient) {
        await stopVoice();
        return;
    }
    if (!state.client || !state.online) return;
    const client = new LiveVoiceClient({
        language: currentLanguage,
        mode: 'auto',
        websocketTicketProvider: async () => {
            const ticket = await state.client.issueTicket('voice');
            if (!ticket) throw new Error('live_websocket_ticket_unavailable');
            return ticket;
        },
    });
    state.voiceClient = client;
    client.onTranscript = data => {
        if (!data || data.is_final !== true) return;
        const text = String(data.text || '').trim();
        if (!text) return;
        appendMessage(data.role === 'assistant' ? 'assistant' : 'user', text, { voice: true });
    };
    client.onStateChange = voiceState => {
        if (voiceState === 'listening') setConnection('online', t('voice_active'));
        else if (voiceState === 'speaking') setConnection('online', t('voice_active'));
        else if (voiceState === 'idle') setConnection('online', t('online'));
    };
    client.onError = data => {
        appendMessage('system', t('voice_error'));
        stopVoice().catch(() => {});
        if (data?.code) console.warn('[MOBILE VOICE]', data.code);
    };
    client.onReconnecting = () => setConnection('reconnecting', t('reconnecting'));
    state.voiceActive = true;
    updateVoiceButton();
    try {
        await client.startStreaming();
    } catch {
        state.voiceClient = null;
        state.voiceActive = false;
        updateVoiceButton();
        appendMessage('system', t('voice_error'));
        try { await client.disconnect(); } catch { /* already closed */ }
    }
}

async function stopVoice() {
    const client = state.voiceClient;
    state.voiceClient = null;
    state.voiceActive = false;
    updateVoiceButton();
    if (client) {
        try { await client.disconnect(); } catch { /* already closed */ }
    }
    if (state.online) setConnection('online', t('online'));
}

async function revokeDevice() {
    if (!state.client) return;
    if (!window.confirm(t('revoke_confirm'))) return;
    try {
        await state.client.revokeSelf();
    } catch {
        // A revoked or expired token should still clear local access.
    }
    await stopVoice();
    state.client.stop();
    state.client = null;
    state.token = '';
    state.online = false;
    clearSession();
    showPairing(t('revoked_hint'), 'success');
}

function bindInstallPrompt() {
    window.addEventListener('beforeinstallprompt', event => {
        event.preventDefault();
        state.installPrompt = event;
        dom.installButton?.classList.remove('hidden');
    });
    dom.installButton?.addEventListener('click', async () => {
        const prompt = state.installPrompt;
        if (!prompt) return;
        state.installPrompt = null;
        dom.installButton.classList.add('hidden');
        try { await prompt.prompt(); } catch { /* user dismissed */ }
    });
}

async function init() {
    applyLanguage();
    bindInstallPrompt();
    const hash = String(window.location.hash || '');
    const pairMatch = hash.match(/(?:^#|&)pair=([A-Za-z0-9]{6})/);
    const stored = readStoredSession();
    state.token = stored.token;
    state.deviceName = stored.name || 'JARVIS Mobile';
    if (dom.deviceName) dom.deviceName.value = state.deviceName;

    if (pairMatch) {
        clearSession();
        state.token = '';
        if (dom.pin) dom.pin.value = pairMatch[1].toUpperCase();
        try {
            history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
        } catch {
            // Some embedded browsers restrict history replacement.
        }
        showPairing('');
        dom.pin?.focus?.();
    } else if (state.token) {
        await startClient();
    } else {
        showPairing('');
    }

    dom.pairForm?.addEventListener('submit', submitPairing);
    dom.composer?.addEventListener('submit', submitMessage);
    dom.input?.addEventListener('input', resizeInput);
    dom.input?.addEventListener('keydown', event => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            submitMessage(event);
        }
    });
    dom.voiceButton?.addEventListener('click', () => { toggleVoice().catch(() => {}); });
    dom.revokeButton?.addEventListener('click', () => { revokeDevice().catch(() => {}); });
    window.addEventListener('pagehide', () => {
        stopVoice().catch(() => {});
        state.client?.stop?.();
    });
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw.js').catch(() => {});
    }
}

init().catch(() => {
    showPairing(t('connection_error'), 'error');
});
