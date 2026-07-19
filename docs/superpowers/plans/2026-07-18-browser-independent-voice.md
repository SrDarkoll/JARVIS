# Browser-Independent Voice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make existing JARVIS voice commands work when microphone capture is available but browser `SpeechRecognition` is missing or fails, without adding a new push-to-talk mode.

**Architecture:** Keep the current browser capture and voice-link interaction, but always preserve recorded audio and make browser speech text optional. A backend transcription coordinator will select a reliable browser hint, Groq Speech-to-Text, or a lazy local `faster-whisper` model, while frontend capability diagnostics distinguish capture failures from recognition-service failures.

**Tech Stack:** Python 3.11/3.12, Quart, OpenAI-compatible Groq API, faster-whisper, vanilla JavaScript ES modules, MediaRecorder, pytest, Node.js syntax/module tests.

---

## File Map

- `src/backend/core/jarvis_config.py`: validated STT environment contract.
- `src/backend/voice/transcription.py`: browser/Groq/local provider coordination and lazy model state.
- `src/backend/voice/service.py`: consume transcription results during the existing identity flow.
- `src/backend/voice/voice_response.py`: include a safe transcription source in diagnostics.
- `src/backend/api/voice_routes.py`: inject the coordinator into the voice domain service.
- `src/backend/api/status_routes.py`: expose sanitized STT readiness.
- `src/backend/jarvis_backend.py`: build one coordinator and inject its snapshot.
- `src/frontend/static/js/modules/voice-capabilities.js`: capability and error classification.
- `src/frontend/static/js/modules/recognition-policy.js`: stop retries when browser recognition degrades.
- `src/frontend/static/js/main.js`: retain audio without browser text and route all fallback states.
- `src/frontend/static/js/i18n.js`: English/Spanish voice diagnostics.
- `tests/test_voice_transcription.py`: provider order, lazy loading, sanitization, and configuration tests.
- `tests/test_frontend_voice_resilience.py`: capability, diagnostic, retry, and source-contract tests.
- `tests/test_smoke.py`: `/api/voice` and `/api/status` integration coverage.
- `.env.example`, `README.md`, `AGENTS.md`: provider settings and browser/platform support contract.

## Task 1: Define the Speech-to-Text configuration contract

**Files:**
- Modify: `src/backend/core/jarvis_config.py`
- Create: `tests/test_voice_transcription.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing configuration tests**

Create `tests/test_voice_transcription.py` with these initial tests:

```python
from __future__ import annotations


def test_stt_config_defaults_to_auto_with_local_fallback():
    from core.jarvis_config import resolve_speech_to_text_config

    config = resolve_speech_to_text_config({})

    assert config.provider == "auto"
    assert config.groq_model == "whisper-large-v3-turbo"
    assert config.local_enabled is True
    assert config.local_model == "medium"
    assert config.local_device == "cpu"
    assert config.local_compute_type == "int8"
    assert config.timeout_seconds == 20.0


def test_stt_config_normalizes_invalid_values():
    from core.jarvis_config import resolve_speech_to_text_config

    config = resolve_speech_to_text_config(
        {
            "JARVIS_STT_PROVIDER": "unknown",
            "JARVIS_LOCAL_STT_ENABLED": "no",
            "JARVIS_STT_TIMEOUT_SECONDS": "500",
        }
    )

    assert config.provider == "auto"
    assert config.local_enabled is False
    assert config.timeout_seconds == 60.0


def test_stt_config_accepts_explicit_provider_modes():
    from core.jarvis_config import resolve_speech_to_text_config

    assert resolve_speech_to_text_config({"JARVIS_STT_PROVIDER": "browser"}).provider == "browser"
    assert resolve_speech_to_text_config({"JARVIS_STT_PROVIDER": "groq"}).provider == "groq"
    assert resolve_speech_to_text_config({"JARVIS_STT_PROVIDER": "local"}).provider == "local"
```

- [ ] **Step 2: Run the tests and confirm the missing contract**

Run:

```powershell
python -m pytest tests\test_voice_transcription.py -q
```

Expected: collection succeeds and all three tests fail because `resolve_speech_to_text_config` does not exist.

- [ ] **Step 3: Implement validated STT settings**

Add this contract to `core/jarvis_config.py` after `RuntimeFeatures`:

```python
@dataclass(frozen=True)
class SpeechToTextConfig:
    provider: str
    groq_model: str
    local_enabled: bool
    local_model: str
    local_device: str
    local_compute_type: str
    timeout_seconds: float


