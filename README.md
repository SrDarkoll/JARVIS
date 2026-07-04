# J.A.R.V.I.S.

J.A.R.V.I.S. is a local desktop AI assistant built with a Python/Quart backend and a vanilla browser frontend. The main target is Windows, although the backend and most web/API features can run on macOS and Linux when optional desktop/audio integrations are available.

## Project Status

J.A.R.V.I.S. is currently a technical beta for local development and power users. A clean clone should install and start when Git LFS model files are present and at least one LLM provider key is configured, but this is not yet a one-click consumer installer.

Expect hardware-specific behavior around microphones, speakers, Spotify devices, local desktop control, WebView2, and voice biometrics. Windows is the primary supported desktop target.

## What Jarvis Can Do

- Voice chat with browser speech hints, optional Whisper fallback, and Piper TTS.
- English/Spanish UI and response mode.
- Admin voice enrollment and guest voice profile registration.
- Per-profile memory and shared memory facts.
- Local reminders, weather, news summaries, time/date answers, and dynamic web search routing.
- Spotify playback, playback controls, AutoMix, and dynamic mix generation using user top tracks, recent tracks, artist/genre context, and playlist fallbacks when the Spotify account permits it.
- Telegram bot integration with `TELEGRAM_CHAT_ID` filtering.
- Local desktop/system tools gated by authorization and security policy.
- Observability/status endpoints for setup, profiles, metrics, security, TTS, and voice identity diagnostics.
- Optional desktop shell through `pywebview` with persistent WebView2 storage.

## Repository Layout

- `start_app.py` - desktop launcher.
- `src/backend/jarvis_backend.py` - Quart/Hypercorn backend entrypoint.
- `src/backend/api/` - HTTP route modules.
- `src/backend/core/` - config, state, security, tool routing, and assistant brain.
- `src/backend/tools/` - built-in tools such as search, Spotify, browser, system, and utilities.
- `src/backend/voice/` - voice registration, transcription, and identity logic.
- `src/frontend/` - static UI and templates.
- `tests/` - regression and smoke tests.
- `models/` - Piper voice models tracked with Git LFS.

## Requirements

Recommended:

- Python 3.11 or 3.12. The setup scripts reject newer Python versions for now because several audio/ML dependencies still emit compatibility warnings or rely on APIs scheduled for removal.
- Git LFS before cloning, because `.onnx` voice models are stored through LFS.
- FFmpeg on `PATH` for audio conversion.
- Windows: eSpeak NG for Piper phonemization, usually installed at `C:\Program Files\eSpeak NG`.
- Windows desktop shell: Microsoft Edge WebView2 runtime, normally already present on Windows 10/11.

Useful Windows install commands:

```powershell
winget install Git.Git
winget install GitHub.GitLFS
winget install Gyan.FFmpeg
winget install eSpeak-NG.eSpeak-NG
```

macOS/Linux can run the backend and web UI, but Windows-only features such as some telemetry, volume control, and native desktop control may degrade or return controlled error messages.

## Install From a Fresh Clone

```powershell
git lfs install
git clone https://github.com/SrDarkoll/J.A.R.V.I.S.git
cd J.A.R.V.I.S
git lfs pull
```

`git lfs pull` must download the real `.onnx` voice models. If those files are only tiny pointer files, TTS and setup validation will fail.

Windows:

```powershell
.\setup.ps1
```

Windows with test tools:

```powershell
.\setup.ps1 -Dev
```

Windows with every optional integration:

```powershell
.\setup.ps1 -Full
```

Windows full development environment:

```powershell
.\setup.ps1 -Dev -Full
```

Linux/macOS:

```bash
chmod +x setup.sh
./setup.sh
```

Linux/macOS with test tools:

```bash
./setup.sh --dev
```

Linux/macOS with every optional integration:

```bash
./setup.sh --full
```

Linux/macOS full development environment:

```bash
./setup.sh --dev --full
```

Manual setup:

```bash
python -m venv venv
# Windows: .\venv\Scripts\Activate.ps1
# Linux/macOS: source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
# Only for full mode:
pip install -r requirements-optional.txt
```

## Environment

Copy `.env.example` to `.env`:

```powershell
Copy-Item .env.example .env
```

Linux/macOS:

```bash
cp .env.example .env
```

The complete list of supported environment variables lives in `.env.example`. Keep real secrets only in `.env`.

Minimum for real LLM responses:

- `GROQ_API_KEY`

## Stable Core Mode

`JARVIS_CORE_MODE=true` is the default and the recommended starting point for
new installations. It keeps Groq chat, local voice input, Piper TTS, basic
memory, Spotify, base tools, and the web UI active.

Core mode skips the subsystems most likely to make startup slow or fragile:

- SpeechBrain voice biometrics
- Hugging Face/FAISS RAG
- Groq vision model initialization
- Dynamic plugins
- Startup news briefing
- Telegram and proactive background behavior

