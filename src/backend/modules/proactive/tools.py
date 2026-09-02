"""LangChain tool adapters for Proactive Desktop Intelligence."""

from __future__ import annotations

from core import jarvis_state
from langchain_core.tools import tool

from modules.proactive.service import proactive_service


@tool
def consultar_estado_proactivo() -> str:
    """Inspecciona el contexto de trabajo actual en el escritorio (app activa, terminal, recursos) y retorna sugerencias proactivas si detecta errores o alertas."""
    try:
        ctx = proactive_service.watcher.capture_context()
        proactive_service.scan_cycle()
        lines = [
            f"Contexto del escritorio: App='{ctx.active_app}' | Ventana='{ctx.window_title}' | CPU={ctx.cpu_percent:.0f}% | RAM={ctx.ram_percent:.0f}%"
        ]
        if ctx.detected_errors:
            lines.append(f"Anomalias detectadas: {', '.join(ctx.detected_errors)}")

        active = proactive_service.get_active_alerts()
        if active:
            lines.append(f"Sugerencias proactivas activas ({len(active)}):")
            for a in active[-3:]:
                act_info = (
                    f" -> Accion disponible: '{a['action']['title']}' (ID: {a['action']['id']})"
                    if a.get("action")
                    else ""
                )
                lines.append(f"- [{a['severity'].upper()}] {a['message']}{act_info}")
        else:
            lines.append("No hay alertas ni anomalias activas en este momento.")

        return "\n".join(lines)
    except Exception as exc:
        return f"Error consultando estado proactivo: {exc}"


@tool
def ejecutar_accion_proactiva(action_id: str) -> str:
    """Aplica la solucion sugerida por una alerta proactiva especificada por su action_id."""
    try:
        return proactive_service.execute_action(
            action_id,
            profile_id=jarvis_state.get_active_profile_id(),
        )
    except Exception as exc:
        return f"Error ejecutando accion proactiva '{action_id}': {exc}"


@tool
def predecir_siguiente_accion(contexto_adicional: str = "") -> str:
    """Evalúa el estado del sistema, horario, app activa y patrones de rutina para predecir y sugerir la siguiente acción óptima del usuario."""
    try:
        ctx = proactive_service.watcher.capture_context(inspect_buffer_text=contexto_adicional)
        predictions = proactive_service.predictor.predict(context=ctx)
        if not predictions:
            return "No hay predicciones claras de rutina en este momento. El sistema continuará aprendiendo tus flujos de trabajo."

        lines = ["=== PREDICCIÓN PROACTIVA DE FLUJO DE TRABAJO ==="]
        for p in predictions[:3]:
            conf_pct = int(p.confidence * 100)
            act_info = f" -> Acción sugerida: '{p.action.title}' (ID: {p.action.action_id})" if p.action else ""
            lines.append(f"\n🔮 [{conf_pct}% Probabilidad] {p.title}:")
            lines.append(f"   Justificación: {p.rationale}{act_info}")

        return "\n".join(lines)
    except Exception as exc:
        return f"Error prediciendo siguiente acción: {exc}"


@tool
def aprender_patron_rutina(
    nombre_rutina: str,
    disparador: str,
    tipo_disparador: str = "time",
    herramienta_accion: str = "reproducir_musica_spotify",
    argumentos_accion: str = "",
) -> str:
    """Registra una nueva rutina aprendida o automatización predictiva basada en horario (ej: '09:00') o aplicación activa (ej: 'code.exe')."""
    try:
        import json

        args_dict = json.loads(argumentos_accion) if argumentos_accion.strip() else {}
        actions_list = [{"tool": herramienta_accion, "args": args_dict}]
        routine = proactive_service.predictor.learn_routine(
            name=nombre_rutina,
            trigger_type=tipo_disparador,
            trigger_condition=disparador,
            actions=actions_list,
        )
        return f"Rutina '{routine.name}' registrada exitosamente (ID: {routine.routine_id}). Se activará cuando {routine.trigger_type} = '{routine.trigger_condition}'."
    except Exception as exc:
        return f"Error aprendiendo patrón de rutina: {exc}"


@tool
def consultar_rutinas_predictivas() -> str:
    """Consulta todas las rutinas y patrones de comportamiento registrados en el motor predictivo."""
    try:
        routines = proactive_service.predictor.list_routines()
        if not routines:
            return "No hay rutinas personalizadas registradas todavía. Puedes crear una con 'aprender_patron_rutina'."

        lines = [f"Rutinas y patrones registrados ({len(routines)}):"]
        for r in routines:
            lines.append(
                f"- **{r.name}** (ID: `{r.routine_id}`): Disparador {r.trigger_type}='{r.trigger_condition}' -> {len(r.actions)} acciones programadas."
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error consultando rutinas predictivas: {exc}"
