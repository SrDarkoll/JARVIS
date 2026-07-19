"""Estado mutable compartido (recordatorios, cachés, heartbeat, perfiles). PROTEGIDO."""

import contextvars
import threading
from collections.abc import Iterator
from contextlib import contextmanager

DEFAULT_PROFILE_ID = "admin"

# Listas y estados marcados como internos para forzar uso de ServiceContainer/Services
_recordatorios: list = []
recordatorios_lock = threading.RLock()

_weather_cache = {"temp": "--", "desc": "Sincronizando...", "last_update": 0}
_noticias_cache = {"resumen": "", "listo": False, "fecha": ""}

heartbeat_state = {
    "ultimo_briefing": "",
    "ultimo_clima": 0,
    "clima_alerta_enviada": False,
    "cpu_high_streak": 0,
    "ram_high_streak": 0,
    "last_plugin_error_alert": "",
}

memoria_lock = threading.RLock()
_perfiles_memoria: dict = {}
_msg_counter_by_profile: dict = {}
_active_profile_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jarvis_active_profile_id",
    default=DEFAULT_PROFILE_ID,
)
_active_profile_id: str = DEFAULT_PROFILE_ID  # legacy, usar helpers de contexto en código nuevo


def normalize_profile_id(profile_id: str | None, fallback: str = DEFAULT_PROFILE_ID) -> str:
    pid = str(profile_id or "").strip().lower()
    return pid or fallback


def get_active_profile_id(default: str = DEFAULT_PROFILE_ID) -> str:
    pid = _active_profile_id_ctx.get(default)
    return normalize_profile_id(pid, default)


def set_active_profile_id(profile_id: str | None) -> contextvars.Token[str]:
    """Fija el perfil activo para la ejecución actual y devuelve un token restaurable."""
    pid = normalize_profile_id(profile_id)
    global _active_profile_id
    _active_profile_id = pid
    return _active_profile_id_ctx.set(pid)


def reset_active_profile_id(token: contextvars.Token[str]) -> None:
    global _active_profile_id
    try:
        _active_profile_id_ctx.reset(token)
    finally:
        _active_profile_id = get_active_profile_id()


@contextmanager
def active_profile(profile_id: str | None) -> Iterator[str]:
    token = set_active_profile_id(profile_id)
    try:
        yield get_active_profile_id()
    finally:
        reset_active_profile_id(token)

# Cache de chat para el perfil activo (dueño habitualmente)
chat_history: list = []
DATOS_CURIOSOS: str = ""
