from __future__ import annotations

import sqlite3
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from core import jarvis_state
from tools import memory


@pytest.fixture
def isolated_memory(monkeypatch, tmp_path):
    path = tmp_path / "facts.db"
    monkeypatch.setenv("JARVIS_DB_PATH", str(path))
    monkeypatch.setattr(memory.memory_manager, "_profiles", {})
    monkeypatch.setattr(
        memory.jarvis_config, "RUNTIME_FEATURES",
        replace(memory.jarvis_config.RUNTIME_FEATURES, cognitive_memory_enabled=False),
    )
    cognitive_writes = []
    monkeypatch.setitem(sys.modules, "modules.memory.service", SimpleNamespace(unified_memory=SimpleNamespace(
        save_cognitive_memory=lambda **kwargs: cognitive_writes.append(kwargs),
    )))
    memory.init_sqlite_db()
    with jarvis_state.active_profile("memory-test"):
        yield path, cognitive_writes


def test_basic_fact_save_confirms_real_sqlite_persistence(isolated_memory):
    path, _ = isolated_memory
    result = memory.guardar_dato_memoria.invoke({"dato": "Proyecto: manejo de errores"})
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT facts FROM profiles WHERE profile_id=?", ("memory-test",)).fetchone()

    assert row == ("- Proyecto: manejo de errores",)
    assert result.verified is True
    assert result.diagnostic_code == "memory_fact_persisted"


def test_basic_fact_save_rolls_back_cache_and_reports_storage_failure(isolated_memory, monkeypatch):
    memory.memory_manager.set_facts("memory-test", "- Anterior: conservar")
    monkeypatch.setattr(memory, "guardar_memoria_perfiles", lambda: False)

    result = memory.guardar_dato_memoria.invoke({"dato": "Nuevo: no persistido"})

    assert memory.memory_manager.snapshot("memory-test").facts == "- Anterior: conservar"
    assert result.status.value == "failed"
    assert result.verified is False
    assert result.diagnostic_code == "memory_fact_save_failed"


def test_full_basic_memory_does_not_claim_an_omitted_fact_was_saved(isolated_memory):
    previous = "\n".join(f"- Clave {index}: valor {index}" for index in range(25))
    memory.memory_manager.set_facts("memory-test", previous)
    result = memory.guardar_dato_memoria.invoke({"dato": "Clave nueva: dato nuevo"})

    assert result.status.value == "failed"
    assert result.diagnostic_code == "memory_fact_not_retained"
    assert memory.memory_manager.snapshot("memory-test").facts == previous


def test_oversized_basic_fact_is_not_reported_as_persisted(isolated_memory):
    result = memory.guardar_dato_memoria.invoke({"dato": "a" * 300})
    assert result.status.value == "failed"
    assert result.verified is False


def test_basic_memory_does_not_activate_experimental_cognitive_sync(isolated_memory):
    _, cognitive_writes = isolated_memory
    memory.guardar_dato_memoria.invoke({"dato": "Preferencia: música tranquila"})
    assert cognitive_writes == []
