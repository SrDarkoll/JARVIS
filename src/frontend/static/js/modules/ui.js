/**
 * Módulo de utilidades de Interfaz de Usuario para JARVIS.
 * Maneja logs, efectos visuales de transcripción y ondas de voz.
 */

import { t } from '../i18n.js';

export class UIManager {
    constructor(elements) {
        this.elements = elements;
        this.waveInterval = null;
        this.currentAssistantSegment = null;
        this.maxConversationSegments = 14;
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

    _conversationLabel(role) {
        if (role === 'user') return t('conversation_user');
        if (role === 'assistant') return t('conversation_jarvis');
        return t('conversation_system');
    }

    _splitConversationLines(text) {
        const cleaned = String(text ?? '').replace(/\s+/g, ' ').trim();
        if (!cleaned) return [];
        const parts = cleaned
            .split(/(?<=[.!?])\s+|\n+/)
            .map(part => part.trim())
            .filter(Boolean);
        return parts.length ? parts : [cleaned];
    }

    _renderConversationBody(body, text) {
        body.textContent = '';
        const lines = this._splitConversationLines(text);
        if (lines.length === 0) {
            body.textContent = '...';
            return;
        }
        for (const line of lines) {
            const item = document.createElement('div');
            item.className = 'conversation-line';
            item.textContent = line;
            body.appendChild(item);
        }
    }

    _trimConversationSegments(container) {
        while (container.children.length > this.maxConversationSegments) {
            container.removeChild(container.firstElementChild);
        }
    }

    addConversationSegment(role, text) {
        const container = this.elements.conversationSegments;
        if (!container) return null;

        const empty = container.querySelector('.conversation-empty');
        if (empty) empty.remove();

        const safeRole = role === 'user' || role === 'assistant' ? role : 'system';
        const item = document.createElement('article');
        item.className = `conversation-segment ${safeRole}`;

        const label = document.createElement('div');
        label.className = 'conversation-speaker';
        label.textContent = this._conversationLabel(safeRole);

        const body = document.createElement('div');
        body.className = 'conversation-body';
        this._renderConversationBody(body, text);

        item.append(label, body);
        container.appendChild(item);
        this._trimConversationSegments(container);
        container.scrollTop = container.scrollHeight;

        if (safeRole === 'assistant') this.currentAssistantSegment = item;
        if (safeRole === 'user') this.currentAssistantSegment = null;
        return item;
    }

    updateConversationSegment(segment, text) {
        const body = segment?.querySelector?.('.conversation-body');
        if (!body) return;
        this._renderConversationBody(body, text);
        const container = this.elements.conversationSegments;
        if (container) container.scrollTop = container.scrollHeight;
    }

    updateAssistantSegment(text) {
        if (!this.currentAssistantSegment || !this.currentAssistantSegment.isConnected) {
            this.currentAssistantSegment = this.addConversationSegment('assistant', text);
            return;
        }
        this.updateConversationSegment(this.currentAssistantSegment, text);
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