def _read_float(
    env: Mapping[str, str], name: str, default: float, minimum: float, maximum: float
) -> float:
    try:
        value = float(str(env.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def resolve_speech_to_text_config(
    env: Mapping[str, str] | None = None,
) -> SpeechToTextConfig:
    source = os.environ if env is None else env
    provider = str(source.get("JARVIS_STT_PROVIDER", "auto") or "auto").strip().lower()
    if provider not in {"auto", "browser", "groq", "local"}:
        provider = "auto"
    return SpeechToTextConfig(
        provider=provider,
        groq_model=str(
            source.get("JARVIS_GROQ_STT_MODEL", "whisper-large-v3-turbo")
            or "whisper-large-v3-turbo"
        ).strip(),
        local_enabled=_read_bool(source, "JARVIS_LOCAL_STT_ENABLED", True),
        local_model=str(source.get("JARVIS_WHISPER_MODEL", "medium") or "medium").strip(),
        local_device=str(source.get("JARVIS_WHISPER_DEVICE", "cpu") or "cpu").strip(),
        local_compute_type=str(
            source.get("JARVIS_WHISPER_COMPUTE_TYPE", "int8") or "int8"
        ).strip(),
        timeout_seconds=_read_float(
            source, "JARVIS_STT_TIMEOUT_SECONDS", 20.0, 5.0, 60.0
        ),
    )
```

Initialize `SPEECH_TO_TEXT = resolve_speech_to_text_config()` after `load_dotenv`
and document these values in `.env.example`:

```dotenv
JARVIS_STT_PROVIDER="auto"
JARVIS_GROQ_STT_MODEL="whisper-large-v3-turbo"
JARVIS_LOCAL_STT_ENABLED="true"
JARVIS_STT_TIMEOUT_SECONDS="20"
```

Keep the existing `JARVIS_WHISPER_*` settings directly below them.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python -m pytest tests\test_voice_transcription.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit the configuration contract**

```powershell
git add src/backend/core/jarvis_config.py tests/test_voice_transcription.py .env.example
git commit -m "feat: define voice transcription configuration"
```

## Task 2: Implement the provider coordinator and lazy local model

**Files:**
- Create: `src/backend/voice/transcription.py`
- Modify: `tests/test_voice_transcription.py`

- [ ] **Step 1: Add failing provider-order and local-state tests**

Append tests using small injected fakes rather than network or model downloads:

```python
from concurrent.futures import ThreadPoolExecutor


class StubTranscriber:
    def __init__(self, text: str = "", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls: list[tuple[bytes, str]] = []

    def transcribe(self, audio_bytes: bytes, language: str) -> str:
        self.calls.append((audio_bytes, language))
        if self.error:
            raise self.error
        return self.text


def test_coordinator_uses_reliable_browser_hint_first():
    from voice.transcription import TranscriptionCoordinator

    groq = StubTranscriber("remote")
    local = StubTranscriber("local")
    coordinator = TranscriptionCoordinator("auto", groq=groq, local=local)

    result = coordinator.transcribe(
        b"wav", "play relaxing music", 0.91, route_mode="secure", language="en"
    )

    assert (result.text, result.source) == ("play relaxing music", "browser")
    assert groq.calls == []
    assert local.calls == []


def test_coordinator_falls_from_groq_to_local():
    from voice.transcription import TranscriptionCoordinator

    groq = StubTranscriber(error=RuntimeError("provider detail must stay private"))
    local = StubTranscriber("local transcript")
    coordinator = TranscriptionCoordinator("auto", groq=groq, local=local)

    result = coordinator.transcribe(
        b"wav", "", None, route_mode="secure", language="en"
    )

    assert (result.text, result.source) == ("local transcript", "local")
    assert len(groq.calls) == 1
    assert len(local.calls) == 1


def test_explicit_provider_does_not_cross_provider_boundary():
    from voice.transcription import TranscriptionCoordinator

    groq = StubTranscriber("remote")
    local = StubTranscriber("local")
    coordinator = TranscriptionCoordinator("local", groq=groq, local=local)

    result = coordinator.transcribe(b"wav", "", None, language="es")

    assert (result.text, result.source) == ("local", "local")
    assert groq.calls == []


def test_all_provider_failures_return_controlled_unavailable():
    from voice.transcription import TranscriptionCoordinator

    coordinator = TranscriptionCoordinator(
        "auto",
        groq=StubTranscriber(error=ConnectionError("secret endpoint")),
        local=StubTranscriber(error=RuntimeError("model path")),
    )

    result = coordinator.transcribe(b"wav", "", None, language="en")

    assert (result.text, result.source) == ("", "unavailable")


def test_lazy_whisper_loads_once_for_concurrent_requests(tmp_path):
    from types import SimpleNamespace
    from voice.transcription import LazyWhisperTranscriber

    loads: list[tuple[str, str, str]] = []

    class FakeModel:
        def transcribe(self, _path, **_kwargs):
            return [SimpleNamespace(text=" local text ", start=0.0, end=1.0)], None

    def loader(model: str, device: str, compute_type: str):
        loads.append((model, device, compute_type))
        return FakeModel()

    local = LazyWhisperTranscriber(
        enabled=True,
        model_name="tiny",
        device="cpu",
        compute_type="int8",
        runtime_dir=tmp_path,
        model_loader=loader,
    )

    with ThreadPoolExecutor(max_workers=4) as pool:
        texts = list(pool.map(lambda _: local.transcribe(b"RIFFaudio", "en"), range(4)))

    assert texts == ["local text"] * 4
    assert loads == [("tiny", "cpu", "int8")]
    assert local.snapshot()["state"] == "loaded"
```

- [ ] **Step 2: Run tests and confirm missing implementation**

Run:

```powershell
python -m pytest tests\test_voice_transcription.py -q
```

Expected: the three configuration tests pass and new tests fail with
`ModuleNotFoundError: No module named 'voice.transcription'`.

- [ ] **Step 3: Implement `voice/transcription.py`**

Create these public types and behavior:

```python
from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from core.runtime_logger import log_warning
from voice.pipeline import (
    hint_necesita_reintento_whisper,
    normalizar_transcript_hint,
    reconstruir_transcripcion_por_pausas,
)


class AudioTranscriber(Protocol):
    def transcribe(self, audio_bytes: bytes, language: str) -> str: ...


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    source: str


def _browser_hint_is_reliable(hint: str, confidence, route_mode: str) -> bool:
    normalized = normalizar_transcript_hint(hint)
    if not normalized:
        return False
    if route_mode == "fast_info" and len(normalized.split()) >= 3:
        return True
    return not hint_necesita_reintento_whisper(normalized, confidence)


class GroqAudioTranscriber:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        *,
        client: Any | None = None,
    ):
        self._api_key = str(api_key or "").strip()
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._api_key or self._client is not None)

    def _get_client(self):
        with self._lock:
            if self._client is None:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=self._api_key,
                    base_url="https://api.groq.com/openai/v1",
                    timeout=self._timeout_seconds,
                    max_retries=1,
                )
            return self._client

    def transcribe(self, audio_bytes: bytes, language: str) -> str:
        if not self.configured or not audio_bytes:
            return ""
        response = self._get_client().audio.transcriptions.create(
            file=("jarvis-voice.wav", audio_bytes, "audio/wav"),
            model=self._model,
            language=language,
            response_format="json",
            temperature=0,
        )
        if isinstance(response, dict):
            return normalizar_transcript_hint(response.get("text", ""))
        return normalizar_transcript_hint(getattr(response, "text", ""))
