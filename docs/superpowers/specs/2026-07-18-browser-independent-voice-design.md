# Browser-Independent Voice Transcription Design

**Date:** 2026-07-18
**Status:** Pending written review

## Objective

Make JARVIS voice commands continue to work when a browser can capture microphone
audio but does not implement the Web Speech `SpeechRecognition` API. Preserve the
existing voice-link button and wake-word behavior where supported, while making
browser speech recognition an optional latency optimization instead of a runtime
requirement.

The approved scope includes recommendations 1, 2, 3, 5, and 6 from the voice
compatibility review. A new push-to-talk mode or control, recommendation 4, is
explicitly excluded.

## Current Failure

The frontend currently uses two independent browser capabilities:

1. `getUserMedia` and `MediaRecorder` capture the audio stream.
2. `SpeechRecognition` produces the command text and detects the wake word.

When `SpeechRecognition` is unavailable or its remote recognition service fails,
the browser can still record valid audio. However, `processVoiceCommand` exits
before reading and sending the recorded audio when the browser transcript is
empty. The backend contains a Whisper fallback path, but the runtime injects
`whisper_model = None`, so that path cannot currently recover the command.

This is a capability mismatch, not a general `localhost` failure. `localhost`
is a secure context for microphone capture, but permission, operating-system
policy, audio-device state, and speech-recognition support remain independent.

## Considered Approaches

### A. Capability-adaptive browser capture and backend transcription

Keep browser audio capture and the current UI. Treat browser speech text as a
fast hint, always retain the recorded audio, and send audio to the backend when
the hint is missing or unreliable. The backend selects browser, Groq, or local
Whisper transcription in a controlled order.

This is the selected approach. It provides broad browser compatibility without
adding native audio dependencies or changing the interaction model.

### B. Chromium-only support policy

Require Edge or Chrome and display an unsupported-browser message elsewhere.
This is the smallest change, but it leaves JARVIS dependent on a limited,
sometimes remote browser speech service and does not satisfy general-use goals.

### C. Native Python microphone capture

Move capture, wake-word detection, and transcription entirely into the desktop
host. This gives the host maximum control but introduces platform audio drivers,
device ownership, packaging work, and a larger cross-platform test surface. It
is deferred unless browser media capture itself proves unreliable after the
selected approach ships.

## Selected Architecture

### Frontend capability layer

A focused voice-capability module will report these capabilities independently:

- secure context
- `navigator.mediaDevices.getUserMedia`
- `MediaRecorder`
- `SpeechRecognition` or `webkitSpeechRecognition`
- optional microphone permission state when the browser exposes it

Capability detection must not request microphone permission on page load.
Permission remains tied to the user's existing voice-link button action.

`SpeechRecognition` remains available for interim text, low-latency command
hints, and passive wake-word detection in browsers that support it. Its absence
must not block active audio capture. The existing button continues to start and
finish active listening; no new push-to-talk mode or control will be created.

### Audio submission

Active listening will retain `MediaRecorder` chunks regardless of whether a
browser transcript is produced. On completion:

1. Stop the stream and recorder safely.
2. Build the existing WAV or supported recorded-audio payload.
3. Send audio to `/api/voice` with the browser transcript and confidence when
   available.
4. If audio capture failed but a valid browser transcript exists, use the
   existing text chat path.
5. If neither audio nor text exists, return to a stable idle/passive state and
   show one actionable diagnostic rather than starting a retry loop.

The existing backend audio-size limits, origin checks, and critical-route
security remain in force.

### Backend transcription coordinator

A dedicated transcription component will own provider selection and lazy model
state. It will return a normalized result containing text and a non-sensitive
source identifier. In `auto` mode, selection order is:

1. A non-empty, sufficiently reliable browser transcript.
2. Groq Speech-to-Text when `GROQ_API_KEY` is configured.
3. Local `faster-whisper` when local fallback is enabled.
4. A controlled unavailable result when every source fails.

Groq will use the existing OpenAI-compatible client dependency and the documented
`whisper-large-v3-turbo` default. The API key remains server-side. Requests use
bounded timeouts and retries, and logs expose only the provider name and exception
class, never raw provider responses, URLs with credentials, or API keys.

The local model will load lazily on the first request that reaches local fallback
and will be protected by a lock so simultaneous requests do not load duplicate
models. The existing `JARVIS_WHISPER_MODEL`, device, compute-type, beam-size, and
language settings remain the local tuning contract. Model-load failure is cached
for the current process and reported as controlled unavailability instead of
crashing the backend.

Configuration will expose an explicit provider policy, Groq STT model, and local
fallback flag. `auto` is the supported default. No additional runtime dependency
is required.

### Diagnostics and error handling

The frontend will map microphone failures to stable categories:

- `permission_denied`: user, browser, or operating-system permission denial
- `device_missing`: no matching input device
- `device_busy`: device or driver cannot provide a stream
- `insecure_context`: microphone APIs unavailable outside a secure origin
- `capture_unsupported`: media capture or recording API unavailable
- `browser_recognition_unavailable`: audio capture remains usable through backend STT
- `recognition_network`: browser recognition service failed; backend STT is used
- `transcription_unavailable`: browser, Groq, and local Whisper produced no text

