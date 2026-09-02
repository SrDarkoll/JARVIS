"""Tests for Unified Interconnected Cognitive Memory Engine."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from unittest.mock import patch

import pytest
from core import jarvis_state
from modules.memory.models import (
    CognitiveNode,
    NodeType,
    SynapseEdge,
)
from modules.memory.service import UnifiedCognitiveMemoryService
from modules.memory.store import ProceduralMemoryStore
from modules.memory.tools import (
    consultar_memoria_unificada,
    enlazar_conceptos,
    guardar_memoria_entrelazada,
)


@pytest.fixture
def temp_cognitive_store(tmp_path):
    yield ProceduralMemoryStore(db_path=str(tmp_path / "cognitive-memory.db"))


def test_cognitive_models():
    edge = SynapseEdge(source_title="JARVIS", relation="escrito_en", target_title="Python")
    assert edge.source_title == "JARVIS"
    assert edge.relation == "escrito_en"

    node = CognitiveNode(
        node_id="n1",
        title="JARVIS",
        content="Asistente de IA local para Windows.",
        node_type=NodeType.PROJECT,
        tags=("ai", "windows", "python"),
        edges=(edge,),
    )
    data = node.to_dict()
    assert data["title"] == "JARVIS"
    assert data["type"] == "project"
    assert len(data["edges"]) == 1
    assert data["edges"][0]["rel"] == "escrito_en"


def test_store_cognitive_nodes_and_edges(temp_cognitive_store):
    store = temp_cognitive_store

    edge = SynapseEdge(source_title="Ramiro", relation="creador_de", target_title="JARVIS")
    node_ramiro = CognitiveNode(
        node_id="node_ramiro",
        title="Ramiro",
        content="Administrador y creador del sistema.",
        node_type=NodeType.FACT,
        tags=("admin", "user"),
        edges=(edge,),
    )
    store.save_cognitive_node(node_ramiro)

    node_jarvis = CognitiveNode(
        node_id="node_jarvis",
        title="JARVIS",
        content="Asistente de escritorio de alta velocidad.",
        node_type=NodeType.PROJECT,
        tags=("ai", "assistant"),
    )
    store.save_cognitive_node(node_jarvis)

    # Verify retrieval and bidirectional edge visibility
    found_ramiro = store.get_cognitive_node("Ramiro")
    assert found_ramiro is not None
    assert len(found_ramiro.edges) >= 1
    assert found_ramiro.edges[0].relation == "creador_de"

    found_jarvis = store.get_cognitive_node("JARVIS")
    assert found_jarvis is not None
    # Jarvis should see the edge connecting to Ramiro
    assert len(found_jarvis.edges) >= 1

    # 1-hop subgraph traversal
    subgraph_nodes, subgraph_edges = store.get_intertwined_subgraph("Ramiro")
    assert len(subgraph_nodes) >= 2  # Ramiro + connected JARVIS
    assert any(n.title == "JARVIS" for n in subgraph_nodes)


def test_intertwined_subgraph_does_not_cross_profile_boundary(temp_cognitive_store):
    store = temp_cognitive_store
    store.save_cognitive_node(
        CognitiveNode(
            node_id="private-a",
            title="Private A",
            content="Only profile A may read this.",
            profile_id="profile_a",
        )
    )
    store.save_cognitive_node(
        CognitiveNode(
            node_id="root-b",
            title="Root B",
            content="Profile B root.",
            edges=(SynapseEdge("Root B", "references", "Private A"),),
            profile_id="profile_b",
        )
    )

    nodes, _ = store.get_intertwined_subgraph("Root B", profile_id="profile_b")

    assert {node.title for node in nodes} == {"Root B"}
    assert all(node.profile_id == "profile_b" for node in nodes)


def test_cognitive_node_ids_are_scoped_by_profile(temp_cognitive_store):
    store = temp_cognitive_store
    store.save_cognitive_node(
        CognitiveNode(
            node_id="shared-id",
            title="Shared title",
            content="Profile A content",
            profile_id="profile_a",
        )
    )
    store.save_cognitive_node(
        CognitiveNode(
            node_id="shared-id",
            title="Shared title",
            content="Profile B content",
            profile_id="profile_b",
        )
    )

    node_a = store.get_cognitive_node("Shared title", profile_id="profile_a")
    node_b = store.get_cognitive_node("Shared title", profile_id="profile_b")

    assert node_a is not None
    assert node_b is not None
    assert node_a.content == "Profile A content"
    assert node_b.content == "Profile B content"


def test_default_profile_is_not_implicitly_shared(temp_cognitive_store):
    store = temp_cognitive_store
    store.save_cognitive_node(
        CognitiveNode(
            node_id="default-only",
            title="Default only",
            content="Not shared with named profiles.",
            profile_id="default",
        )
    )

    assert store.get_cognitive_node("Default only", profile_id="profile_a") is None
    assert store.list_cognitive_nodes(profile_id="profile_a") == []
    assert store.query_cognitive_nodes("Default only", profile_id="profile_a") == []


def test_legacy_tables_migrate_to_profile_scoped_primary_keys(tmp_path):
    db_path = tmp_path / "legacy-memory.db"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE cognitive_nodes (
                node_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                node_type TEXT NOT NULL,
                tags TEXT,
                metadata_json TEXT,
                profile_id TEXT NOT NULL,
                strength REAL DEFAULT 1.0,
                access_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE cognitive_edges (
                edge_id TEXT PRIMARY KEY,
                source_title TEXT NOT NULL,
                relation TEXT NOT NULL,
                target_title TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                profile_id TEXT NOT NULL
            );
            CREATE TABLE procedural_skills (
                skill_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                trigger_phrases TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                description TEXT,
                profile_id TEXT NOT NULL,
                success_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE episodic_memory (
                episode_id TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                goal TEXT,
                outcome TEXT,
                tags TEXT,
                profile_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                details_json TEXT
            );
            INSERT INTO cognitive_nodes (
                node_id, title, content, node_type, tags, metadata_json,
                profile_id, strength, access_count, created_at, updated_at
            ) VALUES (
                'legacy-node', 'Legacy node', 'Preserved', 'fact', '[]', '{}',
                'profile_a', 1.0, 0, '2026-08-20T00:00:00+00:00',
                '2026-08-20T00:00:00+00:00'
            );
            """
        )
        conn.commit()

    store = ProceduralMemoryStore(db_path=str(db_path))

    with closing(sqlite3.connect(db_path)) as conn:
        expected = {
            "cognitive_nodes": ["profile_id", "node_id"],
            "cognitive_edges": ["profile_id", "edge_id"],
            "procedural_skills": ["profile_id", "skill_id"],
            "episodic_memory": ["profile_id", "episode_id"],
        }
        for table, expected_pk in expected.items():
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            actual_pk = [row[1] for row in sorted(rows, key=lambda row: row[5]) if row[5]]
            assert actual_pk == expected_pk

    restored = store.get_cognitive_node("Legacy node", profile_id="profile_a")
    assert restored is not None
    assert restored.content == "Preserved"