```

Implement `LazyWhisperTranscriber` with an `RLock`, states `not_loaded`, `loaded`,
`disabled`, and `unavailable`, a cached model-load failure, a unique temporary WAV
under `runtime_dir`, and guaranteed cleanup. Its default loader imports
`faster_whisper.WhisperModel` only inside the lock. Its `snapshot()` returns only
`{"enabled": bool, "state": str}`.

Implement `TranscriptionCoordinator` with provider order:

```python
class TranscriptionCoordinator:
    def __init__(self, provider: str, *, groq: AudioTranscriber | None, local: AudioTranscriber | None):
        self.provider = provider if provider in {"auto", "browser", "groq", "local"} else "auto"
        self.groq = groq
        self.local = local

    def transcribe(
        self,
        audio_bytes: bytes,
        transcript_hint: str,
        transcript_confidence=None,
        *,
        route_mode: str = "secure",
        language: str = "en",
    ) -> TranscriptionResult:
        hint = normalizar_transcript_hint(transcript_hint)
        if _browser_hint_is_reliable(hint, transcript_confidence, route_mode):
            return TranscriptionResult(hint, "browser")

        providers = {
            "auto": (("groq", self.groq), ("local", self.local)),
            "browser": (),
            "groq": (("groq", self.groq),),
            "local": (("local", self.local),),
        }[self.provider]
        for source, transcriber in providers:
            if transcriber is None:
                continue
            try:
                text = normalizar_transcript_hint(
                    transcriber.transcribe(audio_bytes, language)
                )
            except Exception as exc:
                log_warning(
                    "voice_transcription_provider_failed",
                    provider=source,
                    error=type(exc).__name__,
                )
                continue
            if text:
                return TranscriptionResult(text, source)
        return TranscriptionResult("", "unavailable")