To enable the complete feature set, set `JARVIS_CORE_MODE=false`. Individual
features can also be enabled with `JARVIS_VOICE_ID_ENABLED`,
`JARVIS_RAG_ENABLED`, `JARVIS_VISION_ENABLED`, `JARVIS_PLUGINS_ENABLED`,
`JARVIS_BRIEFING_ENABLED`, and `JARVIS_TELEGRAM_ENABLED`.

Strongly recommended before LAN access or shared-machine use:

- `JARVIS_API_TOKEN`

Optional integrations:

- Spotify: `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, `SPOTIPY_REDIRECT_URI`
- Telegram: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`
- Search/news providers: `NEWSAPI_KEY`, `GOOGLE_API_KEY`, `GOOGLE_CSE_ID`, `BRAVE_API_KEY`, `TAVILY_API_KEY`, `YOUTUBE_API_KEY`
- Hugging Face/RAG: `HF_TOKEN`, `EMBEDDING_MODEL`, `JARVIS_HF_CACHE`
- Optional RVC voice conversion: `JARVIS_USE_RVC`, `RVC_MODEL_PATH`, `RVC_INDEX_PATH`

Environment groups:

| Group | Required? | Variables |
| --- | --- | --- |
| LLM core | Required for real chat | `GROQ_API_KEY` |
| Local security | Recommended for LAN/shared machines | `JARVIS_API_TOKEN`, `JARVIS_CORS_ORIGINS` |
| Spotify | Optional | `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, `SPOTIPY_REDIRECT_URI`, `SPOTIFY_MARKET` |
| Telegram | Optional | `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` |
| Search/news | Optional | `BRAVE_API_KEY`, `TAVILY_API_KEY`, `NEWSAPI_KEY`, `GOOGLE_API_KEY`, `GOOGLE_CSE_ID`, `YOUTUBE_API_KEY` |
| Voice/audio | Optional tuning | `ESPEAK_ROOT`, `JARVIS_WHISPER_*`, `VOICE_ID_*`, `JARVIS_TTS_MAX_CHARS` |
| Runtime paths | Optional relocation | `JARVIS_RUNTIME_DIR`, `JARVIS_DB_PATH`, `JARVIS_FAISS_DIR`, `JARVIS_CACHE_DIR` |

Do not commit `.env`, OAuth caches, voice profiles, logs, memory files, or runtime databases.

Useful safety checks before publishing:

```powershell
git ls-files -- .env
git check-ignore -v .env
```

The first command should print nothing. The second should show that `.env` is ignored by `.gitignore`.

## Running

Recommended desktop app:

```powershell
python start_app.py
```

Backend only:

```powershell
python src/backend/jarvis_backend.py
```

Then open:

```text
http://localhost:5002
```

## Testing

Install dev tools first:

```powershell
.\setup.ps1 -Dev
```

Run all tests:

```powershell
pytest -q
```

`pytest.ini` restricts collection to `tests/` and ignores local runtime/cache folders. This prevents old W&B/temp directories from breaking collection on Windows.

Ruff is useful for future cleanup, but it is not currently enforced as a release gate because the codebase still has legacy style debt.

## Spotify Notes

Jarvis uses Spotify playback APIs and builds an AutoMix playlist/queue after a seed song or mix request. The mix strategy is:

- start from the requested song/artist/genre seed;
- add user top tracks when `user-top-read` is granted;
- add recently played tracks when `user-read-recently-played` is granted;
- add genre-based search results from seed artist genres and user top artist genres;
- add playlist tracks from related playlist searches when the Spotify account and playlist visibility allow it;
- fall back to album/artist context when personalization is unavailable.

If you already authenticated Spotify before these scopes existed, delete `src/backend/.cache-jarvis` and authenticate again.

Spotify API limitations:

- Spotify does not expose a general "users also listen to" endpoint for arbitrary users.
- Some recommendation/audio-analysis style endpoints are not available to all apps or have changed over time. Jarvis treats audio features as optional and falls back when unavailable.
- Playlist content access may be limited by Spotify app mode, ownership, collaboration, and privacy.
- Direct playback control requires Spotify Premium and an active device.

## GitHub-Ready Checklist

Before publishing or tagging a release:

- `git lfs ls-files` shows the `.onnx` models.
- `.env` is not tracked.
- `.env.example` is up to date.
- `pytest -q` passes.
- `python -m compileall -q start_app.py src/backend` passes.
- `node --check src/frontend/static/js/main.js` passes.
- `node --check src/frontend/static/js/modules/api.js` passes.
- A clean clone can import `jarvis_backend` and answer `/api/status`.

## Troubleshooting

- Missing model files: run `git lfs pull`.
- Spotify says auth expired or scopes missing: delete `src/backend/.cache-jarvis`, restart, and authenticate again.
- No Spotify device: open Spotify on a device and play one song once.
- TTS unavailable: verify model files, eSpeak NG, and `ESPEAK_ROOT`.
- Audio conversion fails: verify FFmpeg is on `PATH`.
- Missing API keys: Jarvis should still start, but provider-specific tools will return setup/configuration messages instead of crashing.
