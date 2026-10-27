from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from core import jarvis_state

if TYPE_CHECKING:
    from core.command_pipeline.execution import ToolExecutionService


class ServiceContainer:
    _instance: ServiceContainer | None = None
    _lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls) -> ServiceContainer:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        # --- Observability ---
        self.obs_inc: Callable[[str, int], None] | None = None
        self.obs_event: Callable[..., None] | None = None

        # --- Security ---
        self.security_audit: Callable[..., None] | None = None
        self.security_guard: Callable[..., Any] | None = None
        self.security_allow_fallback: Callable[[], bool] | None = None
        self.proactive_tool_error: Callable[..., None] | None = None

        # --- Core Utilities ---
        self.reparar_unicode: Callable[[str], str] | None = None
        self.invocar_tool: Callable[..., Any] | None = None
        self.recargar_plugins: Callable[[], str] | None = None
        self.tool_execution: ToolExecutionService | None = None

        # --- LLM Engines ---
        self.llm: Any = None
        self.llm_vision: Any = None
        self.llm_fallback: Any = None
        self.llm_with_tools: Any = None

        from services.reminder_service import reminder_service

        self.reminder_service = reminder_service

        # --- States and Context ---
        self.SRC_DIR: str = ""
        self.ROOT_DIR: str = ""

    # ─────────────────────────────────────────
    # Protected State Management
    # ─────────────────────────────────────────

    @property
    def active_profile_id(self) -> str:
        return jarvis_state.get_active_profile_id()

    @active_profile_id.setter
    def active_profile_id(self, value: str):
        jarvis_state.set_active_profile_id(value)

    @property
    def weather_cache(self) -> dict:
        return jarvis_state._weather_cache

    @property
    def noticias_cache(self) -> dict:
        return jarvis_state._noticias_cache

    def add_reminder(self, text: str, minutes: float):
        record = self.reminder_service.add(
            text,
            minutes,
            jarvis_state.get_active_profile_id(),
        )
        self.log_event("reminder_added", text=text, minutes=minutes)
        print(f"  [SERVICES] Reminder added for profile {record.profile_id}")
        return record

    def get_reminders(self, profile_id: str | None = None) -> list[dict]:
        records = self.reminder_service.list_active(
            profile_id or jarvis_state.get_active_profile_id()
        )
        return [
            {
                "id": record.reminder_id,
                "texto": record.text,
                "cuando": datetime.fromtimestamp(record.due_at),
                "profile_id": record.profile_id,
                "status": record.status,
            }
            for record in records
        ]

    def log_event(self, event_type: str, **payload):
        if self.obs_event:
            try:
                self.obs_event(event_type, **payload)
            except Exception as e:
                print(f"[WARN container] Log error {event_type}: {e}")

    def inc_counter(self, metric: str, amount: int = 1):
        if self.obs_inc:
            try:
                self.obs_inc(metric, amount)
            except Exception as e:
                print(f"[WARN container] Inc error {metric}: {e}")


services = ServiceContainer()
