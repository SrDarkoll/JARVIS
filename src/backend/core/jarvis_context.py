from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from typing import Any


class JarvisContext:
    _instance: JarvisContext | None = None
    _lock = threading.Lock()
    deps: dict[str, Any]
    logger: logging.Logger

    def __new__(cls) -> JarvisContext:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.deps = {}
                cls._instance.logger = logging.getLogger("JARVIS")
        return cls._instance

    def set(self, key: str, value: Any) -> None:
        self.deps[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.deps.get(key, default)

    def update(self, mapping: Mapping[str, Any]) -> None:
        self.deps.update(mapping)

    def __getitem__(self, key: str) -> Any:
        return self.deps[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.deps[key] = value


context = JarvisContext()
