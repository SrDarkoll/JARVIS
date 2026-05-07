"""Rutas, variables de entorno y orígenes CORS para JARVIS."""

from __future__ import annotations

import os

from dotenv import load_dotenv  # pyright: ignore[reportMissingImports]

if os.getenv("JARVIS_TEST_MODE") == "1":
    os.environ.setdefault("HF_TOKEN", "fake_token_for_testing")
os.environ.setdefault("WANDB_MODE", "disabled")
os.environ.setdefault("WANDB_DISABLED", "true")
os.environ.setdefault("WANDB_DISABLE_SERVICE", "true")
os.environ.setdefault("WANDB_SILENT", "true")


def _normalize_fs_path(path: str) -> str:
    p = os.path.normpath(path)
    if os.name == "nt" and p.startswith("\\\\?\\"):
        # Convierte rutas extendidas de Windows (\\?\C:\...) a formato estándar.
        if p.startswith("\\\\?\\UNC\\"):
            return "\\" + p[7:]
        return p[4:]
    return p


_CORE_DIR = _normalize_fs_path(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = _normalize_fs_path(os.path.dirname(_CORE_DIR))
SRC_DIR = _normalize_fs_path(os.path.dirname(BASE_DIR))
ROOT_DIR = _normalize_fs_path(os.path.dirname(SRC_DIR))

load_dotenv(os.path.join(ROOT_DIR, ".env"))

MODEL_PATH = os.path.join(ROOT_DIR, "models", "en_GB-northern_english_male-medium.onnx")
SPOTIFY_CACHE = os.path.join(SRC_DIR, ".spotify_cache")
MEMORIA_FILE = os.path.join(BASE_DIR, "memoria_jarvis.json")
MEMORIA_PROFILES_FILE = os.path.join(BASE_DIR, "memoria_jarvis_profiles.json")
OBS_DIR = os.path.join(BASE_DIR, "logs")
BRIEFING_TELEGRAM_SENT_FILE = os.path.join(OBS_DIR, "briefing_telegram_sent.json")
OBS_LOG_FILE = os.path.join(OBS_DIR, "jarvis_events.jsonl")
SECURITY_AUDIT_FILE = os.path.join(OBS_DIR, "security_audit.jsonl")
SECURITY_POLICY_FILE = os.path.join(OBS_DIR, "security_policy.json")
TTS_PRONUN_FILE = os.path.join(BASE_DIR, "tts_pronunciacion.json")
PLUGINS_DIR = os.path.join(SRC_DIR, "backend", "plugins")
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_MODEL = os.getenv("JARVIS_MINIMAX_MODEL", "MiniMax-Text-01")
MINIMAX_VISION_MODEL = os.getenv("JARVIS_MINIMAX_VISION_MODEL", "MiniMax-VL-01")
MINIMAX_BASE_URL = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("JARVIS_GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_VISION_MODEL = os.getenv("JARVIS_GROQ_VISION_MODEL", "llama-3.2-90b-vision-preview")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
_chat_id_env = os.getenv("TELEGRAM_CHAT_ID", "0")
TELEGRAM_CHAT_ID = int(_chat_id_env) if _chat_id_env.strip().lstrip("-").isdigit() else 0
BRIEFING_HORA = 7
HEARTBEAT_INTERVALO = 300
AUTOCURACION_ACTIVA = os.getenv("JARVIS_AUTOCURACION", "true").strip().lower() == "true"
PROACTIVE_ACTIVO = os.getenv("JARVIS_PROACTIVE", "true").strip().lower() == "true"
PROACTIVE_COOLDOWN = int((os.getenv("JARVIS_PROACTIVE_COOLDOWN") or "600").strip() or "600")
STRICT_WEB_SEARCH = os.getenv("JARVIS_STRICT_WEB_SEARCH", "false").strip().lower() == "true"

# Internet Search (Google/Brave/YouTube)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

# Spotify
SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID", "")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET", "")
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI", "https://google.com/callback")
SPOTIFY_MODO_SIMILARES = os.getenv("SPOTIFY_MODO_SIMILARES", "hybrid").strip().lower()
SPOTIFY_AUTO_SHUFFLE = os.getenv("SPOTIFY_AUTO_SHUFFLE", "false").strip().lower() == "true"
SPOTIFY_AUTOMIX_PLAYLIST_NAME = os.getenv("SPOTIFY_AUTOMIX_PLAYLIST_NAME", "JARVIS AutoMix").strip()


def get_cors_origins() -> list[str]:
    """Origenes permitidos para CORS (solo LAN local por defecto)."""
    raw = os.getenv("JARVIS_CORS_ORIGINS", "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    return [
        "http://127.0.0.1:5002",
        "http://localhost:5002",
        "http://127.0.0.1",
        "http://localhost",
    ]
