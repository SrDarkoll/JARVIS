"""Bounded, atomic configuration updates for first-run setup."""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

_ALLOWED_PROVIDERS = {
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}
_KEY_MIN_LENGTH = 16
_KEY_MAX_LENGTH = 512
_ASSIGNMENT_PATTERN = re.compile(
    r"^(?P<prefix>\s*(?:export\s+)?)(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=.*$"
)
_WRITE_LOCK = threading.RLock()
_APPLY_LOCK = threading.RLock()


@dataclass(frozen=True, slots=True)
class ProviderSetup:
    provider: str
    api_key: str = field(repr=False)
    save_local: bool = True


class SetupConfigurationError(RuntimeError):
    """Controlled setup failure whose message is a public diagnostic code."""


def validate_provider_setup(
    provider: object,
    api_key: object,
    save_local: object,
) -> ProviderSetup:
    name = str(provider or "").strip().lower()
    raw_secret = str(api_key or "")
    if name not in _ALLOWED_PROVIDERS:
        raise SetupConfigurationError("invalid_provider")
    if any(char in raw_secret for char in ("\r", "\n", "\0")):
        raise SetupConfigurationError("invalid_key_format")
    secret = raw_secret.strip()
    if not (_KEY_MIN_LENGTH <= len(secret) <= _KEY_MAX_LENGTH):
        raise SetupConfigurationError("invalid_key_format")
    if not secret.isascii() or not secret.isprintable():
        raise SetupConfigurationError("invalid_key_format")
    if save_local is not True:
        raise SetupConfigurationError("invalid_request")
    return ProviderSetup(name, secret, True)


def provider_environment(
    source: Mapping[str, str],
    setup: ProviderSetup,
) -> dict[str, str]:
    result = {str(key): str(value) for key, value in source.items()}
    result["JARVIS_LLM_PROVIDER"] = setup.provider
    result[_ALLOWED_PROVIDERS[setup.provider]] = setup.api_key
    return result


class SetupAttemptLimiter:
    """Bounded, thread-safe sliding-window limiter for provider setup."""

    def __init__(
        self,
        *,
        limit: int = 5,
        window_seconds: float = 600.0,
        max_clients: int = 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = max(1, int(limit))
        self.window_seconds = max(1.0, float(window_seconds))
        self.max_clients = max(1, int(max_clients))
        self._clock = clock
        self._attempts: dict[str, deque[float]] = {}
        self._lock = threading.RLock()

    def check(self, client_id: object) -> float:
        """Record an allowed attempt or return seconds until retry."""

        key = str(client_id or "unknown").strip().lower() or "unknown"
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            for known_key in list(self._attempts):
                attempts = self._attempts[known_key]
                while attempts and attempts[0] <= cutoff:
                    attempts.popleft()
                if not attempts:
                    del self._attempts[known_key]

            attempts = self._attempts.get(key)
            if attempts is None:
                if len(self._attempts) >= self.max_clients:
                    oldest_key = min(
                        self._attempts,
                        key=lambda candidate: self._attempts[candidate][-1],
                    )
                    del self._attempts[oldest_key]
                attempts = self._attempts.setdefault(key, deque())

            if len(attempts) >= self.limit:
                return max(0.0, attempts[0] + self.window_seconds - now)
            attempts.append(now)
            return 0.0

    def reset(self) -> None:
        with self._lock:
            self._attempts.clear()


def _quoted_dotenv_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_configuration(existing: str, setup: ProviderSetup) -> str:
    updates = {
        "JARVIS_LLM_PROVIDER": setup.provider,
        _ALLOWED_PROVIDERS[setup.provider]: _quoted_dotenv_value(setup.api_key),
    }
    newline = "\r\n" if "\r\n" in existing else "\n"
    rendered: list[str] = []
    written: set[str] = set()

    for raw_line in existing.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        ending = raw_line[len(line) :] or newline
        match = _ASSIGNMENT_PATTERN.match(line)
        name = match.group("name") if match else ""
        if name not in updates:
            rendered.append(line + ending)
            continue
        if name in written:
            continue
        rendered.append(f"{name}={updates[name]}{ending}")
        written.add(name)

    if rendered and not rendered[-1].endswith(("\n", "\r")):
        rendered[-1] += newline
    for name, value in updates.items():
        if name not in written:
            rendered.append(f"{name}={value}{newline}")
    return "".join(rendered)


def save_provider_configuration(
    env_path: str | Path,
    setup: ProviderSetup,
) -> dict[str, object]:
    path = Path(env_path)
    parent = path.parent
    if not parent.is_dir():
        raise SetupConfigurationError("configuration_save_failed")

    with _WRITE_LOCK:
        try:
            existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            raise SetupConfigurationError("configuration_save_failed") from None
        rendered = _render_configuration(existing, setup)
        descriptor = -1
        temp_path: Path | None = None
        try:
            descriptor, raw_temp = tempfile.mkstemp(
                prefix=f"{path.name}.",
                suffix=".tmp",
                dir=parent,
            )
            temp_path = Path(raw_temp)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                descriptor = -1
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, path)
            temp_path = None
        except OSError:
            raise SetupConfigurationError("configuration_save_failed") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    return {"provider": setup.provider, "configured": True}


def read_configuration_snapshot(env_path: str | Path) -> bytes | None:
    path = Path(env_path)
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        raise SetupConfigurationError("configuration_save_failed") from None


def restore_configuration_snapshot(
    env_path: str | Path,
    snapshot: bytes | None,
) -> None:
    path = Path(env_path)
    with _WRITE_LOCK:
        try:
            if snapshot is None:
                path.unlink(missing_ok=True)
                return
            descriptor, raw_temp = tempfile.mkstemp(
                prefix=f"{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            temp_path = Path(raw_temp)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(snapshot)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temp_path, 0o600)
                os.replace(temp_path, path)
            finally:
                temp_path.unlink(missing_ok=True)
        except OSError:
            raise SetupConfigurationError("configuration_save_failed") from None


def apply_provider_configuration(
    *,
    provider: object,
    api_key: object,
    save_local: object,
    env_path: str | Path,
    environ: MutableMapping[str, str],
    prepare_runtime: Callable[[Mapping[str, str]], object],
    activate_runtime: Callable[[object], Mapping[str, object]],
) -> dict[str, object]:
    """Validate, persist and activate provider configuration transactionally."""

    setup = validate_provider_setup(provider, api_key, save_local)
    proposed_env = provider_environment(environ, setup)

    with _APPLY_LOCK:
        candidate = prepare_runtime(proposed_env)
        snapshot = read_configuration_snapshot(env_path)
        save_provider_configuration(env_path, setup)
        try:
            runtime_result = dict(activate_runtime(candidate))
        except Exception:
            try:
                restore_configuration_snapshot(env_path, snapshot)
            except SetupConfigurationError:
                raise SetupConfigurationError("configuration_rollback_failed") from None
            raise

        environ["JARVIS_LLM_PROVIDER"] = setup.provider
        environ[_ALLOWED_PROVIDERS[setup.provider]] = setup.api_key

    return {
        "ok": True,
        "configured": True,
        "provider": setup.provider,
        "runtime_ready": bool(runtime_result.get("runtime_ready", True)),
        "restart_required": False,
    }
