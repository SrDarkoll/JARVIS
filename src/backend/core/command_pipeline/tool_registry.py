"""Atomic publication of immutable tool registry snapshots."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolRegistrySnapshot:
    """One coherent version of the available tool collection."""

    version: int
    tools: tuple[Any, ...]
    by_name: Mapping[str, Any]


class ToolRegistryService:
    """Replace the complete registry without exposing partial mutations."""

    def __init__(self, tools=()) -> None:
        self._lock = threading.RLock()
        self._snapshot = self._build(0, tools)

    @staticmethod
    def _build(version: int, tools) -> ToolRegistrySnapshot:
        tool_tuple = tuple(tools)
        by_name: dict[str, Any] = {}
        for tool in tool_tuple:
            name = str(getattr(tool, "name", "") or "").strip()
            if not name:
                raise ValueError("invalid_tool_name")
            if name in by_name:
                raise ValueError("duplicate_tool_name")
            by_name[name] = tool
        return ToolRegistrySnapshot(
            version=version,
            tools=tool_tuple,
            by_name=MappingProxyType(by_name),
        )

    def snapshot(self) -> ToolRegistrySnapshot:
        with self._lock:
            return self._snapshot

    def replace(self, tools) -> ToolRegistrySnapshot:
        with self._lock:
            self._snapshot = self._build(
                self._snapshot.version + 1,
                tools,
            )
            return self._snapshot