```

Add a `snapshot()` method that reports provider, Groq configuration, local enabled,
and local state without keys or paths. Add `build_transcription_coordinator(config,
groq_api_key, runtime_dir)` to construct the real adapters.

- [ ] **Step 4: Run focused tests and Ruff**

```powershell
python -m pytest tests\test_voice_transcription.py -q
python -m ruff check src\backend\voice\transcription.py tests\test_voice_transcription.py --select F
```

Expected: all tests pass and Ruff reports success.

- [ ] **Step 5: Commit the coordinator**

```powershell
git add src/backend/voice/transcription.py tests/test_voice_transcription.py
git commit -m "feat: add adaptive voice transcription coordinator"
```

## Task 3: Integrate transcription with voice processing and status

**Files:**
- Modify: `src/backend/jarvis_backend.py`
- Modify: `src/backend/api/voice_routes.py`
- Modify: `src/backend/api/status_routes.py`
- Modify: `src/backend/voice/service.py`
- Modify: `src/backend/voice/voice_response.py`
- Modify: `tests/test_smoke.py`
- Modify: `tests/test_voice_transcription.py`

- [ ] **Step 1: Write failing integration tests**

Add a service-level test proving an empty browser hint uses the injected coordinator
and a status test proving no secret is exposed:

```python
def test_voice_service_uses_transcription_coordinator_for_empty_hint(monkeypatch):
    from types import SimpleNamespace
    from voice import service as voice_service
    from voice.transcription import TranscriptionResult

    calls = []

    class Coordinator:
        def transcribe(self, audio_bytes, transcript_hint, transcript_confidence, **kwargs):
            calls.append((audio_bytes, transcript_hint, transcript_confidence, kwargs))
            return TranscriptionResult("backend transcript", "groq")

    monkeypatch.setattr(voice_service, "_transcription_service", Coordinator())
    result = voice_service._transcribe_command(
        b"wav", "", None, route_mode="secure", language="en"
    )

    assert result == TranscriptionResult("backend transcript", "groq")
    assert calls[0][1] == ""


def test_transcription_snapshot_contains_no_api_key():
    from core.jarvis_config import resolve_speech_to_text_config
    from voice.transcription import build_transcription_coordinator

    coordinator = build_transcription_coordinator(
        resolve_speech_to_text_config({}), "gsk_secret_value", "."
    )

    snapshot = coordinator.snapshot()
    assert snapshot["provider"] == "auto"
    assert snapshot["groq_configured"] is True
    assert "gsk_secret_value" not in repr(snapshot)
