/**
 * Módulo de utilidades de Interfaz de Usuario para JARVIS.
 * Maneja logs, efectos visuales de transcripción y ondas de voz.
 */

import { t } from '../i18n.js';

export class UIManager {
    constructor(elements) {
        this.elements = elements;
        this.waveInterval = null;
    }

    forwardLogToTerminal(msg) {
        const text = String(msg ?? '').trim();
        if (!text) return;
        if (typeof console !== 'undefined' && console.log) {
            console.log('[JARVIS UI]', text);
        }
        if (typeof fetch !== 'function') return;
        fetch('/api/frontend/log', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source: 'system_log', message: text }),
            keepalive: true
        }).catch(() => {
            // Do not write logging transport failures back into the HUD.
        });
    }

    addLogEntry(msg) {
        this.forwardLogToTerminal(msg);
        if (!this.elements.logContent) return;
        const entry = document.createElement('div');
        entry.className = 'log-entry';
        entry.textContent = msg;
        this.elements.logContent.appendChild(entry);

        if (this.elements.logContent.children.length > 50) {
            this.elements.logContent.removeChild(this.elements.logContent.firstChild);
        }
        this.elements.logContent.scrollTop = this.elements.logContent.scrollHeight;
    }

    setTranscript(text) {
        if (!this.elements.transcriptText) return;
        const current = this.elements.transcriptText.textContent;
        if (current === text) return;
        
        this.elements.transcriptText.classList.add('updating');
        setTimeout(() => {
            this.elements.transcriptText.textContent = text;
            this.elements.transcriptText.classList.remove('updating');
            this.elements.transcriptText.scrollTop = 0;
        }, 200);
    }

    animateWaves(active) {
        if (this.waveInterval) {
            clearInterval(this.waveInterval);
            this.waveInterval = null;
        }
        
        if (!this.elements.voiceWaves || this.elements.voiceWaves.length === 0) return;

        if (active) {
            this.waveInterval = setInterval(() => {
                for (let i = 0; i < this.elements.voiceWaves.length; i++) {
                    const h = 5 + Math.random() * 25;
                    this.elements.voiceWaves[i].style.height = h + 'px';
                }
            }, 100);
        } else {
            for (let i = 0; i < this.elements.voiceWaves.length; i++) {
                this.elements.voiceWaves[i].style.height = '4px';
            }
        }
    }

    updateButtonUI(mode, button) {
        if (!button) return;
        const btnSpan = button.querySelector('span');
        button.classList.remove('mode-passive', 'mode-active', 'mode-processing');
        
        switch (mode) {
            case 'passive':
                btnSpan.textContent = t('mode_passive');
                button.classList.add('mode-passive');
                break;
            case 'active':
                btnSpan.textContent = t('mode_active');
                button.classList.add('mode-active');
                break;
            case 'processing':
                btnSpan.textContent = t('mode_processing');
                button.classList.add('mode-processing');
                break;
            default:
                btnSpan.textContent = t('mode_idle');
                break;
        }
    }
}
