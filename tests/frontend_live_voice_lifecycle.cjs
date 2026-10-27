const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(join(__dirname, '../src/frontend/static/js/main.js'), 'utf8');
// Execute the production functions and recognition handlers with browser I/O replaced.
const declarations = [...source.matchAll(/^    let [^\r\n]+;\r?$/gm)]
    .map(match => match[0]).join('\n');
const functions = [...source.matchAll(/^    (?:async )?function \w+\(/gm)].map(match => {
    const firstLine = source.slice(match.index, source.indexOf('\n', match.index)).trimEnd();
    if (firstLine.endsWith('}')) return firstLine;
    const end = /^    }\r?$/gm;
    end.lastIndex = match.index;
    const closing = end.exec(source);
    assert.ok(closing, `Missing function end: ${match[0]}`);
    return source.slice(match.index, closing.index + closing[0].length);
}).join('\n');
const recognitionHandlers = source.slice(
    source.indexOf('    if (SpeechRecognition) {'),
    source.indexOf('    function activateFromWakeWord()'),
);

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

const settle = () => new Promise(resolve => setImmediate(resolve));

function stream() {
    const track = { stops: 0, stop() { this.stops++; } };
    return { track, getTracks() { return [track]; } };
}

function button() {
    return {
        disabled: false, textContent: '', dataset: {},
        classList: { add() {}, remove() {}, toggle() {} },
        setAttribute() {},
        focus() {}, blur() {},
    };
}

