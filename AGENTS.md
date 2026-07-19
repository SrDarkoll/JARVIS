# AGENTS.md

Operational notes for agents working on J.A.R.V.I.S.

This project is a local desktop AI assistant with a Python/Quart backend and a vanilla browser frontend. Windows is the primary target, but most backend and web features should degrade cleanly on macOS and Linux.

## First Checks

- Run `git status --short` before editing. The working tree may already contain user changes.
- Do not revert unrelated files or generated state unless the user explicitly asks.
- Prefer small, focused patches. Keep existing style unless the style is part of the bug.
- Use `rg` or `rg --files` for discovery before slower search tools.
- Do not commit `.env`, OAuth caches, voice profiles, logs, runtime databases, or scratch artifacts.

## Setup Workflow

Fresh clone:

```powershell
git lfs install
git clone https://github.com/SrDarkoll/J.A.R.V.I.S.git
cd J.A.R.V.I.S
git lfs pull
```

Windows development setup:

```powershell
.\setup.ps1 -Dev
```

Windows full optional setup:

```powershell
.\setup.ps1 -Dev -Full
```

Linux/macOS development setup:

```bash
chmod +x setup.sh
./setup.sh --dev
```

Linux/macOS full optional setup:

```bash
./setup.sh --dev --full
```

Manual setup fallback:

```bash
python -m venv venv
# Windows:
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
# Linux/macOS:
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m pip install -r requirements-dev.txt
# Only when testing full optional integrations:
.\venv\Scripts\python.exe -m pip install -r requirements-optional.txt
# Linux/macOS: venv/bin/python -m pip install -r requirements-optional.txt
```

Environment setup:

```powershell
Copy-Item .env.example .env
```

Minimum real LLM usage requires `GROQ_API_KEY`. For shared machines, LAN exposure, or browser access beyond the local trusted UI, prefer setting `JARVIS_API_TOKEN`.

`JARVIS_CORE_MODE=true` is the stable default. It keeps chat, local voice input,
Piper TTS, basic memory, Spotify, base tools, and the web UI while skipping
voice biometrics, RAG, vision, plugins, briefing, and Telegram. Use
`JARVIS_CORE_MODE=false` only when validating the complete optional feature set.

Run a setup script before the launcher. `start_app.py` automatically re-executes
through the project `venv` when it exists, which prevents global Python package
conflicts. FFmpeg is the only direct audio-conversion backend for browser voice,
voice identity, and Telegram OGG/Opus output.

## Run Commands

Desktop launcher:

```powershell
python start_app.py
```

Backend only:

```powershell
python src/backend/jarvis_backend.py
```

Default local URL:

```text
http://localhost:5002
```

## Verification Commands

Full regression suite:

```powershell
pytest -q
```

Current verified baseline after the latest security/stability pass:

```text
363 passed, 1 skipped in 16.56s
```

Python syntax/import compilation:

```powershell
python -m compileall -q start_app.py src\backend
```

Frontend JavaScript syntax checks:

```powershell
node --check src\frontend\static\js\main.js
node --check src\frontend\static\js\modules\api.js
node --check src\frontend\static\js\modules\recognition-policy.js
node --check src\frontend\static\js\modules\voice-capabilities.js
```

Patch hygiene:

```powershell
git diff --check
```

On Windows, `git diff --check` may print LF/CRLF warnings. Treat whitespace errors as blockers; line-ending warnings alone are informational unless the task is specifically about line endings.

Dependency and release checks:

```powershell
python -m pip check
python -m pip_audit -r requirements.txt
python -m ruff check src/backend tests --select F
```

## Targeted Regression Commands

Use these when touching security middleware, chat streaming, or web search.

Critical-route and chat stream regressions:

```powershell
pytest tests\test_smoke.py::test_critical_route_rejects_untrusted_origin_on_loopback tests\test_smoke.py::test_critical_route_allows_trusted_loopback_origin_without_token tests\test_smoke.py::test_chat_stream_rejects_get tests\test_smoke.py::test_chat_stream_rate_limits_like_chat -q
```

Security confirmation and quick control regressions:

```powershell
pytest tests\test_security_manager.py::TestSecurityGuard::test_security_guard_requires_explicit_confirmation_even_when_authorized tests\test_security_manager.py::TestQuickControlActions -q
```

Search error-sanitization regression:

```powershell
pytest tests\test_search_security.py::test_brave_search_hides_internal_network_errors -q
```

Memory RAG optional-dependency regression:

```powershell
pytest tests\test_memory_rag_resilience.py -q
```

