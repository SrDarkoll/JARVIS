"""Data models for multi-device synchronization and pairing in J.A.R.V.I.S."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DeviceType(StrEnum):
    DESKTOP = "desktop"
    MOBILE_PWA = "mobile_pwa"
    TABLET = "tablet"
    TELEGRAM = "telegram"
    WEB_BROWSER = "web_browser"


class SyncEventType(StrEnum):
    CHAT_MESSAGE = "chat_message"
    ACTION_PLAN = "action_plan"
    SYSTEM_STATE = "system_state"
    MEDIA_STATE = "media_state"
    DEVICE_CONNECTED = "device_connected"
    DEVICE_DISCONNECTED = "device_disconnected"
    REMOTE_ALERT = "remote_alert"


@dataclass
class DeviceSession:
    device_id: str
    name: str
    device_type: DeviceType
    paired_at: float
    last_seen: float
    profile_id: str = "default"
    token_hash: str = ""
    is_active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "device_type": self.device_type.value,
            "paired_at": self.paired_at,
            "last_seen": self.last_seen,
            "profile_id": self.profile_id,
            "is_active": self.is_active,
        }


@dataclass
class PairingSession:
    pin: str
    created_at: float
    expires_at: float
    created_by_profile: str = "admin"
    consumed: bool = False

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at or self.consumed


@dataclass
class SyncEvent:
    event_id: str
    event_type: SyncEventType
    payload: dict[str, Any]
    profile_id: str = "admin"
    sequence: int = 0
    timestamp: float = field(default_factory=time.time)
    origin_device_id: str = "desktop"
    target_device_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "payload": self.payload,
            "profile_id": self.profile_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "origin_device_id": self.origin_device_id,
            "target_device_id": self.target_device_id,
        }
