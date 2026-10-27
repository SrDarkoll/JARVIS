/**
 * Capa de comunicación con la API de JARVIS.
 */

export const API = {
    async fetchSessionProfile() {
        const res = await fetch('/api/session/profile', { cache: 'no-store' });
        if (!res.ok) throw new Error('api_session_profile_fail');
        return await res.json();
    },

    async postChat(message, profileId = 'web_default') {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message, profile_id: profileId })
        });
        const type = String(res.headers.get('content-type') || '').toLowerCase();
        if (!type.includes('application/json')) throw new Error('api_chat_invalid_response');
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'api_chat_fail');
        return data;
    },

    async fetchStatus() {
        const res = await fetch('/api/status', { cache: 'no-store' });
        if (!res.ok) throw new Error('api_status_fail');
        return await res.json();
    },

    async runQuickAction(action) {
        const res = await fetch('/api/control/quick', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || data.result || 'action_fail');
        return data;
    },

    async fetchNews({ automatic = false } = {}) {
        const res = await fetch(automatic ? '/api/noticias?automatic=1' : '/api/noticias', { cache: 'no-store' });
        if (!res.ok) throw new Error('api_noticias_fail');
        return await res.json();
    },

    async fetchObservability(limit = 30) {
        const res = await fetch(`/api/observabilidad?limit=${Math.max(10, Math.min(Number(limit) || 30, 120))}`, { cache: 'no-store' });
        if (!res.ok) throw new Error('api_observabilidad_fail');
        return await res.json();
    },

    async fetchSetupStatus() {
        const res = await fetch('/api/setup/status', { cache: 'no-store' });
        if (!res.ok) throw new Error('api_setup_status_fail');
        return await res.json();
    },

    async fetchLiveVoiceStatus() {
        const res = await fetch('/api/voice/live/status', { cache: 'no-store' });
        if (!res.ok) throw new Error('api_live_voice_status_fail');
        return await res.json();
    },

    async configureProvider(provider, apiKey, saveLocal = true) {
        const res = await fetch('/api/setup/provider', {
            method: 'POST',
            cache: 'no-store',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                provider,
                api_key: apiKey,
                save_local: saveLocal,
            }),
        });
        const type = String(res.headers.get('content-type') || '').toLowerCase();
        if (!type.includes('application/json')) throw new Error('setup_failed');
        const data = await res.json();
        if (!res.ok || data.ok === false) throw new Error(data.error || 'setup_failed');
        return data;
    },

    async claimDueReminders(profileId = 'web_default') {
        const res = await fetch('/api/reminders/claim', {
            method: 'POST',
            cache: 'no-store',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_id: profileId }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'api_reminder_claim_fail');
        return data;
    },

    async acknowledgeReminder(reminderId, profileId = 'web_default') {
        const id = encodeURIComponent(String(reminderId || '').trim());
        if (!id) throw new Error('invalid_reminder_id');
        const res = await fetch(`/api/reminders/${id}/ack`, {
            method: 'POST',
            cache: 'no-store',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_id: profileId }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'api_reminder_ack_fail');
        return data;
    },

    async fetchProfiles() {
        const res = await fetch('/api/perfiles', { cache: 'no-store' });
        if (!res.ok) throw new Error('api_profiles_fail');
        return await res.json();
    },

    async fetchProfileDetail(profileId) {
        const pid = encodeURIComponent(profileId || '');
        const res = await fetch(`/api/perfiles/${pid}`, { cache: 'no-store' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'api_profile_detail_fail');
        return data;
    },

    async updateProfileFacts(profileId, facts) {
        const pid = encodeURIComponent(profileId || '');
        const res = await fetch(`/api/perfiles/${pid}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ facts })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'api_profile_update_fail');
        return data;
    },

    async clearProfileMemory(profileId) {
        const pid = encodeURIComponent(profileId || '');
        const res = await fetch(`/api/perfiles/${pid}`, { method: 'DELETE' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'api_profile_clear_fail');
        return data;
    },

    async startAdminEnrollment(profileId = '') {
        const res = await fetch('/api/voice/registro/admin/iniciar', {
            method: 'POST',
            headers: { 'X-Profile-Id': profileId || '' }
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.ok === false) throw new Error(data.error || 'admin_enrollment_start_fail');
        return data;
    },

    async postVoiceSample(sampleBlob, profileId = '') {
        const res = await fetch('/api/voice/registro/admin/capturar', {
            method: 'POST',
            headers: { 'X-Profile-Id': profileId || '' },
            body: sampleBlob
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.ok === false) throw new Error(data.error || 'admin_enrollment_capture_fail');
        return data;
    }
};