```

In `tests/test_smoke.py`, extend the status assertion with:

```python
assert data["speech_to_text"]["provider"] in {"auto", "browser", "groq", "local"}
assert isinstance(data["speech_to_text"]["groq_configured"], bool)
assert data["speech_to_text"]["local_state"] in {
    "not_loaded", "loaded", "disabled", "unavailable"
}
```

- [ ] **Step 2: Run tests and confirm missing integration**

```powershell
python -m pytest tests\test_voice_transcription.py tests\test_smoke.py::test_status_endpoint_reports_runtime_mode -q
```

Expected: failures because `_transcribe_command` and `speech_to_text` status do not
exist.

- [ ] **Step 3: Build and inject one coordinator**

In `jarvis_backend.py`, build the service after TTS initialization:

```python
from voice.transcription import build_transcription_coordinator

transcription_service = build_transcription_coordinator(
    jarvis_config.SPEECH_TO_TEXT,
    jarvis_config.GROQ_API_KEY,
    os.getenv("JARVIS_RUNTIME_DIR") or BASE_DIR,
)
```

Add `transcription_service` to `VoiceRoutesConfig`; store it as
`_transcription_service` in `voice_routes.py`; synchronize it into
`voice/service.py` alongside `_whisper_model`.

Add a small compatibility boundary in `voice/service.py`:

```python
def _transcribe_command(
    audio_bytes: bytes,
    transcript_hint: str,
    transcript_confidence,
    *,
    route_mode: str,
    language: str,
):
    if _transcription_service is not None:
        return _transcription_service.transcribe(
            audio_bytes,
            transcript_hint,
            transcript_confidence,
            route_mode=route_mode,
            language=language,
        )
    text = _capture_transcribir_dudoso(
        audio_bytes,
        transcript_hint=transcript_hint,
        whisper_model=_whisper_model,
        transcript_confidence=transcript_confidence,
        route_mode=route_mode,
    )
    from voice.transcription import TranscriptionResult

    return TranscriptionResult(text, "browser" if text == transcript_hint else "local")
```

Use the result at the current transcription point and set
`voice_debug["transcription_source"]`. Extend `voice_response.build_voice_debug`
and `_voice_response_for_debug` so `identity_debug.transcription_source` and the
top-level `transcription_source` are always safe strings.

- [ ] **Step 4: Inject sanitized STT status**

Add `transcription_snapshot_fn` to `StatusRoutesConfig`. Store it during
`init_status_routes` and return this fallback-safe block from `/api/status`:

```python
def _speech_to_text_status() -> dict:
    fallback = {
        "provider": "auto",
        "groq_configured": False,
        "local_enabled": False,
        "local_state": "unavailable",
    }
    if not callable(_transcription_snapshot):
        return fallback
    try:
        return {**fallback, **(_transcription_snapshot() or {})}
    except Exception as exc:
        log_warning("transcription_status_failed", error=type(exc).__name__)
        return fallback
```

Pass `transcription_service.snapshot` from `jarvis_backend.py` and add
`"speech_to_text": _speech_to_text_status()` to the status payload.

- [ ] **Step 5: Run backend regressions**

```powershell
python -m pytest tests\test_voice_transcription.py tests\test_i18n_regressions.py tests\test_smoke.py::test_api_status tests\test_smoke.py::test_voice_http_route_delegates_processing_to_voice_service -q
```

Expected: all selected tests pass; legacy transcription shims remain callable.

- [ ] **Step 6: Commit backend integration**

```powershell
git add src/backend/jarvis_backend.py src/backend/api/voice_routes.py src/backend/api/status_routes.py src/backend/voice/service.py src/backend/voice/voice_response.py tests/test_smoke.py tests/test_voice_transcription.py
git commit -m "feat: route voice audio through adaptive transcription"
```

## Task 4: Add browser capability and diagnostic classification

**Files:**
- Create: `src/frontend/static/js/modules/voice-capabilities.js`
- Modify: `src/frontend/static/js/modules/recognition-policy.js`
- Modify: `tests/test_frontend_voice_resilience.py`

- [ ] **Step 1: Add failing JavaScript module tests**

Extend the Python Node helper to load both modules, then assert:

```javascript
const complete = capabilities.detectVoiceCapabilities({
  isSecureContext: true,
  navigator: { mediaDevices: { getUserMedia() {} } },
  MediaRecorder: function MediaRecorder() {},
  SpeechRecognition: function SpeechRecognition() {},
});
if (!complete.canCaptureAudio || !complete.hasBrowserRecognition) process.exit(1);

