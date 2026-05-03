"""Validated action plans for supervised local computer control."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from datetime import datetime
from typing import Any

MAX_PLAN_STEPS = 8

ALLOWED_PLAN_TOOLS = {
    "abrir_aplicacion",
    "abrir_navegador",
    "ajustar_volumen",
    "analizar_pantalla",
    "controlar_pc",
    "controlar_ventana",
    "ejecutar_rutina",
    "enfocar_ventana",
    "leer_pagina_navegador",
    "listar_ventanas",
    "modo_no_molestar",
    "navegar_en_navegador",
    "ver_procesos_pesados",
}

_ACTION_PLANS: dict[str, dict[str, Any]] = {}
_PLAN_LOCK = threading.RLock()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _copy_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(plan)


def _make_plan_id(goal: str, steps: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        {"goal": goal, "steps": steps, "created_at": _now_iso()},
        ensure_ascii=True,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _normalize_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not steps:
        raise ValueError("el plan requiere al menos un paso")
    if len(steps) > MAX_PLAN_STEPS:
        raise ValueError(f"el plan no puede superar {MAX_PLAN_STEPS} pasos")

    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict):
            raise ValueError("cada paso debe ser un objeto")
        tool_name = str(raw.get("tool") or "").strip()
        if tool_name not in ALLOWED_PLAN_TOOLS:
            raise ValueError(f"herramienta no permitida en planes: {tool_name or '<vacia>'}")
        args = raw.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError(f"args de {tool_name} debe ser un objeto")
        description = str(
            raw.get("description")
            or raw.get("descripcion")
            or raw.get("summary")
            or tool_name
        ).strip()
        normalized.append(
            {
                "index": index,
                "tool": tool_name,
                "args": copy.deepcopy(args),
                "description": description[:180],
                "status": "pending",
                "result": "",
            }
        )
    return normalized


def create_action_plan(
    goal: str,
    steps: list[dict[str, Any]],
    *,
    created_by: str | None = None,
) -> dict[str, Any]:
    clean_goal = str(goal or "").strip()
    if not clean_goal:
        raise ValueError("el objetivo del plan no puede estar vacio")
    normalized_steps = _normalize_steps(steps)
    plan_id = _make_plan_id(clean_goal, normalized_steps)
    plan = {
        "id": plan_id,
        "goal": clean_goal[:240],
        "status": "pending",
        "created_by": str(created_by or "jarvis")[:80],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "steps": normalized_steps,
    }
    with _PLAN_LOCK:
        while plan["id"] in _ACTION_PLANS:
            plan["id"] = hashlib.sha256(
                f"{plan['id']}:{len(_ACTION_PLANS)}".encode("utf-8")
            ).hexdigest()[:12]
        _ACTION_PLANS[plan["id"]] = _copy_plan(plan)
    return _copy_plan(plan)


def get_action_plan(plan_id: str) -> dict[str, Any]:
    key = str(plan_id or "").strip()
    with _PLAN_LOCK:
        plan = _ACTION_PLANS.get(key)
        if not plan:
            raise KeyError(f"plan no encontrado: {key}")
        return _copy_plan(plan)


def list_action_plans(limit: int = 10) -> list[dict[str, Any]]:
    max_items = max(1, min(int(limit or 10), 50))
    with _PLAN_LOCK:
        plans = list(_ACTION_PLANS.values())[-max_items:]
        return [_copy_plan(plan) for plan in plans]


def mark_plan_running(plan_id: str) -> dict[str, Any]:
    with _PLAN_LOCK:
        plan = _ACTION_PLANS.get(str(plan_id or "").strip())
        if not plan:
            raise KeyError(f"plan no encontrado: {plan_id}")
        plan["status"] = "running"
        plan["updated_at"] = _now_iso()
        return _copy_plan(plan)


def record_step_result(plan_id: str, index: int, status: str, result: str) -> dict[str, Any]:
    with _PLAN_LOCK:
        plan = _ACTION_PLANS.get(str(plan_id or "").strip())
        if not plan:
            raise KeyError(f"plan no encontrado: {plan_id}")
        step_index = int(index)
        for step in plan["steps"]:
            if int(step["index"]) == step_index:
                step["status"] = str(status or "completed")
                step["result"] = str(result or "")[:1000]
                break
        else:
            raise KeyError(f"paso no encontrado: {step_index}")
        plan["updated_at"] = _now_iso()
        return _copy_plan(plan)


def finish_action_plan(plan_id: str, status: str) -> dict[str, Any]:
    with _PLAN_LOCK:
        plan = _ACTION_PLANS.get(str(plan_id or "").strip())
        if not plan:
            raise KeyError(f"plan no encontrado: {plan_id}")
        plan["status"] = str(status or "completed")
        plan["updated_at"] = _now_iso()
        return _copy_plan(plan)