function fixture() {
    const requests = [], recordings = [], captures = [], chatCalls = [], timers = new Map();
    const assistant = [], audio = [], modes = [], clients = [];
    const dom = {
        activateBtn: button(), liveVoiceBtn: button(), btnLiveVoiceText: button(),
        liveScreenBtn: button(), btnLiveScreenText: button(), commandInput: button(),
        transcriptText: button(), commandStatus: button(), voiceWaves: [],
    };
    let nextTimer = 0;
    const io = {
        getUserMedia: async () => stream(),
        convert: async blob => blob,
        status: async () => ({}),
        startLive: async () => {},
        stopLive: async () => {},
        fetch: async url => {
            if (url === '/api/voice') return voiceResponse();
            if (url === '/api/tts') return {
                ok: true, headers: { get() { return 'audio/wav'; } },
                blob: async () => new Blob(['voice']),
            };
            throw new Error(`Unexpected network request: ${url}`);
        },
    };
    class Recognition {
        starts = 0;
        start() { this.starts++; this.onstart?.(); }
        stop() {}
    }
    class Recorder {
        constructor(mediaStream) {
            this.stream = mediaStream;
            this.state = 'inactive';
            this.mimeType = 'audio/webm';
            this.stops = 0;
            recordings.push(this);
        }
        start() { this.state = 'recording'; }
        stop() { this.state = 'inactive'; this.stops++; }
    }
    class LiveClient {
        constructor() { clients.push(this); }
        async startStreaming() { await io.startLive(); }
        async disconnect() { await io.stopLive(); }
        sendUserText(text) { this.typed = (this.typed || []).concat(text); return true; }
    }
    const voice = {
        silence: null,
        startSilenceDetector(_stream, callback) { this.silence = callback; },
        stopSilenceDetector() { this.silence = null; },
        getGlobalAudioContext() { return {}; },
    };
    const context = vm.createContext({
        dom, voice, MediaRecorder: Recorder, LiveVoiceClient: LiveClient,
        SpeechRecognition: Recognition, currentLang: 'es',
        window: { SpeechRecognition: Recognition, dispatchEvent() {} },
        CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options?.detail; } },
        navigator: { mediaDevices: {
            getUserMedia: (...args) => {
                const pending = io.getUserMedia(...args);
                captures.push(pending);
                return pending;
            },
        } },
        AUDIO_CAPTURE_CONSTRAINTS: { audio: true },
        WAKE_WORD: 'jarvis', WAKE_WORD_REGEX: /\bjarvis\b/i,
        VOICE_DEBUG: false, ENABLE_STREAMING: true,
        ONBOARDING_STAGES: { HIDDEN: 'hidden' },
        CHAT_STREAM_URL: '/api/chat/stream', TTS_API_URL: '/api/tts',
        t: key => key, console: { warn() {} }, performance,
        normalizeCommandText: text => String(text || '').trim(),
        Blob, AbortController, TextDecoder, Number, Date,
        URL: { createObjectURL() { return 'blob:test'; }, revokeObjectURL() {} },
        Audio: class {
            constructor(url) { this.src = url; audio.push(this); }
            play() { this.plays = (this.plays || 0) + 1; return Promise.resolve(); }
            pause() { this.paused = true; }
        },
        ui: {
            addLogEntry() {}, addConversationSegment() {},
            updateAssistantSegment(text) { assistant.push(text); },
            updateButtonUI(mode) { modes.push(mode); }, setTranscript() {}, animateWaves() {},
        },
        hud: { updateLatency() {} },
        reactor: { setState() {}, triggerPulse() {} },
        widgets: { processResponse: value => value },
        localStorage: { setItem() {}, getItem() { return null; } },
        API: {
            fetchLiveVoiceStatus: () => io.status(),
            postChat: text => { chatCalls.push(text); throw new Error('Obsolete command was retried'); },
        },
        detectVoiceCapabilities: () => ({
            secureContext: true, hasGetUserMedia: true,
            hasMediaRecorder: true, hasBrowserRecognition: true,
        }),
        isMicrophonePermissionError: name => name === 'NotAllowedError',
        classifyVoiceError: error => error === 'network' ? 'recognition_network' : '',
        shouldRestartPassiveRecognition: (mode, enrollment, blocked, degraded) => (
            mode === 'passive' && !enrollment && !blocked && !degraded
        ),
        classifyVoiceApiFailure: () => null,
        liveVoiceErrorView: () => ({ code: 'live_voice_unavailable', message: 'failed', terminal: true }),
        shouldCommitLiveTranscript: data => data.is_final,
        commandDoneText: event => event.response,
        commandStateView: state => ({ label: state }),
        microphoneRecoveryView: () => ({ hidden: false, message: 'permission denied' }),
        isSuccessfulCommandOutcome: outcome => outcome === 'succeeded',
        convertBlobToWav: (...args) => io.convert(...args),
        fetch: (url, options) => {
            requests.push({ url, options });
            return io.fetch(url, options);
        },
        setTimeout: (callback, ms) => {
            const id = ++nextTimer;
            timers.set(id, { callback, ms });
            return id;
        },
        clearTimeout: id => timers.delete(id),
        document: { getElementById() { return null; } },
    });
    vm.runInContext(`${declarations}\n${functions}\n${recognitionHandlers}`, context);
    return {
        context, io, dom, voice, requests, recordings, captures, chatCalls, timers,
        assistant, audio, modes, clients,
        read: expression => vm.runInContext(expression, context),
        async classic() {
            await context.startActiveListening();
            const recorder = recordings.at(-1);
            recorder.ondataavailable({ data: new Blob([new Uint8Array(1600)], { type: 'audio/webm' }) });
            return recorder;
        },
        finish(recorder) {
            context.finishActiveListening();
            return recorder.onstop();
        },
    };
}

function voiceResponse({ should_listen = false } = {}) {
    return {
        ok: true, status: 200,
        headers: { get() { return 'application/json'; } },
        json: async () => ({ response: 'Respuesta clásica.', should_listen }),
    };
}

test('Live releases the classic timeout, detector, recorder and tracks before status I/O', async () => {
    const f = fixture();
    const recorder = await f.classic();
    const pending = deferred();
    f.io.status = () => pending.promise;
    const starting = f.context.startLiveVoiceStream();
    assert.equal(f.read('activeTimeout'), null);
    assert.equal(f.voice.silence, null);
    assert.equal(recorder.state, 'inactive');
    assert.equal(recorder.stream.track.stops, 1);
    assert.equal(recorder.onstop, null);
    assert.equal(recorder.ondataavailable, null);
    pending.resolve({});
    await starting;
});

test('typed input during Live uses the same client once, without HTTP or TTS', async () => {
    const f = fixture();
    await f.context.startLiveVoiceStream();
    assert.equal(await f.context.submitTextCommand('Iamramidev'), true);
    assert.deepEqual(f.clients[0].typed, ['Iamramidev']);
    assert.equal(f.requests.length, 0);
    assert.equal(f.chatCalls.length, 0);
    assert.equal(f.read('liveTextCommandPending'), true);
});

