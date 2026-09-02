"""Central Proactive Intelligence Service."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from core import jarvis_state

from modules.proactive.models import DesktopContext, ProactiveAlert
from modules.proactive.predictor import WorkflowPredictorEngine, workflow_predictor
from modules.proactive.reasoner import ProactiveReasoner
from modules.proactive.watcher import DesktopContextWatcher

logger = logging.getLogger(__name__)

ActionInvoker = Callable[[str, dict[str, Any], str, str, str], Any]


class ProactiveObserverService:
    """Orchestrates continuous desktop context inspection and proactive assistance."""

    def __init__(
        self,
        watcher: DesktopContextWatcher | None = None,
        reasoner: ProactiveReasoner | None = None,
        predictor: WorkflowPredictorEngine | None = None,
        action_invoker: ActionInvoker | None = None,
    ) -> None:
        self.watcher = watcher or DesktopContextWatcher()
        self.reasoner = reasoner or ProactiveReasoner()
        self.predictor = predictor or workflow_predictor
        self._lock = threading.RLock()
        self._alerts: list[ProactiveAlert] = []
        self._last_context: DesktopContext | None = None
        self._action_invoker = action_invoker

    def scan_cycle(self, inspect_buffer_text: str = "") -> list[ProactiveAlert]:
        """Perform one perceptual check and update proactive alerts list."""
        ctx = self.watcher.capture_context(inspect_buffer_text)
        with self._lock:
            self._last_context = ctx

        new_alerts = self.reasoner.evaluate_context(ctx)
        if new_alerts:
            with self._lock:
                for alert in new_alerts:
                    self._alerts.append(alert)
                # Keep last 15 alerts max
                self._alerts = self._alerts[-15:]

            # Sync to global security/HUD proactive state if available
            try:
                from services import security_manager

                with security_manager.PROACTIVE_LOCK:
                    for alert in new_alerts:
                        security_manager.PROACTIVE_STATE["alerts"].append(alert.to_dict())
                    security_manager.PROACTIVE_STATE["alerts"] = security_manager.PROACTIVE_STATE["alerts"][-10:]
            except Exception:
                pass

        return new_alerts

    def get_active_alerts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [a.to_dict() for a in self._alerts]

    def get_last_context(self) -> DesktopContext | None:
        with self._lock:
            return self._last_context

    def _invoke_action(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_input: str,
        source: str,
        profile_id: str,
    ) -> Any:
        invoker = self._action_invoker
        if invoker is None:
            from core.brain import tool_manager

            invoker = tool_manager._invocar_tool_entry
        return invoker(tool_name, arguments, user_input, source, profile_id)

    def execute_action(self, action_id: str, profile_id: str | None = None) -> str:
        """Execute the suggested proactive action."""
        with self._lock:
            target_alert = None
            for a in self._alerts:
                if a.suggested_action and a.suggested_action.action_id == action_id:
                    target_alert = a
                    break

        if not target_alert or not target_alert.suggested_action:
            return f"No se encontro ninguna accion proactiva con ID '{action_id}'."

        act = target_alert.suggested_action
        try:
            tool_name = act.command_or_tool
            profile_key = jarvis_state.normalize_profile_id(
                profile_id,
                jarvis_state.get_active_profile_id(),
            )
            result = self._invoke_action(
                tool_name,
                dict(act.arguments),
                f"Ejecuta la accion proactiva: {act.title}",
                "proactive_action",
                profile_key,
            )
            if str(result or "").strip().lower().startswith("access_denied:"):
                return f"Accion proactiva '{act.title}' pendiente: {result}"
            return f"Accion proactiva '{act.title}' ejecutada: {result}"
        except Exception as exc:
            return f"Error ejecutando accion proactiva: {exc}"


# Global singleton instance
proactive_service = ProactiveObserverService()
