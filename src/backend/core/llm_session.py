"""Per-install session id sent to providers that group requests by session."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

_SESSION_FILE = "llm_session_id"
_VALID_ID = re.compile(r"^[0-9a-f]{32}$")


def install_session_id(home: Path) -> str:
    """Return the persisted 32-char hex session id, creating it when missing or invalid."""
    path = Path(home) / _SESSION_FILE
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if _VALID_ID.match(existing):
        return existing

    session_id = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(session_id, encoding="utf-8")
    except OSError:
        pass
    return session_id
