# J.A.R.V.I.S

[🇺🇸 English](#english) | [🇪🇸 Español](#español)

![J.A.R.V.I.S. HUD](./media/readme.png)

---

<a id="english"></a>

# 🇺🇸 English

J.A.R.V.I.S. is a local desktop AI assistant with a Python/Quart backend, voice input capabilities, Piper TTS, profile memory, local desktop tools, and optional integrations (Spotify, Telegram).

The primary target is Windows. Some audio, desktop-control, and system telemetry features depend on Windows APIs.

**⚠️ Important Recommendation:**
We highly recommend running J.A.R.V.I.S. via the standalone desktop application (`start_app.py`) rather than your standard web browser. Traditional web browsers often experience microphone stability and permission issues which can disrupt the voice-activation features.

**🛑 Disclaimer (Alpha Phase):**
J.A.R.V.I.S. is currently in an **Alpha** stage. Please note that the system **contains several known bugs**, some features may be unstable, and the overall experience is still being refined. Use it at your own risk and feel free to report any issues you find in the Issues section.

## ✨ Key Features

- Voice chat with Whisper transcription and Piper TTS.
- Real-time language switching between English and Spanish for prompts, TTS, UI labels, and assistant responses.
- Administrator voice registration with multi-sample biometric profiles.
- Guest recognition and short voice registration from phrases like "my name is Daniel" or "me llamo Daniel".
- Per-profile memory: private facts, chat history, and shared facts.
- Spotify playback commands and optional automix behavior.
- Weather, news, reminders, and dynamic tool routing for time-sensitive queries.
- Telegram bot integration with `TELEGRAM_CHAT_ID` filtering.
- Local system control gated by authorization and a security policy.
- Observability endpoints for metrics, security auditing, voice identity diagnostics, and proactive monitoring.
- Desktop shell using `pywebview` with persistent WebView2 storage.

## 🏛️ Architecture

Main backend entry points:

- `src/backend/jarvis_backend.py`: Main Quart/Hypercorn app at `http://localhost:5002`.
- `src/backend/core/jarvis_brain.py`: Public brain facade.
- `src/backend/core/brain/`: LLM routing, prompts, tool manager, and security engine.
- `src/backend/api/`: HTTP adapters.
- `src/backend/voice/`: Voice domain modules.
- `src/backend/tools/`: Built-in assistant tools.
- `src/backend/services/`: Monitoring, Telegram, memory, and security.

## 🛠️ Prerequisites & Local Dependencies

- **Python 3.11 or 3.12**
- **Git LFS**: Required to download the large voice models correctly.
- **FFmpeg**: Must be installed and added to your system `PATH` for audio conversion.
- **eSpeak NG** (Windows): Required for Piper phonemization. Default installation path should be `C:\Program Files\eSpeak NG`.

### ⚡ Quick Install for Windows (Using Winget)

Open a PowerShell terminal as Administrator and run:

```powershell
winget install Git.Git
winget install GitHub.GitLFS
winget install Gyan.FFmpeg
winget install eSpeak-NG.eSpeak-NG
```

## 🚀 Clone & Setup

1. **Clone the repository:**
   Make sure you have Git LFS installed *before* cloning.

   ```powershell
   git lfs install
   git clone <repo-url>
   cd JARVIS
   git lfs pull
   ```

2. **Run the setup script:**
   - **Windows:** `.\setup.ps1`
   - **Linux/macOS/WSL:** `./setup.sh`

## 💻 Running J.A.R.V.I.S

To start the assistant with the integrated Desktop Application (Recommended):

```powershell
python start_app.py
```

*If you strictly only want to run the backend and use a browser (not recommended), run `python src/backend/jarvis_backend.py` and navigate to `http://localhost:5002`.*

## ⚙️ Environment Variables (.env)

Copy `.env.example` to `.env` and add your API keys. Minimum required:

- `GROQ_API_KEY`: Your Groq API key for fast LLM inference.
- `JARVIS_API_TOKEN`: Recommended token for critical API routes.

## 🔒 Security Model

Tool execution is governed by the formal policy table in `src/backend/core/security/tool_policy.py`. Critical tools (e.g., read files, control PC) require an authorized administrator session. Keep J.A.R.V.I.S. bound to `localhost` unless you add production-grade authentication.

## 🧪 Testing

Run the full verification suite before committing:

```powershell
ruff check src\ tests\
python -m pytest -q -p no:cacheprovider
```

---

<a id="español"></a>

# 🇪🇸 Español

J.A.R.V.I.S. es un asistente de IA local de escritorio con un backend en Python/Quart, capacidades de entrada de voz, TTS Piper, memoria por perfil, herramientas de escritorio locales e integraciones opcionales (Spotify, Telegram).

El objetivo principal es Windows. Algunas funciones de audio, control de escritorio y telemetría del sistema dependen de las API de Windows.

**⚠️ Recomendación Importante:**
Recomendamos encarecidamente ejecutar J.A.R.V.I.S. a través de la aplicación de escritorio (`start_app.py`) en lugar de tu navegador web estándar. Los navegadores tradicionales suelen tener problemas de estabilidad y permisos con el micrófono.

**🛑 Disclaimer (Fase Alpha):**
J.A.R.V.I.S. se encuentra actualmente en una etapa **Alpha**. Ten en cuenta que el sistema **contiene varios errores y bugs conocidos**, algunas funciones podrían ser inestables y la experiencia en general aún está siendo pulida. Úsalo bajo tu propia responsabilidad y siéntete libre de reportar los fallos que encuentres en la sección de Issues.

## ✨ Funciones Principales

- Chat por voz con transcripción Whisper y TTS Piper.
- Cambio de idioma en tiempo real entre inglés y español para prompts, TTS, etiquetas de UI y respuestas.
- Registro de voz del administrador con perfil biométrico de varias muestras.
- Reconocimiento de invitados y registro corto de voz.
- Memoria por perfil: facts privados, historial de chat y facts compartidos.
- Comandos de reproducción en Spotify y comportamiento opcional de automix.
- Clima, noticias, recordatorios y ruteo dinámico de herramientas.
- Integración con bot de Telegram y filtrado por `TELEGRAM_CHAT_ID`.
- Control local del sistema detrás de autorización y política de seguridad.
- Endpoints de observabilidad para métricas, auditoría y monitoreo.
- Shell de escritorio con pywebview y almacenamiento persistente de WebView2.

## 🏛️ Arquitectura

Entradas principales del backend:

- `src/backend/jarvis_backend.py`: app principal Quart/Hypercorn en `http://localhost:5002`.
- `src/backend/core/jarvis_brain.py`: fachada pública del cerebro.
- `src/backend/core/brain/`: ruteo LLM, prompts, tool manager y motor de seguridad.
- `src/backend/api/`: adaptadores HTTP.
- `src/backend/voice/`: módulos del dominio de voz.
- `src/backend/tools/`: herramientas integradas del asistente.
- `src/backend/services/`: monitoreo, Telegram, memoria y seguridad.

## 🛠️ Requisitos del Sistema

- **Python 3.11 o 3.12**
- **Git LFS**: Requerido para descargar los modelos de voz pesados.
- **FFmpeg**: Debe estar instalado y en el `PATH` para conversión de audio.
- **eSpeak NG** (Windows): Requerido para la fonemización de Piper (`C:\Program Files\eSpeak NG`).

### ⚡ Instalación Rápida en Windows (Usando Winget)

Abre PowerShell como Administrador y ejecuta:

```powershell
winget install Git.Git
winget install GitHub.GitLFS
winget install Gyan.FFmpeg
winget install eSpeak-NG.eSpeak-NG
```

## 🚀 Clonación e Instalación

1. **Clonar el repositorio:**
   Asegúrate de tener Git LFS instalado *antes* de clonar.

   ```powershell
   git lfs install
   git clone <repo-url>
   cd JARVIS
   git lfs pull
   ```

2. **Ejecutar el script de setup:**
   - **Windows:** `setup.bat` (o `.\setup.ps1` en PowerShell)
   - **Linux/macOS/WSL:** `./setup.sh`

## 💻 Ejecutando J.A.R.V.I.S

Para iniciar el asistente con la aplicación de escritorio (Recomendado):

```powershell
python start_app.py
```

*Si solo quieres levantar el backend y usar un navegador (no recomendado), ejecuta `python src/backend/jarvis_backend.py` y navega a `http://localhost:5002`.*

## ⚙️ Variables de Entorno (.env)

Copia `.env.example` a `.env` y añade tus claves de API. Requerido como mínimo:

- `GROQ_API_KEY`: Para respuestas principales de chat/modelo.
- `JARVIS_API_TOKEN`: Token recomendado para rutas críticas de API.

## 🔒 Modelo de Seguridad

La ejecución de herramientas se gobierna desde la política en `src/backend/core/security/tool_policy.py`. Herramientas críticas requieren sesión autorizada de administrador. Mantén J.A.R.V.I.S. enlazado a `localhost`.

## 🧪 Pruebas

Ejecuta la verificación completa antes de hacer commit:

```powershell
ruff check src\ tests\
python -m pytest -q -p no:cacheprovider
```
