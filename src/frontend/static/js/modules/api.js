/**
 * Capa de comunicación con la API de JARVIS.
 */

export const API = {
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

    async fetchNews() {
        const res = await fetch('/api/noticias');
        if (!res.ok) throw new Error('api_noticias_fail');
        return await res.json();
    },

    async fetchObservability(limit = 30) {
        const res = await fetch(`/api/observabilidad?limit=${Math.max(10, Math.min(Number(limit) || 30, 120))}`, { cache: 'no-store' });
        if (!res.ok) throw new Error('api_observabilidad_fail');
        return await res.json();
    },

    async fetchOperatorStatus() {
        const res = await fetch('/api/operator/status', { cache: 'no-store' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'api_operator_status_fail');
        return data;
    },

    async fetchSetupStatus() {
        const res = await fetch('/api/setup/status', { cache: 'no-store' });
        if (!res.ok) throw new Error('api_setup_status_fail');
        return await res.json();
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
