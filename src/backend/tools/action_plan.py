"""Action-plan tools for supervised multi-step computer control."""

from __future__ import annotations

import json
from typing import Any

from core.action_plans import (
    create_action_plan,
    finish_action_plan,
    get_action_plan,
    list_action_plans,
    mark_plan_running,
    record_step_result,
)
from core.service_container import services
from langchain_core.tools import tool


def _loads_steps(pasos_json: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(str(pasos_json or ""))
    except Exception as exc:
        raise ValueError(f"pasos_json no es JSON valido: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("pasos_json debe contener una lista de pasos")
    return data


def _plan_widget(plan: dict[str, Any]) -> str:
    return f"<WIDGET>{json.dumps({'type': 'action_plan', 'data': plan}, ensure_ascii=False)}</WIDGET>"


def _invoke_plan_tool(tool_name: str, args: dict, user_input: str) -> str:
    if callable(services.invocar_tool):
        return str(
            services.invocar_tool(
                tool_name,
                args,
                user_input,
                source="action_plan",
                profile_id=None,
            )
        )
    from core.brain.tool_manager import _invocar_tool_entry

    return str(
        _invocar_tool_entry(
            tool_name,
            args,
            user_input,
            source="action_plan",
            profile_id=None,
        )
    )


@tool
def crear_plan_acciones(objetivo: str, pasos_json: str) -> str:
    """Crea un plan supervisado de acciones locales sin ejecutarlo."""
    try:
        plan = create_action_plan(
            objetivo,
            _loads_steps(pasos_json),
            created_by="jarvis_tool",
        )
        return (
            f"Plan de acciones preparado. ID: {plan['id']}. "
            "Revise el plan y confirme antes de ejecutarlo.\n"
            f"{_plan_widget(plan)}"
        )
    except Exception as exc:
        return f"Error creando plan de acciones: {exc}"


@tool
def ver_plan_acciones(plan_id: str = "", limite: int = 5) -> str:
    """Muestra un plan de acciones pendiente o los planes recientes."""
    try:
        if str(plan_id or "").strip():
            plan = get_action_plan(plan_id)
            return f"Plan {plan['id']} ({plan['status']}): {plan['goal']}\n{_plan_widget(plan)}"
        plans = list_action_plans(limit=limite)
        if not plans:
            return "No hay planes de acciones registrados."
        lines = ["Planes de acciones recientes:"]
        for plan in plans:
            lines.append(f"- {plan['id']} [{plan['status']}]: {plan['goal']}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error consultando plan de acciones: {exc}"


@tool
def ejecutar_plan_acciones(plan_id: str, confirmar: bool = False) -> str:
    """Ejecuta un plan de acciones previamente creado solo con confirmacion explicita."""
    if not confirmar:
        return (
            "ACCESO_DENEGADO: Este plan requiere confirmacion explicita. "
            "Vuelva a llamar ejecutar_plan_acciones con confirmar=true."
        )

    try:
        plan = mark_plan_running(plan_id)
    except Exception as exc:
        return f"Error ejecutando plan: {exc}"

    lines = [f"Ejecutando plan {plan['id']}: {plan['goal']}"]
    final_status = "completed"
    for step in plan["steps"]:
        tool_name = step["tool"]
        args = dict(step.get("args") or {})
        user_input = f"plan {plan['id']}: {step['description']}"
        result = _invoke_plan_tool(tool_name, args, user_input)
        result_text = str(result)
        failed = (
            "acceso_denegado" in result_text.lower()
            or result_text.lower().startswith("error")
            or "no se pudo" in result_text.lower()
        )
        record_step_result(
            plan["id"],
            int(step["index"]),
            "failed" if failed else "completed",
            result_text,
        )
        lines.append(f"- Paso {step['index']} {tool_name}: {result_text}")
        if failed:
            final_status = "failed"
            break

    completed = finish_action_plan(plan["id"], final_status)
    if final_status == "completed":
        return f"Plan ejecutado: {completed['id']}.\n" + "\n".join(lines[1:])
    return f"Plan detenido: {completed['id']}.\n" + "\n".join(lines[1:])
