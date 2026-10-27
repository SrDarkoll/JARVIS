"""Local Live planning protocol; this is not an arbitrary tool executor."""

from __future__ import annotations

import json
from typing import Any

from core.command_pipeline.task_context import FOLLOWUP_POLICIES, ContextResolutionError

CONTEXT_TOOL_NAME = "gestionar_tarea_actual"
CONTEXT_INSTRUCTION = (
    "Para seguimientos de tareas usa gestionar_tarea_actual: retry si fallo, repeat para repetir un exito, "
    "correct para corregir parametros, replace para cambiar a un destino compatible, cancel para cancelar. "
    "Cambiar de cancion o de version en Spotify es correct, NO replace: usa changes_json con cancion y omite replacement_tool. "
    "Replace se usa unicamente al pasar a OTRA herramienta, por ejemplo de Spotify a YouTube. "
    "Interpreta el significado de la frase en contexto; no exijas repetir toda la orden. "
    "Usa el action_ref del resultado anterior; si aun no llego, puedes usar 'current' solo para una tarea inequivoca. "
    "No conviertas conversacion, negaciones ni citas en seguimientos. "
    "Los cambios van en changes_json como objeto JSON; nunca incluyas permisos, roles ni confirmaciones. "
    "Si hay ambiguedad pregunta que tarea. Una accion en ejecucion no se reintenta. "
    "Cancelar no deshace efectos ya realizados ni garantiza detener un proceso iniciado. "
    "Para una orden nueva usa su herramienta normal. No reenvies una orden fallida por tu cuenta. "
    "No cambies retry por repeat para evadir un rechazo. Confirma solamente el resultado recibido."
    " Si el usuario pregunta por el estado o por que fallo una tarea, usa operation=status antes de explicarlo. "
    "Status solo consulta el recibo real y conserva una aclaracion de una tarea inequivoca; no ejecuta nada. "
    "Interrumpir la voz no significa cancelar una accion externa. Una accion puede seguir ejecutandose "
    "o haber terminado despues de la interrupcion: consulta status y no inventes un fallo de conexion."
)


def context_tool_declaration(allowed_tools: set[str]) -> dict[str, Any] | None:
    if not allowed_tools:
        return None
    media = sorted(name for name in allowed_tools if (policy := FOLLOWUP_POLICIES.get(name)) and policy.media_field)
    properties = {
        "operation": {"type": "string", "enum": ["retry", "repeat", "correct", "replace", "cancel", "status"],
                      "description": "status consulta el resultado sin ejecutar; correct cambia argumentos en la MISMA herramienta (incluye otra cancion); replace cambia a OTRA herramienta."},
        "action_ref": {"type": "string", "description": "action_ref previo o current si solo hay una tarea elegible."},
        "changes_json": {"type": "string", "description": "Objeto JSON de cambios de argumentos para correct/replace; vacio para las otras operaciones."},
    }
    if media:
        properties["replacement_tool"] = {"type": "string", "enum": media}
    return {
        "name": CONTEXT_TOOL_NAME,
        "description": "Consulta el estado o propone reintentar, repetir, corregir, sustituir o cancelar una tarea real de esta sesion. No otorga permisos ni ejecuta nombres arbitrarios.",
        "parameters": {"type": "object", "properties": properties, "required": ["operation"]},
    }


def parse_context_proposal(arguments: dict[str, Any]) -> dict[str, Any]:
    if set(arguments) - {"operation", "action_ref", "changes_json", "replacement_tool"}:
        raise ContextResolutionError("context_invalid_changes")
    operation = arguments.get("operation")
    reference = arguments.get("action_ref", "current")
    replacement_tool = arguments.get("replacement_tool", "")
    changes_json = arguments.get("changes_json", "{}")
    if changes_json == "":
        changes_json = "{}"
    if (
        not isinstance(operation, str) or operation not in {"retry", "repeat", "correct", "replace", "cancel", "status"}
        or not isinstance(reference, str) or not reference.strip() or len(reference) > 128
        or not isinstance(replacement_tool, str) or len(replacement_tool) > 100
        or not isinstance(changes_json, str) or len(changes_json) > 16_000
    ):
        raise ContextResolutionError("context_invalid_changes")
    try:
        changes = json.loads(changes_json)
    except (ValueError, TypeError):
        raise ContextResolutionError("context_invalid_changes") from None
    if not isinstance(changes, dict):
        raise ContextResolutionError("context_invalid_changes")
    return {"operation": operation, "reference": reference.strip(), "changes": changes,
            "replacement_tool": replacement_tool}


def context_error_response(code: str, language: str) -> str:
    spanish = str(language).startswith("es")
    messages = {
        "context_action_still_running": ("La tarea anterior sigue ejecutandose; no la duplique.", "The previous task is still running; I did not duplicate it."),
        "context_ambiguous": ("Hay varias tareas posibles. Cual deseas retomar?", "There are several possible tasks. Which one do you want to resume?"),
        "context_no_action_requested": ("No ejecute ninguna accion: tu mensaje no solicita actuar.", "I did not execute an action: your message does not request one."),
        "context_operation_not_supported": ("Esa tarea no admite esa modificacion o repeticion de forma segura.", "That task cannot be safely modified or repeated that way."),
        "context_media_handoff_required": ("La reproduccion anterior ya se inicio. No cambie de reproductor para evitar dos reproducciones simultaneas.", "The previous playback already started. I did not switch players to avoid simultaneous playback."),
        "context_retry_not_needed": ("El intento anterior ya termino sin fallo. Para reproducirlo de nuevo, solicita repetirlo.", "The previous attempt finished without failure. Ask to repeat it if you want a new playback."),
        "context_outcome_uncertain": ("La solicitud anterior se envió, pero su efecto no está confirmado. No la repetí para evitar duplicarla.", "The previous request was sent, but its effect is unconfirmed. I did not repeat it to avoid duplication."),
        "context_use_correction": ("Es la misma herramienta: usa gestionar_tarea_actual con operation=correct, changes_json y sin replacement_tool.", "This is the same tool: use gestionar_tarea_actual with operation=correct, changes_json and no replacement_tool."),
        "context_followup_required": ("Es un seguimiento: usa gestionar_tarea_actual con la referencia y operacion indicadas; no pidas al usuario repetir toda la orden.", "This is a follow-up: use gestionar_tarea_actual with the supplied reference and operation; do not ask the user to repeat the whole command."),
    }
    fallback = ("No pude vincular ese seguimiento. Que tarea deseas retomar?", "I could not link that follow-up. Which task do you want to resume?")
    message = messages.get(code, fallback)[0 if spanish else 1]
    state = "running" if code == "context_action_still_running" else "needs_clarification"
    return json.dumps({"ok": False, "state": state, "diagnostic_code": code, "message": message, "verified": False})
