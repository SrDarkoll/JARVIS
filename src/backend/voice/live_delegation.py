"""Delegation of reasoning requests from Gemini Live to the command pipeline."""

from __future__ import annotations

import logging
import math
import os
import threading
from collections import OrderedDict
from collections.abc import Iterable
from datetime import UTC, datetime

from core.command_pipeline.models import (
    CommandOutcome,
    ExecutionReceipt,
    ReceiptStatus,
)
from core.command_pipeline.reasoning import ReasoningMode
from core.errors import LLMServiceError, LLMUnavailableError
from core.security.tool_policy import get_tool_policy
from utils.jarvis_i18n import BACKEND_TRANSLATIONS, language_scope

logger = logging.getLogger(__name__)

DELEGATION_TOOL_NAME = "delegar_razonamiento"
DELEGATION_CHANNEL = "live_delegation"
# Later pieces add their tools here so Live can reach them through delegation.
DELEGATION_EXTRA_TOOLS: frozenset[str] = frozenset()

_DEFAULT_TIMEOUT = 12.0
_MIN_TIMEOUT = 3.0
_MAX_TIMEOUT = 14.0
_CACHE_LIMIT = 64
_HINT_LIMIT = 500

_RECEIPT_CACHE: OrderedDict[tuple[str, str], ExecutionReceipt] = OrderedDict()
_CACHE_LOCK = threading.Lock()

_OUTCOME_TO_STATUS = {
    CommandOutcome.RESPONDED: ReceiptStatus.SUCCEEDED,
    CommandOutcome.EXECUTED: ReceiptStatus.SUCCEEDED,
    CommandOutcome.VERIFIED: ReceiptStatus.SUCCEEDED,
    CommandOutcome.SUCCEEDED: ReceiptStatus.SUCCEEDED,
    CommandOutcome.ACCEPTED: ReceiptStatus.ACCEPTED,
    CommandOutcome.DUPLICATE: ReceiptStatus.DUPLICATE,
    CommandOutcome.BLOCKED: ReceiptStatus.BLOCKED,
    CommandOutcome.UNAVAILABLE: ReceiptStatus.UNAVAILABLE,
    CommandOutcome.PARTIAL: ReceiptStatus.PARTIAL,
    CommandOutcome.FAILED: ReceiptStatus.FAILED,
}


def _text(language: str, key: str) -> str:
    lang = "en" if str(language or "").strip().lower().startswith("en") else "es"
    return BACKEND_TRANSLATIONS[lang][key]


def delegation_declaration(language: str) -> dict:
    return {
        "name": DELEGATION_TOOL_NAME,
        "description": _text(language, "delegation_tool_description"),
        "parameters": {
            "type": "object",
            "properties": {
                "peticion": {
                    "type": "string",
                    "description": "The user's request, in the user's own words.",
                },
            },
            "required": ["peticion"],
        },
    }


def delegated_tool_scope(
    registry_names: Iterable[str],
    *,
    live_allowlist: Iterable[str],
) -> frozenset[str]:
    registered = set(registry_names)
    candidates = (set(live_allowlist) | DELEGATION_EXTRA_TOOLS) & registered
    candidates.discard(DELEGATION_TOOL_NAME)
    allowed = set()
    for name in candidates:
        policy = get_tool_policy(name)
        if policy.risk_level == "critical" or policy.requires_confirmation:
            continue
        allowed.add(name)
    return frozenset(allowed)


def delegation_timeout_seconds() -> float:
    try:
        value = float(os.getenv("JARVIS_LIVE_DELEGATION_TIMEOUT", ""))
    except ValueError:
        return _DEFAULT_TIMEOUT
    if math.isnan(value):
        return _DEFAULT_TIMEOUT
    return min(_MAX_TIMEOUT, max(_MIN_TIMEOUT, value))


def _receipt(
    *,
    request_id: str,
    call_id: str,
    status: ReceiptStatus,
    message: str,
    verified: bool = False,
    diagnostic_code: str = "",
    started_at: datetime,
) -> ExecutionReceipt:
    return ExecutionReceipt(
        request_id=request_id,
        step_id=call_id,
        tool_name=DELEGATION_TOOL_NAME,
        status=status,
        result=message,
        user_message=message,
        verified=verified,
        diagnostic_code=diagnostic_code,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )


def run_delegated_request(
    *,
    user_text: str,
    hint: str,
    profile_id: str,
    language: str,
    session_id: str,
    call_id: str,
) -> ExecutionReceipt:
    """Run one delegated request through the pipeline. Never raises."""
    key = (str(session_id), str(call_id))
    with _CACHE_LOCK:
        cached = _RECEIPT_CACHE.get(key)
        if cached is not None:
            _RECEIPT_CACHE.move_to_end(key)
            return cached

    request_id = f"live-delegation-{session_id}-{call_id}"
    started_at = datetime.now(UTC)
    try:
        from core.brain import processor
        from core.brain.brain_state import tool_registry

        from voice.live_gemini import LIVE_VOICE_TOOL_ALLOWLIST

        scope = delegated_tool_scope(
            tool_registry.snapshot().by_name,
            live_allowlist=LIVE_VOICE_TOOL_ALLOWLIST,
        )
        with language_scope(language):
            request = processor._build_command_request(
                user_text,
                profile_id=profile_id,
                channel=DELEGATION_CHANNEL,
                request_id=request_id,
                reasoning_mode=ReasoningMode.ALWAYS,
                allowed_tools=scope,
                extra_metadata={"live_hint": str(hint or "")[:_HINT_LIMIT]},
            )
            response = processor.process_command(request)
        outcome = CommandOutcome(response.outcome)
        receipt = _receipt(
            request_id=request_id,
            call_id=call_id,
            status=_OUTCOME_TO_STATUS[outcome],
            message=response.text,
            verified=outcome is CommandOutcome.VERIFIED,
            started_at=started_at,
        )
    except (LLMUnavailableError, LLMServiceError) as exc:
        logger.warning("live_delegation_llm_unavailable: %s", type(exc).__name__)
        receipt = _receipt(
            request_id=request_id,
            call_id=call_id,
            status=ReceiptStatus.FAILED,
            message=_text(language, "delegation_unavailable"),
            diagnostic_code="llm_unavailable",
            started_at=started_at,
        )
    except Exception as exc:
        logger.warning("live_delegation_failed: %s", type(exc).__name__)
        receipt = _receipt(
            request_id=request_id,
            call_id=call_id,
            status=ReceiptStatus.FAILED,
            message=_text(language, "delegation_unavailable"),
            diagnostic_code="delegation_failed",
            started_at=started_at,
        )

    with _CACHE_LOCK:
        _RECEIPT_CACHE[key] = receipt
        _RECEIPT_CACHE.move_to_end(key)
        while len(_RECEIPT_CACHE) > _CACHE_LIMIT:
            _RECEIPT_CACHE.popitem(last=False)
    return receipt


def telegram_ready() -> bool:
    from core import jarvis_config

    return bool(jarvis_config.TELEGRAM_ENABLED and jarvis_config.TELEGRAM_TOKEN and jarvis_config.TELEGRAM_CHAT_ID)


def late_result_message(language: str, *, telegram_ready: bool) -> str:
    key = "delegation_background_telegram" if telegram_ready else "delegation_background_history"
    return _text(language, key)


def deliver_late_result(text: str) -> bool:
    if not telegram_ready():
        return False
    try:
        from services.telegram_manager import telegram_manager

        return bool(telegram_manager.send_message_sync(text, audio=False))
    except Exception as exc:
        logger.warning("live_delegation_telegram_failed: %s", type(exc).__name__)
        return False
