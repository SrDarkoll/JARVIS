"""Rutas, variables de entorno y orígenes CORS para JARVIS."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv  # pyright: ignore[reportMissingImports]
from utils.jarvis_i18n import get_model_path

from core.command_pipeline.reasoning import resolve_reasoning_mode
from core.runtime_paths import ensure_runtime_paths, resolve_runtime_paths

if os.getenv("JARVIS_TEST_MODE") == "1":
    os.environ.setdefault("HF_TOKEN", "fake_token_for_testing")
os.environ.setdefault("WANDB_MODE", "disabled")
os.environ.setdefault("WANDB_DISABLED", "true")
os.environ.setdefault("WANDB_DISABLE_SERVICE", "true")
os.environ.setdefault("WANDB_SILENT", "true")


@dataclass(frozen=True)
class RuntimeFeatures:
    core_mode: bool
    voice_id_enabled: bool
    allow_guest_mode: bool
    rag_enabled: bool
    vision_enabled: bool
    plugins_enabled: bool
    briefing_enabled: bool
    telegram_enabled: bool
    monitoring_enabled: bool


@dataclass(frozen=True)
class SpeechToTextConfig:
    provider: str
    groq_model: str
    local_enabled: bool
    local_model: str
    local_device: str
    local_compute_type: str
    timeout_seconds: float


def _read_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _read_float(env: Mapping[str, str], name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(str(env.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _read_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(str(env.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _read_choice(
    env: Mapping[str, str],
    name: str,
    default: str,
    choices: set[str],
) -> str:
    value = str(env.get(name, default) or default).strip().lower()
    return value if value in choices else default


def resolve_spotify_playback_mode(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    return _read_choice(
        source,
        "SPOTIFY_PLAYBACK_MODE",
        "auto",
        {"auto", "api", "desktop"},
    )


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
            source.get("JARVIS_GROQ_STT_MODEL", "whisper-large-v3-turbo") or "whisper-large-v3-turbo"
        ).strip(),
        local_enabled=_read_bool(source, "JARVIS_LOCAL_STT_ENABLED", True),
        local_model=str(source.get("JARVIS_WHISPER_MODEL", "medium") or "medium").strip(),
        local_device=str(source.get("JARVIS_WHISPER_DEVICE", "cpu") or "cpu").strip(),
        local_compute_type=str(source.get("JARVIS_WHISPER_COMPUTE_TYPE", "int8") or "int8").strip(),
        timeout_seconds=_read_float(source, "JARVIS_STT_TIMEOUT_SECONDS", 20.0, 5.0, 60.0),
    )


def resolve_runtime_features(env: Mapping[str, str] | None = None) -> RuntimeFeatures:
    source = os.environ if env is None else env
    core_mode = _read_bool(source, "JARVIS_CORE_MODE", True)
    optional_default = not core_mode
    allow_guest = _read_bool(source, "JARVIS_ALLOW_GUEST_MODE", False)
    if _read_bool(source, "JARVIS_DISABLE_GUEST_MODE", False) or _read_bool(source, "JARVIS_SINGLE_USER_MODE", False):
        allow_guest = False

    return RuntimeFeatures(
        core_mode=core_mode,
        # Voice biometrics are experimental and never enabled implicitly.
        voice_id_enabled=_read_bool(source, "JARVIS_VOICE_ID_ENABLED", False),
        allow_guest_mode=allow_guest,
        rag_enabled=_read_bool(source, "JARVIS_RAG_ENABLED", optional_default),
        vision_enabled=_read_bool(source, "JARVIS_VISION_ENABLED", optional_default),
        plugins_enabled=_read_bool(source, "JARVIS_PLUGINS_ENABLED", optional_default),
        briefing_enabled=_read_bool(source, "JARVIS_BRIEFING_ENABLED", optional_default),
        telegram_enabled=_read_bool(source, "JARVIS_TELEGRAM_ENABLED", optional_default),
        monitoring_enabled=_read_bool(source, "JARVIS_MONITORING_ENABLED", optional_default),
    )


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

RUNTIME_PATHS = ensure_runtime_paths(resolve_runtime_paths())
RUNTIME_DIR = _normalize_fs_path(str(RUNTIME_PATHS.home))
MEMORY_DIR = _normalize_fs_path(str(RUNTIME_PATHS.memory))
CACHE_DIR = _normalize_fs_path(os.getenv("JARVIS_CACHE_DIR") or str(RUNTIME_PATHS.cache))
OBS_DIR = _normalize_fs_path(os.getenv("JARVIS_LOG_DIR") or str(RUNTIME_PATHS.logs))
for _runtime_directory in (MEMORY_DIR, CACHE_DIR, OBS_DIR):
    os.makedirs(_runtime_directory, exist_ok=True)

SPEECH_TO_TEXT = resolve_speech_to_text_config()
REASONING_MODE = resolve_reasoning_mode()
MODEL_PATH = get_model_path("en")
SPOTIFY_CACHE = os.path.join(CACHE_DIR, "spotify-oauth-cache")
MEMORIA_FILE = os.path.join(MEMORY_DIR, "memoria_jarvis.json")
MEMORIA_PROFILES_FILE = os.path.join(
    MEMORY_DIR,
    "memoria_jarvis_profiles.json",
)
UNIFIED_LOG_FILE = os.path.join(OBS_DIR, "log.txt")
UNIFIED_LOG_ENABLED = (
    False
    if os.getenv("JARVIS_TEST_MODE") == "1"
    else _read_bool(
        os.environ,
        "JARVIS_UNIFIED_LOG_ENABLED",
        True,
    )
)
UNIFIED_LOG_MAX_BYTES = _read_int(
    os.environ,
    "JARVIS_UNIFIED_LOG_MAX_BYTES",
    5 * 1024 * 1024,
    64 * 1024,
    100 * 1024 * 1024,
)
UNIFIED_LOG_BACKUP_COUNT = _read_int(
    os.environ,
    "JARVIS_UNIFIED_LOG_BACKUP_COUNT",
    3,
    0,
    20,
)
BRIEFING_TELEGRAM_SENT_FILE = os.path.join(OBS_DIR, "briefing_telegram_sent.json")
OBS_LOG_FILE = os.path.join(OBS_DIR, "jarvis_events.jsonl")
SECURITY_AUDIT_FILE = os.path.join(OBS_DIR, "security_audit.jsonl")
SECURITY_POLICY_FILE = os.path.join(OBS_DIR, "security_policy.json")
TTS_PRONUN_FILE = os.path.join(MEMORY_DIR, "tts_pronunciacion.json")
PLUGINS_DIR = os.path.join(SRC_DIR, "backend", "plugins")
RUNTIME_FEATURES = resolve_runtime_features()
CORE_MODE = RUNTIME_FEATURES.core_mode
VOICE_ID_ENABLED = RUNTIME_FEATURES.voice_id_enabled
RAG_ENABLED = RUNTIME_FEATURES.rag_enabled
VISION_ENABLED = RUNTIME_FEATURES.vision_enabled
PLUGINS_ENABLED = RUNTIME_FEATURES.plugins_enabled
BRIEFING_ENABLED = RUNTIME_FEATURES.briefing_enabled
TELEGRAM_ENABLED = RUNTIME_FEATURES.telegram_enabled
MONITORING_ENABLED = RUNTIME_FEATURES.monitoring_enabled
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("JARVIS_GROQ_MODEL", "qwen/qwen3.6-27b")
GROQ_VISION_MODEL = os.getenv("JARVIS_GROQ_VISION_MODEL", "qwen/qwen3.6-27b")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
_chat_id_env = os.getenv("TELEGRAM_CHAT_ID", "0")
TELEGRAM_CHAT_ID = int(_chat_id_env) if _chat_id_env.strip().lstrip("-").isdigit() else 0
BRIEFING_HORA = 7
HEARTBEAT_INTERVALO = 300
AUTOCURACION_ACTIVA = _read_bool(os.environ, "JARVIS_AUTOCURACION", not CORE_MODE)
PROACTIVE_ACTIVO = _read_bool(os.environ, "JARVIS_PROACTIVE", not CORE_MODE)
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
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
SPOTIFY_PLAYBACK_MODE = resolve_spotify_playback_mode()
SPOTIFY_DESKTOP_START_TIMEOUT = _read_float(os.environ, "SPOTIFY_DESKTOP_START_TIMEOUT", 20.0, 5.0, 60.0)
SPOTIFY_DESKTOP_ACTION_TIMEOUT = _read_float(os.environ, "SPOTIFY_DESKTOP_ACTION_TIMEOUT", 8.0, 2.0, 30.0)
SPOTIFY_MODO_SIMILARES = os.getenv("SPOTIFY_MODO_SIMILARES", "hybrid").strip().lower()
SPOTIFY_AUTO_SHUFFLE = _read_bool(os.environ, "SPOTIFY_AUTO_SHUFFLE", False)
SPOTIFY_EXTENDED_QUOTA_MODE = _read_bool(os.environ, "SPOTIFY_EXTENDED_QUOTA_MODE", False)
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
