from __future__ import annotations

import builtins
import importlib
import sys
import threading

from engines import memory_rag


def test_memory_rag_import_survives_embedding_provider_runtime_error(monkeypatch):
    previous_module = sys.modules.pop("engines.memory_rag", None)
    engines_pkg = sys.modules.get("engines")
    previous_attr = getattr(engines_pkg, "memory_rag", None) if engines_pkg else None
    had_previous_attr = hasattr(engines_pkg, "memory_rag") if engines_pkg else False
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith("langchain_huggingface") or name.startswith("langchain_community.embeddings"):
            raise RuntimeError("torch kernel collision")
        return original_import(name, *args, **kwargs)

    monkeypatch.setenv("JARVIS_TEST_MODE", "1")
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    try:
        module = importlib.import_module("engines.memory_rag")

        assert hasattr(module, "rag_motor")
        assert module.rag_motor.lista is False
        assert module.rag_motor.buscar_contexto("hello") == ""
    finally:
        sys.modules.pop("engines.memory_rag", None)
        if engines_pkg is not None:
            if had_previous_attr:
                engines_pkg.memory_rag = previous_attr
            elif hasattr(engines_pkg, "memory_rag"):
                delattr(engines_pkg, "memory_rag")
        if previous_module is not None:
            sys.modules["engines.memory_rag"] = previous_module


def test_memory_rag_init_disables_rag_when_embedding_provider_fails(monkeypatch):
    monkeypatch.setattr(memory_rag, "_load_huggingface_embeddings_class", lambda: None)
    monkeypatch.setattr(memory_rag, "_EMBEDDINGS_IMPORT_ERROR", RuntimeError("torch kernel collision"))

    motor = object.__new__(memory_rag.MemoryRAG)
    motor.db = None
    motor.embeddings = None
    motor._lock = threading.Lock()
    motor.lista = True

    motor._init_bg()

    assert motor.lista is False
    assert motor.embeddings is None