Terminal device/permission errors stop automatic recognition retries until the
user explicitly presses the existing voice-link button again. Browser recognition
absence or a recognition-network error is non-terminal when audio capture works.

User-facing messages will be translated through the existing English/Spanish
i18n map. Detailed internal exceptions stay in structured logs with sanitized
fields.

### Runtime status and support contract

The status/setup response will expose only safe STT readiness information:

- configured provider policy
- whether Groq STT is configured
- whether local fallback is enabled
- whether the local model is loaded, not loaded, or unavailable

The documented support policy becomes:

- Windows desktop: `start_app.py` with persistent WebView2 storage is the primary
  supported experience.
- Current Edge and Chrome: browser recognition may provide wake word and interim
  text; backend STT remains a fallback.
- Firefox and Safari: microphone capture plus backend STT is supported when their
  media APIs are available; passive browser wake-word detection is best effort.
- HTTP is supported only on loopback `localhost`. Non-loopback or LAN access must
  use HTTPS before microphone support is expected.
- Text input remains the final accessible fallback on every platform.

JARVIS will consistently document `http://localhost:5002` for the application
origin so browser permission records are not split between `localhost` and
`127.0.0.1`.

## Component Boundaries

- `src/frontend/static/js/modules/voice-capabilities.js`: feature detection and
  stable microphone/recognition diagnostic classification.
- `src/frontend/static/js/main.js`: existing voice state machine, audio lifecycle,
  backend submission, and fallback selection.
- `src/frontend/static/js/i18n.js`: translated diagnostics.
- `src/backend/voice/transcription.py`: provider-independent transcription result,
  Groq adapter, lazy local Whisper adapter, and provider coordinator.
- `src/backend/voice/service.py`: consume the coordinator and preserve voice
  identity and response behavior.
- `src/backend/core/jarvis_config.py`: STT provider settings.
- `src/backend/api/status_routes.py`: sanitized readiness reporting.
- `.env.example`, `README.md`, and `AGENTS.md`: installation, support, and
  troubleshooting contract.

Existing compatibility shims will be changed only where required to route through
the coordinator. Voice registration and biometrics continue to receive captured
audio; they are not redesigned by this work.

## Testing Strategy

### Frontend unit and source-contract tests

- Detect each independent browser capability with mocked globals.
- Classify permission, missing-device, busy-device, insecure-context, unsupported
  capture, and recognition-network failures.
- Prove browser recognition absence does not mark microphone capture as blocked.
- Prove active recorded audio is submitted when the transcript is empty.
- Prove valid text remains usable when no audio payload exists.
- Prove permission and device failures do not create passive restart loops.

### Backend unit tests

- Reliable browser hints bypass remote and local transcription.
- Missing or unreliable hints call Groq first in `auto` mode.
- Groq failure falls back to local Whisper.
- Local Whisper loads once under concurrent access.
- Explicit provider settings honor their configured boundaries.
- Every-provider failure returns a controlled empty/unavailable result.
- Provider exceptions and status output do not expose secrets or raw network details.

### Integration and release checks

- `/api/voice` accepts valid audio with an empty transcript hint and reaches the
  transcription coordinator.
- Existing voice identity behavior remains covered with browser hints and audio.
- Core-mode startup works with and without `GROQ_API_KEY`.
- JavaScript syntax, Python compilation, focused Ruff checks, installation
  contracts, full pytest, `pip check`, and `pip-audit` pass.
- Browser QA verifies the existing button in WebView2/Chromium and simulates a
  missing `SpeechRecognition` capability.

All provider network calls are mocked in automated tests. Live microphone and
audio-hardware validation remains a separately reported manual check.

## Non-Goals

- No new push-to-talk button, mode, or interaction.
- No native microphone-capture service in Python.
- No new wake-word engine or wake-word dependency.
- No automatic microphone permission grant.
- No claim that passive wake-word recognition works in browsers without a speech
  recognition implementation.
- No removal of text chat or browser speech hints.
- No unrelated visual redesign or voice-biometric refactor.

## Acceptance Criteria

The work is complete when all of the following are true:

- A browser with `getUserMedia` and `MediaRecorder`, but without
  `SpeechRecognition`, can submit an active voice command through the existing
  voice-link button and receive a JARVIS response.
- Recorded audio is not discarded solely because the browser transcript is empty.
- `auto` transcription uses browser, Groq, and local Whisper in the documented
  order and degrades without crashing.
- Microphone diagnostics distinguish permissions, missing hardware, busy devices,
  insecure contexts, unsupported capture, and recognition-service failures.
- Automatic recognition retries stop for terminal microphone errors.
- No microphone permission is requested before a user action.
- Existing Chromium wake-word and interim-transcript behavior remains intact.
- Documentation states the tested browser/platform contract and HTTPS requirement
  for non-loopback use.
- No new runtime dependency or new push-to-talk mode is introduced.
- The complete regression and release verification matrix passes.

## Primary References

- https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition
- https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia
- https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2.permissionrequested
- https://console.groq.com/docs/speech-to-text
