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


def test_memory_rag_size_limit_uses_default_for_invalid_environment(monkeypatch):
    monkeypatch.setenv("JARVIS_MAX_FAISS_FILE_BYTES", "not-a-number")

    assert memory_rag._read_positive_int_env("JARVIS_MAX_FAISS_FILE_BYTES", 123) == 123

    monkeypatch.setenv("JARVIS_MAX_FAISS_FILE_BYTES", "0")
    assert memory_rag._read_positive_int_env("JARVIS_MAX_FAISS_FILE_BYTES", 123) == 123


def test_embeddings_import_error_exposes_exception_class_only(monkeypatch):
    secret_error = r"provider failed through C:\Users\ramir\private\proxy-token"
    monkeypatch.setattr(memory_rag, "_EMBEDDINGS_IMPORT_ERROR", RuntimeError(secret_error))

    rendered = memory_rag._format_embeddings_import_error()

    assert rendered == "RuntimeError"
    assert secret_error not in rendered


def test_memory_rag_import_failure_is_sanitized(monkeypatch, capsys):
    secret_error = r"provider failed through C:\Users\ramir\private\proxy-token"
    events = []
    monkeypatch.setattr(memory_rag, "_load_huggingface_embeddings_class", lambda: None)
    monkeypatch.setattr(memory_rag, "_EMBEDDINGS_IMPORT_ERROR", RuntimeError(secret_error))
    monkeypatch.setattr(
        "core.jarvis_observability.obs_event",
        lambda event, **fields: events.append((event, fields)),
    )

    motor = object.__new__(memory_rag.MemoryRAG)
    motor.db = None
    motor.embeddings = None
    motor._lock = threading.Lock()
    motor.lista = True

    motor._init_bg()

    assert motor.lista is False
    assert events == [
        ("rag_embedding_provider_unavailable", {"error": "RuntimeError"})
    ]
    assert secret_error not in capsys.readouterr().out


def test_memory_rag_provider_init_error_is_sanitized(monkeypatch, tmp_path, capsys):
    secret_error = r"download failed through C:\Users\ramir\private\proxy-token"
    events = []

    class BrokenEmbeddings:
        def __init__(self, **_kwargs):
            raise RuntimeError(secret_error)

    monkeypatch.setattr(
        memory_rag, "_load_huggingface_embeddings_class", lambda: BrokenEmbeddings
    )
    monkeypatch.setattr(memory_rag, "_HF_CACHE", str(tmp_path / "hf-cache"))
    monkeypatch.setattr(
        "core.jarvis_observability.obs_event",
        lambda event, **fields: events.append((event, fields)),
    )

    motor = object.__new__(memory_rag.MemoryRAG)
    motor.db = None
    motor.embeddings = None
    motor._lock = threading.Lock()
    motor.lista = True

    motor._init_bg()

    assert motor.lista is False
    assert events == [("rag_init_huggingface_error", {"error": "RuntimeError"})]
    assert secret_error not in capsys.readouterr().out


def test_memory_rag_index_load_error_hides_path_and_exception(
    monkeypatch, tmp_path, capsys
):
    secret_error = r"corrupt index at C:\Users\ramir\private\faiss-index"
    events = []
    index_dir = tmp_path / "faiss-index"
    index_dir.mkdir()

    class WorkingEmbeddings:
        def __init__(self, **_kwargs):
            pass

    class BrokenFAISS:
        @staticmethod
        def load_local(*_args, **_kwargs):
            raise RuntimeError(secret_error)

    monkeypatch.setattr(
        memory_rag, "_load_huggingface_embeddings_class", lambda: WorkingEmbeddings
    )
    monkeypatch.setattr(memory_rag, "_HF_CACHE", str(tmp_path / "hf-cache"))
    monkeypatch.setattr(memory_rag, "VECTOR_DB_DIR", str(index_dir))
    monkeypatch.setattr(memory_rag, "_validate_faiss_index", lambda _path: (True, ""))
    monkeypatch.setattr(memory_rag, "FAISS", BrokenFAISS)
    monkeypatch.setattr(
        "core.jarvis_observability.obs_event",
        lambda event, **fields: events.append((event, fields)),
    )

    motor = object.__new__(memory_rag.MemoryRAG)
    motor.db = None
    motor.embeddings = None
    motor._lock = threading.Lock()
    motor.lista = True

    motor._init_bg()

    assert motor.db is None
    assert events == [("rag_index_load_error", {"error": "RuntimeError"})]
    output = capsys.readouterr().out
    assert secret_error not in output
    assert str(index_dir) not in output


def test_memory_rag_indexing_error_is_sanitized(monkeypatch, capsys):
    from types import SimpleNamespace

    secret_error = r"index failed at C:\Users\ramir\private\conversation.txt"
    events = []

    class BrokenFAISS:
        @staticmethod
        def from_documents(*_args, **_kwargs):
            raise RuntimeError(secret_error)

    monkeypatch.setattr(memory_rag, "FAISS", BrokenFAISS)
    monkeypatch.setattr(
        memory_rag,
        "Document",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        "core.jarvis_observability.obs_event",
        lambda event, **fields: events.append((event, fields)),
    )

    motor = object.__new__(memory_rag.MemoryRAG)
    motor.db = None
    motor.embeddings = object()
    motor._lock = threading.Lock()
    motor.lista = True

    motor.agregar_interaccion("private request", "private response")

    assert events == [("rag_indexing_error", {"error": "RuntimeError"})]
    assert secret_error not in capsys.readouterr().out


def test_memory_rag_search_error_hides_query_and_exception(monkeypatch, capsys):
    secret_query = "private request containing API_TOKEN"
    secret_error = r"search failed at C:\Users\ramir\private\faiss-index"
    events = []

    class BrokenDB:
        @staticmethod
        def similarity_search(*_args, **_kwargs):
            raise RuntimeError(secret_error)

    monkeypatch.setattr(
        "core.jarvis_observability.obs_event",
        lambda event, **fields: events.append((event, fields)),
    )

    motor = object.__new__(memory_rag.MemoryRAG)
    motor.db = BrokenDB()
    motor.embeddings = object()
    motor._lock = threading.Lock()
    motor.lista = True

    assert motor.buscar_contexto(secret_query) == ""
    assert events == [("rag_search_error", {"error": "RuntimeError"})]
    output = capsys.readouterr().out
    assert secret_query not in output
    assert secret_error not in output
