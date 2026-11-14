"""Delegation of reasoning requests from Gemini Live to the command pipeline."""

from __future__ import annotations

import logging
import math
import os
import threading
from collections import OrderedDict
from collections.abc import Iterable
from concurrent.futures import Future
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

# Read-only Live tools: a timed-out delegated plan keeps running in the background
# while Live accepts new actions, so it must never drive desktop side effects.
DELEGATION_READ_ONLY_LIVE_TOOLS: frozenset[str] = frozenset({
    "buscar_en_internet",
    "buscar_en_wikipedia",
    "cancion_actual_spotify",
    "evaluar_expresion_matematica",
    "listar_ventanas",
    "obtener_clima",
    "obtener_deportes_espn",
    "ver_procesos_pesados",
})
OWNER_PROFILE_IDS: frozenset[str] = frozenset({"", "default", "admin", "owner", "web_default"})

_DEFAULT_TIMEOUT = 12.0
_MIN_TIMEOUT = 3.0
_MAX_TIMEOUT = 14.0
_CACHE_LIMIT = 64
_HINT_LIMIT = 500

_RECEIPT_CACHE: OrderedDict[tuple[str, str], Future[ExecutionReceipt]] = OrderedDict()
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


def is_owner_profile(profile_id: str) -> bool:
    return str(profile_id or "").strip().lower() in OWNER_PROFILE_IDS


def delegated_tool_scope(
    registry_names: Iterable[str],
    *,
    live_allowlist: Iterable[str],
    owner_profile: bool = True,
) -> frozenset[str]:
    registered = set(registry_names)
    candidates = ((set(live_allowlist) & DELEGATION_READ_ONLY_LIVE_TOOLS) | DELEGATION_EXTRA_TOOLS) & registered
    candidates.discard(DELEGATION_TOOL_NAME)
    allowed = set()
    for name in candidates:
        policy = get_tool_policy(name)
        if policy.risk_level == "critical" or policy.requires_confirmation:
            continue
        if not owner_profile and policy.risk_level != "public":
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
    """Run one delegated request through the pipeline, once per call id. Never raises."""
    key = (str(session_id), str(call_id))
    with _CACHE_LOCK:
        existing = _RECEIPT_CACHE.get(key)
        if existing is not None:
            _RECEIPT_CACHE.move_to_end(key)
            owner = False
            future = existing
        else:
            owner = True
            future = Future()
            _RECEIPT_CACHE[key] = future
            _evict_completed_locked()
    if not owner:
        # No own timeout: the Live layer enforces the action timeout.
        return future.result()

    receipt = None
    try:
        receipt = _execute_delegation(
            user_text=user_text,
            hint=hint,
            profile_id=profile_id,
            language=language,
            session_id=session_id,
            call_id=call_id,
        )
    finally:
        if receipt is None:  # BaseException escaped; still release waiters.
            receipt = _failed_receipt(
                f"live-delegation-{session_id}-{call_id}",
                call_id,
                language,
                "delegation_failed",
                datetime.now(UTC),
            )
        future.set_result(receipt)
    return receipt


def _evict_completed_locked() -> None:
    """Keep the cache bounded; only completed entries are evicted, never in-flight ones."""
    if len(_RECEIPT_CACHE) <= _CACHE_LIMIT:
        return
    for stale in [k for k, f in _RECEIPT_CACHE.items() if f.done()]:
        if len(_RECEIPT_CACHE) <= _CACHE_LIMIT:
            break
        del _RECEIPT_CACHE[stale]


def _failed_receipt(
    request_id: str,
    call_id: str,
    language: str,
    code: str,
    started_at: datetime,
) -> ExecutionReceipt:
    return _receipt(
        request_id=request_id,
        call_id=call_id,
        status=ReceiptStatus.FAILED,
        message=_text(language, "delegation_unavailable"),
        diagnostic_code=code,
        started_at=started_at,
    )


def _execute_delegation(
    *,
    user_text: str,
    hint: str,
    profile_id: str,
    language: str,
    session_id: str,
    call_id: str,
) -> ExecutionReceipt:
    # FAILED receipts are cached intentionally: exact replays of a call_id get the
    # same outcome; Gemini issues new call_ids for genuine retries.
    request_id = f"live-delegation-{session_id}-{call_id}"
    started_at = datetime.now(UTC)
    try:
        from core.brain import processor
        from core.brain.brain_state import tool_registry

        from voice.live_gemini import LIVE_VOICE_TOOL_ALLOWLIST

        scope = delegated_tool_scope(
            tool_registry.snapshot().by_name,
            live_allowlist=LIVE_VOICE_TOOL_ALLOWLIST,
            owner_profile=is_owner_profile(profile_id),
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
        return _failed_receipt(request_id, call_id, language, "llm_unavailable", started_at)
    except Exception as exc:
        logger.warning("live_delegation_failed: %s", type(exc).__name__)
        return _failed_receipt(request_id, call_id, language, "delegation_failed", started_at)
    return receipt


def unavailable_message(language: str) -> str:
    return _text(language, "delegation_unavailable")


def telegram_ready() -> bool:
    from core import jarvis_config

    return bool(jarvis_config.TELEGRAM_ENABLED and jarvis_config.TELEGRAM_TOKEN and jarvis_config.TELEGRAM_CHAT_ID)


def late_result_message(language: str, *, telegram_ready: bool) -> str:
    key = "delegation_background_telegram" if telegram_ready else "delegation_background_history"
    return _text(language, key)


def deliver_late_result(text: str, *, ready: bool) -> bool:
    """Send a late result; ``ready`` is the readiness decided once at timeout time."""
    if not ready:
        return False
    try:
        from services.telegram_manager import telegram_manager

        return bool(telegram_manager.send_message_sync(text, audio=False))
    except Exception as exc:
        logger.warning("live_delegation_telegram_failed: %s", type(exc).__name__)
        return False
