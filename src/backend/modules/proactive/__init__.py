"""Proactive Intelligence and Desktop Context Perception module."""

from modules.proactive.models import (
    AlertSeverity,
    DesktopContext,
    PredictedIntention,
    ProactiveAction,
    ProactiveAlert,
    UserRoutine,
)
from modules.proactive.predictor import (
    WorkflowPredictorEngine,
    workflow_predictor,
)
from modules.proactive.reasoner import ProactiveReasoner
from modules.proactive.service import (
    ProactiveObserverService,
    proactive_service,
)
from modules.proactive.tools import (
    aprender_patron_rutina,
    consultar_estado_proactivo,
    consultar_rutinas_predictivas,
    ejecutar_accion_proactiva,
    predecir_siguiente_accion,
)
from modules.proactive.watcher import DesktopContextWatcher

__all__ = [
    "AlertSeverity",
    "DesktopContext",
    "DesktopContextWatcher",
    "PredictedIntention",
    "ProactiveAction",
    "ProactiveAlert",
    "ProactiveObserverService",
    "ProactiveReasoner",
    "UserRoutine",
    "WorkflowPredictorEngine",
    "aprender_patron_rutina",
    "consultar_estado_proactivo",
    "consultar_rutinas_predictivas",
    "ejecutar_accion_proactiva",
    "predecir_siguiente_accion",
    "proactive_service",
    "workflow_predictor",
]
