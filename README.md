# J.A.R.V.I.S.

J.A.R.V.I.S. is a local desktop AI assistant built with a Python/Quart backend and a vanilla browser frontend. The main target is Windows, although the backend and most web/API features can run on macOS and Linux when optional desktop/audio integrations are available.

## Project Status

J.A.R.V.I.S. is currently a technical beta for local development and power users. A clean clone should install and start when Git LFS model files are present. A Groq key is required for AI-generated replies, but setup diagnostics and local-only features remain available without it. This is not yet a one-click consumer installer.

Expect hardware-specific behavior around microphones, speakers, Spotify devices, local desktop control, WebView2, and voice biometrics. Windows is the primary supported desktop target.

## What Jarvis Can Do

- Voice chat with browser speech hints, optional Whisper fallback, and Piper TTS.
- English/Spanish UI and response mode.
- Admin voice enrollment and guest voice profile registration.
- Per-profile memory and shared memory facts.
- Local reminders, weather, news summaries, time/date answers, and dynamic web search routing.
- Spotify playback and controls through either the Windows desktop client or an eligible Web API account, plus API-backed AutoMix and dynamic recommendations when the account permits them.
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

- Python 3.11 or 3.12. The setup scripts reject newer Python versions for now because several ML dependencies still need a validated compatibility window. Python 3.13 remains unsupported even though `pydub` is no longer a direct dependency.
- Git LFS before cloning, because `.onnx` voice models are stored through LFS.
- FFmpeg on `PATH` for browser voice, voice identity, and Telegram OGG/Opus conversion.
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
# Windows:
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
# Linux/macOS:
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m pip install -r requirements-dev.txt
# Only for full mode:
.\venv\Scripts\python.exe -m pip install -r requirements-optional.txt
# Linux/macOS: venv/bin/python -m pip install -r requirements-optional.txt
```

Run `setup.ps1` or `setup.sh` before the launcher. When the project `venv`
exists, `start_app.py` automatically relaunches itself through that interpreter
to avoid conflicts with packages installed in the user's global Python.

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

Without `GROQ_API_KEY`, status and setup diagnostics, Piper TTS, and local
preflight tools continue to work. Chat requests that need an AI provider return
the controlled `llm_unconfigured` error with HTTP 503.

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
`JARVIS_BRIEFING_ENABLED`, `JARVIS_TELEGRAM_ENABLED`, and
`JARVIS_MONITORING_ENABLED`.

`JARVIS_MONITORING_ENABLED` defaults to `false` in core mode and `true` in full
mode. Installing APScheduler does not enable background jobs by itself.

Strongly recommended before LAN access or shared-machine use:

- `JARVIS_API_TOKEN`

Optional integrations:

- Spotify Desktop on Windows: `SPOTIFY_PLAYBACK_MODE=desktop` (no developer keys required)
- Spotify Web API: `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, `SPOTIPY_REDIRECT_URI`
- Telegram: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`
- Search/news providers: `NEWSAPI_KEY`, `GOOGLE_API_KEY`, `GOOGLE_CSE_ID`, `BRAVE_API_KEY`, `TAVILY_API_KEY`, `YOUTUBE_API_KEY`
- Hugging Face/RAG: `HF_TOKEN`, `EMBEDDING_MODEL`, `JARVIS_HF_CACHE`
- Optional RVC voice conversion: `JARVIS_USE_RVC`, `RVC_MODEL_PATH`, `RVC_INDEX_PATH`

Environment groups:

| Group | Required? | Variables |
| --- | --- | --- |
| LLM core | Required for real chat | `GROQ_API_KEY` |
| Local security | Recommended for LAN/shared machines | `JARVIS_API_TOKEN`, `JARVIS_CORS_ORIGINS` |
| Spotify | Optional | `SPOTIFY_PLAYBACK_MODE`, `SPOTIFY_DESKTOP_START_TIMEOUT`, `SPOTIFY_DESKTOP_ACTION_TIMEOUT`, `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, `SPOTIPY_REDIRECT_URI`, `SPOTIFY_MARKET`, `SPOTIFY_EXTENDED_QUOTA_MODE` |
| Telegram | Optional | `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` |
| Search/news | Optional | `BRAVE_API_KEY`, `TAVILY_API_KEY`, `NEWSAPI_KEY`, `GOOGLE_API_KEY`, `GOOGLE_CSE_ID`, `YOUTUBE_API_KEY` |
| Voice/audio | Optional tuning | `JARVIS_STT_PROVIDER`, `JARVIS_GROQ_STT_MODEL`, `JARVIS_LOCAL_STT_ENABLED`, `JARVIS_STT_TIMEOUT_SECONDS`, `JARVIS_WHISPER_*`, `ESPEAK_ROOT`, `VOICE_ID_*`, `JARVIS_TTS_MAX_CHARS` |
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

