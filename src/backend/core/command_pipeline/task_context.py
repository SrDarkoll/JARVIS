"""Ephemeral task provenance; never executes tools or grants profile privileges."""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import uuid4

from core.command_pipeline.models import ActionStep, CommandRequest, ReceiptStatus


class ContextResolutionError(ValueError):
    """A controlled diagnostic, not a provider or permission exception."""


@dataclass(frozen=True, slots=True)
class ActionExecutionView:
    state: str
    receipt_status: ReceiptStatus | None = None
    verified: bool = False
    busy: bool = False
    diagnostic_code: str = ""

    @property
    def active(self) -> bool:
        return self.busy or self.state in {
            "pending", "running", "waiting_confirmation", "confirmation_required",
            "timed_out", "cancellation_requested",
        }


@dataclass(frozen=True, slots=True)
class FollowupPolicy:
    fields: frozenset[str]
    repeat: bool = False
    media_field: str = ""


FOLLOWUP_POLICIES = {
    "reproducir_en_spotify": FollowupPolicy(frozenset({"cancion"}), True, "cancion"),
    "reproducir_mix_spotify": FollowupPolicy(frozenset({"semilla"}), True, "semilla"),
    "reproducir_en_youtube": FollowupPolicy(frozenset({"query"}), True, "query"),
    "obtener_clima": FollowupPolicy(frozenset({"ciudad"}), True),
    "buscar_en_internet": FollowupPolicy(frozenset({"query"}), True),
    "buscar_en_wikipedia": FollowupPolicy(frozenset({"consulta"}), True),
    "abrir_navegador": FollowupPolicy(frozenset({"destino"})),
    "abrir_aplicacion": FollowupPolicy(frozenset({"nombre_app"})),
    "abrir_documento": FollowupPolicy(frozenset({"nombre"})),
}
_FAILURES = {ReceiptStatus.FAILED, ReceiptStatus.UNAVAILABLE}
_COMPLETED = _FAILURES | {ReceiptStatus.SUCCEEDED, ReceiptStatus.DUPLICATE}


def _correctable_choice(view: ActionExecutionView) -> bool:
    return view.receipt_status is ReceiptStatus.PARTIAL and view.diagnostic_code in {
        "spotify_ambiguous_results", "document_ambiguous",
    }


