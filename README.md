# J.A.R.V.I.S.

[![CI](https://github.com/SrDarkoll/JARVIS/actions/workflows/ci.yml/badge.svg)](https://github.com/SrDarkoll/JARVIS/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/SrDarkoll/JARVIS?include_prereleases&sort=semver)](https://github.com/SrDarkoll/JARVIS/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.11-3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](#system-requirements)

**A local desktop AI assistant built around Gemini 3.1 Flash Live for natural full-duplex voice conversations, real-time tool calling, persistent memory, media control, and guarded desktop automation.**

Talk naturally. Interrupt while it is speaking. Ask it to act on your PC without leaving the conversation.

> **Project status:** Alpha. Windows is the primary target for desktop automation.

## ▶ Watch J.A.R.V.I.S. in Action

<!--
DEMO VIDEO SETUP

1. Record a short 20-30 second demo with system audio + microphone.
2. Save it as media/demo.mp4, or replace the two media/demo.mp4 links below
   with a GitHub-hosted video / YouTube URL.
3. Keep media/readme.png as the thumbnail, or replace it with a frame from
   the final video.

A good demo sequence:
- Say "JARVIS" / start the live session.
- Ask it to play a song on Spotify.
- Interrupt it while it is speaking (barge-in).
- Ask it to change PC volume or open an application.
- End with one fast information tool such as weather or web search.
-->

<p align="center">
  <a href="media/demo.mp4">
    <img src="media/readme.png" alt="Watch the J.A.R.V.I.S. real-time voice demo with sound" width="100%">
  </a>
</p>

<p align="center">
  <strong>🔊 Sound on.</strong> The point of the demo is the conversation: native speech, interruption, and tool execution in real time.<br>
  <a href="media/demo.mp4"><strong>▶ Watch the demo with sound</strong></a>
</p>

---

## 🌟 What Makes It Different

Most voice assistants are assembled as a sequential pipeline:

`record → transcribe → LLM → text-to-speech → play audio`

J.A.R.V.I.S. can instead use **Gemini Live native bidirectional audio**, keeping speech and tool use inside one live session.

- ⚡ **Native speech-to-speech streaming** — 16 kHz PCM microphone input and 24 kHz PCM audio output over a persistent WebSocket session.
- 🛑 **Real-time barge-in** — Interrupt J.A.R.V.I.S. while it is answering and immediately take back the conversation.
- 🛠️ **Function calling inside the voice session** — Gemini Live can invoke the same registered J.A.R.V.I.S. tools used by the command pipeline and receive their results without leaving the conversation.
- 👑 **`Charon` by default** — Gemini Live uses the calm `Charon` voice profile by default, with other supported voices configurable in `.env`.
- 🧠 **Persistent memory** — Conversation context and useful facts can survive between sessions.
- 👤 **Optional speaker identification** — SpeechBrain ECAPA biometrics can identify registered speakers when explicitly enabled.

### Voice Paths

| Mode | Pipeline | Use case |
| --- | --- | --- |
| **Gemini Live** | Microphone ↔ Gemini Live ↔ audio + tools | Lowest-latency conversational experience, native audio, barge-in, live tool calling |
| **Standard / fallback** | STT → Gemini/Groq → Piper TTS | Compatibility, local speech output, alternate inference providers, fallback operation |

> [!IMPORTANT]
> `GEMINI_API_KEY` is required for the Gemini Live experience shown in the demo. Groq is supported for standard/fallback inference and STT; it does **not** replace Gemini Live's native full-duplex audio session.

---

## 🎙️ What J.A.R.V.I.S. Can Do

### Core capabilities

- 🗣️ **Real-time voice** — Native Gemini Live audio or the standard STT → LLM → Piper path.
- 🎵 **Spotify control** — Search/play music, build a mix, pause/resume, skip, queue tracks, like/unlike songs, and inspect the currently playing track. Playback can use Spotify Desktop automation or a cached Web API session.
- ▶️ **YouTube playback** — Search for a requested video and open/play the closest result.
- 💻 **Desktop & window control** — Open applications, adjust volume, manage windows, inspect heavy processes, and use guarded PC actions.
- 🌐 **Web & browser tools** — Web search, Wikipedia lookup, browser navigation, page reading, clicking, and typing when the relevant browser mode/dependencies are available.
- 🌦️ **Utility tools** — Weather, ESPN sports data, reminders, basic math, news summaries, and routines.
- 🧠 **Memory** — Store useful facts and persistent conversation context in local runtime data.
- 🧩 **Action plans** — Build, inspect, and execute multi-step tool plans through the guarded tool layer.
- 🇬🇧 🇲🇽 **English & Spanish voice support** — Includes bundled local Piper voice models for English and Spanish fallback speech.

### Optional / full-mode capabilities

Installed with the optional dependency set and enabled explicitly when needed:

- 👤 SpeechBrain speaker biometrics
- 🧠 RAG / embeddings with FAISS + sentence-transformers
- 👁️ Screen/vision analysis
- 🤖 Telegram integration and proactive scheduling
- 🌐 Playwright browser automation
- 📡 Monitoring and briefing services
- 🧩 Runtime plugins
- 🎙️ Optional RVC voice conversion

J.A.R.V.I.S. defaults to **Core Mode**, keeping the common desktop, voice, memory, Spotify, and web UI paths lightweight while heavier integrations stay opt-in.

---

## ⚡ Quick Start — Windows

If you only want to run J.A.R.V.I.S., you do not need to set up a development environment manually.

### 1. Download

Download the newest Windows release package from the [Releases page](https://github.com/SrDarkoll/JARVIS/releases) (such as the [v0.1.0-alpha.4 release](https://github.com/SrDarkoll/JARVIS/releases/tag/v0.1.0-alpha.4)) and extract it to a folder on your PC.

### 2. Install

Run:

```text
Install-JARVIS.bat
```

The installer creates the project virtual environment, installs the core dependencies, creates `.env` when needed, and can create desktop/Start Menu shortcuts.

### 3. Configure Gemini

Open `.env` and add your Gemini API key:

```env
GEMINI_API_KEY="your_gemini_api_key_here"
```

Optional alternate/fallback provider:

```env
GROQ_API_KEY="your_groq_api_key_here"
```

### 4. Launch

Double-click:

```text
Start-JARVIS.bat
```

or run:

```powershell
python start_app.py
```

The J.A.R.V.I.S. HUD will open locally.

---

## 🧠 AI & Voice Configuration

The defaults in `.env.example` currently use:

| Variable | Purpose | Default |
| --- | --- | --- |
| `JARVIS_GEMINI_LIVE_MODEL` | Native real-time audio model | `gemini-3.1-flash-live-preview` |
| `JARVIS_GEMINI_LIVE_VOICE` | Gemini Live voice | `Charon` |
| `JARVIS_GEMINI_MODEL` | Standard Gemini text/reasoning model | `gemini-2.5-flash` |
| `JARVIS_GEMINI_VISION_MODEL` | Gemini vision model | `gemini-2.5-flash` |
| `JARVIS_GROQ_MODEL` | Alternate/fallback Groq model | `qwen/qwen3.6-27b` |
| `JARVIS_STT_PROVIDER` | Speech-to-text provider selection | `auto` |
| `JARVIS_GROQ_STT_MODEL` | Groq STT model | `whisper-large-v3-turbo` |
| `JARVIS_LOCAL_STT_ENABLED` | Enable local Faster-Whisper fallback | `true` |
| `JARVIS_WHISPER_MODEL` | Local Whisper model | `medium` |
| `JARVIS_CORE_MODE` | Lightweight stable feature set | `true` |
| `JARVIS_VOICE_ID_ENABLED` | Experimental speaker biometrics | disabled unless enabled |

The exact model IDs are configurable; you are not locked to these defaults.

---

## 🔐 Security & Privacy

J.A.R.V.I.S. can touch real desktop applications, so tools are not treated as equally trusted.

- **Tool policy by risk level** — Public, elevated, and critical actions are evaluated through a central permission policy.
- **Explicit confirmation for critical actions** — Sensitive tools such as PC control, process termination, memory deletion, action-plan execution, file writes, and terminal commands can require explicit confirmation.
- **Privileged tools are opt-in** — Arbitrary terminal execution and text-file creation are excluded from the normal tool catalog unless `JARVIS_SYSTEM_TOOLS_ENABLED=true`.
- **File writes are constrained** — Allowed write roots can be limited with `JARVIS_FILE_WRITE_ROOTS`.
- **Optional API token** — Local/LAN API access can be protected with `JARVIS_API_TOKEN`; CORS origins are configurable.
- **Voice biometrics are off by default** — Speaker identification must be explicitly enabled.
- **Public-IP geolocation is off by default** — It must be explicitly enabled if desired.
- **Secrets stay in `.env`** — API keys, tokens, caches, voice profiles, memory databases, and logs should never be committed.

> [!WARNING]
> The unified diagnostic log (`log.txt`) can contain conversation text in plaintext, frontend events, tool activity, and backend output. Keep the J.A.R.V.I.S. runtime/data directory private.

Mutable application data defaults to the platform application-data directory and can be overridden with `JARVIS_DATA_DIR`. `JARVIS_RUNTIME_DIR` remains a compatibility alias for existing installations.

---

## 🛠 Developer & Modder Guide

### System Requirements

- **Python 3.11 or 3.12**
- **Git & Git LFS** for repository/model assets
- **FFmpeg** on `PATH` for audio workflows
- **eSpeak NG** on Windows for Piper phonemization

Windows prerequisites can be installed with:

```powershell
winget install Git.Git
winget install GitHub.GitLFS
winget install Gyan.FFmpeg
winget install eSpeak-NG.eSpeak-NG
```

> Windows is the primary desktop-automation target. Linux/macOS setup scripts are provided, but OS-specific automation features may not be available outside Windows.

### Clone & Core Setup

```powershell
git lfs install
git clone https://github.com/SrDarkoll/JARVIS.git
cd JARVIS
git lfs pull

.\setup.ps1
```

### Full Optional Integrations

```powershell
.\setup.ps1 -Full
```

`-Full` installs the heavier RAG, biometrics, Telegram/scheduling, Playwright, vision/search, and Windows telemetry/control dependencies, then installs Playwright Chromium when possible.

### Development Dependencies

```powershell
.\setup.ps1 -Dev
```

For everything:

```powershell
.\setup.ps1 -Dev -Full
```

On Linux/macOS:

```bash
chmod +x setup.sh
./setup.sh --dev
```

### Local Piper Voices

The project bundles English and Spanish Piper neural voice models (paired `.onnx` and `.onnx.json` files) under `models/`. Additional voices can be downloaded with Piper's voice downloader.

Example:

```powershell
.\venv\Scripts\python.exe -m piper.download_voices en_US-lessac-medium --download-dir models
```

---

## 🔧 Useful Environment Variables

| Variable | Description | Default |
| --- | --- | --- |
| `GEMINI_API_KEY` | Gemini inference + Gemini Live API key | `""` |
| `GROQ_API_KEY` | Alternate/fallback inference and optional Groq STT | `""` |
| `SPOTIFY_PLAYBACK_MODE` | `auto`, `desktop`, or `api`. `SPOTIFY_PLAYBACK_MODE=desktop` controls Spotify Desktop without developer keys (does not bypass Spotify Free restrictions) | `auto` |
| `JARVIS_CORE_MODE` | Keep heavy optional services disabled | `true` |
| `JARVIS_VOICE_ID_ENABLED` | Enable experimental SpeechBrain speaker ID | disabled |
| `JARVIS_RAG_ENABLED` | Enable RAG/embeddings path | disabled |
| `JARVIS_VISION_ENABLED` | Enable vision features | disabled |
| `JARVIS_TELEGRAM_ENABLED` | Enable Telegram integration | disabled |
| `JARVIS_MONITORING_ENABLED` | Enable monitoring service | disabled |
| `JARVIS_PLUGINS_ENABLED` | Enable runtime plugins | disabled |
| `JARVIS_SYSTEM_TOOLS_ENABLED` | Opt in to terminal/text-write tools | `false` |
| `JARVIS_API_TOKEN` | Protect critical API routes when configured | `""` |
| `JARVIS_DATA_DIR` | Override mutable application-data location | platform default |
| `JARVIS_UNIFIED_LOG_ENABLED` | Unified diagnostic log | `true` |

See [`.env.example`](.env.example) for the full configuration surface.

### Browser & Audio Recognition Support

The web HUD uses native `SpeechRecognition` in Chrome/Edge for fast client-side hints, and provides adaptive audio fallback in Firefox and Safari. For non-loopback network access over LAN, HTTPS is required for microphone permissions.

---

## 🧪 Live Integration Tests

The repository includes scripts for testing real desktop integrations without mocks:

```powershell
# Spotify Desktop: playback, queueing, and controls
python scripts\test_live_spotify.py --queue "Save Your Tears The Weeknd"
python scripts\test_live_spotify.py --controls

# Voice biometrics
python scripts\test_live_voice_id.py --status
python scripts\test_live_voice_id.py --register "Administrator"
python scripts\test_live_voice_id.py --identify
python scripts\test_live_voice_id.py --list
```

### Automated Quality Checks

```powershell
pytest -q
python -m ruff check src/backend
python -m compileall -q start_app.py src/backend
```

---

## 📁 Architecture

```text
JARVIS/
├── src/
│   ├── backend/
│   │   ├── api/                 # HTTP + WebSocket endpoints
│   │   ├── core/                # orchestration, LLM providers, policies, plans
│   │   ├── engines/             # TTS, speaker ID, RAG/database engines
│   │   ├── modules/
│   │   │   ├── spotify/         # Spotify Desktop + Web API
│   │   │   └── youtube/         # YouTube search/playback service
│   │   ├── services/            # memory, monitoring, security, Telegram
│   │   ├── tools/               # browser, desktop, search, system, utilities
│   │   └── voice/               # Gemini Live + hybrid/fallback voice pipelines
│   └── frontend/
│       ├── static/               # HUD CSS/JS, live voice, widgets, audio
│       └── templates/            # desktop web HUD
├── models/                       # local Piper voice models
├── scripts/                      # live tests + release/archive builders
├── third_party/                  # notices, model cards, bundled licenses
└── start_app.py                  # main launcher
```

The Gemini Live implementation dynamically exposes the registered base tool schemas to the live model, executes requested functions through the same J.A.R.V.I.S. tool layer, and returns tool results to the active voice session.

---

## 🤝 Contributing

Contributions, integrations, tools, and fixes are welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

---

## 📜 License & Third-Party Notices

J.A.R.V.I.S. source code is licensed under the [MIT License](LICENSE).

Bundled voice models, ML components, third-party libraries, and assets retain their respective licenses. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and the files under `third_party/` for details.