The launcher uses the project `venv` automatically. If it is missing, the
launcher exits with the exact setup command instead of importing from global
site-packages.

Backend only:

```powershell
python src/backend/jarvis_backend.py
```

Then open:

```text
http://localhost:5002
```

## Voice And Browser Support

`python start_app.py` with the WebView2 desktop shell is the primary Windows
path. `http://localhost:5002` is the stable loopback URL when using a regular
browser. Opening Jarvis from any non-loopback address, including a LAN IP,
requires HTTPS before browsers will expose microphone capture.

Browser voice uses two independent capabilities:

- `MediaRecorder` captures the command audio sent to `/api/voice`.
- `SpeechRecognition` is an optional browser hint and enables the passive
  "Jarvis" wake word when the browser speech service works.

The absence or network failure of `SpeechRecognition` no longer disables voice
commands. Use the existing **voice link** button, speak normally, and Jarvis will
send the captured audio to the backend. Edge and Chrome can provide both the
wake word and a transcript hint. Firefox and Safari can use backend
transcription when their current browser/runtime does not expose compatible
browser speech recognition; passive wake-word support is best effort there.
Text input remains available in every case.

The default `JARVIS_STT_PROVIDER=auto` order is:

1. Accept a sufficiently reliable browser transcript hint.
2. Transcribe recorded audio with Groq using
   `JARVIS_GROQ_STT_MODEL=whisper-large-v3-turbo` when `GROQ_API_KEY` is set.
3. Fall back to local `faster-whisper` when
   `JARVIS_LOCAL_STT_ENABLED=true`.
4. Return a controlled unavailable response while leaving text input active.

Set `JARVIS_STT_PROVIDER` to `browser`, `groq`, or `local` to restrict the
backend provider path. The local Whisper model is downloaded and loaded lazily,
only when a request reaches that fallback; its first request can therefore take
longer and requires enough disk space for the selected `JARVIS_WHISPER_MODEL`.
`/api/status` reports the selected provider and safe availability state without
returning keys or model paths.

Voice diagnostics distinguish these cases:

- **Permission denied:** allow microphone access for the Jarvis origin and
  check Windows/macOS privacy settings.
- **Device missing:** connect or enable an input microphone.
- **Device busy:** close another application holding exclusive microphone
  access, then press the voice link again.
- **Insecure context:** use `http://localhost:5002` locally or HTTPS for LAN
  access.
- **Browser speech network failure:** continue with the existing voice link;
  recorded audio will use backend STT.

