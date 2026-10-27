/**
 * Mobile device synchronization client for the private JARVIS PWA.
 *
 * Security boundaries:
 * - only the scoped device token is stored, never the master API token;
 * - the long-lived token is sent only in X-JARVIS-DEVICE-TOKEN over HTTPS;
 * - WebSockets use one-use, short-lived, path-scoped tickets;
 * - events are deduplicated and gaps trigger a canonical snapshot refresh;
 * - commands are never queued while offline.
 */

export const DEVICE_TOKEN_KEY = 'jarvis_device_token';
export const DEVICE_NAME_KEY = 'jarvis_device_name';
export const SYNC_EVENT_TYPES = Object.freeze([
    'chat_message',
    'system_state',
    'media_state',
    'device_connected',
    'device_disconnected',
    'remote_alert',
    'action_plan',
]);

const MAX_COMMAND_LENGTH = 4000;
const MAX_SEEN_EVENTS = 800;

function asObject(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}

export function normalizeCommandText(value, maxLength = MAX_COMMAND_LENGTH) {
    const limit = Math.max(1, Number(maxLength) || MAX_COMMAND_LENGTH);
    return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, limit);
}

export function normalizeDeviceEvent(raw) {
    const data = asObject(raw);
    if (!data) return null;
    const eventId = String(data.event_id ?? '').trim();
    const eventType = String(data.event_type ?? '').trim();
    const sequence = Number(data.sequence);
    const payload = asObject(data.payload);
    if (!eventId || !SYNC_EVENT_TYPES.includes(eventType)) return null;
    if (!Number.isSafeInteger(sequence) || sequence < 1) return null;
    if (!payload) return null;
    return {
        eventId,
        eventType,
        payload,
        profileId: String(data.profile_id ?? '').trim(),
        sequence,
        timestamp: Number(data.timestamp) || 0,
        originDeviceId: String(data.origin_device_id ?? '').trim(),
        targetDeviceId: data.target_device_id ? String(data.target_device_id) : null,
    };
}

export function shouldApplyDeviceEvent(eventId, seen) {
    const id = String(eventId ?? '').trim();
    if (!id) return false;
    if (!(seen instanceof Set)) return true;
    if (seen.has(id)) return false;
    seen.add(id);
    if (seen.size > MAX_SEEN_EVENTS) {
        const oldest = seen.values().next().value;
        if (oldest !== undefined) seen.delete(oldest);
    }
    return true;
}

export function reconnectDelay(attempt, { baseMs = 500, maxMs = 15000 } = {}) {
    const base = Math.max(100, Number(baseMs) || 500);
    const cap = Math.max(base, Number(maxMs) || 15000);
    const step = Math.max(1, Math.min(Number(attempt) || 1, 12));
    const exponential = Math.min(cap, base * (2 ** (step - 1)));
    const jitter = Math.floor(Math.random() * Math.min(400, Math.max(50, exponential * 0.2)));
    return Math.min(cap, exponential + jitter);
}

export function buildSyncSocketUrl(locationRef, ticket) {
    const location = locationRef || globalThis.location || { protocol: 'http:', host: 'localhost' };
    const protocol = String(location.protocol) === 'https:' ? 'wss:' : 'ws:';
    const params = new URLSearchParams();
    const value = String(ticket ?? '').trim();
    if (value) params.set('ws_ticket', value);
    const query = params.toString();
    return `${protocol}//${location.host}/api/sync/ws${query ? `?${query}` : ''}`;
}

