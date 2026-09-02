/**
 * Módulo de gestión del HUD (Heads-Up Display) de JARVIS.
 * Maneja telemetría, reloj, clima y alertas proactivas.
 */

import { MemoryGraphRenderer } from './memory-graph.js';

export function classifyProactiveActionResult(httpResponse, payload = {}) {
    const httpOk = Boolean(httpResponse?.ok);
    const receipts = Array.isArray(payload?.receipts) ? payload.receipts : [];
    const text = String(
        payload?.response ?? payload?.message ?? payload?.text ?? ''
    ).trim();
    const normalized = text
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase();

    if (!httpOk || payload?.error) {
        return { state: 'error', detail: text || String(payload?.error || '') };
    }

    const blockedReceipt = receipts.find((receipt) => receipt?.status === 'blocked');
    const confirmationRequired = (
        blockedReceipt?.diagnostic_code === 'tool_confirmation_required'
        || /confirmacion|confirmation/.test(normalized)
    );
    if (confirmationRequired) {
        return { state: 'confirmation', detail: text };
    }

    if (blockedReceipt || receipts.some((receipt) => (
        ['failed', 'unavailable'].includes(String(receipt?.status || '').toLowerCase())
    ))) {
        return { state: 'error', detail: text };
    }

    const failureText = /access_denied|\berror\b|\bfailed\b|\bfallo\b|no se encontro|no se pudo|no pude|no disponible/.test(normalized);
    if (failureText) {
        return { state: 'error', detail: text };
    }

    const receiptSuccess = receipts.some((receipt) => (
        ['succeeded', 'duplicate'].includes(String(receipt?.status || '').toLowerCase())
    ));
    if (text || receiptSuccess || payload?.ok === true) {
        return { state: 'success', detail: text };
    }
    return { state: 'error', detail: '' };
}

export class HUDManager {
    constructor(elements) {
        this.elements = elements;
        this.locale = localStorage.getItem('jarvis_locale') || 'en-US';
        this.memoryGraph = null;
        if (this.elements?.memoryGraphCanvas) {
            this.memoryGraph = new MemoryGraphRenderer({
                canvas: this.elements.memoryGraphCanvas,
                countEl: this.elements.memoryNodeCount,
                infoEl: this.elements.memoryGraphInfo,
                refreshBtn: this.elements.refreshMemoryGraph,
            });
        }
    }

    refreshMemoryGraph() {
        return this.memoryGraph?.refresh();
    }

    setLocale(locale) {
        this.locale = locale;
        localStorage.setItem('jarvis_locale', locale);
        this.updateTime(); // Immediately refresh display
    }

    updateTime() {
        const now = new Date();
        if (this.elements.clock) {
            this.elements.clock.textContent = now.toLocaleTimeString(this.locale, { hour12: false });
        }
        if (this.elements.date) {
            this.elements.date.textContent = now.toLocaleDateString(this.locale, { day: '2-digit', month: '2-digit', year: 'numeric' });
        }
    }

    tempToBarWidth(temp) {
        const t = Number(temp);
        if (!Number.isFinite(t)) return 0;
        return Math.min(100, Math.max(0, ((t - 28) / 62) * 100));
    }

    parseWeatherTemp(raw) {
        if (raw === undefined || raw === null) return null;
        const s = String(raw).trim();
        if (s === '' || s === '--') return null;
        const n = parseFloat(s.replace(/[^\d.-]/g, ''));
        return Number.isFinite(n) ? n : null;
    }

    renderProactiveAlerts(alerts) {
        if (!this.elements.proactiveAlerts) return;
        this.elements.proactiveAlerts.innerHTML = '';

        const t = (key) => {
            // Import helper or use global t if available. Assuming i18n is available globally or we import it.
            // Since hud.js doesn't import t, we'll use a fallback or assume it's passed.
            // Actually, we can just use the locale to choose the text here.
            const isEn = this.locale.startsWith('en');
            const texts = {
                'no_alerts': isEn ? 'No proactive alerts.' : 'Sin alertas proactivas.',
                'unknown': isEn ? 'Alert without detail.' : 'Alerta sin detalle.'
            };
            return texts[key] || key;
        };

        if (!alerts || alerts.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'control-empty';
            empty.textContent = t('no_alerts');
            this.elements.proactiveAlerts.appendChild(empty);
            return;
        }

        alerts.slice(-5).reverse().forEach((alert) => {
            const item = document.createElement('div');
            const sev = (alert.severity || 'info').toLowerCase();
            item.className = `control-alert ${sev}`;

            const textSpan = document.createElement('span');
            textSpan.textContent = `[${alert.ts || '--:--'}] ${alert.message || t('unknown')}`;
            item.appendChild(textSpan);

            if (alert.action && alert.action.id) {
                const actBtn = document.createElement('button');
                actBtn.className = 'proactive-action-btn';
                actBtn.textContent = `⚡ ${alert.action.title || 'Aplicar'}`;
                actBtn.title = alert.action.description || 'Ejecutar solución sugerida';
                actBtn.onclick = async (e) => {
                    e.stopPropagation();
                    actBtn.disabled = true;
                    actBtn.textContent = '⏳ ...';
                    try {
                        const response = await fetch('/api/chat', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ message: `ejecutar_accion_proactiva(action_id='${alert.action.id}')` })
                        });
                        let payload = {};
                        try {
                            payload = await response.json();
                        } catch (parseError) {
                            payload = {};
                        }
                        const result = classifyProactiveActionResult(response, payload);
                        actBtn.dataset.state = result.state;
                        actBtn.title = result.detail || actBtn.title;
                        if (result.state === 'success') {
                            actBtn.textContent = this.locale.startsWith('en') ? '✅ Done' : '✅ Listo';
                        } else if (result.state === 'confirmation') {
                            actBtn.textContent = this.locale.startsWith('en') ? '⚠ Confirm' : '⚠ Confirmar';
                        } else {
                            actBtn.textContent = this.locale.startsWith('en') ? '❌ Failed' : '❌ Error';
                            actBtn.disabled = false;
                        }
                    } catch (err) {
                        actBtn.textContent = '❌ Error';
                        actBtn.dataset.state = 'error';
                        actBtn.disabled = false;
                    }
                };
                item.appendChild(actBtn);
            }

