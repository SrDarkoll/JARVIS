"""Exactly-once tool execution for a single command pipeline process."""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from core.command_pipeline.models import (
    ActionStep,
    CommandRequest,
    ExecutionReceipt,
    ReceiptStatus,
)


class ControlledToolBlockedError(PermissionError):
    """Blocked operation whose public response is safe to expose."""

    diagnostic_code = "tool_blocked"
    message_en = "I need explicit confirmation before performing that action."
    message_es = (
        "Necesito confirmacion explicita antes de realizar esa accion."
    )

    def __init__(self, public_message: str = "") -> None:
        super().__init__(self.diagnostic_code)
        self.public_message = str(public_message or "").strip()

    def user_message(self, language: str) -> str:
        if self.public_message:
            return self.public_message
        if str(language or "").lower().startswith("es"):
            return self.message_es
        return self.message_en


class ToolConfirmationRequiredError(ControlledToolBlockedError):
    """The action is valid but still needs explicit user confirmation."""

    diagnostic_code = "tool_confirmation_required"


class ToolAuthorizationRequiredError(ControlledToolBlockedError):
    """The current profile is not authorized to perform the action."""

    diagnostic_code = "tool_authorization_required"
    message_en = "Administrator authorization is required for that action."
    message_es = (
        "Se requiere autorizacion de administrador para realizar esa accion."
    )


class ToolPolicyBlockedError(ControlledToolBlockedError):
    """The configured security policy forbids the action."""

    diagnostic_code = "tool_policy_blocked"
    message_en = "That action is blocked by the security policy."
    message_es = "Esa accion esta bloqueada por la politica de seguridad."


def operation_signature(step: ActionStep) -> str:
    """Return a stable signature for one tool operation."""
    arguments = json.dumps(
        dict(step.arguments),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"{step.tool_name}:{arguments}"


def validate_plan_operations(
    steps: Iterable[ActionStep],
    *,
    repeatable_tools: frozenset[str] = frozenset(),
) -> None:
    """Reject duplicate canonical operations unless explicitly repeatable."""
    seen: set[str] = set()
    for step in steps:
        signature = operation_signature(step)
        if signature in seen and step.tool_name not in repeatable_tools:
            raise ValueError("duplicate_plan_operation")
        seen.add(signature)


class ToolExecutionService:
    """Coalesce matching attempts and retain a bounded completed-receipt cache."""

    def __init__(
        self,
        invoke_once: Callable[[CommandRequest, ActionStep], Any],
        *,
        max_records: int = 1024,
    ) -> None:
        self._invoke_once = invoke_once
        self._max_records = max(32, int(max_records))
        self._lock = threading.RLock()
        self._records: OrderedDict[str, Future[ExecutionReceipt]] = OrderedDict()

    def _key(self, request: CommandRequest, step: ActionStep) -> str:
        return (
            f"{request.request_id}:{step.step_id}:{operation_signature(step)}"
        )

    def _trim_completed_records(self) -> None:
        while len(self._records) > self._max_records:
            completed_key = next(
                (
                    key
                    for key, future in self._records.items()
                    if future.done()
                ),
                None,
            )
            if completed_key is None:
                break
            self._records.pop(completed_key)

    def execute(
        self,
        request: CommandRequest,
        step: ActionStep,
    ) -> ExecutionReceipt:
        """Execute once or replay the matching operation as a duplicate."""
        key = self._key(request, step)
        with self._lock:
            future = self._records.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._records[key] = future
                self._trim_completed_records()

        if not owner:
            original = future.result(timeout=30)
            return replace(original, status=ReceiptStatus.DUPLICATE)

        started_at = datetime.now(UTC)
        try:
            value = self._invoke_once(request, step)
            receipt = ExecutionReceipt(
                request_id=request.request_id,
                step_id=step.step_id,
                tool_name=step.tool_name,
                status=ReceiptStatus.SUCCEEDED,
                result=value,
                user_message=str(value or ""),
                verified=True,
                diagnostic_code="",
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        except ControlledToolBlockedError as exc:
            receipt = ExecutionReceipt(
                request_id=request.request_id,
                step_id=step.step_id,
                tool_name=step.tool_name,
                status=ReceiptStatus.BLOCKED,
                result=None,
                user_message=exc.user_message(request.language),
                verified=False,
                diagnostic_code=exc.diagnostic_code,
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        except PermissionError:
            blocked_message = (
                "Necesito confirmacion explicita antes de realizar esa accion."
                if request.language.startswith("es")
                else "I need explicit confirmation before performing that action."
            )
            receipt = ExecutionReceipt(
                request_id=request.request_id,
                step_id=step.step_id,
                tool_name=step.tool_name,
                status=ReceiptStatus.BLOCKED,
                result=None,
                user_message=blocked_message,
                verified=False,
                diagnostic_code="tool_blocked",
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        except LookupError:
            receipt = ExecutionReceipt(
                request_id=request.request_id,
                step_id=step.step_id,
                tool_name=step.tool_name,
                status=ReceiptStatus.UNAVAILABLE,
                result=None,
                user_message="The requested tool is unavailable.",
                verified=False,
                diagnostic_code="tool_unavailable",
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        except Exception:
            receipt = ExecutionReceipt(
                request_id=request.request_id,
                step_id=step.step_id,
                tool_name=step.tool_name,
                status=ReceiptStatus.FAILED,
                result=None,
                user_message="The requested action failed.",
                verified=False,
                diagnostic_code="tool_execution_failed",
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )

        future.set_result(receipt)
        with self._lock:
            self._trim_completed_records()
        return receipt