On Windows, if permission was previously denied, reset it in the browser's site
settings and in **Settings > Privacy & security > Microphone**, then reload
Jarvis and press the voice link. Browser support details for secure microphone
contexts and speech recognition are documented by MDN under
[`getUserMedia`](https://developer.mozilla.org/docs/Web/API/MediaDevices/getUserMedia)
and [`SpeechRecognition`](https://developer.mozilla.org/docs/Web/API/SpeechRecognition).

## Testing

Install dev tools first:

```powershell
.\setup.ps1 -Dev
```

Run all tests:

```powershell
pytest -q
```

Release checks:

```powershell
python -m pip check
python -m pip_audit -r requirements.txt
python -m ruff check src/backend tests --select F
python -m compileall -q start_app.py src/backend
node --check src/frontend/static/js/main.js
node --check src/frontend/static/js/modules/api.js
git diff --check
```

`pytest.ini` restricts collection to `tests/` and ignores local runtime/cache folders. This prevents old W&B/temp directories from breaking collection on Windows.

Ruff is useful for future cleanup, but it is not currently enforced as a release gate because the codebase still has legacy style debt.

## Spotify Notes

### Spotify playback modes

- `SPOTIFY_PLAYBACK_MODE=auto` uses an already-authorized Web API token when
  one is valid. If no cached token exists or the API cannot perform playback,
  Jarvis controls the Windows Spotify Desktop client instead without opening an
  OAuth browser during the command.
- `SPOTIFY_PLAYBACK_MODE=desktop` does not require Spotify developer keys. It
  opens or restores Spotify, briefly focuses it, searches through Windows UI
  Automation, ranks accessible results, activates one match, and verifies the
  now-playing metadata before reporting success.
- `SPOTIFY_PLAYBACK_MODE=api` keeps the Spotipy/OAuth integration and requires
  an eligible Spotify developer application and account.

Desktop mode requires Windows, the installed Spotify Desktop client, and a
signed-in Spotify session. It does not bypass Spotify Free restrictions,
advertisements, regional availability, account limits, or DRM. Search and
control may hold foreground focus for a few seconds, bounded by
`SPOTIFY_DESKTOP_ACTION_TIMEOUT`; switching to another window cancels the click
instead of sending input to the wrong application. Desktop mix requests start
the requested seed and then rely on Spotify's own autoplay and recommendations.

Deterministic UI Automation is always attempted first and does not use fixed
screen coordinates. Optional visual recovery requires full mode, Pillow, and a
configured vision model. It captures only the Spotify window, sends that image
to the configured model provider, validates the returned bounds and foreground
window, and deletes the local image immediately after the attempt.

For Web API mode, register exactly `http://127.0.0.1:8888/callback` in the
Spotify developer dashboard and use the same value in `.env`; Spotify does not
accept `localhost` redirect aliases. Delete `src/backend/.cache-jarvis` and
authenticate again after changing the redirect URI or scopes.

Jarvis builds an API-backed AutoMix playlist/queue after a seed song or mix
request when Web API access is available. The development-mode mix strategy
uses endpoints available to current apps:

- start from the requested song/artist/genre seed;
- add user top tracks when `user-top-read` is granted;
- add recently played tracks when `user-read-recently-played` is granted;
- add genre-based search results from seed artist genres and user top artist genres;
- add tracks from the seed album;
- add artist, genre, and text-search matches, with search pages capped at Spotify's current limit;
- use playlist contents only for playlists the current user owns or collaborates on.

Set `SPOTIFY_EXTENDED_QUOTA_MODE=true` only for an app that Spotify has actually approved for extended quota mode. In that mode Jarvis may also try Recommendations, Audio Features, Related Artists, and artist top tracks, while still degrading to the development-mode strategy if an endpoint fails.

Spotify API limitations:

- Spotify does not expose a general "users also listen to" endpoint for arbitrary users. Jarvis approximates it with the user's top/recent history, genres, album context, and catalog search.
- Development-mode apps cannot rely on Recommendations, Audio Features, Related Artists, Spotify editorial playlists, or artist top tracks. Extended-quota apps are not affected by those development-mode restrictions.
- Development-mode apps require the app owner to have Premium; new apps are limited to five authorized users.
- Playlist contents are available only for playlists the current user owns or collaborates on.
- Web API playback control requires Spotify Premium and an active device. This
  requirement does not apply to local desktop UI Automation, although normal
  Spotify Free account restrictions still apply.

See Spotify's official [Web API endpoint changes](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api), [February 2026 migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide), and [redirect URI requirements](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri).

## GitHub-Ready Checklist

Before publishing or tagging a release:

- `git lfs ls-files` shows the `.onnx` models.
- `.env` is not tracked.
- `.env.example` is up to date.
- `python -m pip check` reports no broken requirements.
- `python -m pip_audit -r requirements.txt` reports no known vulnerable runtime dependency.
- `pytest -q` passes.
- `python -m compileall -q start_app.py src/backend` passes.
- `node --check src/frontend/static/js/main.js` passes.
- `node --check src/frontend/static/js/modules/api.js` passes.
- A clean clone can import `jarvis_backend` and answer `/api/status`.

## Troubleshooting

- Missing model files: run `git lfs pull`.
- Spotify Web API says auth expired, redirect invalid, or scopes missing: register `http://127.0.0.1:8888/callback`, delete `src/backend/.cache-jarvis`, restart, and authenticate again.
- Spotify Desktop cannot search: keep Spotify signed in, restore its main window, use an English or Spanish accessible interface, and verify `SPOTIFY_PLAYBACK_MODE=desktop`.
- No Spotify API device: open Spotify on a device and play one song once, or use desktop mode on Windows.
- TTS unavailable: verify model files, eSpeak NG, and `ESPEAK_ROOT`.
- Audio conversion fails: verify FFmpeg is on `PATH`. Browser voice, voice identity, and Telegram OGG/Opus output all use FFmpeg; `pydub` is not required.
- Browser wake word unavailable: press the existing voice link and use backend STT; Firefox and Safari may not expose `SpeechRecognition`.
- Microphone works on localhost but not a LAN IP: use HTTPS for the non-loopback origin.
- Missing API keys: Jarvis should still start, but provider-specific tools will return setup/configuration messages instead of crashing.
