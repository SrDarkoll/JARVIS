"""Platform-appropriate paths for mutable JARVIS runtime data."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    home: Path
    logs: Path
    memory: Path
    cache: Path
    models_cache: Path
    temp: Path
    webview: Path

    def directories(self) -> tuple[Path, ...]:
        return (
            self.home,
            self.logs,
            self.memory,
            self.cache,
            self.models_cache,
            self.temp,
            self.webview,
        )


def resolve_runtime_paths(
    env: Mapping[str, str] | None = None,
    *,
    platform_name: str | None = None,
) -> RuntimePaths:
    """Resolve paths without creating or modifying the filesystem."""
    source = os.environ if env is None else env
    platform = sys.platform if platform_name is None else platform_name
    explicit = str(
        source.get("JARVIS_DATA_DIR")
        or source.get("JARVIS_RUNTIME_DIR")
        or ""
    ).strip()

    if explicit:
        home = Path(explicit).expanduser().resolve()
    elif platform == "win32":
        root = str(source.get("LOCALAPPDATA") or "").strip()
        if not root:
            root = str(Path.home() / "AppData" / "Local")
        home = (Path(root).expanduser() / "Jarvis").resolve()
    elif platform == "darwin":
        root = str(source.get("HOME") or "").strip()
        base = Path(root).expanduser() if root else Path.home()
        home = (
            base / "Library" / "Application Support" / "Jarvis"
        ).resolve()
    else:
        root = str(source.get("XDG_DATA_HOME") or "").strip()
        if not root:
            root = str(Path.home() / ".local" / "share")
        home = (Path(root).expanduser() / "jarvis").resolve()

    return RuntimePaths(
        home=home,
        logs=home / "logs",
        memory=home / "memory",
        cache=home / "cache",
        models_cache=home / "models",
        temp=home / "temp",
        webview=home / "webview",
    )


def ensure_runtime_paths(paths: RuntimePaths) -> RuntimePaths:
    """Create every runtime directory and verify it is writable."""
    for directory in paths.directories():
        probe_path: Path | None = None
        descriptor = -1
        try:
            directory.mkdir(parents=True, exist_ok=True)
            descriptor, raw_probe_path = tempfile.mkstemp(
                prefix=".jarvis-write-probe-",
                dir=directory,
            )
            probe_path = Path(raw_probe_path)
            os.close(descriptor)
            descriptor = -1
            probe_path.unlink()
            probe_path = None
        except OSError as exc:
            raise OSError(
                f"runtime_path_unwritable:{directory}"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if probe_path is not None:
                probe_path.unlink(missing_ok=True)
    return paths