const firefoxLike = capabilities.detectVoiceCapabilities({
  isSecureContext: true,
  navigator: { mediaDevices: { getUserMedia() {} } },
  MediaRecorder: function MediaRecorder() {},
});
if (!firefoxLike.canCaptureAudio || firefoxLike.hasBrowserRecognition) process.exit(1);

const cases = new Map([
  ['NotAllowedError', 'permission_denied'],
  ['NotFoundError', 'device_missing'],
  ['NotReadableError', 'device_busy'],
  ['SecurityError', 'insecure_context'],
  ['network', 'recognition_network'],
]);
for (const [error, expected] of cases) {
  if (capabilities.classifyVoiceError(error) !== expected) process.exit(1);
}
```

Update restart assertions so recognition does not restart when
`browserRecognitionDegraded` is true.

- [ ] **Step 2: Run tests and confirm missing module/signature**

```powershell
python -m pytest tests\test_frontend_voice_resilience.py -q
```

Expected: failures because `voice-capabilities.js` and the degraded-state policy
do not exist.

- [ ] **Step 3: Implement pure capability helpers**

Create `voice-capabilities.js`:

```javascript
const ERROR_KIND = new Map([
    ['not-allowed', 'permission_denied'],
    ['service-not-allowed', 'permission_denied'],
    ['notallowederror', 'permission_denied'],
    ['notfounderror', 'device_missing'],
    ['audio-capture', 'device_busy'],
    ['notreadableerror', 'device_busy'],
    ['securityerror', 'insecure_context'],
    ['network', 'recognition_network'],
]);

export function classifyVoiceError(errorType) {
    return ERROR_KIND.get(String(errorType || '').trim().toLowerCase()) || 'unknown';
}

export function detectVoiceCapabilities(scope = globalThis) {
    const mediaDevices = scope?.navigator?.mediaDevices;
    const hasGetUserMedia = typeof mediaDevices?.getUserMedia === 'function';
    const hasMediaRecorder = typeof scope?.MediaRecorder === 'function';
    const hasBrowserRecognition = typeof (
        scope?.SpeechRecognition || scope?.webkitSpeechRecognition
    ) === 'function';
    const secureContext = scope?.isSecureContext !== false;
    return {
        secureContext,
        hasGetUserMedia,
        hasMediaRecorder,
        hasBrowserRecognition,
        canCaptureAudio: secureContext && hasGetUserMedia && hasMediaRecorder,
    };
}
```

Extend `shouldRestartPassiveRecognition` with a fourth
`browserRecognitionDegraded` argument and require it to be false.

- [ ] **Step 4: Run frontend unit tests and syntax checks**

```powershell
python -m pytest tests\test_frontend_voice_resilience.py -q
node --check src\frontend\static\js\modules\voice-capabilities.js
node --check src\frontend\static\js\modules\recognition-policy.js
```

Expected: all tests pass and syntax checks are silent.

- [ ] **Step 5: Commit capability helpers**

```powershell
git add src/frontend/static/js/modules/voice-capabilities.js src/frontend/static/js/modules/recognition-policy.js tests/test_frontend_voice_resilience.py
git commit -m "feat: classify browser voice capabilities"
```

## Task 5: Preserve and submit audio without browser recognition

**Files:**
- Modify: `src/frontend/static/js/main.js`
- Modify: `src/frontend/static/js/i18n.js`
- Modify: `tests/test_frontend_voice_resilience.py`

- [ ] **Step 1: Add failing frontend source-contract tests**

Add focused assertions that production code imports capability helpers, does not
exit before flushing recorded audio, and posts audio with an empty transcript:

```python
def test_main_submits_audio_when_browser_transcript_is_empty():
    source = (ROOT / "src/frontend/static/js/main.js").read_text(encoding="utf-8")

    assert "detectVoiceCapabilities" in source
    assert "classifyVoiceError" in source
    assert "browserRecognitionDegraded" in source
    assert "const audioBlob = await stopBiometricRecording();" in source
    assert source.index("const audioBlob = await stopBiometricRecording();") < source.index(
        "if (!transcript && !hasAudio)"
    )
    assert "if (audioBlob && audioBlob.size > 1000)" in source
    assert "'X-Transcript': encodeURIComponent(transcript)" in source