test('failed Live text send does not fall through to unrelated HTTP context', async () => {
    const f = fixture();
    await f.context.startLiveVoiceStream();
    f.clients[0].sendUserText = () => false;
    assert.equal(await f.context.submitTextCommand('otra vez'), false);
    assert.equal(f.requests.length, 0);
    assert.equal(f.chatCalls.length, 0);
});

test('queued classic silence, timeout, recognition and onstop cannot submit during Live', async () => {
    const f = fixture();
    const recorder = await f.classic();
    const silence = f.voice.silence;
    const timeout = [...f.timers.values()].find(timer => timer.ms === 9000).callback;
    const data = recorder.ondataavailable;
    f.context.finishActiveListening();
    const stopped = recorder.onstop;
    await f.context.startLiveVoiceStream();
    silence();
    timeout();
    data({ data: new Blob([new Uint8Array(1800)]) });
    await stopped();
    f.read('activeRecognition.onend()');
    f.read("activeRecognition.onerror({error: 'NotAllowedError'})");
    await settle();
    assert.deepEqual(f.requests, []);
    assert.equal(f.read('audioChunks.length'), 0);
    assert.equal(f.read('currentMode'), 'active');
    assert.equal(f.read('microphonePermissionBlocked'), false);
});

for (const phase of ['connecting', 'active', 'closing']) {
    test(`classic entry points stay blocked while Live is ${phase}`, async () => {
        const f = fixture();
        const pending = deferred();
        if (phase === 'connecting') f.io.status = () => pending.promise;
        const starting = f.context.startLiveVoiceStream();
        if (phase !== 'connecting') await starting;
        let stopping;
        if (phase === 'closing') {
            f.io.stopLive = () => pending.promise;
            stopping = f.context.stopLiveVoiceStream();
        }
        const before = f.read('currentMode');
        f.context.startPassiveListening();
        await f.context.startActiveListening(true, { forceFromProcessing: true });
        f.context.finishActiveListening();
        await f.context.processVoiceCommand();
        f.context.activateFromWakeWord();
        f.context.safeStartRecognition('passive');
        assert.equal(f.captures.length, 0);
        assert.equal(f.read('passiveRecognition.starts'), 0);
        assert.equal(f.read('currentMode'), before);
        pending.resolve({});
        await starting;
        await stopping;
    });
}

test('late conversion cannot submit or clear chunks from the next classic capture', async () => {
    const f = fixture();
    const recorder = await f.classic();
    const conversion = deferred();
    f.io.convert = () => conversion.promise;
    const processing = f.finish(recorder);
    await f.context.startLiveVoiceStream();
    await f.context.stopLiveVoiceStream();
    const fresh = await f.classic();
    conversion.resolve(new Blob([new Uint8Array(1600)]));
    await processing;
    assert.deepEqual(f.requests, []);
    assert.equal(f.read('audioChunks.length'), 1);
    assert.equal(f.read('mediaRecorder'), fresh);
    assert.equal(f.read('currentMode'), 'active');
});

for (const boundary of ['fetch', 'json']) {
    for (const shouldListen of [false, true]) {
        test(`late /api/voice ${boundary} is discarded after handoff (listen=${shouldListen})`, async () => {
            const f = fixture();
            const recorder = await f.classic();
            const pending = deferred();
            const response = voiceResponse({ should_listen: shouldListen });
            if (boundary === 'json') response.json = () => pending.promise;
            f.io.fetch = () => boundary === 'fetch' ? pending.promise : response;
            const processing = f.finish(recorder);
            await settle();
            assert.equal(f.requests.length, 1);
            assert.equal(f.requests[0].url, '/api/voice');
            await f.context.startLiveVoiceStream();
            await f.context.stopLiveVoiceStream();
            await f.classic();
            pending.resolve(boundary === 'fetch' ? response : {
                response: 'Resultado obsoleto.', should_listen: shouldListen,
            });
            await processing;
            assert.equal(f.requests.length, 1, 'Accepted server work must not cause a new TTS/request');
            assert.deepEqual(f.assistant, []);
            assert.equal(f.read('currentMode'), 'active');
        });
    }
}

