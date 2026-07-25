import logging
import threading
import time as _time
from typing import Any

from core import jarvis_state
from core.brain import brain_state
from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_ID = jarvis_state.DEFAULT_PROFILE_ID
MAX_HISTORY_LENGTH = 40
PENDING_ACTION_TIMEOUT = 300  # Segundos (5 minutos)

_PENDING_AUTH_ACTIONS: dict[str, dict[str, Any]] = {}
_PENDING_AUTH_LOCK = threading.RLock()


def _registrar_accion_pendiente_auth(profile_id: str, tool_name: str, args: dict, user_input: str) -> None:
    """
    Registra una acción que requiere confirmación por parte del usuario.
    """
    pid = str(profile_id or DEFAULT_PROFILE_ID).strip().lower() or DEFAULT_PROFILE_ID
    with _PENDING_AUTH_LOCK:
        _PENDING_AUTH_ACTIONS[pid] = {
            "tool": str(tool_name or "").strip(),
            "args": dict(args or {}),
            "user_input": str(user_input or "").strip(),
            "created_at": _time.time(),
        }
        logger.debug(f"Acción pendiente de auth registrada para {pid}: {tool_name}")


def _extraer_accion_pendiente_auth(profile_id: str, *, pop: bool = False) -> dict | None:
    """
    Extrae o lee la acción pendiente de autorización, descartándola si ha expirado.
    """
    pid = str(profile_id or DEFAULT_PROFILE_ID).strip().lower() or DEFAULT_PROFILE_ID

    with _PENDING_AUTH_LOCK:
        action = _PENDING_AUTH_ACTIONS.get(pid)
        if not action:
            return None

        # Validar expiración (limpieza automática si pasó el tiempo)
        if _time.time() - action["created_at"] > PENDING_ACTION_TIMEOUT:
            logger.debug(f"Acción pendiente para {pid} expiró y fue descartada.")
            _PENDING_AUTH_ACTIONS.pop(pid, None)
            return None

        if pop:
            logger.debug(f"Acción pendiente extraída (pop) para {pid}.")
            return _PENDING_AUTH_ACTIONS.pop(pid, None)

        return action


def _get_history_for_profile(pid: str) -> list:
    """
    Obtiene el historial de chat de un perfil específico.
    """
    with brain_state.memoria_lock:
        return list((jarvis_state._perfiles_memoria.get(pid) or {}).get("history", []))


def _append_to_profile_history(
    pid: str, human_msg: HumanMessage, ai_msg: AIMessage, tool_results: list[str] | None = None
) -> None:
    """
    Añade mensajes al historial, inyectando resultados de herramientas si existen
    y truncando el historial para no superar MAX_HISTORY_LENGTH.
    """
    with brain_state.memoria_lock:
        perfil = jarvis_state._perfiles_memoria.setdefault(pid, {"history": [], "facts": ""})
        h = perfil.setdefault("history", [])
        h.append(human_msg)

        if tool_results:
            # Casteo seguro a str por si el LLM devuelve un dict/list en el content
            combined = str(ai_msg.content) + "\n\n[Datos verificados en tiempo real: " + " | ".join(tool_results) + "]"
            h.append(AIMessage(content=combined))
        else:
            h.append(ai_msg)

        # Truncamiento de historial a MAX_HISTORY_LENGTH
        if len(h) > MAX_HISTORY_LENGTH:
            h[:] = h[-MAX_HISTORY_LENGTH:]

    # Mantener sincronizado el fallback global
    if pid == jarvis_state.DEFAULT_PROFILE_ID and jarvis_state._perfiles_memoria.get(pid):
        jarvis_state.chat_history[:] = jarvis_state._perfiles_memoria[pid]["history"]
