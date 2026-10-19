"""Persistent, profile-scoped reminders for stable Core Mode."""

from __future__ import annotations

import math
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from core import jarvis_state
from core.jarvis_config import RUNTIME_PATHS

_MAX_TEXT_CHARS = 500
_MAX_MINUTES = 525_600.0


def _profile_key(profile_id: object) -> str:
    raw = jarvis_state.normalize_profile_id(str(profile_id or ""))
    normalized = re.sub(r"[^a-z0-9_.-]+", "_", raw.lower()).strip("._-")
    return (normalized or jarvis_state.DEFAULT_PROFILE_ID)[:64]


@dataclass(frozen=True, slots=True)
class ReminderRecord:
    reminder_id: str
    profile_id: str
    text: str
    due_at: float
    status: str
    created_at: float
    claimed_at: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.reminder_id,
            "profile_id": self.profile_id,
            "text": self.text,
            "due_at": self.due_at,
            "due_at_iso": datetime.fromtimestamp(self.due_at, tz=UTC).isoformat(),
            "status": self.status,
        }


class ReminderService:
    """Own reminder persistence and atomic delivery leases."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self._clock = clock
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout = 5000")
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    def _ensure_schema(self) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    reminder_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    due_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    claimed_at REAL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reminders_due
                ON reminders(profile_id, status, due_at)
                """
            )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ReminderRecord:
        claimed_at = row["claimed_at"]
        return ReminderRecord(
            reminder_id=str(row["reminder_id"]),
            profile_id=str(row["profile_id"]),
            text=str(row["text"]),
            due_at=float(row["due_at"]),
            status=str(row["status"]),
            created_at=float(row["created_at"]),
            claimed_at=(float(claimed_at) if claimed_at is not None else None),
        )

    def add(
        self,
        text: str,
        minutes: float,
        profile_id: str,
    ) -> ReminderRecord:
        clean_text = " ".join(str(text or "").split()).strip()
        try:
            delay_minutes = float(minutes)
        except (TypeError, ValueError):
            raise ValueError("invalid_reminder") from None
        if (
            not clean_text
            or len(clean_text) > _MAX_TEXT_CHARS
            or not math.isfinite(delay_minutes)
            or delay_minutes < 0
            or delay_minutes > _MAX_MINUTES
        ):
            raise ValueError("invalid_reminder")

        now = float(self._clock())
        record = ReminderRecord(
            reminder_id=str(uuid4()),
            profile_id=_profile_key(profile_id),
            text=clean_text,
            due_at=now + (delay_minutes * 60.0),
            status="pending",
            created_at=now,
        )
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO reminders(
                    reminder_id, profile_id, text, due_at,
                    status, created_at, claimed_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    record.reminder_id,
                    record.profile_id,
                    record.text,
                    record.due_at,
                    record.status,
                    record.created_at,
                ),
            )
        return record

    def list_active(self, profile_id: str) -> list[ReminderRecord]:
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT reminder_id, profile_id, text, due_at,
                       status, created_at, claimed_at
                FROM reminders
                WHERE profile_id = ? AND status IN ('pending', 'claimed')
                ORDER BY due_at, created_at
                """,
                (_profile_key(profile_id),),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def claim_due(
        self,
        profile_id: str,
        *,
        limit: int = 10,
        lease_seconds: float = 60.0,
    ) -> list[ReminderRecord]:
        now = float(self._clock())
        max_items = max(1, min(int(limit or 10), 50))
        lease = max(1.0, min(float(lease_seconds), 3600.0))
        lease_cutoff = now - lease

        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT reminder_id, profile_id, text, due_at,
                       status, created_at, claimed_at
                FROM reminders
                WHERE profile_id = ?
                  AND due_at <= ?
                  AND (
                    status = 'pending'
                    OR (status = 'claimed' AND claimed_at <= ?)
                  )
                ORDER BY due_at, created_at
                LIMIT ?
                """,
                (_profile_key(profile_id), now, lease_cutoff, max_items),
            ).fetchall()
            if not rows:
                connection.commit()
                return []
            reminder_ids = [str(row["reminder_id"]) for row in rows]
            placeholders = ",".join("?" for _ in reminder_ids)
            connection.execute(
                f"""
                UPDATE reminders
                SET status = 'claimed', claimed_at = ?
                WHERE reminder_id IN ({placeholders})
                """,
                (now, *reminder_ids),
            )
            connection.commit()

        return [
            ReminderRecord(
                reminder_id=str(row["reminder_id"]),
                profile_id=str(row["profile_id"]),
                text=str(row["text"]),
                due_at=float(row["due_at"]),
                status="claimed",
                created_at=float(row["created_at"]),
                claimed_at=now,
            )
            for row in rows
        ]

    def acknowledge(self, reminder_id: str, profile_id: str) -> bool:
        clean_id = str(reminder_id or "").strip()
        if not clean_id:
            return False
        with self._lock, closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE reminders
                SET status = 'delivered'
                WHERE reminder_id = ? AND profile_id = ? AND status = 'claimed'
                """,
                (clean_id, _profile_key(profile_id)),
            )
            return cursor.rowcount == 1


reminder_service = ReminderService(RUNTIME_PATHS.memory / "reminders.db")


__all__ = ["ReminderRecord", "ReminderService", "reminder_service"]