def _arguments_json(arguments: Mapping[str, Any]) -> str:
    try:
        value = json.dumps(dict(arguments), sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise ContextResolutionError("context_invalid_changes") from None
    if len(value) > 16_000 or not all(isinstance(key, str) for key in arguments):
        raise ContextResolutionError("context_invalid_changes")
    return value


def _no_action_requested(text: str) -> bool:
    normalized = "".join(
        char for char in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(char)
    )
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = " ".join(normalized.split())
    return bool(re.search(
        r"\b(?:solo quiero (?:hablar|conversar|que me expliques)|es que tengo una duda|"
        r"no (?:quiero que (?:pongas|reproduzcas)|reproduzcas|pongas|ejecutes)|"
        r"don t (?:play|execute)|do not (?:play|execute)|"
        r"(?:el mensaje|el texto|la frase|the message|the text) (?:dice|es|says))\b",
        normalized,
    ))


@dataclass(frozen=True, slots=True)
class TaskRecord:
    reference: str
    action_id: str
    tool_name: str
    arguments_json: str = field(repr=False)
    turn_id: int
    epoch: int
    touched_at: float
    parent_ref: str = ""
    closed: bool = False
    continuation_turn: int | None = None

    @property
    def arguments(self) -> dict[str, Any]:
        return json.loads(self.arguments_json)


@dataclass(frozen=True, slots=True)
class ContextualActionPermit:
    authority: TaskContext = field(repr=False, compare=False)
    nonce: str = field(repr=False)
    turn_id: int
    epoch: int
    parent_ref: str
    tool_name: str
    arguments_json: str = field(repr=False)
    user_text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ResolvedContextAction:
    operation: str
    action_id: str
    parent_ref: str
    tool_name: str = ""
    arguments_json: str = "{}"
    permit: ContextualActionPermit | None = field(default=None, repr=False)

    @property
    def arguments(self) -> dict[str, Any]:
        return json.loads(self.arguments_json)


_EXECUTION_PERMIT: ContextVar[ContextualActionPermit | None] = ContextVar("task_execution_permit", default=None)


@contextmanager
def contextual_execution_scope(permit: ContextualActionPermit | None) -> Iterator[None]:
    token = _EXECUTION_PERMIT.set(permit)
    try:
        yield
    finally:
        _EXECUTION_PERMIT.reset(token)


def contextual_execution_decision(request: CommandRequest, step: ActionStep) -> bool | None:
    permit = _EXECUTION_PERMIT.get()
    if permit is None:
        return None
    if not isinstance(permit, ContextualActionPermit):
        return False
    return permit.authority.claim(permit, request, step)


class TaskContext:
    """Bind proposals to one real input turn and an authoritative action view."""

    def __init__(
        self, session_id: str, profile_id: str,
        read_action: Callable[[str], ActionExecutionView | None], *,
        monotonic: Callable[[], float] = time.monotonic,
        max_records: int = 32, ttl_seconds: float = 300.0,
    ) -> None:
        self.session_id = session_id
        self.profile_id = profile_id
        self._read_action = read_action
        self._monotonic = monotonic
        self._max_records = max(1, max_records)
        self._ttl = max(0.0, ttl_seconds)
        self._records: OrderedDict[str, TaskRecord] = OrderedDict()
        self._issued: dict[str, ContextualActionPermit] = {}
        self._lock = threading.RLock()
        self._turn = 0
        self._text = ""
        self._epoch = 0
        self._reserved_turn = -1

    @property
    def current_turn(self) -> int:
        return self._turn

    def begin_turn(self, text: str = "") -> int:
        with self._lock:
            self._turn += 1
            self._issued.clear()
            self._text = " ".join(text.split())[:16_000]
            return self._turn

    def update_turn_text(self, turn_id: int, text: str) -> None:
        with self._lock:
            if turn_id == self._turn:
                self._text = " ".join(text.split())[:16_000]

    def invalidate(self) -> None:
        with self._lock:
            self._epoch += 1
            self._issued.clear()
            self._text = ""

    def revoke_proposals(self) -> None:
        """Revoke unclaimed permissions without consuming a real input turn."""
        with self._lock:
            self._issued.clear()

    def get_record(self, reference: str) -> TaskRecord | None:
        return self._records.get(reference)

    def resolve_reference(self, reference: str, turn_id: int) -> TaskRecord | None:
        with self._lock:
            if reference != "current":
                return self._records.get(reference)
            candidates = [
                record for record in self._records.values()
                if not record.closed and record.epoch == self._epoch and (
                    self._eligible(record, turn_id)
                    or bool((view := self._read_action(record.action_id)) and view.active)
                )
            ]
            if len(candidates) != 1:
                raise ContextResolutionError("context_ambiguous" if candidates else "context_reference_unavailable")
            return candidates[0]

    def close_action(self, action_id: str) -> None:
        with self._lock:
            for reference, record in self._records.items():
                if record.action_id == action_id:
                    self._records[reference] = replace(record, closed=True)
            self._issued.clear()

    def followup_hint(self, turn_id: int) -> dict[str, Any] | None:
        with self._lock:
            if turn_id != self._turn or _no_action_requested(self._text):
                return None
            try:
                record = self.resolve_reference("current", turn_id)
            except ContextResolutionError:
                return None
            if record is None or not self._eligible(record, turn_id):
                return None
            policy = FOLLOWUP_POLICIES.get(record.tool_name)
            view = self._read_action(record.action_id)
            if policy is None or view is None or view.active or (
                view.receipt_status not in _COMPLETED and not _correctable_choice(view)
            ):
                return None
            operations = ["correct", "cancel"]
            if view.receipt_status in _FAILURES:
                operations.append("retry")
                if policy.media_field:
                    operations.append("replace")
            elif policy.repeat and view.receipt_status in {ReceiptStatus.SUCCEEDED, ReceiptStatus.DUPLICATE}:
                operations.append("repeat")
            return {"action_ref": record.reference, "tool_name": record.tool_name,
                    "allowed_operations": operations, "followup_tool": "gestionar_tarea_actual"}

    def touch_action(self, action_id: str) -> None:
        with self._lock:
            for reference, record in self._records.items():
                if record.action_id == action_id:
                    self._records[reference] = replace(record, touched_at=self._monotonic())

    def record_action(
        self, action_id: str, tool_name: str, arguments: Mapping[str, Any], turn_id: int,
        *, parent_ref: str = "",
    ) -> str:
        with self._lock:
            if turn_id != self._turn or not self._text:
                raise ContextResolutionError("context_input_unavailable")
            serialized = _arguments_json(arguments)
            if any(
                record.epoch == self._epoch and record.turn_id == turn_id
                and record.tool_name == tool_name and record.arguments_json == serialized
                for record in self._records.values()
            ):
                raise ContextResolutionError("context_duplicate_operation")
            while len(self._records) >= self._max_records:
                needed = {permit.parent_ref for permit in self._issued.values()}
                removable = next((
                    ref for ref, record in self._records.items()
                    if ref not in needed and (not (view := self._read_action(record.action_id)) or not view.active)
                ), None)
                if removable is None:
                    raise ContextResolutionError("context_capacity_reached")
                self._records.pop(removable)
            reference = uuid4().hex
            self._records[reference] = TaskRecord(
                reference, action_id, tool_name, serialized, turn_id,
                self._epoch, self._monotonic(), parent_ref,
            )
            if parent_ref in self._records:
                self._records[parent_ref] = replace(self._records[parent_ref], closed=True)
            return reference

    def _eligible(self, record: TaskRecord, turn_id: int) -> bool:
        return (
            not record.closed and record.epoch == self._epoch
            and (record.continuation_turn if record.continuation_turn is not None else record.turn_id) == turn_id - 1
            and self._monotonic() - record.touched_at <= self._ttl
        )

    def prepare(
        self, operation: str, reference: str, turn_id: int, *,
        changes: Mapping[str, Any] | None = None, replacement_tool: str = "",
    ) -> ResolvedContextAction:
        with self._lock:
            if turn_id != self._turn or not self._text:
                raise ContextResolutionError("context_input_unavailable")
            if self._reserved_turn == turn_id and operation != "status":
                raise ContextResolutionError("context_turn_already_used")
            if not isinstance(operation, str) or operation not in {"retry", "repeat", "correct", "replace", "cancel", "status"}:
                raise ContextResolutionError("context_operation_not_supported")
            if operation not in {"cancel", "status"} and _no_action_requested(self._text):
                raise ContextResolutionError("context_no_action_requested")
            record = self.resolve_reference(reference, turn_id)
            if record is None or record.epoch != self._epoch:
                raise ContextResolutionError("context_reference_unavailable")
            reference = record.reference
            view = self._read_action(record.action_id)
            if view is None:
                raise ContextResolutionError("context_reference_unavailable")
            if operation == "status":
                if changes or replacement_tool:
                    raise ContextResolutionError("context_invalid_changes")
                eligible = [candidate for candidate in self._records.values() if self._eligible(candidate, turn_id)]
                if self._eligible(record, turn_id) and len(eligible) == 1 and not _no_action_requested(self._text):
                    self._records[reference] = replace(record, continuation_turn=turn_id)
                return ResolvedContextAction(operation, record.action_id, reference)
            if record.closed:
                raise ContextResolutionError("context_reference_unavailable")
            if operation == "cancel":
                if changes or replacement_tool:
                    raise ContextResolutionError("context_invalid_changes")
                self._records[reference] = replace(record, closed=True)
                self._issued.clear()
                self._reserved_turn = turn_id
                return ResolvedContextAction(operation, record.action_id, reference)
            if not self._eligible(record, turn_id):
                raise ContextResolutionError("context_reference_expired")
            eligible = [candidate for candidate in self._records.values() if self._eligible(candidate, turn_id)]
            if len(eligible) != 1:
                raise ContextResolutionError("context_ambiguous")
            if view.active:
                raise ContextResolutionError("context_action_still_running")
            policy = FOLLOWUP_POLICIES.get(record.tool_name)
            if policy is None:
                raise ContextResolutionError("context_operation_not_supported")
            if view.receipt_status not in _COMPLETED and not (operation == "correct" and _correctable_choice(view)):
                raise ContextResolutionError("context_outcome_uncertain")
            arguments = record.arguments
            tool_name = record.tool_name
            patch = dict(changes or {})
            if operation in {"retry", "repeat"}:
                if patch or replacement_tool:
                    raise ContextResolutionError("context_invalid_changes")
                if operation == "retry" and view.receipt_status not in _FAILURES:
                    raise ContextResolutionError("context_retry_not_needed")
                if operation == "repeat" and (not policy.repeat or view.receipt_status in _FAILURES):
                    raise ContextResolutionError("context_operation_not_supported")
            elif operation == "replace":
                target = FOLLOWUP_POLICIES.get(replacement_tool)
                if replacement_tool == tool_name:
                    raise ContextResolutionError("context_use_correction")
                if not policy.media_field or target is None or not target.media_field:
                    raise ContextResolutionError("context_transition_not_supported")
                if view.receipt_status not in _FAILURES:
                    raise ContextResolutionError("context_media_handoff_required")
                arguments = {target.media_field: arguments.get(policy.media_field, "")}
                tool_name, policy = replacement_tool, target
            elif replacement_tool or not patch:
                raise ContextResolutionError("context_invalid_changes")
            if (
                not set(patch).issubset(policy.fields)
                or any(not isinstance(value, str) or not value.strip() for value in patch.values())
            ):
                raise ContextResolutionError("context_invalid_changes")
            arguments.update(patch)
            arguments_json = _arguments_json(arguments)
            permit = ContextualActionPermit(
                self, uuid4().hex, turn_id, self._epoch, reference,
                tool_name, arguments_json, self._text,
            )
            self._issued[permit.nonce] = permit
            self._reserved_turn = turn_id
            return ResolvedContextAction(operation, record.action_id, reference, tool_name, arguments_json, permit)

    def claim(self, permit: ContextualActionPermit, request: CommandRequest, step: ActionStep) -> bool:
        try:
            arguments_json = _arguments_json(step.arguments)
        except ContextResolutionError:
            return False
        with self._lock:
            issued = self._issued.pop(permit.nonce, None)
            record = self._records.get(permit.parent_ref)
            view = self._read_action(record.action_id) if record else None
            return (
                issued is permit and permit.authority is self
                and permit.turn_id == self._turn and permit.epoch == self._epoch
                and request.profile_id == self.profile_id
                and request.request_id == f"live-{self.session_id}"
                and request.channel == "gemini_live" and request.text == permit.user_text
                and self._text == permit.user_text
                and step.tool_name == permit.tool_name and arguments_json == permit.arguments_json
                and record is not None and record.epoch == self._epoch
                and self._monotonic() - record.touched_at <= self._ttl
                and view is not None and not view.active and not _no_action_requested(request.text)
            )
