"""Predictive Workflow and User Routine Intelligence Engine."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Any

from core.jarvis_config import RUNTIME_DIR

from modules.proactive.models import (
    DesktopContext,
    PredictedIntention,
    ProactiveAction,
    UserRoutine,
)

logger = logging.getLogger(__name__)


class WorkflowPredictorEngine:
    """Anticipates user workflows and intentions based on context, schedules, and active tools."""

    def __init__(self, storage_path: str | None = None) -> None:
        self.storage_path = storage_path or os.path.join(RUNTIME_DIR, "user_routines.json")
        self._lock = threading.RLock()
        self._routines: list[UserRoutine] = []
        self._load_routines()

    def _load_routines(self) -> None:
        with self._lock:
            if not os.path.exists(self.storage_path):
                self._routines = []
                return
            try:
                with open(self.storage_path, encoding="utf-8") as f:
                    raw = json.load(f)
                    self._routines = [UserRoutine.from_dict(item) for item in raw if isinstance(item, dict)]
            except Exception as exc:
                logger.warning("Failed to load user routines: %s", exc)
                self._routines = []

    def _save_routines(self) -> None:
        with self._lock:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(self.storage_path)), exist_ok=True)
                with open(self.storage_path, "w", encoding="utf-8") as f:
                    json.dump([r.to_dict() for r in self._routines], f, indent=2)
            except Exception as exc:
                logger.error("Failed to save user routines: %s", exc)

    def learn_routine(
        self,
        name: str,
        trigger_type: str,
        trigger_condition: str,
        actions: list[dict[str, Any]],
        days_of_week: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6),
    ) -> UserRoutine:
        """Register a new proactive routine or learned behavioral pattern."""
        routine = UserRoutine(
            routine_id=f"rt_{uuid.uuid4().hex[:8]}",
            name=name.strip(),
            trigger_type=trigger_type.strip().lower(),
            trigger_condition=trigger_condition.strip(),
            actions=tuple(actions),
            days_of_week=days_of_week,
        )
        with self._lock:
            self._routines.append(routine)
            self._save_routines()
        return routine

    def list_routines(self) -> list[UserRoutine]:
        with self._lock:
            return list(self._routines)

    def delete_routine(self, routine_id: str) -> bool:
        with self._lock:
            initial_len = len(self._routines)
            self._routines = [r for r in self._routines if r.routine_id != routine_id]
            if len(self._routines) != initial_len:
                self._save_routines()
                return True
            return False

    def predict(
        self,
        context: DesktopContext | None = None,
        now: datetime | None = None,
    ) -> list[PredictedIntention]:
        """Generate ranked list of predicted intentions based on current state."""
        dt = now or datetime.now()
        current_hour = dt.hour
        current_weekday = dt.weekday()

        predictions: list[PredictedIntention] = []

        # 1. Evaluate Learned / User-Defined Routines
        with self._lock:
            for routine in self._routines:
                if current_weekday not in routine.days_of_week:
                    continue

                matched = False
                conf = 0.50

                if routine.trigger_type == "time":
                    # Check hour/min proximity
                    try:
                        r_hour, r_min = map(int, routine.trigger_condition.split(":"))
                        diff_minutes = abs((current_hour * 60 + dt.minute) - (r_hour * 60 + r_min))
                        if diff_minutes <= 25:
                            matched = True
                            conf = max(0.60, 0.95 - (diff_minutes * 0.015))
                    except Exception:
                        pass
                elif routine.trigger_type == "app" and context:
                    app_cond = routine.trigger_condition.lower()
                    if app_cond in context.active_app.lower() or app_cond in context.window_title.lower():
                        matched = True
                        conf = 0.85

                if matched:
                    first_action = routine.actions[0] if routine.actions else {}
                    act = ProactiveAction(
                        action_id=f"act_routine_{routine.routine_id}",
                        title=f"Ejecutar {routine.name}",
                        command_or_tool=first_action.get("tool", "ejecutar_accion_proactiva"),
                        arguments=first_action.get("args", {}),
                        description=f"Rutina programada para {routine.trigger_condition}",
                    )
                    predictions.append(
                        PredictedIntention(
                            prediction_id=f"pred_rt_{routine.routine_id}",
                            title=f"Rutina: {routine.name}",
                            rationale=f"Coincide con tu patrón programado ({routine.trigger_type} = {routine.trigger_condition})",
                            confidence=conf,
                            action=act,
                        )
                    )

        # 2. Built-in Context Heuristics

        # Heuristic A: Morning Kickoff & Briefing (06:00 - 11:00)
        if 6 <= current_hour <= 11:
            predictions.append(
                PredictedIntention(
                    prediction_id="pred_morning_briefing",
                    title="Briefing y Estado Matutino",
                    rationale="Es hora de inicio de jornada. Puedes revisar el clima, noticias y tareas pendientes.",
                    confidence=0.75,
                    action=ProactiveAction(
                        action_id="act_morning_briefing",
                        title="Generar Briefing",
                        command_or_tool="consultar_memoria_unificada",
                        arguments={"consulta": "tareas proyectos pendientes"},
                        description="Consulta las tareas y contexto pendiente para hoy.",
                    ),
                )
            )

        # Heuristic B: Development & Coding Session
        if context and (context.is_ide or context.is_terminal or "code" in context.active_app.lower()):
            predictions.append(
                PredictedIntention(
                    prediction_id="pred_dev_session",
                    title="Sesión de Desarrollo Activa",
                    rationale=f"Detectado entorno de desarrollo ({context.active_app}). Sugerir consultar notas del proyecto o música de concentración.",
                    confidence=0.82,
                    action=ProactiveAction(
                        action_id="act_dev_music",
                        title="Poner Música de Concentración",
                        command_or_tool="reproducir_musica_spotify",
                        arguments={"busqueda": "Synthwave Lofi coding focus", "tipo": "track"},
                        description="Inicia música de enfoque en Spotify.",
                    ),
                )
            )

        # Heuristic C: Night Consolidation & Sleep Cycle (22:00 - 04:00)
        if current_hour >= 22 or current_hour <= 4:
            predictions.append(
                PredictedIntention(
                    prediction_id="pred_night_consolidation",
                    title="Consolidación de Memoria Nocturna",
                    rationale="Fin de jornada detectado. Sugerir consolidar recuerdos y optimizar la base de datos mental.",
                    confidence=0.78,
                    action=ProactiveAction(
                        action_id="act_night_consolidate",
                        title="Consolidar Bóveda y Memoria",
                        command_or_tool="ejecutar_consolidacion_memoria",
                        arguments={},
                        description="Ejecuta el ciclo de consolidación nocturna de notas y grafo cognitivo.",
                    ),
                )
            )

        # Heuristic D: Afternoon / General Productivity (12:00 - 21:59)
        if 12 <= current_hour <= 21:
            predictions.append(
                PredictedIntention(
                    prediction_id="pred_afternoon_productivity",
                    title="Productividad y Enfoque de Tarde",
                    rationale="Jornada de tarde activa. Puedes revisar el estado de proyectos o ejecutar tareas pendientes.",
                    confidence=0.72,
                    action=ProactiveAction(
                        action_id="act_afternoon_check",
                        title="Revisar Proyectos Activos",
                        command_or_tool="consultar_memoria_unificada",
                        arguments={"consulta": "proyectos activos"},
                        description="Revisa los proyectos y tareas activas en la memoria cognitiva.",
                    ),
                )
            )

        # Sort descending by confidence
        predictions.sort(key=lambda p: p.confidence, reverse=True)
        return predictions


# Global singleton instance
workflow_predictor = WorkflowPredictorEngine()
