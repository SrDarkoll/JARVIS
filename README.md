# J.A.R.V.I.S.

J.A.R.V.I.S. is a local desktop AI assistant built with a Python/Quart backend and a vanilla browser frontend. The main target is Windows, although the backend and most web/API features can run on macOS and Linux when optional desktop/audio integrations are available.

This project is still alpha software. Expect hardware-specific behavior around microphones, speakers, Spotify devices, local desktop control, and voice biometrics.

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

- Python 3.11 or 3.12.
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

Windows:

```powershell
.\setup.ps1
```

Windows with test tools:

```powershell
.\setup.ps1 -Dev
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

Manual setup:

```bash
python -m venv venv
# Windows: .\venv\Scripts\Activate.ps1
# Linux/macOS: source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

## Environment

Copy `.env.example` to `.env`:

```powershell
Copy-Item .env.example .env
```

Minimum for real LLM responses:

- `GROQ_API_KEY` or `MINIMAX_API_KEY`

Strongly recommended before LAN access or shared-machine use:

- `JARVIS_API_TOKEN`

Optional integrations:

- Spotify: `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, `SPOTIPY_REDIRECT_URI`
- Telegram: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`
- Search/news providers: `NEWSAPI_KEY`, `GOOGLE_API_KEY`, `GOOGLE_CSE_ID`, `BRAVE_API_KEY`, `TAVILY_API_KEY`, `YOUTUBE_API_KEY`

Do not commit `.env`, OAuth caches, voice profiles, logs, memory files, or runtime databases.

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
- `python -m compileall -q src/backend` passes.
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