LLM OpenAI-compatible fallback regression:

```powershell
pytest tests\test_llm_engine_fallback.py -q
```

Router/tool routing smoke regression:

```powershell
pytest tests\test_smoke.py::test_dynamic_queries_force_web_tools tests\test_compound_router.py -q
```

Spotify recommendation regressions:

```powershell
pytest tests\test_spotify_recs.py -q
```

Adaptive voice transcription regressions:

```powershell
pytest tests\test_voice_transcription.py tests\test_frontend_voice_resilience.py -q
```

Spotify OAuth must use the exact registered redirect
`http://127.0.0.1:8888/callback`; `localhost` aliases are rejected. Default
development-mode mixes must avoid restricted recommendation/audio-feature,
related-artist, artist-top-track, and public-playlist-content endpoints. Enable
`SPOTIFY_EXTENDED_QUOTA_MODE` only for an app approved for extended quota mode.

## Security Workflow

- Add or update tests before changing security-sensitive behavior.
- Critical backend routes must not rely only on loopback detection when an untrusted `Origin` or `Referer` is present.
- If `JARVIS_API_TOKEN` is absent, trusted local browser origins may still be allowed, but hostile origins should receive a controlled 403.
- Authorization and explicit user confirmation are separate concepts. Do not treat an authorized source as confirmation for destructive or sensitive actions.
- Network/provider failures should return generic user-facing messages. Do not expose proxy details, local IPs, tokens, stack traces, or raw exception text.
- Logs may include exception class names for diagnosis, but avoid raw credential-bearing URLs or headers.

## API And Chat Workflow

- `/api/chat` and `/api/chat/stream` should share equivalent validation where possible: empty message checks, maximum message length, and rate limiting.
- `/api/chat/stream` is expected to be `POST` only.
- Use controlled JSON errors for bad input instead of silent failures.
- Keep rate-limit buckets separate when endpoint behavior differs.

## Test Runtime Notes

- `tests/conftest.py` sets repo-local runtime paths. Avoid tests that depend on global OS temp behavior when possible.
- On Windows, stale or locked temp directories can cause pytest setup issues. Prefer repo-local scratch paths and explicit cleanup of files created by the test.
- Avoid using real network calls in unit tests. Mock provider clients and requests.
- `JARVIS_MONITORING_ENABLED` defaults off in core mode and on in full mode. Installing APScheduler does not enable jobs by itself.
- Without `GROQ_API_KEY`, AI-backed routes must return controlled `llm_unconfigured` errors while setup/status and local preflight paths remain usable.

## Linting Notes

Ruff is configured in `pyproject.toml`, but the codebase currently has legacy style debt. Use Ruff for focused cleanup when requested, but do not treat a full-project Ruff run as a release gate unless that cleanup is part of the task.

Useful focused lint command:

```powershell
ruff check src tests
```

## Frontend Workflow

- For JavaScript-only changes, run the `node --check` commands listed above.
- For visual or interaction changes, start the app and verify the affected flow in a browser.
- Keep the UI compatible with the WebView2 desktop shell.
- Keep `SpeechRecognition` optional. It may accelerate transcription and provide the passive wake word, but it must not gate `MediaRecorder` capture or `/api/voice` submission.
- Use `src/frontend/static/js/modules/voice-capabilities.js` to distinguish secure context, `getUserMedia`, `MediaRecorder`, and browser recognition support.
- Do not request microphone permission during page startup. Request it only after the existing voice-link user gesture or an established wake-word flow.
- Preserve the actual recorded MIME type so Safari/default `MediaRecorder` formats can reach backend FFmpeg when browser-side WAV conversion is unavailable.
- A browser recognition network error should stop recognition retries while allowing active audio capture, silence detection, and the active timeout to finish.

## GitHub-Ready Checklist

Before publishing a branch or release:

- `git status --short` shows only intentional changes.
- `.env` is not tracked.
- `.env.example` documents required and optional variables.
- `git lfs ls-files` includes tracked `.onnx` voice models.
- `python -m pip check` reports no broken requirements.
- `python -m pip_audit -r requirements.txt` reports no known runtime vulnerability.
- `pytest -q` passes.
- `python -m compileall -q start_app.py src\backend` passes.
- `node --check src\frontend\static\js\main.js` passes.
- `node --check src\frontend\static\js\modules\api.js` passes.
- `git diff --check` has no whitespace errors.
- A fresh setup can start with `python start_app.py` or `python src/backend/jarvis_backend.py`.
