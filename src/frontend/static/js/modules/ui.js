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
        const lower = String(msg || '').toLowerCase();
        let cat = 'all';
        if (lower.includes('tts') || lower.includes('audio') || lower.includes('speak') || lower.includes('voz') || lower.includes('mic')) {
            cat = 'voice';
        } else if (lower.includes('error') || lower.includes('falló') || lower.includes('errkind') || lower.includes('bloqueo')) {
            cat = 'err';
        } else if (lower.includes('ia') || lower.includes('neuronal') || lower.includes('gemini') || lower.includes('groq') || lower.includes('llama')) {
            cat = 'ia';
        }
        entry.dataset.category = cat;
        entry.textContent = msg;

        if (this.currentLogFilter && this.currentLogFilter !== 'all') {
            if (cat !== this.currentLogFilter) {
                entry.classList.add('filter-hidden');
            }
        }

        this.elements.logContent.appendChild(entry);

        if (this.elements.logContent.children.length > 60) {
            this.elements.logContent.removeChild(this.elements.logContent.firstChild);
        }
        this.elements.logContent.scrollTop = this.elements.logContent.scrollHeight;
    }

    setLogFilter(filter) {
        this.currentLogFilter = filter;
        if (!this.elements.logContent) return;
        const entries = this.elements.logContent.querySelectorAll('.log-entry');
        entries.forEach(entry => {
            if (filter === 'all' || entry.dataset.category === filter) {
                entry.classList.remove('filter-hidden');
            } else {
                entry.classList.add('filter-hidden');
            }
        });
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

        const actions = document.createElement('div');
        actions.className = 'segment-actions';

        if (safeRole === 'assistant') {
            const copyBtn = document.createElement('button');
            copyBtn.type = 'button';
            copyBtn.className = 'msg-action-btn copy-action-btn';
            copyBtn.title = t('copy_action');
            copyBtn.setAttribute('aria-label', t('copy_action'));
            copyBtn.innerHTML = `<svg viewBox="0 0 24 24" width="11" height="11"><path fill="currentColor" d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg><span class="copy-feedback"></span>`;
            copyBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                const content = body.textContent || '';
                navigator.clipboard?.writeText?.(content).then(() => {
                    const fb = copyBtn.querySelector('.copy-feedback');
                    if (fb) {
                        fb.textContent = t('copy_copied');
                        fb.classList.add('active');
                        setTimeout(() => {
                            fb.textContent = '';
                            fb.classList.remove('active');
                        }, 1800);
                    }
                }).catch(() => {});
            });
            actions.appendChild(copyBtn);
        } else if (safeRole === 'user') {
            const resendBtn = document.createElement('button');
            resendBtn.type = 'button';
            resendBtn.className = 'msg-action-btn resend-action-btn';
            resendBtn.title = t('resend_action');
            resendBtn.setAttribute('aria-label', t('resend_action'));
            resendBtn.innerHTML = `<svg viewBox="0 0 24 24" width="11" height="11"><path fill="currentColor" d="M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>`;
            resendBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                const input = document.getElementById('command-input');
                if (input) {
                    input.value = text;
                    input.focus();
                }
            });
            actions.appendChild(resendBtn);
        }

        item.append(label, body, actions);
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
                    this.elements.voiceWaves[i].style.transform = `scaleY(${h / 4})`;
                }
            }, 100);
        } else {
            for (let i = 0; i < this.elements.voiceWaves.length; i++) {
                this.elements.voiceWaves[i].style.transform = 'scaleY(1)';
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