def test_main_does_not_add_a_new_push_to_talk_mode():
    source = (ROOT / "src/frontend/static/js/main.js").read_text(encoding="utf-8").lower()
    template = (ROOT / "src/frontend/templates/index.html").read_text(encoding="utf-8").lower()

    assert "push-to-talk" not in source
    assert "push-to-talk" not in template
    assert 'id="ptt"' not in template
    assert 'data-mode="push-to-talk"' not in template
```

- [ ] **Step 2: Run tests and confirm current early exit**

```powershell
python -m pytest tests\test_frontend_voice_resilience.py -q
```

Expected: new source-contract tests fail because audio is still discarded before
`stopBiometricRecording()` when no browser transcript exists.

- [ ] **Step 3: Integrate capability state without requesting permission**

Import the new helpers at the top of `main.js`. Initialize one capability snapshot
and `browserRecognitionDegraded = false`. On the existing voice-link click:

- Refresh capability detection.
- Log `voice_insecure_context`, `voice_capture_unsupported`, or
  `voice_recognition_backend_fallback` through i18n.
- Return before active listening only when `getUserMedia` is unavailable or the
  context is insecure.
- Do not call `getUserMedia` from startup code.

For recognition `network` errors, mark browser recognition degraded, stop its
automatic restart, and let MediaRecorder plus the silence/active timeout complete.
Reset the degraded state only on an explicit voice-link click or a successful
recognition start/result.

Pass `browserRecognitionDegraded` into every
`shouldRestartPassiveRecognition` call.

- [ ] **Step 4: Reorder active command processing**

Change `processVoiceCommand` to flush audio before deciding whether the request is
usable:

```javascript
let transcript = (fullTranscript + " " + latestInterimTranscript).trim();
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
```

Use `/api/voice` whenever `hasAudio` is true, even when `transcript === ''`. After
the response, use `data.transcription_source` for a concise diagnostic and add the
recognized user segment from `data.identity_debug.transcript` when the browser did
not supply one. Use classic/streaming text chat only when valid transcript text
exists but audio capture is unavailable.

If the backend response is non-JSON or non-2xx, produce one controlled frontend
message and return to passive/idle without a retry flood.

- [ ] **Step 5: Add translated diagnostics**

Add matching English and Spanish keys:

```javascript
"voice_permission_denied": "> ALERT: Microphone permission was denied.",
"voice_device_missing": "> ALERT: No microphone input device was found.",
"voice_device_busy": "> ALERT: The microphone is busy or unavailable.",
"voice_insecure_context": "> ALERT: Voice capture requires localhost or HTTPS.",
"voice_capture_unsupported": "> ALERT: This browser cannot record microphone audio.",
"voice_recognition_backend_fallback": "> Browser speech recognition unavailable; backend transcription active.",
"voice_recognition_network": "> Browser speech service unavailable; using backend transcription.",
"voice_transcribing_backend": "> Transcribing captured audio...",
"voice_no_input": "> No usable audio or transcript was captured.",
"voice_transcription_unavailable": "> Voice transcription is currently unavailable. Text input remains active.",
```

Use equivalent natural Spanish text for the Spanish map.

- [ ] **Step 6: Run frontend regressions**

```powershell
python -m pytest tests\test_frontend_voice_resilience.py -q
node --check src\frontend\static\js\main.js
node --check src\frontend\static\js\modules\voice.js
```

Expected: all tests pass and syntax checks are silent.

- [ ] **Step 7: Commit frontend fallback behavior**

```powershell
git add src/frontend/static/js/main.js src/frontend/static/js/i18n.js tests/test_frontend_voice_resilience.py
git commit -m "fix: keep voice commands working without browser recognition"
```

## Task 6: Document supported browsers and provider operation

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `tests/test_installation_contract.py`

- [ ] **Step 1: Add failing documentation contract tests**

Add assertions:

```python
def test_voice_support_contract_documents_adaptive_transcription():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "JARVIS_STT_PROVIDER" in env_example
    assert "whisper-large-v3-turbo" in env_example
    assert "SpeechRecognition" in readme
    assert "Firefox" in readme
    assert "Safari" in readme
    assert "non-loopback" in readme.lower()
    assert "HTTPS" in readme
    assert "voice-capabilities.js" in agents
