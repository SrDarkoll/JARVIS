from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from core import jarvis_state

if TYPE_CHECKING:
    from core.command_pipeline.execution import ToolExecutionService


def _log_info(message: str, **ctx: Any) -> None:
    # Lazy: the runtime logger imports the unified log, which must not load
    # before the container during early startup.
    from core.runtime_logger import log_info

    log_info(message, **ctx)


def _log_warning(message: str, **ctx: Any) -> None:
    from core.runtime_logger import log_warning

    log_warning(message, **ctx)


class ServiceContainer:
    _instance: ServiceContainer | None = None
    _lock = threading.Lock()
    _initialized: bool = False

    # Wired during backend startup; tools degrade or fail without them.
    REQUIRED: tuple[str, ...] = (
        "obs_event",
        "obs_inc",
        "reparar_unicode",
        "security_guard",
        "invocar_tool",
        "tool_execution",
    )

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
        self.llm_fallbacks: tuple[Any, ...] = ()
        self.llm_with_tools: Any = None

        from services.reminder_service import reminder_service

        self.reminder_service = reminder_service

        # --- States and Context ---
        self.SRC_DIR: str = ""
        self.ROOT_DIR: str = ""

        # Freeze the attribute set: later assignments must target a declared slot.
        object.__setattr__(self, "_declared", frozenset(vars(self)))

    def __setattr__(self, name: str, value: Any) -> None:
        declared = getattr(self, "_declared", None)
        if declared is not None and name not in declared:
            raise AttributeError(
                f"ServiceContainer has no dependency slot {name!r}; declare it in __init__ first"
            )
        object.__setattr__(self, name, value)

    def missing_required(self) -> list[str]:
        """Names in REQUIRED that are still unset after wiring."""
        return sorted(name for name in self.REQUIRED if getattr(self, name, None) is None)

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
        _log_info("reminder_added", profile_id=record.profile_id)
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
            except Exception as exc:
                _log_warning("service_log_event_failed", event_type=event_type, error=type(exc).__name__)

    def inc_counter(self, metric: str, amount: int = 1):
        if self.obs_inc:
            try:
                self.obs_inc(metric, amount)
            except Exception as exc:
                _log_warning("service_inc_counter_failed", metric=metric, error=type(exc).__name__)


services = ServiceContainer()