for (const [rejected, afterStop] of [[false, false], [false, true], [true, false], [true, true]]) {
    test(`delayed classic microphone permission is discarded (rejected=${rejected}, stopped=${afterStop})`, async () => {
        const f = fixture();
        const permission = deferred();
        f.io.getUserMedia = () => permission.promise;
        const capture = f.context.startActiveListening();
        await f.context.startLiveVoiceStream();
        if (afterStop) await f.context.stopLiveVoiceStream();
        const late = stream();
        if (rejected) permission.reject(Object.assign(new Error('denied'), { name: 'NotAllowedError' }));
        else permission.resolve(late);
        await capture;
        assert.equal(f.recordings.length, 0);
        if (!rejected) assert.equal(late.track.stops, 1);
        assert.equal(f.read('activeAudioStream'), null);
        assert.equal(f.read('microphonePermissionBlocked'), false);
        assert.equal(f.read('currentMode'), afterStop ? 'passive' : 'active');
    });
}

test('failed Live connection restores controls and permits one new classic capture', async () => {
    const f = fixture();
    const recorder = await f.classic();
    f.io.startLive = async () => { throw new Error('provider unavailable'); };
    await f.context.startLiveVoiceStream();
    assert.equal(f.dom.liveVoiceBtn.disabled, false);
    assert.equal(f.read('isLiveVoiceActive'), false);
    assert.equal(f.read('currentMode'), 'idle');
    assert.equal(recorder.stream.track.stops, 1);
    await f.classic();
    assert.equal(f.captures.length, 2);
    assert.equal(f.read('currentMode'), 'active');
});

test('normal Live stop resumes passive recognition and classic audio still submits', async () => {
    const f = fixture();
    await f.context.startLiveVoiceStream();
    await f.context.stopLiveVoiceStream();
    assert.equal(f.read('currentMode'), 'passive');
    [...f.timers.values()].find(timer => timer.ms === 100).callback();
    assert.equal(f.read('passiveRecognition.starts'), 1);
    const recorder = await f.classic();
    await f.finish(recorder);
    await settle();
    assert.deepEqual(f.requests.map(request => request.url), ['/api/voice', '/api/tts']);
    assert.deepEqual(f.assistant, ['Respuesta clásica.']);
    assert.equal(f.audio.length, 1);
    f.audio[0].onended();
    assert.equal(f.read('currentMode'), 'passive');
});

test('a pending Piper blob cannot start playback after Live takes ownership', async () => {
    const f = fixture();
    const blob = deferred();
    f.io.fetch = async () => ({
        ok: true, headers: { get() { return 'audio/wav'; } }, blob: () => blob.promise,
    });
    const speaking = f.context.speak('Respuesta anterior.');
    await settle();
    await f.context.startLiveVoiceStream();
    blob.resolve(new Blob(['audio']));
    await speaking;
    assert.equal(f.audio.length, 0);
    assert.equal(f.read('currentMode'), 'active');
});

test('obsolete text-only voice stream cannot update UI or start Piper', async () => {
    const f = fixture();
    await f.context.startActiveListening(false);
    f.read("fullTranscript = 'Consulta de prueba'");
    const chunk = deferred();
    f.io.fetch = async () => ({ ok: true, body: { getReader: () => ({
        read: () => chunk.promise, async cancel() {},
    }) } });
    const processing = f.context.processVoiceCommand();
    await settle();
    const previousSegments = f.assistant.slice();
    await f.context.startLiveVoiceStream();
    chunk.resolve({ done: true, value: new TextEncoder().encode(
        'data: {"type":"token","text":"Respuesta obsoleta"}\n\n'
        + 'data: {"type":"done","response":"Respuesta obsoleta","outcome":"succeeded"}\n\n',
    ) });
    await processing;
    assert.deepEqual(f.assistant, previousSegments);
    assert.deepEqual(f.requests.map(request => request.url), ['/api/chat/stream']);
    assert.equal(f.read('currentMode'), 'active');
});

