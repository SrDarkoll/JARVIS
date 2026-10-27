"""Compose user-facing responses from validated plans and receipts."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from core.command_pipeline.models import (
    ActionPlan,
    CommandOutcome,
    CommandRequest,
    CommandResponse,
    ExecutionReceipt,
    ReceiptStatus,
)
from core.command_pipeline.truthfulness import reports_unexecuted_action

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
            outcome = plan.direct_outcome
            if outcome not in {
                CommandOutcome.RESPONDED, CommandOutcome.BLOCKED,
                CommandOutcome.UNAVAILABLE, CommandOutcome.FAILED,
            }:
                outcome = CommandOutcome.RESPONDED
            if reports_unexecuted_action(text):
                text = (
                    "No he ejecutado ninguna acción en este turno. ¿Qué necesitas que haga?"
                    if request.language.startswith("es")
                    else "I have not executed any action in this turn. What would you like me to do?"
                )
                outcome = CommandOutcome.RESPONDED
            return CommandResponse(
                request_id=request.request_id,
                text=text,
                should_listen=(plan.requires_follow_up or text.rstrip().endswith("?")),
                outcome=outcome,
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

        failed_receipts = [receipt for receipt in receipts if receipt.status in _FAILED_STATUSES]
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

        has_executed_step = any(
            receipt.status in {
                ReceiptStatus.SUCCEEDED,
                ReceiptStatus.ACCEPTED,
                ReceiptStatus.DUPLICATE,
            }
            for receipt in receipts
        )
        if missing_result:
            outcome = CommandOutcome.FAILED
        elif any(receipt.status is ReceiptStatus.PARTIAL for receipt in receipts):
            outcome = CommandOutcome.PARTIAL
        elif failed_receipts and has_executed_step:
            outcome = CommandOutcome.PARTIAL
        elif any(receipt.status is ReceiptStatus.FAILED for receipt in receipts):
            outcome = CommandOutcome.FAILED
        elif blocked:
            outcome = CommandOutcome.BLOCKED
        elif any(receipt.status is ReceiptStatus.UNAVAILABLE for receipt in receipts):
            outcome = CommandOutcome.UNAVAILABLE
        elif all_duplicates:
            outcome = CommandOutcome.DUPLICATE
        elif any(receipt.status is ReceiptStatus.ACCEPTED for receipt in receipts):
            outcome = CommandOutcome.ACCEPTED
        elif receipts and all(receipt.verified for receipt in receipts):
            outcome = CommandOutcome.VERIFIED
        else:
            outcome = CommandOutcome.EXECUTED

        text = "\n".join(messages)
        if (
            self._synthesizer is not None
            and receipts
            and all(receipt.status is ReceiptStatus.SUCCEEDED for receipt in receipts)
            and all(receipt.verified for receipt in receipts)
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
                if synthesized.strip() and not reports_unexecuted_action(synthesized):
                    text = synthesized.strip()

        return CommandResponse(
            request_id=request.request_id,
            text=text,
            should_listen=plan.requires_follow_up or blocked,
            outcome=outcome,
            receipts=receipts,
            metrics=frozen_metrics,
        )