def test_store_closes_every_created_connection(tmp_path):
    tracked_connections = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        tracked_connections.append(connection)
        return connection

    with patch("modules.memory.store.sqlite3.connect", side_effect=tracking_connect):
        store = ProceduralMemoryStore(db_path=str(tmp_path / "connection-lifetime.db"))
        store.list_cognitive_nodes(profile_id="profile_a")
        store.optimize()

    assert tracked_connections
    for connection in tracked_connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_service_auto_linking_and_recall(temp_cognitive_store):
    service = UnifiedCognitiveMemoryService(store=temp_cognitive_store)

    # 1. Save base project node
    service.save_cognitive_memory(
        title="Proyecto JARVIS",
        content="Plataforma de inteligencia artificial en Windows.",
        node_type=NodeType.PROJECT,
        tags=["ai", "python"],
    )

    # 2. Save preference node mentioning 'Proyecto JARVIS' -> should auto-link!
    service.save_cognitive_memory(
        title="Editor Preferido",
        content="Uso VS Code para programar en el Proyecto JARVIS.",
        node_type=NodeType.PREFERENCE,
    )

    # 3. Recall intertwined context
    recalled = service.recall_unified_context("VS Code")
    assert "RED COGNITIVA ENTRELAZADA" in recalled
    assert "Editor Preferido" in recalled
    assert "Proyecto JARVIS" in recalled  # Traversed via associative link!

    # 4. Check prompt context generation
    prompt_ctx = service.get_holistic_context_for_prompt()
    assert "MEMORIA COGNITIVA ENTRELAZADA" in prompt_ctx
    assert "Proyecto JARVIS" in prompt_ctx


def test_service_uses_active_profile_when_profile_is_omitted(temp_cognitive_store):
    service = UnifiedCognitiveMemoryService(store=temp_cognitive_store)

    with jarvis_state.active_profile("profile_a"):
        service.save_cognitive_memory(
            title="Active profile secret",
            content="Visible only to profile A.",
        )

    assert temp_cognitive_store.get_cognitive_node(
        "Active profile secret",
        profile_id="profile_a",
    ) is not None
    assert temp_cognitive_store.get_cognitive_node(
        "Active profile secret",
        profile_id="default",
    ) is None
    assert temp_cognitive_store.get_cognitive_node(
        "Active profile secret",
        profile_id="profile_b",
    ) is None


def test_langchain_memory_tools_use_request_profile(temp_cognitive_store):
    service = UnifiedCognitiveMemoryService(store=temp_cognitive_store)

    with patch("modules.memory.tools.unified_memory", service):
        with jarvis_state.active_profile("profile_a"):
            saved = guardar_memoria_entrelazada.invoke(
                {
                    "concepto": "Tool profile secret",
                    "contenido": "Private tool memory for profile A.",
                }
            )
        with jarvis_state.active_profile("profile_b"):
            hidden = consultar_memoria_unificada.invoke({"consulta": "Tool profile secret"})
        with jarvis_state.active_profile("profile_a"):
            visible = consultar_memoria_unificada.invoke({"consulta": "Tool profile secret"})

    assert "Memoria guardada" in saved
    assert "Private tool memory for profile A" not in hidden
    assert "RED COGNITIVA ENTRELAZADA" not in hidden
    assert "Tool profile secret" in visible
    assert "Private tool memory for profile A" in visible


def test_langchain_unified_memory_tools():
    # Save intertwined memory
    out_save = guardar_memoria_entrelazada.invoke({
        "concepto": "Modo Noche",
        "contenido": "Prefiero que el tema sea siempre oscuro.",
        "tipo": "preference",
        "relaciones": "aplica_a:UI_HUD",
        "etiquetas": "tema,ui,dark",
    })
    assert "Memoria guardada" in out_save
    assert "PREFERENCE" in out_save

    # Link concepts
    out_link = enlazar_conceptos.invoke({
        "origen": "Modo Noche",
        "relacion": "mejora_experiencia",
        "destino": "Usuario",
    })
    assert "Enlace creado" in out_link

    # Recall unified context
    out_query = consultar_memoria_unificada.invoke({"consulta": "Modo Noche"})
    assert "RED COGNITIVA ENTRELAZADA" in out_query
    assert "Modo Noche" in out_query
