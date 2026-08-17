"""Rotating, redacted, human-readable runtime journal for JARVIS."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO

_LOGGER_NAME = "JARVIS_UNIFIED_FILE"
_MAX_MESSAGE_CHARS = 20_000
_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "cookie",
    "password",
    "secret",
    "token",
)
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:authorization|api[_-]?key|client_secret|password|secret|token)"
    r"\b\s*[:=]\s*)(?:bearer\s+)?[^\s,;&]+"
)
_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)([?&](?:access_token|api[_-]?key|client_secret|password|secret|token)=)"
    r"[^&#\s]+"
)
_BEARER_TOKEN_RE = re.compile(r"(?i)(\bbearer\s+)[^\s,;&]+")
_RUNTIME_STREAM_PREFIXES = (
    "[INFO] JARVIS:",
    "[WARNING] JARVIS:",
    "[ERROR] JARVIS:",
    "[CRITICAL] JARVIS:",
)

_config_lock = threading.RLock()
_file_logger: logging.Logger | None = None
_enabled = False
_log_path: Path | None = None
_console_lock = threading.RLock()
_console_capture_installed = False
_original_stdout: TextIO | None = None
_original_stderr: TextIO | None = None


def _normalize_category(category: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(category or "LOG").strip())
    return (normalized or "LOG")[:32].upper()


def _known_environment_secrets() -> tuple[str, ...]:
    values = []
    for key, value in os.environ.items():
        key_lower = key.lower()
        if not any(marker in key_lower for marker in _SENSITIVE_KEY_MARKERS):
            continue
        clean_value = str(value or "").strip()
        if len(clean_value) >= 8:
            values.append(clean_value)
    return tuple(sorted(set(values), key=len, reverse=True))


def redact_text(value: object) -> str:
    text = str(value if value is not None else "")
    for secret in _known_environment_secrets():
        text = text.replace(secret, "[REDACTED]")
    text = _SENSITIVE_QUERY_RE.sub(r"\1[REDACTED]", text)
    text = _CREDENTIAL_ASSIGNMENT_RE.sub(r"\1[REDACTED]", text)
    text = _BEARER_TOKEN_RE.sub(r"\1[REDACTED]", text)
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > _MAX_MESSAGE_CHARS:
        text = text[:_MAX_MESSAGE_CHARS] + "...[TRUNCATED]"
    return text


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key or "").strip().lower()
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def _redact_value(value: object, *, key: object = "") -> object:
    if _is_sensitive_key(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _redact_value(item_value, key=item_key) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_redact_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(value)


def _format_context_value(value: object, *, key: object = "") -> str:
    redacted = _redact_value(value, key=key)
    if isinstance(redacted, (dict, list)):
        return json.dumps(redacted, ensure_ascii=False, sort_keys=True, default=str)
    return redact_text(redacted)


def configure_unified_log(
    log_file: str | os.PathLike[str],
    *,
    enabled: bool,
    max_bytes: int,
    backup_count: int,
) -> None:
    """Configure the process-local rotating writer without capturing streams."""
    global _enabled, _file_logger, _log_path
    with _config_lock:
        if _file_logger is not None:
            for handler in tuple(_file_logger.handlers):
                handler.close()
                _file_logger.removeHandler(handler)

        _enabled = bool(enabled)
        _log_path = Path(log_file).resolve()
        logger = logging.getLogger(_LOGGER_NAME)
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.INFO)

        if _enabled:
            _log_path.parent.mkdir(parents=True, exist_ok=True)
            formatter = logging.Formatter(
                "[%(asctime)s.%(msecs)03d] [%(category)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler = RotatingFileHandler(
                _log_path,
                maxBytes=max(1, int(max_bytes)),
                backupCount=max(0, int(backup_count)),
                encoding="utf-8",
                delay=False,
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)

            # Also mirror to project root logs/log.txt for convenient access
            if os.getenv("JARVIS_TEST_MODE") != "1":
                try:
                    from core import jarvis_config

                    project_log = Path(jarvis_config.ROOT_DIR) / "logs" / "log.txt"
                    if project_log.resolve() != _log_path.resolve():
                        project_log.parent.mkdir(parents=True, exist_ok=True)
                        local_handler = RotatingFileHandler(
                            project_log,
                            maxBytes=max(1, int(max_bytes)),
                            backupCount=max(0, int(backup_count)),
                            encoding="utf-8",
                            delay=False,
                        )
                        local_handler.setFormatter(formatter)
                        logger.addHandler(local_handler)
                except Exception:
                    pass

        _file_logger = logger


def _ensure_configured() -> None:
    if _file_logger is not None:
        return
    from core import jarvis_config

    configure_unified_log(
        jarvis_config.UNIFIED_LOG_FILE,
        enabled=jarvis_config.UNIFIED_LOG_ENABLED,
        max_bytes=jarvis_config.UNIFIED_LOG_MAX_BYTES,
        backup_count=jarvis_config.UNIFIED_LOG_BACKUP_COUNT,
    )


def write_log(category: str, message: object, **context: object) -> None:
    """Append one sanitized physical line; logging failures never escape."""
    try:
        _ensure_configured()
        if not _enabled or _file_logger is None:
            return
        clean_message = redact_text(message)
        context_parts = [
            f"{redact_text(key)}={_format_context_value(value, key=key)}" for key, value in context.items()
        ]
        if context_parts:
            clean_message = f"{clean_message} | {' | '.join(context_parts)}"
        _file_logger.info(
            clean_message,
            extra={"category": _normalize_category(category)},
        )
    except Exception:
        return


def write_conversation(
    role: str,
    text: object,
    *,
    profile_id: str,
    channel: str = "brain",
) -> None:
    normalized_role = str(role or "SYSTEM").strip().upper() or "SYSTEM"
    normalized_profile = str(profile_id or "unknown").strip() or "unknown"
    write_log(
        "CONVERSATION",
        f"{normalized_role}({normalized_profile}): {text}",
        channel=channel,
    )


class UnifiedLogTee:
    """Forward writes to a stream and mirror complete lines to the journal."""

    def __init__(
        self,
        original: TextIO,
        category: str,
        *,
        suppress_runtime_lines: bool = False,
    ) -> None:
        self._original = original
        self._category = _normalize_category(category)
        self._suppress_runtime_lines = suppress_runtime_lines
        self._lock = threading.RLock()
        self._buffers: dict[int, str] = {}

    def write(self, value: str) -> int:
        text = str(value or "")
        written = self._original.write(text)
        thread_id = threading.get_ident()
        with self._lock:
            buffered = self._buffers.get(thread_id, "") + text
            while "\n" in buffered:
                line, buffered = buffered.split("\n", 1)
                self._capture_line(line.rstrip("\r"))
            self._buffers[thread_id] = buffered
        return len(text) if written is None else written

    def _capture_line(self, line: str) -> None:
        clean_line = line.strip()
        if not clean_line:
            return
        if self._suppress_runtime_lines and clean_line.startswith(_RUNTIME_STREAM_PREFIXES):
            return
        write_log(self._category, clean_line)

    def flush(self) -> None:
        self._original.flush()

    def isatty(self) -> bool:
        return bool(getattr(self._original, "isatty", lambda: False)())

    def fileno(self) -> int:
        return self._original.fileno()

    @property
    def encoding(self):
        return getattr(self._original, "encoding", None)

    @property
    def errors(self):
        return getattr(self._original, "errors", None)

    def __getattr__(self, name: str):
        return getattr(self._original, name)


def install_console_capture(
    *,
    log_file: str | os.PathLike[str] | None = None,
    enabled: bool | None = None,
    max_bytes: int | None = None,
    backup_count: int | None = None,
) -> bool:
    """Install stdout/stderr mirroring once for the current process."""
    global _console_capture_installed, _original_stderr, _original_stdout
    from core import jarvis_config

    configure_unified_log(
        log_file or jarvis_config.UNIFIED_LOG_FILE,
        enabled=jarvis_config.UNIFIED_LOG_ENABLED if enabled is None else enabled,
        max_bytes=(jarvis_config.UNIFIED_LOG_MAX_BYTES if max_bytes is None else max_bytes),
        backup_count=(jarvis_config.UNIFIED_LOG_BACKUP_COUNT if backup_count is None else backup_count),
    )
    if not _enabled:
        return False

    with _console_lock:
        if _console_capture_installed:
            return True
        _original_stdout = sys.stdout
        _original_stderr = sys.stderr
        sys.stdout = UnifiedLogTee(
            _original_stdout,
            "STDOUT",
            suppress_runtime_lines=True,
        )
        sys.stderr = UnifiedLogTee(
            _original_stderr,
            "STDERR",
            suppress_runtime_lines=True,
        )
        _console_capture_installed = True
    write_log("SYSTEM", "Unified log started", path=str(_log_path or ""))
    return True


def is_console_capture_installed() -> bool:
    return _console_capture_installed


def restore_console_capture() -> None:
    global _console_capture_installed, _original_stderr, _original_stdout
    with _console_lock:
        if not _console_capture_installed:
            return
        if _original_stdout is not None:
            sys.stdout = _original_stdout
        if _original_stderr is not None:
            sys.stderr = _original_stderr
        _original_stdout = None
        _original_stderr = None
        _console_capture_installed = False


def reset_unified_log_for_tests() -> None:
    """Close handlers and restore streams so tests do not leak global state."""
    global _enabled, _file_logger, _log_path
    restore_console_capture()
    with _config_lock:
        if _file_logger is not None:
            for handler in tuple(_file_logger.handlers):
                handler.close()
                _file_logger.removeHandler(handler)
        _file_logger = None
        _log_path = None
        _enabled = False
