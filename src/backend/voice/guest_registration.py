"""Guest profile registration persistence."""

from __future__ import annotations

from core.runtime_logger import log_warning


def persist_guest_profile_registration(profile_id: str, nombre: str, user_text: str, reply: str) -> None:
    """Persist a guest name fact plus the first registration exchange."""
    try:
        from langchain_core.messages import AIMessage, HumanMessage
        from services.memory_manager import memory_manager
        from tools.memory import _fusionar_facts_memoria, guardar_memoria_async

        pdata = memory_manager.get_profile_data(profile_id)
        fact = f"Nombre del usuario: {nombre}"
        facts = _fusionar_facts_memoria(pdata.get("facts", ""), [fact])
        memory_manager.set_facts(profile_id, facts)
        memory_manager.append_history(
            profile_id,
            [
                HumanMessage(content=user_text),
                AIMessage(content=reply),
            ],
        )
        guardar_memoria_async(profile_id)
    except Exception as e:
        log_warning(
            "guest_profile_memory_persist_failed",
            profile_id=profile_id,
            error=str(e),
        )
