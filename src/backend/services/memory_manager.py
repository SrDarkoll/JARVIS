"""Thread-safe, profile-isolated conversation memory."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from core import jarvis_state
from core.command_pipeline.models import CommandRequest, CommandResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

MAX_PROFILE_HISTORY = 40


@dataclass(frozen=True, slots=True)
class ProfileMemorySnapshot:
    """Immutable view of one profile's conversation state."""

    profile_id: str
    history: tuple[BaseMessage, ...]
    facts: str
    message_count: int


class MemoryManager:
    """Own all profile memory reads and mutations under one lock."""

    def __init__(self) -> None:
        self.lock = jarvis_state.memoria_lock
        self._profiles = jarvis_state._perfiles_memoria
        self._message_counts = jarvis_state._msg_counter_by_profile
        self._default_id = jarvis_state.DEFAULT_PROFILE_ID

    def snapshot(self, profile_id: str) -> ProfileMemorySnapshot:
        profile = jarvis_state.normalize_profile_id(
            profile_id,
            self._default_id,
        )
        with self.lock:
            data = self._profiles.setdefault(
                profile,
                {"history": [], "facts": ""},
            )
            return ProfileMemorySnapshot(
                profile_id=profile,
                history=tuple(data.get("history", ())),
                facts=str(data.get("facts", "")),
                message_count=int(self._message_counts.get(profile, 0)),
            )

    def next_message_count(self, profile_id: str) -> int:
        profile = jarvis_state.normalize_profile_id(
            profile_id,
            self._default_id,
        )
        with self.lock:
            value = int(self._message_counts.get(profile, 0)) + 1
            self._message_counts[profile] = value
            return value

    def get_history(self, profile_id: str) -> list[BaseMessage]:
        return list(self.snapshot(profile_id).history)

    def get_profile_data(self, profile_id: str) -> dict[str, Any]:
        snapshot = self.snapshot(profile_id)
        return {
            "history": list(snapshot.history),
            "facts": snapshot.facts,
        }

    def append_history(
        self,
        profile_id: str,
        messages: Iterable[BaseMessage],
    ) -> None:
        profile = jarvis_state.normalize_profile_id(
            profile_id,
            self._default_id,
        )
        with self.lock:
            data = self._profiles.setdefault(
                profile,
                {"history": [], "facts": ""},
            )
            history = data.setdefault("history", [])
            history.extend(messages)
            if len(history) > MAX_PROFILE_HISTORY:
                history[:] = history[-MAX_PROFILE_HISTORY:]

    def append_interaction(
        self,
        request: CommandRequest,
        response: CommandResponse,
    ) -> None:
        self.append_history(
            request.profile_id,
            (
                HumanMessage(content=request.text),
                AIMessage(content=response.text),
            ),
        )

    def set_profile_history(
        self,
        profile_id: str,
        history: Iterable[BaseMessage],
    ) -> None:
        profile = jarvis_state.normalize_profile_id(
            profile_id,
            self._default_id,
        )
        with self.lock:
            data = self._profiles.setdefault(
                profile,
                {"history": [], "facts": ""},
            )
            data["history"] = list(history or ())[-MAX_PROFILE_HISTORY:]

    def set_facts(self, profile_id: str, facts: str) -> None:
        profile = jarvis_state.normalize_profile_id(
            profile_id,
            self._default_id,
        )
        with self.lock:
            data = self._profiles.setdefault(
                profile,
                {"history": [], "facts": ""},
            )
            data["facts"] = str(facts or "")

    def get_all_profiles(self) -> dict[str, dict[str, Any]]:
        """Return a persistence-safe snapshot of every profile."""
        with self.lock:
            return {
                profile: {
                    "history": list(data.get("history", ())),
                    "facts": str(data.get("facts", "")),
                }
                for profile, data in self._profiles.items()
            }

    def load_snapshot(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Replace profile memory with a previously persisted snapshot."""
        with self.lock:
            self._profiles.clear()
            for profile_id, data in snapshot.items():
                profile = jarvis_state.normalize_profile_id(
                    profile_id,
                    self._default_id,
                )
                self._profiles[profile] = {
                    "history": list(data.get("history", ()))[
                        -MAX_PROFILE_HISTORY:
                    ],
                    "facts": str(data.get("facts", "")),
                }


memory_manager = MemoryManager()
