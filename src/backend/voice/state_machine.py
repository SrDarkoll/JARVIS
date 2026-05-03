"""Explicit voice flow state machine used by voice routes.

This module centralizes stage values and allowed transitions to avoid
scattered stringly-typed flow control.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VoiceStage(str, Enum):
    IDLE = "idle"
    AWAITING_SAMPLE = "awaiting_sample"
    AWAITING_NAME = "awaiting_name"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    INVITADO_ENROLLMENT = "invitado_enrollment"
    ADMIN_ENROLLMENT = "admin_enrollment"


@dataclass(frozen=True)
class VoiceTransitionResult:
    ok: bool
    error: str = ""


_ALLOWED_TRANSITIONS: dict[VoiceStage, set[VoiceStage]] = {
    VoiceStage.IDLE: {
        VoiceStage.AWAITING_SAMPLE,
        VoiceStage.AWAITING_NAME,
        VoiceStage.AWAITING_CONFIRMATION,
        VoiceStage.INVITADO_ENROLLMENT,
        VoiceStage.ADMIN_ENROLLMENT,
    },
    VoiceStage.AWAITING_SAMPLE: {VoiceStage.AWAITING_NAME, VoiceStage.IDLE},
    VoiceStage.AWAITING_NAME: {VoiceStage.AWAITING_CONFIRMATION, VoiceStage.IDLE},
    VoiceStage.AWAITING_CONFIRMATION: {VoiceStage.AWAITING_NAME, VoiceStage.IDLE},
    VoiceStage.INVITADO_ENROLLMENT: {VoiceStage.IDLE},
    VoiceStage.ADMIN_ENROLLMENT: {VoiceStage.IDLE},
}


def normalize_stage(raw: str | None) -> VoiceStage:
    try:
        return VoiceStage(str(raw or "").strip().lower())
    except Exception:
        return VoiceStage.IDLE


def can_transition(current: VoiceStage, target: VoiceStage) -> VoiceTransitionResult:
    if current == target:
        return VoiceTransitionResult(True)
    allowed = _ALLOWED_TRANSITIONS.get(current, set())
    if target in allowed:
        return VoiceTransitionResult(True)
    return VoiceTransitionResult(
        False,
        f"Invalid voice stage transition: {current.value} -> {target.value}",
    )
