/**
 * Desktop-side mobile pairing dialog.
 * The QR SVG is inserted only as a same-origin data URL; device metadata is
 * rendered with textContent and never interpreted as HTML.
 */

import { t } from '../i18n.js';

async function readJson(response) {
    try {
        return await response.json();
    } catch {
        return {};
    }
}

function formatLastSeen(timestamp, online) {
    if (online) return t('mobile_pair_online');
    const seconds = Number(timestamp) || 0;
    if (!seconds) return t('mobile_pair_offline');
    try {
        return new Date(seconds * 1000).toLocaleString();
    } catch {
        return t('mobile_pair_offline');
    }
}

export function initMobilePairing({
    dialog,
    openButton,
    closeButtons = [],
    refreshButton,
    qrImage,
    qrWrap,
    pinValue,
    status,
    deviceList,
} = {}) {
    if (!dialog || !openButton) return;

    const setStatus = (message, kind = '') => {
        if (!status) return;
        status.textContent = message || '';
        status.dataset.state = kind;
    };

    const setOpen = open => {
        dialog.classList.toggle('hidden', !open);
        if (open) {
            try {
                if (typeof dialog.showModal === 'function') dialog.showModal();
                else dialog.setAttribute('open', '');
            } catch { /* already open */ }
        } else {
            try {
                if (typeof dialog.close === 'function') dialog.close();
                else dialog.removeAttribute('open');
            } catch { /* already closed */ }
        }
    };

    const renderDevices = devices => {
        if (!deviceList) return;
        deviceList.replaceChildren();
        if (!Array.isArray(devices) || devices.length === 0) {
            const empty = document.createElement('li');
            empty.className = 'mobile-device-empty';
            empty.textContent = t('mobile_pair_empty');
            deviceList.appendChild(empty);
            return;
        }
        for (const device of devices) {
            if (!device || typeof device !== 'object') continue;
            const item = document.createElement('li');
            item.className = 'mobile-device-item';
            const copy = document.createElement('div');
            const name = document.createElement('strong');
            name.textContent = String(device.name || 'JARVIS Mobile');
            const meta = document.createElement('small');
            const type = String(device.device_type || 'mobile_pwa');
            meta.textContent = `${type} · ${formatLastSeen(device.last_seen, device.is_online)}`;
            copy.append(name, meta);
            const revoke = document.createElement('button');
            revoke.type = 'button';
            revoke.className = 'onboarding-link-action';
            revoke.textContent = t('mobile_pair_revoke');
            revoke.addEventListener('click', async () => {
                if (!window.confirm(t('mobile_pair_revoke_confirm'))) return;
                revoke.disabled = true;
                try {
                    await fetch('/api/sync/devices/revoke', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        credentials: 'same-origin',
                        cache: 'no-store',
                        body: JSON.stringify({ device_id: String(device.device_id || '') }),
                    });
                } catch {
                    // Refresh below exposes the current server state.
                }
                await refreshDevices();
            });
            item.append(copy, revoke);
            deviceList.appendChild(item);
        }
    };

    const refreshDevices = async () => {
        try {
            const response = await fetch('/api/sync/devices', {
                credentials: 'same-origin',
                cache: 'no-store',
            });
            const data = await readJson(response);
            if (response.ok) renderDevices(data.devices);
        } catch {
            // The dialog remains usable for pairing even if listing fails.
        }
    };

    const generate = async () => {
        setStatus(t('mobile_pair_loading'));
        qrWrap?.classList.add('hidden');
        if (refreshButton) refreshButton.disabled = true;
        try {
            const response = await fetch('/api/sync/pair/init', {
                method: 'POST',
                credentials: 'same-origin',
                cache: 'no-store',
            });
            const data = await readJson(response);
            if (!response.ok) {
                if (data.code === 'remote_access_not_configured') {
                    setStatus(t('mobile_pair_config_required'), 'error');
                } else if (response.status === 403) {
                    setStatus(t('mobile_pair_forbidden'), 'error');
                } else {
                    setStatus(t('mobile_pair_error'), 'error');
                }
                return;
            }
            if (qrImage) {
                qrImage.src = String(data.qr_data_url || '');
                qrImage.alt = t('mobile_pair_qr_alt');
            }
            if (qrWrap) qrWrap.classList.remove('hidden');
            if (pinValue) pinValue.textContent = String(data.pin || '');
            const expires = Number(data.expires_at) || 0;
            setStatus(expires ? `${t('mobile_pair_expires')}: ${new Date(expires * 1000).toLocaleTimeString()}` : '');
            await refreshDevices();
        } catch {
            setStatus(t('mobile_pair_error'), 'error');
        } finally {
            if (refreshButton) refreshButton.disabled = false;
        }
    };

    const open = async () => {
        setOpen(true);
        setStatus(t('mobile_pair_loading'));
        await generate();
    };
    const close = () => setOpen(false);

    openButton.addEventListener('click', () => { open().catch(() => {}); });
    closeButtons.forEach(button => button?.addEventListener('click', close));
    refreshButton?.addEventListener('click', () => { generate().catch(() => {}); });
    dialog.addEventListener('cancel', event => {
        event.preventDefault();
        close();
    });
}