test('obsolete text-only voice failure cannot retry the command with postChat', async () => {
    const f = fixture();
    await f.context.startActiveListening(false);
    f.read("fullTranscript = 'Consulta de prueba'");
    const response = deferred();
    f.io.fetch = () => response.promise;
    const processing = f.context.processVoiceCommand();
    await settle();
    await f.context.startLiveVoiceStream();
    response.reject(new Error('network unavailable'));
    await processing;
    assert.deepEqual(f.chatCalls, []);
    assert.deepEqual(f.requests.map(request => request.url), ['/api/chat/stream']);
    assert.equal(f.read('currentMode'), 'active');
});

test('cancelling Live during the status request cannot create a late client', async () => {
    const f = fixture();
    const status = deferred();
    f.io.status = () => status.promise;
    const starting = f.context.startLiveVoiceStream();
    await f.context.stopLiveVoiceStream({ resumePassive: false });
    status.resolve({});
    await starting;
    assert.equal(f.clients.length, 0);
    assert.equal(f.dom.liveVoiceBtn.disabled, false);
    assert.equal(f.read('currentMode'), 'idle');
});

test('Live shutdown waits for pending capture and ignores callbacks from its old client', async () => {
    const f = fixture();
    const capture = deferred();
    f.io.startLive = () => capture.promise;
    const starting = f.context.startLiveVoiceStream();
    await settle();
    const oldClient = f.clients[0];
    const stopping = f.context.stopLiveVoiceStream();
    await settle();
    await f.context.startActiveListening(true, { forceFromProcessing: true });
    assert.equal(f.captures.length, 0);
    capture.resolve();
    await starting;
    await stopping;
    assert.equal(f.read('isLiveVoiceActive'), false);
    assert.equal(f.read('liveVoiceClient'), null);
    assert.equal(f.read('currentMode'), 'passive');
    oldClient.onStateChange('speaking');
    oldClient.onTranscript({ text: 'Evento obsoleto', role: 'assistant', is_final: true });
    assert.equal(f.read('currentMode'), 'passive');
    assert.notEqual(f.dom.transcriptText.textContent, 'Evento obsoleto');
});

test('Live handoff settles a pending recorder flush and invalidates its queued onstop', async () => {
    const f = fixture();
    const recorder = await f.classic();
    const processing = f.context.processVoiceCommand();
    const stopped = recorder.onstop;
    await f.context.startLiveVoiceStream();
    await processing;
    await f.context.stopLiveVoiceStream();
    const fresh = await f.classic();
    await stopped();
    assert.equal(f.read('mediaRecorder'), fresh);
    assert.equal(f.read('audioChunks.length'), 1);
    assert.deepEqual(f.requests, []);
});

test('classic follow-up still starts one new capture when should_listen is true', async () => {
    const f = fixture();
    const recorder = await f.classic();
    f.read("fullTranscript = 'Consulta de prueba'");
    const fetch = f.io.fetch;
    f.io.fetch = url => url === '/api/voice'
        ? voiceResponse({ should_listen: true }) : fetch(url);
    await f.finish(recorder);
    await settle();
    f.audio[0].onended();
    await settle();
    assert.equal(f.captures.length, 2);
    assert.equal(f.read('currentMode'), 'active');
    assert.equal(f.recordings.at(-1).state, 'recording');
});

test('voice enrollment cannot take the microphone from Live', async () => {
    const f = fixture();
    await f.context.startLiveVoiceStream();
    await f.context.runAdminEnrollmentWizard();
    assert.deepEqual(f.requests, []);
    assert.equal(f.captures.length, 0);
    assert.equal(f.read('currentMode'), 'active');
});

test('a recorder conversion rejection after handoff settles the obsolete processing promise', async () => {
    const f = fixture();
    const recorder = await f.classic();
    const conversion = deferred();
    f.io.convert = () => conversion.promise;
    let settled = false;
    const processing = f.context.processVoiceCommand().then(() => { settled = true; });
    const stopped = Promise.resolve(recorder.onstop()).catch(() => {});
    await f.context.startLiveVoiceStream();
    conversion.reject(new Error('decoder unavailable'));
    await stopped;
    await settle();
    assert.equal(settled, true);
    await processing;
    assert.deepEqual(f.requests, []);
    assert.equal(f.read('currentMode'), 'active');
});
