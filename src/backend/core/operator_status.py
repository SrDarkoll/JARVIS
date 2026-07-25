"""Operator-mode status payloads for the local JARVIS HUD."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

try:
    from security.tool_policy import export_tool_policy_table
except ImportError:  # pragma: no cover - used when imported as core.operator_status
    from core.security.tool_policy import export_tool_policy_table


MISSION_ACTIVE_STATUSES = {"pending", "running"}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _safe_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _collapse_text(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _mission_requires_confirmation(plan: dict[str, Any], policies: dict[str, dict[str, Any]]) -> bool:
    if str(plan.get("status") or "").lower() == "pending":
        return True
    for step in _safe_list(plan.get("steps")):
        policy = _safe_dict(policies.get(str(step.get("tool") or "")))
        if bool(policy.get("requires_confirmation")):
            return True
        if str(policy.get("risk_level") or "").lower() == "critical":
            return True
    return False


def summarize_mission(plan: dict[str, Any], policies: dict[str, dict[str, Any]]) -> dict[str, Any]:
    steps = [_safe_dict(step) for step in _safe_list(plan.get("steps"))]
    total_steps = len(steps)
    completed_steps = sum(1 for step in steps if str(step.get("status") or "") == "completed")
    failed_steps = sum(1 for step in steps if str(step.get("status") or "") == "failed")
    pending_steps = sum(1 for step in steps if str(step.get("status") or "pending") == "pending")
    progress = int((completed_steps / total_steps) * 100) if total_steps else 0

    next_step = None
    for step in steps:
        status = str(step.get("status") or "pending")
        if status in {"pending", "running"}:
            next_step = {
                "index": int(step.get("index") or 0),
                "tool": str(step.get("tool") or ""),
                "description": _collapse_text(step.get("description") or step.get("tool"), 140),
                "status": status,
            }
            break

    return {
        "id": str(plan.get("id") or ""),
        "goal": _collapse_text(plan.get("goal"), 220),
        "status": str(plan.get("status") or "unknown"),
        "created_at": str(plan.get("created_at") or ""),
        "updated_at": str(plan.get("updated_at") or ""),
        "total_steps": total_steps,
        "completed_steps": completed_steps,
        "failed_steps": failed_steps,
        "pending_steps": pending_steps,
        "progress_percent": progress,
        "requires_confirmation": _mission_requires_confirmation(plan, policies),
        "next_step": next_step,
        "steps": [
            {
                "index": int(step.get("index") or 0),
                "tool": str(step.get("tool") or ""),
                "description": _collapse_text(step.get("description") or step.get("tool"), 140),
                "status": str(step.get("status") or "pending"),
                "result": _collapse_text(step.get("result"), 180),
            }
            for step in steps
        ],
    }


def _mission_counts(plans: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {"total": 0, "pending": 0, "running": 0, "completed": 0, "failed": 0}
    for plan in plans:
        counts["total"] += 1
        status = str(_safe_dict(plan).get("status") or "pending").lower()
        counts[status] = counts.get(status, 0) + 1
    return counts


def _memory_summary(active_profile_id: str, profiles: dict[str, Any]) -> dict[str, Any]:
    summaries = {}
    for profile_id, raw in _safe_dict(profiles).items():
        pdata = _safe_dict(raw)
        facts = str(pdata.get("facts") or "")
        history = _safe_list(pdata.get("history"))
        summaries[str(profile_id)] = {
            "profile_id": str(profile_id),
            "is_active": str(profile_id) == active_profile_id,
            "facts_len": len(facts),
            "facts_preview": _collapse_text(facts, 160),
            "history_len": len(history),
        }
    return {
        "profiles_total": len(summaries),
        "active_profile": summaries.get(active_profile_id)
        or {
            "profile_id": active_profile_id,
            "is_active": True,
            "facts_len": 0,
            "facts_preview": "",
            "history_len": 0,
        },
        "profiles": summaries,
    }


def _tool_guard_summary(policy_overrides: dict[str, Any] | None) -> dict[str, Any]:
    policies = export_tool_policy_table(policy_overrides or {})
    critical = [name for name, policy in policies.items() if str(policy.get("risk_level") or "").lower() == "critical"]
    confirmation = [name for name, policy in policies.items() if bool(policy.get("requires_confirmation"))]
    audited = [name for name, policy in policies.items() if bool(policy.get("audit_log"))]
    return {
        "policies": policies,
        "critical_count": len(critical),
        "confirmation_required_count": len(confirmation),
        "audited_count": len(audited),
        "critical_tools": sorted(critical),
        "confirmation_required_tools": sorted(confirmation),
        "audited_tools": sorted(audited),
    }


def build_operator_status(
    *,
    active_profile_id: str,
    authorized: bool,
    profiles: dict[str, Any],
    plans: list[dict[str, Any]],
    security_snapshot: dict[str, Any],
    proactive_snapshot: dict[str, Any],
    audit: list[dict[str, Any]],
    policy_overrides: dict[str, Any] | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    profile_id = str(active_profile_id or "guest").strip() or "guest"
    is_authorized = bool(authorized)
    role = "admin" if is_authorized else "guest"
    mode = "ADMIN_OPERATOR" if is_authorized else "GUEST_VIEW_ONLY"
    tool_guard = _tool_guard_summary(policy_overrides)
    mission_summaries = [summarize_mission(_safe_dict(plan), tool_guard["policies"]) for plan in plans]
    active_missions = [
        mission for mission in mission_summaries if str(mission.get("status") or "").lower() in MISSION_ACTIVE_STATUSES
    ]

    security = _safe_dict(security_snapshot)
    proactive = _safe_dict(proactive_snapshot)
    audit_entries = _safe_list(audit)

    return {
        "timestamp": now_iso or _now_iso(),
        "operator": {
            "profile_id": profile_id,
            "role": role,
            "authorized": is_authorized,
            "mode": mode,
            "can_execute_missions": is_authorized,
            "confirmation_required": True,
        },
        "missions": {
            "active": active_missions[-1] if active_missions else None,
            "recent": mission_summaries[-6:],
            "counts": _mission_counts(mission_summaries),
        },
        "memory": _memory_summary(profile_id, profiles),
        "security": {
            "strict_mode": bool(security.get("strict_mode", False)),
            "last_block_reason": str(security.get("last_block_reason") or ""),
            "last_block_ts": str(security.get("last_block_ts") or ""),
            "audit_events": len(audit_entries),
            "recent_audit": audit_entries[-8:],
        },
        "proactive": {
            "enabled": bool(proactive.get("enabled", True)),
            "errors_5m": int(proactive.get("errors_5m") or 0),
            "alerts": _safe_list(proactive.get("alerts"))[-6:],
        },
        "tool_guard": tool_guard,
    }