            this.elements.proactiveAlerts.appendChild(item);
        });
    }

    updateLatency(latencyMs, breakdownText = '') {
        if (latencyMs !== undefined && Number.isFinite(latencyMs)) {
            const lw = Math.min(100, Math.max(2, (latencyMs / 1200) * 100));
            if (this.elements.latencyBar) {
                this.elements.latencyBar.style.width = `${lw}%`;
                if (latencyMs > 3000) {
                    this.elements.latencyBar.style.background = '#ff3366';
                    this.elements.latencyBar.style.boxShadow = '0 0 10px #ff3366';
                } else if (latencyMs > 1200) {
                    this.elements.latencyBar.style.background = '#ffaa00';
                    this.elements.latencyBar.style.boxShadow = '0 0 10px #ffaa00';
                } else {
                    this.elements.latencyBar.style.background = 'var(--primary)';
                    this.elements.latencyBar.style.boxShadow = '0 0 10px var(--primary)';
                }
            }
            if (this.elements.latencyValue) {
                this.elements.latencyValue.textContent = `${Math.round(latencyMs)} ms`;
            }
            const breakdownEl = document.getElementById('latency-breakdown');
            if (breakdownEl && breakdownText) {
                breakdownEl.textContent = breakdownText;
            }
        }
    }

    updateHudFromPayload(data, opts = {}) {
        if (!data) return;
        const { latencyMs } = opts;

        if (data.cpu !== undefined && data.cpu !== null) {
            const cpu = Math.min(100, Math.max(0, Number(data.cpu)));
            if (this.elements.cpuBar) this.elements.cpuBar.style.width = `${cpu}%`;
            if (this.elements.cpuValue) this.elements.cpuValue.textContent = `${Math.round(cpu)}%`;
        }

        const isEn = this.locale.startsWith('en');

        if (data.temp !== undefined && data.temp !== null) {
            let t = Number(data.temp);
            if (Number.isFinite(t)) {
                if (this.elements.tempBar) this.elements.tempBar.style.width = `${this.tempToBarWidth(t)}%`;
                const label = isEn ? `${((t * 9/5) + 32).toFixed(1)}°F` : `${t.toFixed(1)}°C`;
                if (this.elements.tempValue) this.elements.tempValue.textContent = label;
            }
        }

        if (data.ram !== undefined && data.ram !== null) {
            const ram = Math.min(100, Math.max(0, Number(data.ram)));
            if (this.elements.energyBar) this.elements.energyBar.style.width = `${ram}%`;
            if (this.elements.ramValue) this.elements.ramValue.textContent = `${Math.round(ram)}%`;
        }

        if (latencyMs !== undefined && Number.isFinite(latencyMs)) {
            this.updateLatency(latencyMs);
        }

        if (data.weather) {
            const w = data.weather;
            let outdoor = this.parseWeatherTemp(w.temp);
            if (this.elements.tempDisplay) {
                if (outdoor !== null) {
                    const label = isEn ? `${Math.round((outdoor * 9/5) + 32)}°F` : `${Math.round(outdoor)}°C`;
                    this.elements.tempDisplay.textContent = label;
                } else {
                    this.elements.tempDisplay.textContent = '—';
                }
            }
            if (this.elements.weatherDesc) {
                this.elements.weatherDesc.textContent = String(w.desc || 'Sin datos').trim();
            }
        }

        if (this.elements.securityModeLabel && data.security) {
            this.elements.securityModeLabel.textContent = data.security.strict_mode ? 'ACTIVE' : 'INACTIVE';
        }
        if (this.elements.securityBlockedCount && data.security) {
            this.elements.securityBlockedCount.textContent = String(data.security.blocked_total ?? 0);
        }
        if (this.elements.proactiveModeLabel && data.proactive) {
            this.elements.proactiveModeLabel.textContent = data.proactive.enabled ? 'ACTIVE' : 'PAUSED';
        }
        if (data.proactive && data.proactive.alerts) {
            this.renderProactiveAlerts(data.proactive.alerts);
        }
    }

}
