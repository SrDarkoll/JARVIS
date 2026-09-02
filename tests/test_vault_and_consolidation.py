"""Tests for Obsidian/Markdown Vault Synchronizer and Sleep Consolidation."""

from __future__ import annotations

import pytest
from core import jarvis_state
from modules.memory.consolidator import SleepConsolidationEngine
from modules.memory.store import ProceduralMemoryStore
from modules.memory.tools import (
    buscar_en_boveda,
    crear_nota_boveda,
    ejecutar_consolidacion_memoria,
    sincronizar_boveda_conocimiento,
)
from modules.memory.vault import VaultSyncManager


@pytest.fixture
def temp_vault_environment(tmp_path):
    store = ProceduralMemoryStore(db_path=str(tmp_path / "vault-memory.db"))
    vault = VaultSyncManager(vault_dir=str(tmp_path / "vault"), store=store)
    yield vault, store, SleepConsolidationEngine(store=store, vault=vault)


def test_vault_create_note_and_parse_wikilinks(temp_vault_environment):
    vault, store, _ = temp_vault_environment

    # 1. Create notes with [[wikilinks]]
    path1 = vault.create_or_update_note(
        title="Arquitectura JARVIS",
        content="El asistente utiliza un backend en Quart y una base de datos SQLite.",
        links=["Python", "Quart", "Windows UIA"],
        tags=["dev", "ai"],
    )
    assert path1.endswith("Arquitectura JARVIS.md")

    path2 = vault.create_or_update_note(
        title="Python",
        content="Lenguaje de programación principal del proyecto.",
        links=["JARVIS"],
        tags=["programming"],
    )
    assert path2.endswith("Python.md")

    # 2. Check indexed cognitive node and edges
    profile_id = jarvis_state.get_active_profile_id()
    node = store.get_cognitive_node("Arquitectura JARVIS", profile_id=profile_id)
    assert node is not None
    assert "dev" in node.tags or "ai" in node.tags
    assert len(node.edges) >= 3  # Links to Python, Quart, Windows UIA

    # 3. Check bidirectional traversal in graph
    subgraph_nodes, subgraph_edges = store.get_intertwined_subgraph(
        "Arquitectura JARVIS",
        profile_id=profile_id,
    )
    assert len(subgraph_nodes) >= 2  # Arquitectura + connected Python


def test_vault_search(temp_vault_environment):
    vault, _, _ = temp_vault_environment
    vault.create_or_update_note(
        title="Notas de Voz",
        content="La capa de voz full-duplex usa WebSocket y formato PCM a 16kHz.",
        tags=["audio", "voice"],
    )

    results = vault.search_vault("full-duplex")
    assert len(results) >= 1
    assert results[0]["title"] == "Notas de Voz"


def test_vault_notes_and_index_are_isolated_by_profile(temp_vault_environment):
    vault, store, _ = temp_vault_environment
    vault.create_or_update_note(
        title="Profile A private note",
        content="Confidential profile A vault content.",
        profile_id="profile_a",
    )

    assert vault.search_vault("Confidential", profile_id="profile_a")
    assert vault.search_vault("Confidential", profile_id="profile_b") == []
    assert store.get_cognitive_node(
        "Profile A private note",
        profile_id="profile_a",
    ) is not None
    assert store.get_cognitive_node(
        "Profile A private note",
        profile_id="profile_b",
    ) is None


def test_sleep_consolidation_cycle(temp_vault_environment):
    _, _, consolidator = temp_vault_environment

    stats = consolidator.run_consolidation_cycle()
    assert "vault_notes" in stats
    assert "duration_ms" in stats
    assert stats["duration_ms"] >= 0


def test_vault_langchain_tools():
    # Create note tool
    out_create = crear_nota_boveda.invoke({
        "titulo": "Proyecto Demo",
        "contenido": "Nota de prueba para verificación.",
        "enlaces": "JARVIS,Pruebas",
        "etiquetas": "test,demo",
    })
    assert "guardada exitosamente" in out_create

    # Sync vault tool
    out_sync = sincronizar_boveda_conocimiento.invoke({})
    assert "Bóveda sincronizada exitosamente" in out_sync

    # Search vault tool
    out_search = buscar_en_boveda.invoke({"consulta": "Proyecto Demo"})
    assert "Proyecto Demo" in out_search

    # Run consolidation tool
    out_cons = ejecutar_consolidacion_memoria.invoke({})
    assert "Ciclo de consolidación completado" in out_cons


def test_vault_tools_in_base_catalog():
    from core import core_tools

    tools = core_tools.get_base_tools()
    tool_names = {t.name for t in tools}

    assert "sincronizar_boveda_conocimiento" in tool_names
    assert "ejecutar_consolidacion_memoria" in tool_names
    assert "crear_nota_boveda" in tool_names
    assert "buscar_en_boveda" in tool_names