export function createRequestId() {
    const value = globalThis.crypto;
    if (value && typeof value.randomUUID === 'function') return value.randomUUID();
    return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function readStoredSession(storage = globalThis.localStorage) {
    try {
        const token = String(storage?.getItem?.(DEVICE_TOKEN_KEY) || '').trim();
        const name = String(storage?.getItem?.(DEVICE_NAME_KEY) || '').trim();
        return { token, name };
    } catch {
        return { token: '', name: '' };
    }
}

export function storeSession(token, name, storage = globalThis.localStorage) {
    try {
        if (token) storage?.setItem?.(DEVICE_TOKEN_KEY, String(token));
        if (name) storage?.setItem?.(DEVICE_NAME_KEY, String(name));
        return true;
    } catch {
        return false;
    }
}

export function clearSession(storage = globalThis.localStorage) {
    try {
        storage?.removeItem?.(DEVICE_TOKEN_KEY);
        storage?.removeItem?.(DEVICE_NAME_KEY);
        return true;
    } catch {
        return false;
    }
}

export class DeviceSyncError extends Error {
    constructor(message, { status = 0, code = '', data = null } = {}) {
        super(message);
        this.name = 'DeviceSyncError';
        this.status = Number(status) || 0;
        this.code = String(code || '');
        this.data = data;
    }
}

async function readResponse(response) {
    const contentType = String(response?.headers?.get?.('content-type') || '').toLowerCase();
    if (contentType.includes('application/json')) {
        try {
            return await response.json();
        } catch {
            return {};
        }
    }
    return {};
}

export class DeviceSyncClient {
    constructor({
        token = '',
        fetchImpl = globalThis.fetch?.bind(globalThis),
        WebSocketImpl = globalThis.WebSocket,
        locationRef = globalThis.location,
        onEvent = null,
        onSnapshot = null,
        onStatus = null,
        onError = null,
        onAuthError = null,
        onRevoked = null,
        onResync = null,
    } = {}) {
        this.token = String(token || '').trim();
        this.fetchImpl = fetchImpl;
        this.WebSocketImpl = WebSocketImpl;
        this.locationRef = locationRef;
        this.onEvent = onEvent;
        this.onSnapshot = onSnapshot;
        this.onStatus = onStatus;
        this.onError = onError;
        this.onAuthError = onAuthError;
        this.onRevoked = onRevoked;
        this.onResync = onResync;
        this.ws = null;
        this.deviceId = '';
        this.profileId = '';
        this.lastSequence = 0;
        this.seenEvents = new Set();
        this._started = false;
        this._manualClose = false;
        this._connecting = false;
        this._reconnectAttempt = 0;
        this._reconnectTimer = null;
        this._heartbeatTimer = null;
        this._resyncing = false;
    }

    _headers(extra = {}) {
        const headers = { Accept: 'application/json', ...extra };
        if (this.token) headers['X-JARVIS-DEVICE-TOKEN'] = this.token;
        return headers;
    }

    async _request(path, { method = 'GET', body, headers = {} } = {}) {
        if (typeof this.fetchImpl !== 'function') {
            throw new DeviceSyncError('fetch_unavailable', { code: 'fetch_unavailable' });
        }
        const init = {
            method,
            headers: this._headers(headers),
            cache: 'no-store',
            credentials: 'same-origin',
        };
        if (body !== undefined) {
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(body);
        }
        let response;
        try {
            response = await this.fetchImpl(path, init);
        } catch {
            throw new DeviceSyncError('network_unavailable', { code: 'network_unavailable' });
        }
        const data = await readResponse(response);
        if (!response.ok) {
            const code = String(data.error || data.message || `http_${response.status}`);
            const error = new DeviceSyncError(code, {
                status: response.status,
                code,
                data,
            });
            if (response.status === 401) this.onAuthError?.(error);
            throw error;
        }
        return data;
    }

    status() {
        return this._request('/api/sync/status');
    }

    snapshot() {
        return this._request('/api/sync/snapshot');
    }

    history(afterSequence = 0, limit = 50) {
        const cursor = Math.max(0, Number(afterSequence) || 0);
        const size = Math.max(1, Math.min(100, Number(limit) || 50));
        return this._request(`/api/sync/history?after_sequence=${cursor}&limit=${size}`);
    }

    dispatchCommand(message, clientRequestId = createRequestId()) {
        const text = normalizeCommandText(message);
        if (!text) return Promise.reject(new DeviceSyncError('message_required', { code: 'message_required' }));
        return this._request('/api/sync/dispatch', {
            method: 'POST',
            body: { message: text, client_request_id: clientRequestId },
        }).then(data => ({ ...data, client_request_id: clientRequestId }));
    }

    issueTicket(purpose = 'sync') {
        return this._request('/api/sync/ws-ticket', {
            method: 'POST',
            body: { purpose },
        }).then(data => String(data.ticket || ''));
    }

    revokeSelf() {
        return this._request('/api/sync/devices/self/revoke', { method: 'POST', body: {} });
    }

    _emitStatus(status, detail = {}) {
        try {
            this.onStatus?.({ status, ...detail });
        } catch {
            // UI callbacks must never break the synchronization loop.
        }
    }

    async start() {
        if (this._started) return true;
        if (!this.token) throw new DeviceSyncError('device_auth_required', { status: 401, code: 'device_auth_required' });
        this._started = true;
        this._manualClose = false;
        this._emitStatus('connecting');
        const status = await this.status();
        this.deviceId = String(status.current_device?.device_id || this.deviceId || '');
        this.profileId = String(status.current_device?.profile_id || this.profileId || '');
        await this.refreshSnapshot();
        this._emitStatus('online');
        await this._openSocket();
        return true;
    }

    async refreshSnapshot() {
        const data = await this.snapshot();
        const messages = Array.isArray(data.messages) ? data.messages : [];
        this.lastSequence = Math.max(0, Number(data.latest_sequence) || 0);
        this.seenEvents.clear();
        this.profileId = String(data.profile_id || this.profileId || '');
        try {
            this.onSnapshot?.(messages, data);
        } catch {
            // Isolate UI failures from the sync loop.
        }
    }

    async _openSocket() {
        if (this._connecting || this._manualClose || !this._started) return;
        this._connecting = true;
        this._emitStatus(this._reconnectAttempt > 0 ? 'reconnecting' : 'connecting');
        try {
            const ticket = await this.issueTicket('sync');
            if (!ticket) throw new DeviceSyncError('websocket_ticket_unavailable', { code: 'websocket_ticket_unavailable' });
            const socket = new this.WebSocketImpl(buildSyncSocketUrl(this.locationRef, ticket));
            this.ws = socket;
            socket.onmessage = event => this._handleSocketMessage(event);
            socket.onopen = () => {
                if (this.ws !== socket) return;
                this._connecting = false;
                this._emitStatus('online');
                this._startHeartbeat();
            };
            socket.onerror = () => {
                if (this.ws !== socket) return;
                this._emitStatus('reconnecting', { reason: 'socket_error' });
            };
            socket.onclose = event => {
                if (this.ws !== socket) return;
                this.ws = null;
                this._connecting = false;
                this._stopHeartbeat();
                if (this._manualClose || !this._started) return;
                this._emitStatus('reconnecting', { code: event?.code || 0, reason: 'socket_closed' });
                this._scheduleReconnect();
            };
        } catch (error) {
            this._connecting = false;
            if (!this._started || this._manualClose) return;
            if (error?.status === 401) {
                this._manualClose = true;
                this.onAuthError?.(error);
                return;
            }
            this._emitStatus('reconnecting', { reason: error?.code || 'ticket_failed' });
            this._scheduleReconnect();
        }
    }

    _handleSocketMessage(event) {
        let data;
        try {
            data = JSON.parse(String(event?.data ?? ''));
        } catch {
            return;
        }
        if (!data || typeof data !== 'object') return;
        if (data.type === 'pong') return;
        if (data.event_type === 'handshake') {
            this.deviceId = String(data.device_id || this.deviceId || '');
            this.profileId = String(data.profile_id || this.profileId || '');
            this.lastSequence = Math.max(this.lastSequence, Number(data.latest_sequence) || 0);
            this._reconnectAttempt = 0;
            this._connecting = false;
            this._emitStatus('online');
            this._startHeartbeat();
            return;
        }
        const normalized = normalizeDeviceEvent(data);
        if (!normalized) return;
        if (this.profileId && normalized.profileId && normalized.profileId !== this.profileId) return;
        if (!shouldApplyDeviceEvent(normalized.eventId, this.seenEvents)) return;
        if (normalized.eventType === 'device_disconnected'
            && normalized.targetDeviceId
            && normalized.targetDeviceId === this.deviceId) {
            this._manualClose = true;
            this._started = false;
            this._stopHeartbeat();
            try { this.ws?.close?.(); } catch { /* already closed */ }
            this.onRevoked?.(normalized);
            this._emitStatus('revoked');
            return;
        }
        if (normalized.sequence > this.lastSequence + 1) {
            this.lastSequence = Math.max(this.lastSequence, normalized.sequence);
            this._resync();
            return;
        }
        this.lastSequence = Math.max(this.lastSequence, normalized.sequence);
        try {
            this.onEvent?.(normalized);
        } catch {
            // Isolate UI failures from the sync loop.
        }
    }

    async _resync() {
        if (this._resyncing) return;
        this._resyncing = true;
        this._emitStatus('reconnecting', { reason: 'sequence_gap' });
        try {
            await this.refreshSnapshot();
            this._emitStatus('online');
            try { this.onResync?.(); } catch { /* UI callback */ }
        } catch (error) {
            if (error?.status === 401) {
                this.onAuthError?.(error);
                return;
            }
            this._emitStatus('reconnecting', { reason: 'resync_failed' });
        } finally {
            this._resyncing = false;
        }
    }

    _startHeartbeat() {
        this._stopHeartbeat();
        this._heartbeatTimer = setInterval(() => {
            if (!this.ws || this.ws.readyState !== 1) return;
            try {
                this.ws.send(JSON.stringify({ type: 'ping' }));
            } catch {
                // The close handler owns reconnection.
            }
        }, 25000);
    }

    _stopHeartbeat() {
        if (this._heartbeatTimer !== null) {
            clearInterval(this._heartbeatTimer);
            this._heartbeatTimer = null;
        }
    }

    _scheduleReconnect() {
        if (this._manualClose || !this._started || this._reconnectTimer !== null) return;
        this._reconnectAttempt += 1;
        const delay = reconnectDelay(this._reconnectAttempt);
        this._emitStatus('offline', { retryInMs: delay });
        this._reconnectTimer = setTimeout(() => {
            this._reconnectTimer = null;
            if (!this._manualClose && this._started) this._openSocket();
        }, delay);
    }

    stop() {
        this._started = false;
        this._manualClose = true;
        this._connecting = false;
        this._stopHeartbeat();
        if (this._reconnectTimer !== null) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        const socket = this.ws;
        this.ws = null;
        try { socket?.close?.(1000, 'client_stop'); } catch { /* already closed */ }
        this._emitStatus('offline');
    }
}

export async function claimPairing({
    pin,
    deviceName = 'Móvil',
    deviceType = 'mobile_pwa',
    fetchImpl = globalThis.fetch?.bind(globalThis),
} = {}) {
    const normalizedPin = String(pin || '').trim().toUpperCase();
    if (!/^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}$/.test(normalizedPin)) {
        throw new DeviceSyncError('invalid_pin', { code: 'invalid_pin' });
    }
    if (typeof fetchImpl !== 'function') {
        throw new DeviceSyncError('fetch_unavailable', { code: 'fetch_unavailable' });
    }
    let response;
    try {
        response = await fetchImpl('/api/sync/pair/claim', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            credentials: 'same-origin',
            cache: 'no-store',
            body: JSON.stringify({
                pin: normalizedPin,
                device_name: String(deviceName || 'Móvil').trim().slice(0, 64) || 'Móvil',
                device_type: deviceType === 'tablet' ? 'tablet' : 'mobile_pwa',
            }),
        });
    } catch {
        throw new DeviceSyncError('network_unavailable', { code: 'network_unavailable' });
    }
    const data = await readResponse(response);
    if (!response.ok) {
        throw new DeviceSyncError(String(data.message || data.error || `http_${response.status}`), {
            status: response.status,
            code: String(data.message || data.error || ''),
            data,
        });
    }
    if (!data.device_token) {
        throw new DeviceSyncError('pairing_response_invalid', { code: 'pairing_response_invalid' });
    }
    return data;
}
