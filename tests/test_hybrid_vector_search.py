"""Tests for Local Hybrid Vector and Graph Search Engine."""

from __future__ import annotations

import tempfile

from modules.memory.embeddings import LocalSemanticVectorizer
from modules.memory.hybrid_search import HybridMemorySearchEngine
from modules.memory.models import CognitiveNode, NodeType, SynapseEdge
from modules.memory.store import ProceduralMemoryStore
from modules.memory.tools import busqueda_semantica_hibrida


def test_local_semantic_vectorizer():
    vectorizer = LocalSemanticVectorizer(dimension=128)

    vec_audio1 = vectorizer.encode("Streaming de audio y voz en tiempo real con WebSockets")
    vec_audio2 = vectorizer.encode("Transmisión de voz en vivo y sonido full duplex")
    vec_cooking = vectorizer.encode("Receta de cocina para hacer pizza con queso y salsa")

    # Audio texts should have higher semantic cosine similarity than cooking
    sim_audio = vectorizer.cosine_similarity(vec_audio1, vec_audio2)
    sim_unrelated = vectorizer.cosine_similarity(vec_audio1, vec_cooking)

    assert sim_audio > sim_unrelated
    assert sim_audio > 0.15


def test_hybrid_search_with_rrf():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    store = ProceduralMemoryStore(db_path=db_path)
    engine = HybridMemorySearchEngine(store=store)

    # 1. Save connected nodes
    edge = SynapseEdge(source_title="Latencia Audio", relation="optimiza", target_title="Gemini Live")
    node1 = CognitiveNode(
        node_id="n1",
        title="Latencia Audio",
        content="Optimización de búfer PCM a 16kHz y 62ms TTFA.",
        node_type=NodeType.FACT,
        tags=("audio", "performance"),
        edges=(edge,),
    )
    node2 = CognitiveNode(
        node_id="n2",
        title="Gemini Live",
        content="Sesión de voz bidireccional continua sobre WebSockets.",
        node_type=NodeType.PROJECT,
        tags=("ai", "voice"),
    )
    node3 = CognitiveNode(
        node_id="n3",
        title="Control de Spotify",
        content="Reproducción de canciones y gestión de listas.",
        node_type=NodeType.SKILL,
        tags=("music",),
    )
    store.save_cognitive_node(node1)
    store.save_cognitive_node(node2)
    store.save_cognitive_node(node3)

    # 2. Search for fuzzy query
    results = engine.search("reducir retardo de sonido en vivo", top_k=2)
    assert len(results) >= 1
    top_node = results[0]["node"]
    # Either Latencia Audio or Gemini Live should be at the top due to vector similarity & graph connection
    assert top_node.title in {"Latencia Audio", "Gemini Live"}
    assert results[0]["score"] > 0


def test_hybrid_search_tool():
    from modules.memory.service import unified_memory

    unified_memory.save_cognitive_memory(
        title="Streaming Audio",
        content="Protocolo de baja latencia con WebSockets PCM a 16kHz.",
        tags=["audio", "pcm"],
    )
    out = busqueda_semantica_hibrida.invoke({"consulta": "audio"})
    assert "RED COGNITIVA ENTRELAZADA" in out
    assert "Streaming Audio" in out


def test_hybrid_search_in_base_catalog(monkeypatch):
    from core import core_tools

    monkeypatch.setenv("JARVIS_COGNITIVE_MEMORY_ENABLED", "true")
    tools = core_tools.get_base_tools()
    tool_names = {t.name for t in tools}

    assert "busqueda_semantica_hibrida" in tool_names
