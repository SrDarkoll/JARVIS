import { API } from './modules/api.js';
import { HUDManager } from './modules/hud.js';
import { UIManager } from './modules/ui.js';
import { convertBlobToWav } from './modules/audio-encoder.js';
import { VoiceManager } from './modules/voice.js';
import { ArcReactor } from './modules/reactor.js';
import { WidgetManager } from './modules/widgets.js';
import {
    isMicrophonePermissionError,
    shouldRestartPassiveRecognition,
} from './modules/recognition-policy.js';
import {
    classifyVoiceError,
    detectVoiceCapabilities,
} from './modules/voice-capabilities.js';
import { classifyVoiceApiFailure } from './modules/voice-api.js';
import { LiveVoiceClient } from './modules/live-voice.js';
import { t, setLanguage, currentLang, updateUI } from './i18n.js';

// --- CONSTANTES GLOBALES ---
const TTS_API_URL = '/api/tts';
const CHAT_STREAM_URL = '/api/chat/stream';
const ENABLE_STREAMING = true;

document.addEventListener('DOMContentLoaded', () => {
    if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
            navigator.serviceWorker.register('/sw.js')
                .then((registration) => console.log('ServiceWorker registered:', registration.scope))
                .catch((err) => console.log('ServiceWorker error:', err));
        });
    }

    // --- ELEMENTOS DEL DOM ---
    const dom = {
        loader: document.getElementById('loader'),
        app: document.getElementById('app'),
        progressBar: document.querySelector('.progress-fill'),
        transcriptText: document.getElementById('transcript'),
        activateBtn: document.getElementById('activate-btn'),
        liveVoiceBtn: document.getElementById('live-voice-btn'),
        btnLiveVoiceText: document.getElementById('btn-live-voice-text'),
        logContent: document.getElementById('log-content'),
        clock: document.getElementById('clock'),
        date: document.getElementById('date'),
        voiceWaves: document.getElementById('voice-waves')?.children || [],
        cpuBar: document.getElementById('cpu-bar'),
        tempBar: document.getElementById('temp-bar'),
        energyBar: document.getElementById('energy-bar'),
        latencyBar: document.getElementById('latency-bar'),
        cpuValue: document.getElementById('cpu-value'),
        tempValue: document.getElementById('temp-value'),
        ramValue: document.getElementById('ram-value'),
        latencyValue: document.getElementById('latency-value'),
        tempDisplay: document.getElementById('temp-display'),
        weatherDesc: document.getElementById('weather-desc'),
        securityModeLabel: document.getElementById('security-mode-label'),
        proactiveModeLabel: document.getElementById('proactive-mode-label'),
        securityBlockedCount: document.getElementById('security-blocked-count'),
        proactiveAlerts: document.getElementById('proactive-alerts'),
        voiceObsCount: document.getElementById('voice-observability-count'),
        voiceObsContent: document.getElementById('voice-observability-content'),
        conversationSegments: document.getElementById('conversation-segments'),
        quickActionButtons: document.querySelectorAll('.quick-action'),
        widgetContainer: 'widget-container',
        langSelector: document.getElementById('lang-selector')
    };

    // --- MANAGERS ---
    const hud = new HUDManager(dom);
    const ui = new UIManager(dom);
    const voice = new VoiceManager();
    const reactor = new ArcReactor('arc-canvas');
    const widgets = new WidgetManager(dom.widgetContainer);

    // --- CONSTANTES ---
    const WAKE_WORD = 'jarvis';
    const WAKE_WORD_REGEX = /\b(?:jarvis|jarvi|jarbis|yarvis|yarbis)\b/i;
    const AUDIO_CAPTURE_CONSTRAINTS = {
        audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
            sampleRate: 48000,
            sampleSize: 16
        }
    };

    function getRecognitionLang(langCode = currentLang) {
        return String(langCode || 'en').toLowerCase() === 'es' ? 'es-ES' : 'en-US';
    }

    function applyRecognitionLanguage(langCode = currentLang) {
        const recognitionLang = getRecognitionLang(langCode);
        if (passiveRecognition) passiveRecognition.lang = recognitionLang;
        if (activeRecognition) activeRecognition.lang = recognitionLang;
    }

    async function syncLanguageFromBackend() {
        try {
            const res = await fetch('/api/language');
            if (!res.ok) return;
            const data = await res.json();
            const backendLang = data.current || data.language;
            if (!backendLang) return;
            setLanguage(backendLang);
            if (dom.langSelector) dom.langSelector.value = backendLang;
            applyRecognitionLanguage(backendLang);
            if (hud && hud.setLocale && data.locale) hud.setLocale(data.locale);
        } catch (err) {
            console.warn('[LANG] Backend language sync failed:', err);
        }
    }

    function createMediaRecorder(stream) {
        const preferredMime = 'audio/webm;codecs=opus';
        let selectedMime = '';

        try {
            if (typeof MediaRecorder?.isTypeSupported === 'function') {
                if (MediaRecorder.isTypeSupported(preferredMime)) {
                    selectedMime = preferredMime;
                } else if (MediaRecorder.isTypeSupported('audio/webm')) {
                    selectedMime = 'audio/webm';
                }
            }
        } catch (_) {
            selectedMime = '';
        }

        return selectedMime
            ? new MediaRecorder(stream, { mimeType: selectedMime })
            : new MediaRecorder(stream);
    }

    function extractCommandAfterWakeWord(rawText) {
        const fullText = String(rawText || '').trim();
        if (!fullText) return '';
        const wakeMatch = fullText.match(WAKE_WORD_REGEX);
        if (!wakeMatch || typeof wakeMatch.index !== 'number') return '';
        const command = fullText
            .slice(wakeMatch.index + wakeMatch[0].length)
            .replace(/^[.,¡!¿?\s]+|[.,¡!¿?\s]+$/g, '');
        return /[a-zA-Z0-9áéíóúÁÉÍÓÚñÑ]/.test(command) ? command : '';
    }

    function isWakeWordOnly(rawText) {
        const fullText = String(rawText || '').trim();
        if (!fullText || !WAKE_WORD_REGEX.test(fullText)) return false;
        const cleaned = fullText
            .replace(WAKE_WORD_REGEX, '')
            .replace(/[.,¡!¿?\s]+/g, '');
        return cleaned.length === 0;
    }

    // --- ESTADO ---
    let isSpeaking = false;
    let currentMode = 'idle';
    let adminEnrollmentActive = false;
    let wakeWordTriggered = false;
    let mediaRecorder = null;
    let audioChunks = [];
    let recordedAudioMimeType = '';
    let lastIdentifiedProfileId = null;
    let lastIdentifiedName = t('label_admin');
    let lastVoiceObsSignature = '';
    let latestInterimTranscript = '';
    let latestTranscriptConfidence = null;
    let fullTranscript = '';
    let activeTimeout = null;
    let currentAudio = null;
    let activeAudioStream = null;
    let activeCommandProcessRequested = false;
    let ttsChainActive = false;
    let ttsChainToken = 0;
    let activeRestartPending = false;

    // --- ESTADO AVANZADO TTS ---
    let activeSpeakToken = 0;
    let activeTtsController = null;
    let ttsBackoffUntil = 0;
    let wakeWordCooldownUntil = 0;
    let hudPollTimer = null;
    let voiceObsPollTimer = null;
    const VOICE_DEBUG = true;

    // --- REFERENCIA AL TRANSCRIPT TEXT ---
    let transcriptText = dom.transcriptText;

    function setCurrentMode(nextMode, reason = '') {
        if (!nextMode || nextMode === currentMode) return;
        const prevMode = currentMode;
        currentMode = nextMode;
        if (VOICE_DEBUG) {
            const suffix = reason ? ` | ${reason}` : '';
            ui.addLogEntry(`> DEBUG MODE: ${prevMode} -> ${nextMode}${suffix}`);
        }
    }

    function interrumpirAudio() {
        if (!isSpeaking && !currentAudio && !activeTtsController) return;
        activeSpeakToken++;
        ttsChainActive = false;
        ttsChainToken++;
        if (activeTtsController) {
            try { activeTtsController.abort(new Error('tts_interrupted')); } catch (_) { }
            activeTtsController = null;
        }
        if (typeof window.speechSynthesis !== 'undefined') {
            window.speechSynthesis.cancel();
        }
        if (currentAudio) {
            currentAudio.pause();
            if (currentAudio.src && currentAudio.src.startsWith('blob:')) {
                URL.revokeObjectURL(currentAudio.src);
            }
            currentAudio.src = '';
            currentAudio = null;
        }
        isSpeaking = false;
        animateWaves(false);
        setCurrentMode('transition', 'interrumpirAudio');
        addLogEntry("> [INTERRUMPIDO] Audio detenido.");
    }

    function speakBrowserFallback(text, onDone) {
        try {
            if (!('speechSynthesis' in window) || typeof SpeechSynthesisUtterance === 'undefined') {
                return false;
            }
            const utter = new SpeechSynthesisUtterance(String(text || '').trim());
            utter.lang = '';
            utter.rate = 1.0;
            utter.pitch = 1.0;
            utter.onend = () => { if (onDone) onDone(true); };
            utter.onerror = () => { if (onDone) onDone(false); };
            window.speechSynthesis.cancel();
            window.speechSynthesis.speak(utter);
            return true;
        } catch (_) {
            return false;
        }
    }

    function applySecurityGlow(reply) {
        if (reply && (reply.includes("ACCESO_DENEGADO") || reply.includes("autorización") || reply.includes("POLITICA_SEGURIDAD"))) {
            const core = document.getElementById('jarvis-core');
            if (core) {
                core.style.filter = 'drop-shadow(0 0 30px #ff003c)';
                setTimeout(() => { core.style.filter = ''; }, 2000);
            }
        }
    }

    function getRandomWakeResponse() {
        const name = lastIdentifiedName || t('label_admin');
        const esOwner = (lastIdentifiedProfileId === null || lastIdentifiedProfileId === 'admin');
        
        if (esOwner) {
            const responses = t('wake_admin');
            return responses[Math.floor(Math.random() * responses.length)];
        } else {
            const responses = t('wake_guest');
            const resp = responses[Math.floor(Math.random() * responses.length)];
            return resp.replace('{name}', name);
        }
    }

    // --- HELPER WRAPPERS ---
    function addLogEntry(msg) { ui.addLogEntry(msg); }
    function setTranscript(text) { ui.setTranscript(text); }
    function animateWaves(active) { ui.animateWaves(active); }
    function updateButtonUI(mode) { ui.updateButtonUI(mode, dom.activateBtn); }
    function getGlobalAudioContext() { return voice.getGlobalAudioContext(); }

    function saveProfile(profileId, name) {
        lastIdentifiedProfileId = profileId;
        lastIdentifiedName = name || t('label_guest');
        lastVoiceObsSignature = '';
    }

    function logError(context, err, recoverFn) {
        const msg = err instanceof Error ? err.message : String(err || 'Unknown error');
        ui.addLogEntry(`> ${context}: ${msg}`);
        if (typeof console !== 'undefined' && console.warn) {
            console.warn(`[${context}]`, err);
        }
        if (typeof recoverFn === 'function') {
            try { recoverFn(); } catch (_) { }
        }
    }

    // --- SISTEMA DE ARRANQUE ---
    if (dom.loader) {
        dom.loader.querySelector('.status-msg').textContent = t('boot_click');
        dom.loader.style.cursor = "pointer";
    }

    let bootStarted = false;
    dom.loader?.addEventListener('click', () => {
        if (bootStarted) return;
        bootStarted = true;
        dom.loader.querySelector('.status-msg').textContent = t('boot_loading');

        let progress = 0;
        const bootInterval = setInterval(() => {
            progress += Math.random() * 5;
            if (progress >= 100) {
                progress = 100;
                clearInterval(bootInterval);
                setTimeout(() => {
                    dom.loader.classList.add('hidden');
                    dom.app.classList.remove('hidden');
                    const greetings = t('boot_greetings');
                    speak(greetings[Math.floor(Math.random() * greetings.length)], () => {
                        startPassiveListening();
                        loadNewsBackground();
                    });
                    ui.addLogEntry(t('boot_protocols'));
                    window.addEventListener('resize', () => reactor.resize());
                }, 500);
            }
            if (dom.progressBar) dom.progressBar.style.width = progress + '%';
        }, 80);
    });

    // --- HUD Y RELOJ ---
    setInterval(() => hud.updateTime(), 1000);
    hud.updateTime();

    async function pollStatus() {
        const t0 = performance.now();
        try {
            const res = await fetch('/api/status/full');
            if (!res.ok) throw new Error('status_full_fail');
            const data = await res.json();
            hud.updateHudFromPayload(data, { latencyMs: performance.now() - t0 });
        } catch (e) {
            if (dom.latencyValue) dom.latencyValue.textContent = '—';
            console.warn('[pollStatus]', e);
        }
    }

    function formatObsTime(ts) {
        if (!ts) return '--:--:--';
        const raw = String(ts);
        const match = raw.match(/T(\d{2}:\d{2}:\d{2})/);
        if (match) return match[1];
        const d = new Date(raw);
        if (!Number.isNaN(d.getTime())) {
            return d.toLocaleTimeString('es-ES', { hour12: false });
        }
        return raw.slice(-8);
    }

    function fmtObsNum(value, digits = 3) {
        const n = Number(value);
        if (!Number.isFinite(n)) return 'N/A';
        return n.toFixed(digits);
    }

    function renderVoiceObservability(events) {
        if (!dom.voiceObsContent || !dom.voiceObsCount) return;
        const filtered = (events || []).filter((ev) => {
            const name = String(ev?.event || '');
            return name.startsWith('voice_') && name !== 'voice_request_context';
        });

        dom.voiceObsCount.textContent = String(filtered.length);

        if (filtered.length === 0) {
            dom.voiceObsContent.innerHTML = '<div class="control-empty">Sin eventos de voz.</div>';
            return;
        }

        const recent = filtered.slice(-6).reverse();
        dom.voiceObsContent.innerHTML = '';

        recent.forEach((ev) => {
            const item = document.createElement('div');
            const sev = (ev?.conversion_ok === false || ev?.status >= 400) ? 'warning' : 'info';
            item.className = `control-alert ${sev}`;

            const time = formatObsTime(ev.ts);
            const eventName = String(ev.event || 'voice_unknown').replace(/^voice_/, '').toUpperCase();
            const source = ev.identity_source || ev.identify_decision || '--';
            const sim = fmtObsNum(ev.similarity ?? ev.top_similarity, 3);
            const profile = ev.profile_id || ev.top_profile_id || '--';
            const reqId = ev.request_id ? String(ev.request_id).slice(-10) : '---------';

            const header = document.createElement('span');
            header.className = 'voice-obs-header';
            header.textContent = `[${time}] ${eventName}`;

            const details = document.createElement('span');
            details.className = 'voice-obs-details';
            details.textContent = `src=${source} | perfil=${profile} | sim=${sim} | req=${reqId}`;

            item.append(header, details);
            dom.voiceObsContent.appendChild(item);
        });
    }

    async function pollVoiceObservability() {
        try {
            const data = await API.fetchObservability(36);
            const events = Array.isArray(data?.events) ? data.events : [];
            const signature = events.slice(-8).map((ev) => `${ev?.ts || ''}|${ev?.event || ''}|${ev?.request_id || ''}`).join('::');
            if (signature !== lastVoiceObsSignature) {
                lastVoiceObsSignature = signature;
                renderVoiceObservability(events);
            }
        } catch (e) {
            if (dom.voiceObsContent && !dom.voiceObsContent.children.length) {
                dom.voiceObsContent.innerHTML = '<div class="control-empty">Obs no disponible.</div>';
            }
        }
    }

    pollStatus();
    hudPollTimer = setInterval(pollStatus, 5000);
    pollVoiceObservability();
    voiceObsPollTimer = setInterval(pollVoiceObservability, 4500);

    // --- ACCIONES Y REGISTRO ---
    async function runQuickAction(action) {
        if (!action) return;
        if (action === 'voice_enroll_admin') return runAdminEnrollmentWizard();
        ui.addLogEntry(`> Panel: ejecutando ${action}...`);
        try {
            const data = await API.runQuickAction(action);
            ui.addLogEntry(`> Panel: ${data.result || 'OK'}`);
            if (data.result && (action.includes('rutina') || action === 'analizar_pantalla')) speak(data.result);
            pollStatus();
        } catch (e) { logError('Panel', e); }
    }

    async function runAdminEnrollmentWizard() {
        if (adminEnrollmentActive) return;
        ui.addLogEntry(t('bio_start'));
        const prevMode = currentMode;
        adminEnrollmentActive = true;
        setCurrentMode('processing', 'runAdminEnrollmentWizard');
        clearPassiveRestartTimer();
        clearActiveStartTimer();
        if (activeTimeout) {
            clearTimeout(activeTimeout);
            activeTimeout = null;
        }
        voice.stopSilenceDetector();
        safeStopRecognition('active');
        passiveErrorRestartPending = false;
        safeStopRecognition('passive');
        if (activeAudioStream) {
            activeAudioStream.getTracks().forEach(t => t.stop());
            activeAudioStream = null;
        }
        mediaRecorder = null;
        audioChunks = [];
        try {
            const profileHeader = lastIdentifiedProfileId || '';
            const initData = await API.startAdminEnrollment(profileHeader);
            const target = initData.target_samples || 3;
            for (let i = 1; i <= target; i++) {
                ui.addLogEntry(`> BIO: Muestra ${i}/${target}...`);
                const sample = await captureVoiceSample(5000);
                if (!sample) {
                    ui.addLogEntry(t('bio_mic_error'));
                    return;
                }
                const capData = await API.postVoiceSample(sample, lastIdentifiedProfileId || profileHeader);
                if (capData.done) { 
                    saveProfile('admin', t('label_admin')); 
                    ui.addLogEntry(t('bio_completed')); 
                    return; 
                }
            }
            ui.addLogEntry(t('bio_incomplete'));
        } catch (e) { logError('BIO', e); }
        finally {
            adminEnrollmentActive = false;
            if (prevMode === 'active') {
                startActiveListening(true, { forceFromProcessing: true });
            } else {
                startPassiveListening();
            }
        }
    }

    async function captureVoiceSample(durationMs = 4500) {
        let stream;
        let rec;
        try {
            stream = await navigator.mediaDevices.getUserMedia(AUDIO_CAPTURE_CONSTRAINTS);
            rec = createMediaRecorder(stream);
        } catch { return null; }

        return new Promise((resolve) => {
            const chunks = [];
            rec.ondataavailable = (ev) => { if (ev.data?.size > 0) chunks.push(ev.data); };
            rec.onstop = async () => {
                try {
                    stream.getTracks().forEach(t => t.stop());
                    const chunkType = chunks.find(chunk => chunk?.type)?.type || '';
                    resolve(await convertBlobToWav(
                        new Blob(chunks, { type: chunkType }),
                        getGlobalAudioContext(),
                        { enhanceSpeech: false }
                    ));
                } catch { resolve(null); }
            };
            rec.start();
            setTimeout(() => {
                try { if (rec.state !== 'inactive') rec.stop(); } catch { /* silencioso */ }
            }, durationMs);
        });
    }

    dom.quickActionButtons.forEach(btn => btn.addEventListener('click', () => runQuickAction(btn.dataset.action)));
    
    if (dom.langSelector) {
        dom.langSelector.value = currentLang;
        dom.langSelector.addEventListener('change', async (e) => {
            const newLang = e.target.value;
            
            // 1. Update frontend UI text
            setLanguage(newLang);
            applyRecognitionLanguage(newLang);
            
            // 2. Update dynamic button text
            const txt = dom.activateBtn.querySelector('.btn-text');
            if (currentMode === 'idle') {
                txt.textContent = t('btn_init_voice');
            } else if (currentMode === 'active') {
                txt.textContent = t('btn_stop_voice');
            } else if (currentMode === 'processing') {
                txt.textContent = t('mode_processing');
            }
            // 3. Hot-swap backend (TTS voice model, Whisper lang, prompts)
            try {
                const res = await fetch('/api/language', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ language: newLang })
                });
                const data = await res.json();
                if (data.ok) {
                    console.log(`[LANG] Backend switched to ${data.name} (TTS: ${data.tts_swapped ? 'OK' : 'SKIPPED'})`);
                    // Update HUD locale for clock/date
                    if (hud && hud.setLocale) {
                        hud.setLocale(data.locale);
                    }
                } else {
                    console.warn('[LANG] Backend switch failed:', data.error);
                }
            } catch (err) {
                console.warn('[LANG] Could not reach backend:', err);
            }
        });
        updateUI(); // Initial translation
    }
    syncLanguageFromBackend();
    dom.activateBtn?.addEventListener('click', () => {
        dom.activateBtn.blur();
        resetMicrophonePermissionBlock();
        const capabilities = refreshVoiceCapabilities(true);
        if (isSpeaking) {
            interrumpirAudio();
            if (mediaRecorder && mediaRecorder.state !== 'inactive') {
                try { mediaRecorder.stop(); } catch (e) { }
            }
            mediaRecorder = null;
            startPassiveListening();
            return;
        }
        if (currentMode === 'active') {
            finishActiveListening();
        } else {
            if (!capabilities.secureContext) {
                ui.addLogEntry(t('voice_insecure_context'));
                setCurrentMode('idle', 'voice_insecure_context');
                updateButtonUI('idle');
                return;
            }
            if (!capabilities.hasGetUserMedia) {
                ui.addLogEntry(t('voice_capture_unsupported'));
                setCurrentMode('idle', 'voice_capture_unsupported');
                updateButtonUI('idle');
                return;
            }
            if (!capabilities.hasMediaRecorder) {
                ui.addLogEntry(t('voice_capture_unsupported'));
            }
            if (!capabilities.hasBrowserRecognition) {
                browserRecognitionDegraded = true;
                logBrowserRecognitionFallback('unsupported');
            }
            const inactiveMode = ['passive', 'idle', 'transition'].includes(currentMode);
            if (inactiveMode) {
                setCurrentMode('transition', 'activate_btn_handoff');
                clearPassiveRestartTimer();
                clearActiveStartTimer();
                passiveErrorRestartPending = false;
                safeStopRecognition('passive');
                safeStopRecognition('active');
                const response = getRandomWakeResponse();
                speak(response, () => {
                    startActiveListening(true);
                });
            } else {
                startActiveListening(true);
            }
        }
    });

    // --- LIVE FULL-DUPLEX VOZ ---
    let liveVoiceClient = null;
    let isLiveVoiceActive = false;

    async function stopLiveVoiceStream() {
        if (!isLiveVoiceActive) return;
        isLiveVoiceActive = false;
        if (liveVoiceClient) {
            liveVoiceClient.disconnect();
            liveVoiceClient = null;
        }
        if (dom.btnLiveVoiceText) dom.btnLiveVoiceText.textContent = t('btn_live_voice');
        if (dom.liveVoiceBtn) dom.liveVoiceBtn.classList.remove('live-active');
        setCurrentMode('idle', 'stop_live_voice');
        updateButtonUI('idle');
        startPassiveListening();
    }

    async function startLiveVoiceStream() {
        if (isLiveVoiceActive) return;
        safeStopRecognition('passive');
        safeStopRecognition('active');
        if (isSpeaking) {
            interrumpirAudio();
        }
        clearPassiveRestartTimer();
        clearActiveStartTimer();

        liveVoiceClient = new LiveVoiceClient({
            language: currentLang,
            profileId: lastIdentifiedProfileId || 'default',
            mode: 'auto'
        });

        liveVoiceClient.onStateChange = (state) => {
            if (state === 'speaking') {
                setCurrentMode('processing', 'live_speaking');
                if (dom.transcriptText) dom.transcriptText.textContent = t('mode_live_speaking');
                if (reactor && reactor.triggerPulse) reactor.triggerPulse(0.8);
            } else if (state === 'listening') {
                setCurrentMode('active', 'live_listening');
                if (dom.transcriptText) dom.transcriptText.textContent = t('mode_live_connected');
            } else if (state === 'closed') {
                if (isLiveVoiceActive) {
                    stopLiveVoiceStream();
                }
            }
        };

        liveVoiceClient.onTranscript = (data) => {
            const role = data.role === 'assistant' ? 'jarvis' : 'user';
            const text = data.text || '';
            if (text) {
ui.addConversationSegment(role, text);
                if (dom.transcriptText) dom.transcriptText.textContent = text;
            }
        };

        liveVoiceClient.onToolExecuting = (data) => {
            const toolName = data.tool || data.name || 'herramienta';
            ui.addLogEntry(`> [LIVE TOOL] Ejecutando: ${toolName}...`);
            if (dom.transcriptText) dom.transcriptText.textContent = `[Ejecutando ${toolName}...]`;
            if (reactor && reactor.triggerPulse) reactor.triggerPulse(0.9);
        };

        liveVoiceClient.onInterrupted = (data) => {
            ui.addLogEntry(t('mode_live_interrupted'));
            if (reactor && reactor.triggerPulse) reactor.triggerPulse(1.0);
        };

        liveVoiceClient.onError = (err) => {
            console.warn('[LIVE_VOICE] Error:', err);
            ui.addLogEntry(`> LIVE ERROR: ${err.message || 'Error de conexión'}`);
        };

        liveVoiceClient.onSessionReady = (data) => {
            ui.addLogEntry(`> LIVE FULL-DUPLEX: Enlace establecido (Modo: ${data.mode || 'live'}).`);
        };

        try {
            await liveVoiceClient.startStreaming();
            isLiveVoiceActive = true;
            if (dom.btnLiveVoiceText) dom.btnLiveVoiceText.textContent = t('btn_stop_live');
            if (dom.liveVoiceBtn) dom.liveVoiceBtn.classList.add('live-active');
            setCurrentMode('active', 'live_stream_started');
            if (dom.transcriptText) dom.transcriptText.textContent = t('mode_live_connected');
        } catch (err) {
            console.error('[LIVE_VOICE] Failed to start:', err);
            ui.addLogEntry(`> LIVE ERROR: No se pudo iniciar el streaming de audio.`);
            stopLiveVoiceStream();
        }
    }

    dom.liveVoiceBtn?.addEventListener('click', () => {
        dom.liveVoiceBtn.blur();
        if (isLiveVoiceActive) {
            stopLiveVoiceStream();
        } else {
            startLiveVoiceStream();
        }
    });

    async function cancelVoiceRegistration() {
        try {
            await fetch('/api/voice/cancelar', { method: 'POST' });
            addLogEntry('> Registro de voz cancelado.');
        } catch (e) { /* silencioso */ }
    }

    // --- RECONOCIMIENTO DE VOZ Y CEREBRO ---
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    let voiceCapabilities = detectVoiceCapabilities(window);
    let passiveRecognition = null;
    let activeRecognition = null;
    let passiveRecognitionRunning = false;
    let activeRecognitionRunning = false;
    let passiveRestartTimer = null;
    let activeStartTimer = null;
    let passiveErrorRestartPending = false;
    let microphonePermissionBlocked = false;
    let microphoneBlockLogged = false;
    let browserRecognitionDegraded = false;
    let browserRecognitionFallbackLogged = false;

    function isExpectedRecognitionStartError(err) {
        const name = String(err?.name || '').toLowerCase();
        const msg = String(err?.message || '').toLowerCase();
        return name === 'invalidstateerror' || msg.includes('already started');
    }

    function isExpectedRecognitionStopError(err) {
        const name = String(err?.name || '').toLowerCase();
        const msg = String(err?.message || '').toLowerCase();
        return name === 'invalidstateerror' || msg.includes('not started');
    }

    function safeStartRecognition(kind) {
        if (adminEnrollmentActive) {
            if (VOICE_DEBUG) ui.addLogEntry(`> DEBUG REC: start(${kind}) skipped (admin enrollment)`);
            return false;
        }
        if (microphonePermissionBlocked || browserRecognitionDegraded) {
            return false;
        }
        const recognition = kind === 'passive' ? passiveRecognition : activeRecognition;
        if (!recognition) return false;
        const isRunning = kind === 'passive' ? passiveRecognitionRunning : activeRecognitionRunning;
        if (isRunning) {
            if (VOICE_DEBUG) ui.addLogEntry(`> DEBUG REC: start(${kind}) skipped (already running)`);
            return false;
        }

        if (kind === 'passive') {
            passiveRecognitionRunning = true;
        } else {
            activeRecognitionRunning = true;
        }

        try {
            recognition.start();
            if (VOICE_DEBUG) ui.addLogEntry(`> DEBUG REC: start(${kind})`);
            return true;
        } catch (e) {
            if (kind === 'passive') {
                passiveRecognitionRunning = false;
            } else {
                activeRecognitionRunning = false;
            }
            if (!isExpectedRecognitionStartError(e)) {
                logError('Recognition start', e);
            } else if (VOICE_DEBUG) {
                ui.addLogEntry(`> DEBUG REC: start(${kind}) ignored (${String(e?.name || 'InvalidStateError')})`);
            }
            return false;
        }
    }

    function safeStopRecognition(kind) {
        const recognition = kind === 'passive' ? passiveRecognition : activeRecognition;
        if (!recognition) return false;

        if (kind === 'passive') {
            passiveRecognitionRunning = false;
            clearPassiveRestartTimer();
        } else {
            activeRecognitionRunning = false;
            clearActiveStartTimer();
        }

        try {
            recognition.stop();
            if (VOICE_DEBUG) ui.addLogEntry(`> DEBUG REC: stop(${kind})`);
            return true;
        } catch (e) {
            if (!isExpectedRecognitionStopError(e)) {
                logError('Recognition stop', e);
            } else if (VOICE_DEBUG) {
                ui.addLogEntry(`> DEBUG REC: stop(${kind}) ignored (${String(e?.name || 'InvalidStateError')})`);
            }
            return false;
        }
    }

    function clearPassiveRestartTimer() {
        if (passiveRestartTimer) {
            clearTimeout(passiveRestartTimer);
            passiveRestartTimer = null;
        }
    }

    function clearActiveStartTimer() {
        if (activeStartTimer) {
            clearTimeout(activeStartTimer);
            activeStartTimer = null;
        }
    }

    function resetMicrophonePermissionBlock() {
        microphonePermissionBlocked = false;
        microphoneBlockLogged = false;
    }

    function refreshVoiceCapabilities(explicitRetry = false) {
        voiceCapabilities = detectVoiceCapabilities(window);
        if (explicitRetry) {
            browserRecognitionDegraded = false;
            browserRecognitionFallbackLogged = false;
        }
        return voiceCapabilities;
    }

    function voiceErrorTranslationKey(errorType) {
        const kind = classifyVoiceError(errorType);
        return {
            permission_denied: 'voice_permission_denied',
            device_missing: 'voice_device_missing',
            device_busy: 'voice_device_busy',
            insecure_context: 'voice_insecure_context',
            recognition_network: 'voice_recognition_network',
        }[kind] || 'bio_mic_blocked';
    }

    function logBrowserRecognitionFallback(reason = 'network') {
        if (browserRecognitionFallbackLogged) return;
        browserRecognitionFallbackLogged = true;
        const key = reason === 'network'
            ? 'voice_recognition_network'
            : 'voice_recognition_backend_fallback';
        ui.addLogEntry(t(key));
    }

    function markBrowserRecognitionHealthy() {
        browserRecognitionDegraded = false;
        browserRecognitionFallbackLogged = false;
    }

    function markBrowserRecognitionDegraded(errorType = 'network') {
        browserRecognitionDegraded = true;
        passiveErrorRestartPending = false;
        activeRestartPending = false;
        clearPassiveRestartTimer();
        clearActiveStartTimer();
        logBrowserRecognitionFallback(
            classifyVoiceError(errorType) === 'recognition_network' ? 'network' : 'unsupported'
        );
    }

    function markMicrophonePermissionBlocked(errorType = '') {
        microphonePermissionBlocked = true;
        passiveErrorRestartPending = false;
        activeRestartPending = false;
        clearPassiveRestartTimer();
        clearActiveStartTimer();
        if (activeTimeout) {
            clearTimeout(activeTimeout);
            activeTimeout = null;
        }
        voice.stopSilenceDetector();
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            try { mediaRecorder.stop(); } catch (_) { }
        }
        mediaRecorder = null;
        if (activeAudioStream) {
            activeAudioStream.getTracks().forEach(track => track.stop());
            activeAudioStream = null;
        }
        setCurrentMode('idle', 'microphone_permission_blocked');
        updateButtonUI('idle');
        if (!microphoneBlockLogged) {
            microphoneBlockLogged = true;
            ui.addLogEntry(t(voiceErrorTranslationKey(errorType)));
        }
    }

    function schedulePassiveRestart(delayMs = 300) {
        if (!shouldRestartPassiveRecognition(
            currentMode,
            adminEnrollmentActive,
            microphonePermissionBlocked,
            browserRecognitionDegraded
        )) {
            clearPassiveRestartTimer();
            return;
        }
        clearPassiveRestartTimer();
        passiveRestartTimer = setTimeout(() => {
            passiveRestartTimer = null;
            if (shouldRestartPassiveRecognition(
                currentMode,
                adminEnrollmentActive,
                microphonePermissionBlocked,
                browserRecognitionDegraded
            )) {
                safeStartRecognition('passive');
            }
        }, delayMs);
        if (VOICE_DEBUG) ui.addLogEntry(`> DEBUG TIMER: passive restart in ${delayMs}ms`);
    }

    function scheduleActiveRecognitionStart(delayMs = 100) {
        if (adminEnrollmentActive || browserRecognitionDegraded) {
            clearActiveStartTimer();
            if (VOICE_DEBUG) ui.addLogEntry('> DEBUG TIMER: active start skipped (admin enrollment)');
            return;
        }
        clearActiveStartTimer();
        activeStartTimer = setTimeout(() => {
            activeStartTimer = null;
            if (currentMode === 'active') {
                safeStartRecognition('active');
            }
        }, delayMs);
        if (VOICE_DEBUG) ui.addLogEntry(`> DEBUG TIMER: active start in ${delayMs}ms`);
    }

    if (SpeechRecognition) {
        passiveRecognition = new SpeechRecognition();
        passiveRecognition.lang = getRecognitionLang();
        passiveRecognition.interimResults = true;
        passiveRecognition.continuous = true;
        passiveRecognition.maxAlternatives = 5;
        passiveRecognition.onstart = () => {
            passiveRecognitionRunning = true;
            markBrowserRecognitionHealthy();
            if (VOICE_DEBUG) ui.addLogEntry('> DEBUG REC: passive onstart');
        };

        passiveRecognition.onresult = (event) => {
            markBrowserRecognitionHealthy();
            for (let i = event.resultIndex; i < event.results.length; i++) {
                if (Date.now() < wakeWordCooldownUntil) continue;
                if (currentMode !== 'passive') continue;

                const isFinalResult = event.results[i].isFinal;

                for (let j = 0; j < event.results[i].length; j++) {
                    const transcript = event.results[i][j].transcript.toLowerCase().trim();

                    // If assistant is currently speaking, allow instant barge-in / stop keywords
                    if (isSpeaking) {
                        const stopPattern = /\b(para|parar|cállate|callate|silencio|stop|detente|alto|ya|cancela|basta|shut\s*up|quiet)\b/i;
                        if (stopPattern.test(transcript)) {
                            interrumpirAudio();
                            ui.addLogEntry('> [INTERRUPCIÓN] Audio detenido por comando de voz.');
                            return;
                        }
                    }

                    // Only extract inline commands from FINAL results to avoid
                    // cutting off the user's sentence on a partial interim match.
                    if (isFinalResult) {
                        const inlineCommand = extractCommandAfterWakeWord(transcript);
                        if (inlineCommand) {
                            processWakeWordCommand(inlineCommand);
                            return;
                        }
                    }

                    // Wake-word-only detection works on both interim and final
                    // so responsiveness is preserved for "Jarvis" alone.
                    if (isWakeWordOnly(transcript) || transcript.includes(WAKE_WORD) || WAKE_WORD_REGEX.test(transcript)) {
                        // On interim results, only activate if it looks like JUST
                        // the wake word (no trailing command being spoken).
                        if (!isFinalResult) {
                            if (isWakeWordOnly(transcript)) {
                                activateFromWakeWord();
                                return;
                            }
                            // Interim has wake word + more text — wait for final
                            continue;
                        }
                        activateFromWakeWord();
                        return;
                    }
                }
            }
        };

        passiveRecognition.onend = () => {
            passiveRecognitionRunning = false;
            if (currentMode === 'passive') {
                schedulePassiveRestart(320);
            }
        };
        passiveRecognition.onerror = (err) => {
            passiveRecognitionRunning = false;
            if (classifyVoiceError(err.error) === 'recognition_network') {
                markBrowserRecognitionDegraded(err.error);
                return;
            }
            if (isMicrophonePermissionError(err.error)) {
                markMicrophonePermissionBlocked(err.error);
                return;
            }
            if (err.error === 'no-speech' || err.error === 'aborted') {
                if (currentMode === 'passive') {
                    schedulePassiveRestart(360);
                }
            }
        };

        // Función para procesar comando después de wake word
        // Procesa inmediatamente el texto para eliminar latencia en comandos continuos
        function processWakeWordCommand(command) {
            if (!command || command.trim().length === 0) {
                activateFromWakeWord();
                return;
            }

            ui.addLogEntry('> Comando continuo detectado. Ejecutando directo para menor latencia...');
            setTranscript(`"${command}"`);

            clearPassiveRestartTimer();
            clearActiveStartTimer();
            passiveErrorRestartPending = false;
            safeStopRecognition('passive');
            
            // Poner modo 'active' para que finishActiveListening lo intercepte
            setCurrentMode('active', 'wakeword_inline_fast');
            fullTranscript = command;
            latestInterimTranscript = '';
            activeCommandProcessRequested = false;
            updateButtonUI('active');
            
            // Terminar inmediatamente y mandar al backend sin grabar audio extra (cero latencia)
            finishActiveListening();
        }

        activeRecognition = new SpeechRecognition();
        activeRecognition.lang = getRecognitionLang();
        activeRecognition.interimResults = true;
        activeRecognition.continuous = true;
        activeRecognition.maxAlternatives = 3;
        activeRecognition.onstart = () => {
            activeRecognitionRunning = true;
            activeRestartPending = false;
            markBrowserRecognitionHealthy();
            if (VOICE_DEBUG) ui.addLogEntry('> DEBUG REC: active onstart');
        };

        activeRecognition.onresult = (event) => {
            if (currentMode !== 'active') return;
            markBrowserRecognitionHealthy();
            let interimTranscript = '';
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const result = event.results[i];
                const primaryAlt = result[0];
                const confidence = Number(primaryAlt?.confidence);
                if (Number.isFinite(confidence)) {
                    latestTranscriptConfidence = Math.max(latestTranscriptConfidence ?? 0, confidence);
                }
                if (result.isFinal) {
                    fullTranscript += primaryAlt?.transcript || '';
                } else {
                    interimTranscript += primaryAlt?.transcript || '';
                }
            }
            latestInterimTranscript = interimTranscript.trim();
            armActiveTimeout();
            const tCombined = `${fullTranscript} ${latestInterimTranscript}`.trim();
            const t = tCombined.toLowerCase().trim();
            if (!t) return;
            if (t === WAKE_WORD || t === 'jarvi' || t === 'yarvis') return;
            setTranscript(`"${tCombined.trim()}"`);
        };

        activeRecognition.onerror = (event) => {
            activeRecognitionRunning = false;
            const errType = String(event?.error || '').toLowerCase();
            if (VOICE_DEBUG) ui.addLogEntry(`> DEBUG REC: active onerror (${errType || 'unknown'})`);
            if (classifyVoiceError(errType) === 'recognition_network') {
                markBrowserRecognitionDegraded(errType);
                return;
            }
            if (isMicrophonePermissionError(errType)) {
                markMicrophonePermissionBlocked(errType);
                return;
            }
            if (errType === 'no-speech' || errType === 'aborted') {
                activeRestartPending = true;
                if (VOICE_DEBUG) ui.addLogEntry(`> DEBUG REC: active retry scheduled (${errType})`);
                scheduleActiveRecognitionStart(220);
                return;
            }
            if (errType) {
                logError('Recognition', new Error(`active_${errType}`));
                startPassiveListening();
            }
        };

        activeRecognition.onend = () => {
            activeRecognitionRunning = false;
            if (VOICE_DEBUG) ui.addLogEntry('> DEBUG REC: active onend');
            if (currentMode === 'active') {
                if (browserRecognitionDegraded) {
                    if (VOICE_DEBUG) ui.addLogEntry('> DEBUG FLOW: backend STT waiting for captured audio');
                    return;
                }
                if (activeRestartPending) {
                    if (VOICE_DEBUG) ui.addLogEntry('> DEBUG FLOW: active onend con retry pendiente');
                    return;
                }
                if (activeCommandProcessRequested) {
                    if (VOICE_DEBUG) ui.addLogEntry('> DEBUG FLOW: active onend ignorado (proceso ya solicitado)');
                    return;
                }
                finishActiveListening();
            }
        };
    }

    function activateFromWakeWord() {
        if (Date.now() < wakeWordCooldownUntil) {
            return;
        }
        if (currentMode === 'active' || currentMode === 'processing') {
            return;
        }
        wakeWordCooldownUntil = Date.now() + 700;
        if (isSpeaking) interrumpirAudio();
        wakeWordTriggered = true;
        setCurrentMode('transition', 'activateFromWakeWord');
        clearPassiveRestartTimer();
        passiveErrorRestartPending = false;
        safeStopRecognition('passive');
        ui.addLogEntry('> ¡Wake Word detectado!');
        startActiveListening(true);
    }

    async function startActiveListening(withBiometry = true, options = {}) {
        if (adminEnrollmentActive) return;
        const forceFromProcessing = !!options.forceFromProcessing;
        if (currentMode === 'active') return;
        if (currentMode === 'processing' && !forceFromProcessing) return;

        clearPassiveRestartTimer();
        clearActiveStartTimer();
        passiveErrorRestartPending = false;
        safeStopRecognition('passive');

        if (activeAudioStream) {
            activeAudioStream.getTracks().forEach(t => t.stop());
            activeAudioStream = null;
        }

        interrumpirAudio();
        setCurrentMode('active', 'startActiveListening');
        activeCommandProcessRequested = false;
        activeRestartPending = false;
        fullTranscript = '';
        latestInterimTranscript = '';
        latestTranscriptConfidence = null;
        updateButtonUI('active');

        const capabilities = refreshVoiceCapabilities();
        if (!capabilities.secureContext) {
            ui.addLogEntry(t('voice_insecure_context'));
            setCurrentMode('idle', 'voice_insecure_context');
            updateButtonUI('idle');
            return;
        }
        if (!capabilities.hasGetUserMedia) {
            ui.addLogEntry(t('voice_capture_unsupported'));
            setCurrentMode('idle', 'voice_capture_unsupported');
            updateButtonUI('idle');
            return;
        }
        if (!capabilities.hasBrowserRecognition) {
            browserRecognitionDegraded = true;
            logBrowserRecognitionFallback('unsupported');
        }

        try {
            const stream = await navigator.mediaDevices.getUserMedia(AUDIO_CAPTURE_CONSTRAINTS);
            resetMicrophonePermissionBlock();
            activeAudioStream = stream;
            window._activeStartTime = Date.now();

            if (withBiometry && capabilities.hasMediaRecorder) {
                audioChunks = [];
                recordedAudioMimeType = '';
                try {
                    mediaRecorder = createMediaRecorder(stream);
                    recordedAudioMimeType = mediaRecorder.mimeType || '';
                    mediaRecorder.ondataavailable = (e) => {
                        if (e.data && e.data.size > 0) {
                            recordedAudioMimeType = e.data.type || recordedAudioMimeType;
                            audioChunks.push(e.data);
                        }
                    };
                    mediaRecorder.start(100);
                } catch (_) {
                    mediaRecorder = null;
                    recordedAudioMimeType = '';
                    ui.addLogEntry(t('voice_capture_unsupported'));
                }
            } else if (withBiometry) {
                mediaRecorder = null;
                recordedAudioMimeType = '';
                ui.addLogEntry(t('voice_capture_unsupported'));
            }

            voice.startSilenceDetector(stream, () => {
                if (currentMode === 'active') {
                    finishActiveListening();
                }
            }, { silenceMs: 1300, minSpeechMs: 220, checkIntervalMs: 60 });

            scheduleActiveRecognitionStart(100);

            armActiveTimeout(9000);
        } catch (e) {
            if (isMicrophonePermissionError(e?.name)) {
                markMicrophonePermissionBlocked(e?.name);
                return;
            }
            logError('Micro', e, startPassiveListening);
        }
    }

    /**
     * Detiene la grabación biométrica de forma segura y retorna el WAV blob.
     */
    async function stopBiometricRecording() {
        const flushChunksToWav = async () => {
            if (!audioChunks.length) {
                recordedAudioMimeType = '';
                return null;
            }
            const chunkType = audioChunks.find(chunk => chunk?.type)?.type || '';
            const raw = new Blob(audioChunks, {
                type: chunkType || recordedAudioMimeType || 'application/octet-stream'
            });
            const wav = await convertBlobToWav(raw, getGlobalAudioContext(), { enhanceSpeech: false });
            audioChunks = [];
            recordedAudioMimeType = '';
            return wav || raw;
        };

        if (!mediaRecorder) {
            return flushChunksToWav();
        }

        if (mediaRecorder.state === 'inactive') {
            mediaRecorder = null;
            return flushChunksToWav();
        }

        return new Promise((resolve) => {
            mediaRecorder.onstop = async () => {
                mediaRecorder = null;
                resolve(await flushChunksToWav());
            };
            mediaRecorder.stop();
        });
    }

    /**
     * Finaliza la escucha activa de forma segura, garantizando que el MediaRecorder
     * termine de procesar los chunks antes de disparar el procesamiento.
     */
    function finishActiveListening() {
        if (currentMode !== 'active') return;
        if (activeCommandProcessRequested) {
            if (VOICE_DEBUG) ui.addLogEntry('> DEBUG FLOW: finishActiveListening ignored (process already requested)');
            return;
        }
        activeCommandProcessRequested = true;

        clearActiveStartTimer();
        voice.stopSilenceDetector();
        safeStopRecognition('active');

        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            mediaRecorder.onstop = () => processVoiceCommand();
            mediaRecorder.stop();
        } else {
            processVoiceCommand();
        }
    }

    function startPassiveListening() {
        clearActiveStartTimer();
        clearPassiveRestartTimer();
        if (adminEnrollmentActive) return;
        if (microphonePermissionBlocked) {
            setCurrentMode('idle', 'microphone_permission_blocked');
            updateButtonUI('idle');
            return;
        }
        passiveErrorRestartPending = false;
        activeCommandProcessRequested = false;
        activeRestartPending = false;

        if (activeAudioStream) {
            activeAudioStream.getTracks().forEach(t => t.stop());
            activeAudioStream = null;
            mediaRecorder = null;
        }
        if (activeTimeout) clearTimeout(activeTimeout);
        voice.stopSilenceDetector();
        setCurrentMode('passive', 'startPassiveListening');
        wakeWordTriggered = false;
        updateButtonUI('passive');
        safeStopRecognition('active');
        schedulePassiveRestart(100);
    }

    function armActiveTimeout(ms = 6500) {
        if (activeTimeout) clearTimeout(activeTimeout);
        activeTimeout = setTimeout(() => { if (currentMode === 'active') finishActiveListening(); }, ms);
    }

    async function getLlamaResponseClassic(text) {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text, profile_id: lastIdentifiedProfileId })
        });
        return await res.json();
    }

    async function getLlamaResponses(userInput) {
        if (!ENABLE_STREAMING) {
            addLogEntry(t('cog_classic'));
            try { return await getLlamaResponseClassic(userInput); }
            catch (e) {
                addLogEntry(t('cog_error').replace('{detail}', e?.message || '---'));
                return { response: t('cog_fail_msg'), should_listen: false };
            }
        }
        addLogEntry(t('cog_connecting'));
        ui.updateAssistantSegment(t('cog_connecting').replace(/^>\s*/, ''));
        const controller = new AbortController();
        const STREAM_TOTAL_TIMEOUT_MS = 60000;
        const STREAM_IDLE_TIMEOUT_MS = 20000;
        let totalTimer = null, idleTimer = null, reader = null;
        const clearTimers = () => { if (totalTimer) clearTimeout(totalTimer); if (idleTimer) clearTimeout(idleTimer); };
        const resetIdleTimer = () => {
            if (idleTimer) clearTimeout(idleTimer);
            idleTimer = setTimeout(() => { controller.abort(new Error('stream_idle_timeout')); }, STREAM_IDLE_TIMEOUT_MS);
        };
        try {
            totalTimer = setTimeout(() => { controller.abort(new Error('stream_total_timeout')); }, STREAM_TOTAL_TIMEOUT_MS);
            resetIdleTimer();
            const response = await fetch(CHAT_STREAM_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: userInput, profile_id: lastIdentifiedProfileId || 'web_default' }),
                signal: controller.signal
            });
            if (!response.ok || !response.body) { clearTimers(); throw new Error('stream_unavailable'); }
            reader = response.body.getReader();
            const decoder = new TextDecoder();
            let carry = '', acc = '', finalReply = '', shouldListen = false, gotDone = false;
            function consumeSseBlock(block) {
                for (const line of block.split('\n')) {
                    const trimmed = line.trim();
                    if (!trimmed.startsWith('data:')) continue;
                    const raw = trimmed.slice(5).trim();
                    if (!raw) continue;
                    let ev;
                    try { ev = JSON.parse(raw); } catch { continue; }
                    if (ev.type === 'token' && ev.text) {
                        acc += ev.text;
                        setTranscript(acc);
                        ui.updateAssistantSegment(acc);
                    } else if (ev.type === 'status') {
                        addLogEntry(t('cog_status').replace('{text}', ev.text));
                        if (!acc) {
                            const statusText = `[ ${ev.text}... ]`;
                            setTranscript(statusText);
                            ui.updateAssistantSegment(statusText);
                        }
                    } else if (ev.type === 'done') {
                        finalReply = ev.response || acc || '';
                        shouldListen = !!ev.should_listen;
                        gotDone = true;
                    } else if (ev.type === 'error') { throw new Error(ev.message || 'stream_error'); }
                }
            }
            while (true) {
                const { done, value } = await reader.read();
                if (value) { carry += decoder.decode(value, { stream: true }).replace(/\r/g, ''); resetIdleTimer(); }
                let sep;
                while ((sep = carry.indexOf('\n\n')) >= 0) {
                    const block = carry.slice(0, sep);
                    carry = carry.slice(sep + 2);
                    consumeSseBlock(block);
                    if (gotDone) break;
                }
                if (gotDone) { try { await reader.cancel(); } catch (_) { } break; }
                if (done) { if (carry.trim()) consumeSseBlock(carry.replace(/\r/g, '')); break; }
            }
            clearTimers();
            let rawReply = gotDone ? finalReply : acc;
            const cleanReply = widgets.processResponse(rawReply);
            applySecurityGlow(cleanReply);
            return { response: cleanReply, should_listen: shouldListen };
        } catch (error) {
            clearTimers();
            if (reader) { try { await reader.cancel(); } catch (_) { } reader = null; }
            addLogEntry(t('streaming_fail').replace('{detail}', error?.message || '---'));
            addLogEntry(t('streaming_fallback'));
            try { return await getLlamaResponseClassic(userInput); }
            catch (e2) {
                addLogEntry('> ERROR: Agente desconectado.');
                return { response: t('cog_fail_msg'), should_listen: false };
            }
        }
    }

    function sanitizeJarvisReply(reply) {
        let r = reply || '';
        r = r.replace(/\{[^}]*"name"[^}]*\}/g, '');
        r = r.replace(/\[.*?\]/g, '');
        r = r.replace(/```[\s\S]*?```/g, '');
        r = r.replace(/\*\*/g, '');
        r = r.trim();
        return r || t('boot_greetings')[0];
    }

    function splitForTts(text) {
        if (!text) return [];
        const cleaned = String(text)
            .replace(/\s+/g, ' ')
            .replace(/^\s+|\s+$/g, '');
        if (cleaned.length < 2) return [];
        const parts = cleaned
            .split(/(?<=[.!?])\s+|\n+/)
            .map(p => p.trim())
            .filter(p => p.length > 2 && !/^\W+$/.test(p));
        // Si no hay separadores pero hay texto, devolver el texto como una sola oración
        return parts.length > 0 ? parts : (cleaned.length > 2 ? [cleaned] : []);
    }

    async function speakSentenceChain(sentences, index, callback) {
        const isFirstSentence = index === 0;
        if (isFirstSentence) {
            ttsChainActive = true;
            ttsChainToken += 1;
        }
        const chainToken = ttsChainToken;

        const finalizeChain = () => {
            if (chainToken !== ttsChainToken) return;
            ttsChainActive = false;
            wakeWordCooldownUntil = Math.max(wakeWordCooldownUntil, Date.now() + 1800);
            callback?.();
        };

        if (chainToken !== ttsChainToken) return;
        if (index >= sentences.length) {
            addLogEntry('> TTS: Cola completada.');
            return finalizeChain();
        }
        if (!sentences[index] || !sentences[index].trim()) {
            return speakSentenceChain(sentences, index + 1, callback);
        }
        addLogEntry(`> TTS: Hablando ${index + 1}/${sentences.length}: "${sentences[index].substring(0, 50)}"`);
        speak(sentences[index], (ok) => {
            if (chainToken !== ttsChainToken) return;
            if (!ok && index < sentences.length - 1) {
                addLogEntry(t('tts_error').replace('{index}', index + 1));
            }
            speakSentenceChain(sentences, index + 1, callback);
        });
    }

    async function processVoiceCommand() {
        if (currentMode !== 'active') return;
        setCurrentMode('processing', 'processVoiceCommand');
        updateButtonUI('processing');
        if (activeTimeout) {
            clearTimeout(activeTimeout);
            activeTimeout = null;
        }
        voice.stopSilenceDetector();
        if (activeAudioStream) {
            activeAudioStream.getTracks().forEach(t => t.stop());
            activeAudioStream = null;
        }
        let transcript = (fullTranscript + " " + latestInterimTranscript).trim();

        // Si vino del modo "despierta y habla", fullTranscript ya contiene comandoInicial.
        // No prepender hint de nuevo — solo usar transcript directamente.
        if (window._transcriptHint) {
            window._transcriptHint = null;
        }

        try {
            const audioBlob = await stopBiometricRecording();
            const hasAudio = !!(audioBlob && audioBlob.size > 1000);

            if (!transcript && !hasAudio) {
                ui.addLogEntry(t('voice_no_input'));
                startPassiveListening();
                return;
            }

            if (transcript) {
                ui.addLogEntry(t('log_user').replace('{text}', transcript));
                ui.addConversationSegment('user', transcript);
            } else {
                ui.addLogEntry(t('voice_transcribing_backend'));
            }

            const _tl = transcript.toLowerCase();
            if (
                _tl.includes('registra mi voz')
                || _tl.includes('registrar mi voz')
                || _tl.includes('register my voice')
                || _tl.includes('enroll my voice')
                || _tl.includes('register admin voice')
                || _tl.includes('admin voice enrollment')
            ) {
                ui.addLogEntry('> BIO: Registro de voz detectado por comando.');
                return runAdminEnrollmentWizard();
            }

            let data;
            if (audioBlob && audioBlob.size > 1000) {
                try {
                    const res = await fetch('/api/voice', {
                        method: 'POST',
                        headers: {
                            'X-Transcript': encodeURIComponent(transcript),
                            'X-Profile-Id': lastIdentifiedProfileId || '',
                            'X-Transcript-Confidence': Number.isFinite(latestTranscriptConfidence)
                                ? String(latestTranscriptConfidence)
                                : ''
                        },
                        body: audioBlob
                    });
                    const responseType = String(res.headers.get('content-type') || '').toLowerCase();
                    if (!responseType.includes('application/json')) {
                        throw new Error('voice_invalid_response');
                    }
                    data = await res.json();
                    const voiceFailureKey = classifyVoiceApiFailure(res.status, data);
                    if (!res.ok && !voiceFailureKey) {
                        throw new Error(`voice_http_${res.status}`);
                    }
                    if (voiceFailureKey) {
                        ui.addLogEntry(t(voiceFailureKey));
                        data.should_listen = false;
                    }
                    const dbg = data.identity_debug || {};
                    const transcriptionSource = String(
                        data.transcription_source || dbg.transcription_source || 'unavailable'
                    );
                    ui.addLogEntry(`> STT: ${transcriptionSource}`);
                    if (!transcript) {
                        const backendTranscript = String(dbg.transcript || '').trim();
                        if (backendTranscript) {
                            transcript = backendTranscript;
                            ui.addLogEntry(t('log_user').replace('{text}', transcript));
                            ui.addConversationSegment('user', transcript);
                        } else if (transcriptionSource === 'unavailable') {
                            ui.addLogEntry(t('voice_transcription_unavailable'));
                            data.should_listen = false;
                        }
                    }
                    const simRaw = Number(dbg.similarity ?? NaN);
                    const sim = Number.isFinite(simRaw) ? simRaw.toFixed(3) : 'N/A';
                    const topRaw = Number(dbg.top_similarity ?? NaN);
                    const topSim = Number.isFinite(topRaw) ? topRaw.toFixed(3) : 'N/A';
                    const topName = dbg.top_nombre || 'N/A';
                    const reqId = dbg.request_id || 'N/A';
                    ui.addLogEntry(`> BIO: Fuente=${data.identity_source || 'desconocida'}, Perfil=${data.profile_id || 'N/A'}, Nombre=${data.nombre || 'N/A'}, Sim=${sim}, Top=${topName}(${topSim}), Req=${reqId}`);
                } catch (bioErr) {
                    // A failed voice request returns to passive mode without retrying.
                    console.warn('[VOICE API]', bioErr);
                    ui.addLogEntry(t('voice_transcription_unavailable'));
                    startPassiveListening();
                    return;
                }
            } else {
                data = await getLlamaResponses(transcript);
            }
            if (data.profile_id) saveProfile(data.profile_id, data.nombre);
            const rawResponse = data.response || "No tengo respuesta.";
            const sanitized = sanitizeJarvisReply(rawResponse);
            const cleanText = widgets.processResponse(sanitized);
            ui.updateAssistantSegment(cleanText);
            const sentences = splitForTts(cleanText);
            ui.addLogEntry(`> JARVIS: ${cleanText}`);
            ui.addLogEntry(`> TTS: ${sentences.length} oracion(es) para hablar.`);
            if (sentences.length === 0) {
                // Si no hay oraciones, habla el texto directo
                ui.addLogEntry('> TTS: Sin oraciones, hablando texto directo.');
                speak(cleanText, () => {
                    if (data.should_listen) startActiveListening(true, { forceFromProcessing: true });
                    else startPassiveListening();
                });
            } else {
                speakSentenceChain(sentences, 0, () => {
                    if (data.should_listen) startActiveListening(true, { forceFromProcessing: true });
                    else startPassiveListening();
                });
            }
        } catch (e) {
            console.warn('[VOICE PROCESSING]', e);
            ui.addLogEntry(t('voice_transcription_unavailable'));
            startPassiveListening();
        }
    }

    async function speak(text, onFinished) {
        if (!text) {
            addLogEntry(t('tts_empty'));
            return onFinished?.();
        }
        if (Date.now() < ttsBackoffUntil) {
            addLogEntry('> TTS: En backoff, saltando.');
            if (onFinished) onFinished(false);
            return;
        }
        if (isSpeaking) interrumpirAudio();
        const speakToken = ++activeSpeakToken;
        addLogEntry(`> TTS: speak() token=${speakToken}, isSpeaking=${isSpeaking}, texto="${text.substring(0, 40)}..."`);
        isSpeaking = true;
        wakeWordCooldownUntil = Date.now() + 3000;
        animateWaves(true);
        addLogEntry(`> J.A.R.V.I.S.: "${text}"`);
        setTranscript(String(text || '').trim());

        let isTimeout = false;
        try {
            const ttsController = new AbortController();
            activeTtsController = ttsController;
            const ttsTimer = setTimeout(() => { isTimeout = true; ttsController.abort(); }, 28000);
            let response;
            try {
                response = await fetch(TTS_API_URL, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text }),
                    signal: ttsController.signal
                });
            } finally { clearTimeout(ttsTimer); }

            if (speakToken !== activeSpeakToken) {
                addLogEntry(`> TTS: Token mismatch (${speakToken} !== ${activeSpeakToken}), cancelando.`);
                return;
            }
            activeTtsController = null;
            if (!response.ok) throw new Error(`tts_http_${response.status}`);
            const contentType = response.headers.get('content-type');
            if (!contentType || !contentType.includes('audio')) throw new Error('CORRUPT_BLOB_OR_JSON_ERROR');
            const blob = await response.blob();
            const audio = new Audio(URL.createObjectURL(blob));
            currentAudio = audio;
            addLogEntry(`> TTS: Audio cargado (${blob.size} bytes), reproduciendo...`);

            const finalizeSpeak = (ok, errKind = '') => {
                if (speakToken !== activeSpeakToken) return;
                if (currentAudio && currentAudio.src && currentAudio.src.startsWith('blob:')) URL.revokeObjectURL(currentAudio.src);
                currentAudio = null;
                isSpeaking = false;
                animateWaves(false);
                if (errKind === 'timeout') ttsBackoffUntil = Date.now() + 12000;
                wakeWordCooldownUntil = Date.now() + (ok ? 1400 : 4000);
                addLogEntry(`> TTS: finalizeSpeak ok=${ok}, errKind=${errKind || 'ninguno'}`);
                if (onFinished) onFinished(ok);
            };

            const maxAudioTimer = setTimeout(() => { try { audio.pause(); } catch (_) { } finalizeSpeak(false, 'timeout'); }, 45000);
            audio.onended = () => { clearTimeout(maxAudioTimer); finalizeSpeak(true); };
            audio.onerror = () => { clearTimeout(maxAudioTimer); finalizeSpeak(false, 'audio_error'); };
            const playPromise = audio.play();
            if (playPromise !== undefined) {
                playPromise.catch(() => {
                    clearTimeout(maxAudioTimer);
                    addLogEntry("> ALERTA: Audio bloqueado por el navegador.");
                    finalizeSpeak(false, 'play_blocked');
                });
            }
        } catch (error) {
            const msg = isTimeout ? 'tts_timeout' : String(error?.message || 'tts_error');
            if (!isTimeout && (speakToken !== activeSpeakToken || msg.includes('tts_interrupted') || msg.toLowerCase().includes('abort'))) return;
            const retryable = msg.includes('tts_timeout') || msg.includes('tts_http_429') || msg.includes('tts_busy');
            if (retryable) {
                addLogEntry("> ALERTA: Voz principal saturada. Activando voz de respaldo.");
                const fallback = speakBrowserFallback(text, (ok) => {
                    if (speakToken !== activeSpeakToken) return;
                    activeTtsController = null;
                    currentAudio = null;
                    isSpeaking = false;
                    animateWaves(false);
                    wakeWordCooldownUntil = Date.now() + (ok ? 1200 : 3500);
                    if (onFinished) onFinished(ok);
                });
                if (fallback) return;
            }
            if (msg.includes('tts_timeout')) ttsBackoffUntil = Date.now() + 12000;
            addLogEntry(`> ERROR: Falla en voz (${msg}).`);
            activeTtsController = null;
            currentAudio = null;
            isSpeaking = false;
            animateWaves(false);
            wakeWordCooldownUntil = Date.now() + 4000;
            if (onFinished) onFinished(false);
        }
    }

    // --- CARGAR NOTICIAS ---
    let newsPollAttempts = 0;

    /**
     * Versión background: no bloquea el arranque, solo lee si está listo.
     * Máximo 3 intentos rápidos, si no está listo lo ignora.
     */
    async function loadNewsBackground() {
        const today = new Date().toISOString().split('T')[0];
        const briefingKey = `briefing_fecha_${currentLang}`;
        const alreadyRead = localStorage.getItem(briefingKey);
        if (alreadyRead === today) {
            addLogEntry("> Briefing ya emitido hoy. Modo centinela.");
            return;
        }
        try {
            const data = await API.fetchNews();
            if (data.listo && data.resumen) {
                localStorage.setItem(briefingKey, today);
                const oraciones = splitForTts(data.resumen);
                speakSentenceChain([t('briefing_intro')], 0, () => {
                    speakSentenceChain(oraciones, 0, () => { });
                });
            }
            // Si no está listo, no bloquea — se lee en otro momento
        } catch (e) { /* silencioso en background */ }
    }

    /**
     * Versión síncrona de boot: bloquea hasta que las noticias estén listas
     * o se agoten los intentos. Solo se usa en el arranque original.
     */
    async function loadNews() {
        const today = new Date().toISOString().split('T')[0];
        const briefingKey = `briefing_fecha_${currentLang}`;
        const alreadyRead = localStorage.getItem(briefingKey);
        const yesterday = new Date(Date.now() - 86400000).toISOString().split('T')[0];
        if (alreadyRead && alreadyRead < yesterday) {
            localStorage.removeItem(briefingKey);
        }
        if (alreadyRead === today) {
            addLogEntry("> Briefing ya emitido hoy. Modo centinela.");
            startPassiveListening();
            return;
        }
        try {
            const data = await API.fetchNews();
            if (data.listo && data.resumen) {
                localStorage.setItem(briefingKey, today);
                const oraciones = splitForTts(data.resumen);
                speakSentenceChain([t('briefing_intro')], 0, () => {
                    speakSentenceChain(oraciones, 0, () => startPassiveListening());
                });
            } else {
                if (++newsPollAttempts < 48) setTimeout(loadNews, 5000);
                else startPassiveListening();
            }
        } catch (e) {
            console.warn('[loadNews]', e);
            startPassiveListening();
        }
    }

    // --- LIMPIEZA AL SALIR ---
    function cleanupOnUnload() {
        clearPassiveRestartTimer();
        clearActiveStartTimer();
        if (activeTimeout) {
            clearTimeout(activeTimeout);
            activeTimeout = null;
        }
        if (voiceObsPollTimer) {
            clearInterval(voiceObsPollTimer);
            voiceObsPollTimer = null;
        }
        if (hudPollTimer) {
            clearInterval(hudPollTimer);
            hudPollTimer = null;
        }
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            mediaRecorder.stream.getTracks().forEach(t => t.stop());
            mediaRecorder = null;
        }
        if (activeAudioStream) {
            activeAudioStream.getTracks().forEach(t => t.stop());
            activeAudioStream = null;
        }
        passiveErrorRestartPending = false;
        safeStopRecognition('passive');
        safeStopRecognition('active');
        if (voice && voice.audioCtx && voice.audioCtx.state !== 'closed') {
            voice.audioCtx.close();
        }
        voice.stopSilenceDetector();
        if (liveVoiceClient) {
            liveVoiceClient.disconnect();
            liveVoiceClient = null;
        }
        if (reactor && reactor.dispose) reactor.dispose();
    }

    window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isSpeaking) {
            interrumpirAudio();
        }
    });

    document.getElementById('transcript')?.addEventListener('click', () => {
        if (isSpeaking) {
            interrumpirAudio();
        }
    });

    window.addEventListener('beforeunload', cleanupOnUnload);
    window.addEventListener('pagehide', cleanupOnUnload);

    // INICIAR — voz se activa en el callback de speak() dentro del boot
});
