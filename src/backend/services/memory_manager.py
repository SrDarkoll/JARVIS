"""
MemoryManager: Encapsulamiento del estado mutable de perfiles y chat.
Resuelve el riesgo de 'Shared Mutable State' centralizando el acceso y los locks.
"""
from __future__ import annotations

from typing import Any

from core import jarvis_state
from langchain_core.messages import AIMessage, HumanMessage


class MemoryManager:
    def __init__(self):
        self.lock = jarvis_state.memoria_lock
        self._perfiles = jarvis_state._perfiles_memoria
        self._default_id = jarvis_state.DEFAULT_PROFILE_ID
        self._global_history = jarvis_state.chat_history # Retrocompatibilidad
        self._global_facts = jarvis_state.DATOS_CURIOSOS # Retrocompatibilidad

    def get_profile_data(self, profile_id: str) -> dict[str, Any]:
        """Obtiene una copia segura de los datos de un perfil."""
        pid = (profile_id or self._default_id).strip().lower()
        with self.lock:
            data = self._perfiles.get(pid)
            if data is None:
                data = {"history": [], "facts": ""}
                self._perfiles[pid] = data
            return {
                "history": list(data.get("history", [])),
                "facts": str(data.get("facts", ""))
            }

    def append_history(self, profile_id: str, messages: list[HumanMessage | AIMessage]):
        """Agrega mensajes al historial de un perfil con límite de 40 items."""
        pid = (profile_id or self._default_id).strip().lower()
        with self.lock:
            perfil = self._perfiles.setdefault(pid, {"history": [], "facts": ""})
            hist = perfil.setdefault("history", [])
            for msg in messages:
                hist.append(msg)

            if len(hist) > 40:
                hist[:] = hist[-40:]
                perfil["history"] = hist

            # Sincronización con el historial global para el Administrador (retrocompatibilidad UI)
            if pid == self._default_id:
                self._global_history[:] = perfil["history"]

    def set_profile_history(self, profile_id: str, history: list[HumanMessage | AIMessage]):
        """Reemplaza el historial completo de un perfil (snapshot), con límite de 40 items."""
        pid = (profile_id or self._default_id).strip().lower()
        with self.lock:
            perfil = self._perfiles.setdefault(pid, {"history": [], "facts": ""})
            perfil["history"] = list(history or [])[-40:]

            if pid == self._default_id:
                self._global_history[:] = perfil["history"]

    def set_facts(self, profile_id: str, facts: str):
        """Actualiza los hechos (facts) de un perfil."""
        pid = (profile_id or self._default_id).strip().lower()
        with self.lock:
            perfil = self._perfiles.setdefault(pid, {"history": [], "facts": ""})
            perfil["facts"] = str(facts or "")

            if pid == self._default_id:
                jarvis_state.DATOS_CURIOSOS = perfil["facts"]

    def get_all_profiles(self) -> dict[str, dict[str, Any]]:
        """Devuelve un snapshot de todos los perfiles para persistencia."""
        with self.lock:
            return {pid: {"history": list(d["history"]), "facts": d["facts"]}
                    for pid, d in self._perfiles.items()}

    def load_snapshot(self, snapshot: dict[str, dict[str, Any]]):
        """Carga un snapshot completo (usado al arrancar desde DB)."""
        with self.lock:
            self._perfiles.clear()
            for pid, data in snapshot.items():
                self._perfiles[pid] = {
                    "history": list(data.get("history", [])),
                    "facts": str(data.get("facts", ""))
                }

            # Sincronizar global si existe el default
            if self._default_id in self._perfiles:
                self._global_history[:] = self._perfiles[self._default_id]["history"]
                jarvis_state.DATOS_CURIOSOS = self._perfiles[self._default_id]["facts"]

memory_manager = MemoryManager()



