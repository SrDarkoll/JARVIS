"""Compose user-facing responses from validated plans and receipts."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from core.command_pipeline.models import (
    ActionPlan,
    CommandRequest,
    CommandResponse,
    ExecutionReceipt,
    ReceiptStatus,
)

_FAILED_STATUSES = {
    ReceiptStatus.BLOCKED,
    ReceiptStatus.UNAVAILABLE,
    ReceiptStatus.FAILED,
}


def _frozen_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


class ResponseComposer:
    """Build a channel-neutral response without invoking a tool-capable model."""

    def __init__(self, synthesizer=None) -> None:
        self._synthesizer = synthesizer

    def compose(
        self,
        request: CommandRequest,
        plan: ActionPlan,
        receipts: tuple[ExecutionReceipt, ...],
        metrics: Mapping[str, Any] | None = None,
    ) -> CommandResponse:
        frozen_metrics = _frozen_mapping(metrics)
        if plan.direct_response and not receipts:
            text = plan.direct_response.strip()
            return CommandResponse(
                request_id=request.request_id,
                text=text,
                should_listen=(plan.requires_follow_up or text.rstrip().endswith("?")),
                outcome="succeeded",
                metrics=frozen_metrics,
            )

        messages: list[str] = []
        seen_messages: set[str] = set()
        for receipt in receipts:
            message = receipt.user_message.strip()
            if not message or message in seen_messages:
                continue
            messages.append(message)
            seen_messages.add(message)

        failed = any(receipt.status in _FAILED_STATUSES for receipt in receipts)
        blocked = any(receipt.status is ReceiptStatus.BLOCKED for receipt in receipts)
        all_duplicates = bool(receipts) and all(receipt.status is ReceiptStatus.DUPLICATE for receipt in receipts)
        missing_result = not messages and not all_duplicates

        if not messages:
            if all_duplicates:
                messages.append(
                    "La accion ya se habia completado."
                    if request.language.startswith("es")
                    else "The action was already completed."
                )
            else:
                messages.append(
                    "No pude completar la accion solicitada."
                    if request.language.startswith("es")
                    else "I could not complete the requested action."
                )

        if failed or missing_result:
            outcome = "partial" if len(receipts) > 1 else "failed"
        elif all_duplicates:
            outcome = "duplicate"
        else:
            outcome = "succeeded"

        text = "\n".join(messages)
        if (
            self._synthesizer is not None
            and receipts
            and all(receipt.status is ReceiptStatus.SUCCEEDED for receipt in receipts)
            and not plan.requires_follow_up
        ):
            try:
                synthesized = self._synthesizer.synthesize(
                    request,
                    plan,
                    receipts,
                    text,
                )
            except Exception:
                pass
            else:
                if synthesized.strip():
                    text = synthesized.strip()

        return CommandResponse(
            request_id=request.request_id,
            text=text,
            should_listen=plan.requires_follow_up or blocked,
            outcome=outcome,
            receipts=receipts,
            metrics=frozen_metrics,
        )
