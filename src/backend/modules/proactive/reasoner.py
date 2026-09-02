"""Proactive reasoning engine and suggestion generator."""

from __future__ import annotations

import re
import time
import uuid

from modules.proactive.models import (
    AlertSeverity,
    DesktopContext,
    ProactiveAction,
    ProactiveAlert,
)


class ProactiveReasoner:
    """Evaluates desktop context and generates prioritized, actionable advice."""

    def __init__(self, default_cooldown_seconds: int = 300) -> None:
        self.default_cooldown = default_cooldown_seconds
        self._last_alert_time: dict[str, float] = {}

    def _is_cooling_down(self, key: str, cooldown: int | None = None) -> bool:
        limit = cooldown if cooldown is not None else self.default_cooldown
        last = self._last_alert_time.get(key, 0.0)
        return (time.time() - last) < limit

    def _record_alert(self, key: str) -> None:
        self._last_alert_time[key] = time.time()

    def evaluate_context(self, context: DesktopContext) -> list[ProactiveAlert]:
        """Analyze desktop state and generate alerts that pass cooldown checks."""
        alerts: list[ProactiveAlert] = []

        # 1. ModuleNotFoundError detection in Terminal / IDE
        if "modulenotfound" in context.detected_errors:
            key = "py_module_missing"
            if not self._is_cooling_down(key, cooldown=300):
                # Try extract missing module name
                match = re.search(r"modulenotfounderror:\s*no module named\s*['\"]?([a-zA-Z0-9_\-]+)", context.window_title, re.I)
                pkg = match.group(1) if match else "dependencia"
                action = ProactiveAction(
                    action_id=str(uuid.uuid4())[:8],
                    title=f"Instalar {pkg}",
                    command_or_tool="ejecutar_comando_terminal",
                    arguments={"comando": f"pip install {pkg}"},
                    description=f"Instala el paquete Python faltante '{pkg}'.",
                )
                alerts.append(
                    ProactiveAlert(
                        alert_id=str(uuid.uuid4())[:8],
                        key=key,
                        message=f"Detecté un error: falta el módulo Python '{pkg}'. ¿Deseas instalarlo?",
                        severity=AlertSeverity.WARNING,
                        suggested_action=action,
                        details={"package": pkg, "app": context.active_app},
                    )
                )
                self._record_alert(key)

        # 2. NPM Error detection
        if "npmerror" in context.detected_errors:
            key = "npm_error"
            if not self._is_cooling_down(key, cooldown=300):
                action = ProactiveAction(
                    action_id=str(uuid.uuid4())[:8],
                    title="Verificar npm",
                    command_or_tool="ejecutar_comando_terminal",
                    arguments={"comando": "npm install"},
                    description="Reinstala o repara dependencias de Node.",
                )
                alerts.append(
                    ProactiveAlert(
                        alert_id=str(uuid.uuid4())[:8],
                        key=key,
                        message="Detecté un error de NPM en tu terminal. ¿Deseas revisar las dependencias?",
                        severity=AlertSeverity.WARNING,
                        suggested_action=action,
                    )
                )
                self._record_alert(key)

        # 3. Critical RAM Usage Spike
        if context.ram_percent >= 92.0:
            key = "ram_critical"
            if not self._is_cooling_down(key, cooldown=600):
                action = ProactiveAction(
                    action_id=str(uuid.uuid4())[:8],
                    title="Ver procesos pesados",
                    command_or_tool="ver_procesos_pesados",
                    description="Identifica qué aplicación está consumiendo más memoria.",
                )
                alerts.append(
                    ProactiveAlert(
                        alert_id=str(uuid.uuid4())[:8],
                        key=key,
                        message=f"Alerta de memoria: Uso de RAM al {context.ram_percent:.0f}%. ¿Deseas revisar procesos pesados?",
                        severity=AlertSeverity.CRITICAL,
                        suggested_action=action,
                    )
                )
                self._record_alert(key)

        # 4. Critical CPU Usage Spike
        if context.cpu_percent >= 90.0:
            key = "cpu_critical"
            if not self._is_cooling_down(key, cooldown=600):
                action = ProactiveAction(
                    action_id=str(uuid.uuid4())[:8],
                    title="Ver procesos pesados",
                    command_or_tool="ver_procesos_pesados",
                    description="Muestra los procesos que saturan el procesador.",
                )
                alerts.append(
                    ProactiveAlert(
                        alert_id=str(uuid.uuid4())[:8],
                        key=key,
                        message=f"Procesador saturado: Uso de CPU al {context.cpu_percent:.0f}%.",
                        severity=AlertSeverity.WARNING,
                        suggested_action=action,
                    )
                )
                self._record_alert(key)

        # 5. High-confidence Workflow & Routine Predictions
        try:
            from modules.proactive.predictor import workflow_predictor

            predictions = workflow_predictor.predict(context=context)
            for pred in predictions:
                if pred.confidence >= 0.75 and pred.action:
                    key = f"workflow_{pred.prediction_id}"
                    if not self._is_cooling_down(key, cooldown=900):
                        alerts.append(
                            ProactiveAlert(
                                alert_id=str(uuid.uuid4())[:8],
                                key=key,
                                message=f"Sugerencia Anticipada: {pred.title}. {pred.rationale}",
                                severity=AlertSeverity.INFO,
                                suggested_action=pred.action,
                                details={"confidence": pred.confidence},
                            )
                        )
                        self._record_alert(key)
                        break
        except Exception:
            pass

        return alerts
