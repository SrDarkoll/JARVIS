"""Truthful presentation of central tool receipts in Gemini Live."""

from __future__ import annotations

from core.command_pipeline.models import ExecutionReceipt, ReceiptStatus


def live_action_state(receipt: ExecutionReceipt) -> str:
    if receipt.diagnostic_code == "tool_confirmation_required":
        return "confirmation_required"
    return {
        ReceiptStatus.SUCCEEDED: "succeeded",
        ReceiptStatus.DUPLICATE: "duplicate",
        ReceiptStatus.BLOCKED: "blocked",
        ReceiptStatus.UNAVAILABLE: "unavailable",
        ReceiptStatus.FAILED: "failed",
    }[receipt.status]


def live_tool_response(receipt: ExecutionReceipt) -> dict[str, object]:
    state = live_action_state(receipt)
    return {
        "ok": state in {"succeeded", "duplicate"},
        "state": state,
        "message": receipt.user_message[:2000],
        "diagnostic_code": receipt.diagnostic_code,
        "verified": receipt.verified,
    }


__all__ = ["live_action_state", "live_tool_response"]