```

- [ ] **Step 2: Run and confirm documentation gaps**

```powershell
python -m pytest tests\test_installation_contract.py -q
```

Expected: the new contract fails on missing support/provider documentation.

- [ ] **Step 3: Update README and AGENTS**

Document:

- `SpeechRecognition` is an optional hint/wake-word acceleration.
- Recorded audio falls back through Groq and local faster-whisper in `auto` mode.
- `start_app.py`/WebView2 is the primary Windows path.
- Edge/Chrome support browser wake word when their speech service works.
- Firefox/Safari use the existing voice-link button plus backend STT; passive wake
  word remains best effort.
- `http://localhost:5002` is the stable loopback origin.
- Any non-loopback/LAN browser access requires HTTPS for microphone capture.
- Exact error categories and the Windows/browser permission reset checklist.
- Local Whisper downloads and loads lazily only when the configured provider path
  reaches it.

Add targeted verification commands for `test_voice_transcription.py` and
`test_frontend_voice_resilience.py` to AGENTS.

- [ ] **Step 4: Run documentation contracts**

```powershell
python -m pytest tests\test_installation_contract.py -q
```

Expected: all installation/documentation contract tests pass.

- [ ] **Step 5: Commit documentation**

```powershell
git add README.md AGENTS.md tests/test_installation_contract.py
git commit -m "docs: define adaptive voice browser support"
```

## Task 7: Full verification and browser simulation

**Files:**
- Verify: entire repository
- Modify only if measured baseline changes: `AGENTS.md`

- [ ] **Step 1: Run focused voice regression matrix**

```powershell
python -m pytest tests\test_voice_transcription.py tests\test_frontend_voice_resilience.py tests\test_i18n_regressions.py tests\test_smoke.py -q
```

Expected: zero failures; one existing environment-dependent skip is acceptable.

- [ ] **Step 2: Run complete release checks**

```powershell
python -m pytest -q
python -m compileall -q start_app.py src\backend
python -m ruff check src\backend tests --select F
python -m pip check
python -m pip_audit -r requirements.txt
node --check src\frontend\static\js\main.js
node --check src\frontend\static\js\modules\api.js
node --check src\frontend\static\js\modules\voice.js
node --check src\frontend\static\js\modules\recognition-policy.js
node --check src\frontend\static\js\modules\voice-capabilities.js
git diff --check
```

Expected: pytest passes, compilation/syntax commands are silent, Ruff and pip
checks report success, and `pip-audit` reports no known runtime vulnerabilities.

- [ ] **Step 3: Start the backend and inspect sanitized status**

Start the backend through the project environment, poll `/api/status`, and verify:

```text
speech_to_text.provider = auto|browser|groq|local
speech_to_text.groq_configured = true|false
speech_to_text.local_enabled = true|false
speech_to_text.local_state = not_loaded|loaded|disabled|unavailable
```

Confirm no API key, filesystem path, provider exception, or raw network error is
present in the JSON response.

- [ ] **Step 4: Run browser capability simulation**

Use the local UI in Chromium/WebView2 and execute both cases:

1. Normal environment: existing voice-link button initializes audio, browser
   recognition remains available, and no console errors occur.
2. Override both `window.SpeechRecognition` and `window.webkitSpeechRecognition`
   to `undefined` before module initialization: the existing voice-link button
   still records and submits audio, the HUD reports backend transcription, and no
   new push-to-talk control appears.

Also test a rejected microphone permission and confirm only one terminal
diagnostic appears with no passive restart loop.

- [ ] **Step 5: Check repository hygiene**

```powershell
git status --short
git diff --check origin/master...HEAD
git grep -n -E "gsk_[A-Za-z0-9_-]{20,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"
git ls-files -- .env src/backend/.cache-jarvis src/backend/logs
```

Expected: only intentional committed changes, no whitespace errors, no secret
matches, and no tracked runtime files.

- [ ] **Step 6: Record a changed full-suite baseline only when measured**

If the full pytest count changed, replace the AGENTS baseline with the exact
measured output and commit only that line:

```powershell
git add AGENTS.md
git commit -m "docs: record adaptive voice test baseline"
```

If the baseline did not change, do not create this commit.
