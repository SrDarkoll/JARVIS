"""Profile-scoped policy for automatic, non-urgent JARVIS notices."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from threading import RLock

from core import jarvis_state
from core.jarvis_config import RUNTIME_PATHS

OWNER_ALIASES = frozenset({"admin", "owner", "default", "web_default"})
URGENT_SEVERITIES = frozenset({"warning", "error", "critical", "urgent"})


def _profile_key(profile_id: str) -> str:
    normalized = jarvis_state.normalize_profile_id(profile_id)
    return "web_default" if normalized.lower() in OWNER_ALIASES else normalized


class NotificationPolicy:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._lock = RLock()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=1.0)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS quiet_profiles "
                "(profile_id TEXT PRIMARY KEY, quiet INTEGER NOT NULL CHECK(quiet IN (0,1)))"
            )
        except Exception:
            connection.close()
            raise
        return connection

    def snapshot(self, profile_id: str) -> dict[str, object]:
        profile = _profile_key(profile_id)
        try:
            with self._lock, closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT quiet FROM quiet_profiles WHERE profile_id=?",
                    (profile,),
                ).fetchone()
            quiet = bool(row[0]) if row else False
            return {
                "profile_id": profile,
                "quiet": quiet,
                "auto_announce": not quiet,
                "state": "available",
                "code": "notifications_quiet" if quiet else "notifications_active",
            }
        except (OSError, sqlite3.Error):
            return {
                "profile_id": profile,
                "quiet": None,
                "auto_announce": False,
                "state": "unavailable",
                "code": "notification_policy_unavailable",
            }

    def set_quiet(self, profile_id: str, quiet: bool) -> dict[str, object]:
        if not isinstance(quiet, bool):
            raise ValueError("quiet_mode_requires_boolean")
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO quiet_profiles(profile_id,quiet) VALUES(?,?) "
                "ON CONFLICT(profile_id) DO UPDATE SET quiet=excluded.quiet",
                (_profile_key(profile_id), int(quiet)),
            )
        return self.snapshot(profile_id)

    def allows_automatic(self, profile_id: str, *, severity: str = "info") -> bool:
        if str(severity or "info").strip().lower() in URGENT_SEVERITIES:
            return True
        return bool(self.snapshot(profile_id)["auto_announce"])


notification_policy = NotificationPolicy(RUNTIME_PATHS.memory / "notification_policy.sqlite3")
