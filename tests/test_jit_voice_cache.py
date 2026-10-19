"""Tests for Sub-Millisecond JIT Voice Memory Cache."""

from __future__ import annotations

import time

import pytest
from modules.memory.jit_cache import JITVoiceMemoryCache
from modules.memory.models import CognitiveNode, NodeType, SynapseEdge
from modules.memory.store import ProceduralMemoryStore
from modules.memory.tools import consultar_memoria_jit


@pytest.fixture
def temp_jit_env(tmp_path):
    store = ProceduralMemoryStore(db_path=str(tmp_path / "jit-memory.db"))
    yield JITVoiceMemoryCache(store=store), store


def test_jit_cache_warmup_and_fast_lookup(temp_jit_env):
    cache, store = temp_jit_env

    # 1. Populate store with nodes
    edge = SynapseEdge(source_title="Latencia Audio", relation="optimiza", target_title="Gemini Live")
    node = CognitiveNode(
        node_id="n_audio",
        title="Latencia Audio",
        content="Optimizacion de bufer a 16kHz para menos de 50ms de latencia.",
        node_type=NodeType.FACT,
        tags=("audio", "voice"),
        edges=(edge,),
    )
    store.save_cognitive_node(node)

    # 2. Warm up
    count = cache.warm_up()
    assert count >= 1

    # 3. Microsecond exact lookup
    t0 = time.perf_counter()
    retrieved = cache.get_fast_node("Latencia Audio")
    elapsed_us = (time.perf_counter() - t0) * 1_000_000

    assert retrieved is not None
    assert retrieved.node_id == "n_audio"
    # Should execute in less than 1 millisecond (1000 us)
    assert elapsed_us < 1000


def test_jit_query_fuzzy_and_exact(temp_jit_env):
    cache, store = temp_jit_env

    node = CognitiveNode(
        node_id="n_spotify",
        title="Control Spotify",
        content="Comandos de reproduccion y listas de musica.",
        node_type=NodeType.SKILL,
        tags=("musica", "spotify"),
    )
    store.save_cognitive_node(node)
    cache.warm_up()

    # Exact query
    res_exact = cache.query_jit("Control Spotify")
    assert "MEMORIA JIT EN RAM" in res_exact
    assert "Control Spotify" in res_exact

    # Fuzzy vector query
    res_fuzzy = cache.query_jit("musica y canciones")
    assert "Control Spotify" in res_fuzzy


def test_jit_cache_partitions_same_id_and_title_by_profile(temp_jit_env):
    cache, store = temp_jit_env
    store.save_cognitive_node(
        CognitiveNode(
            node_id="shared-node",
            title="Shared concept",
            content="Profile A private content",
            profile_id="profile_a",
        )
    )
    store.save_cognitive_node(
        CognitiveNode(
            node_id="shared-node",
            title="Shared concept",
            content="Profile B private content",
            profile_id="profile_b",
        )
    )

    cache.warm_up(profile_id="profile_a")
    cache.warm_up(profile_id="profile_b")

    node_a = cache.get_fast_node("Shared concept", profile_id="profile_a")
    node_b = cache.get_fast_node("Shared concept", profile_id="profile_b")
    assert node_a is not None
    assert node_b is not None
    assert node_a.content == "Profile A private content"
    assert node_b.content == "Profile B private content"

    result_a = cache.query_jit("Shared concept", profile_id="profile_a")
    result_b = cache.query_jit("Shared concept", profile_id="profile_b")
    assert "Profile A private content" in result_a
    assert "Profile B private content" not in result_a
    assert "Profile B private content" in result_b
    assert "Profile A private content" not in result_b


def test_jit_profile_invalidation_discards_stale_nodes(temp_jit_env):
    cache, store = temp_jit_env
    store.save_cognitive_node(
        CognitiveNode(
            node_id="replaceable-node",
            title="Replaceable concept",
            content="Old private content",
            profile_id="profile_a",
        )
    )
    cache.warm_up(profile_id="profile_a")
    assert "Old private content" in cache.query_jit("Replaceable concept", profile_id="profile_a")

    store.save_cognitive_node(
        CognitiveNode(
            node_id="replaceable-node",
            title="Replaceable concept",
            content="New private content",
            profile_id="profile_a",
        )
    )
    cache.invalidate(profile_id="profile_a")

    refreshed = cache.query_jit("Replaceable concept", profile_id="profile_a")
    assert "New private content" in refreshed
    assert "Old private content" not in refreshed


def test_jit_update_does_not_mark_partial_partition_as_warmed(temp_jit_env):
    cache, store = temp_jit_env
    store.save_cognitive_node(
        CognitiveNode(
            node_id="persisted-node",
            title="Persisted concept",
            content="Already in SQLite",
            profile_id="profile_a",
        )
    )
    cache.update_node(
        CognitiveNode(
            node_id="new-node",
            title="New concept",
            content="Just added to RAM",
            profile_id="profile_a",
        )
    )

    result = cache.query_jit("Persisted concept", profile_id="profile_a")

    assert "Already in SQLite" in result


def test_jit_tool_invocation():
    from modules.memory.service import unified_memory

    unified_memory.save_cognitive_memory(
        title="Live Voice Streaming",
        content="Sub-segundo bidirectional WebSocket streaming.",
        tags=["voice", "live"],
    )
    out = consultar_memoria_jit.invoke({"consulta": "Live Voice Streaming"})
    assert "MEMORIA JIT EN RAM" in out
    assert "Live Voice Streaming" in out


def test_jit_tool_in_base_catalog(monkeypatch):
    from core import core_tools

    monkeypatch.setenv("JARVIS_COGNITIVE_MEMORY_ENABLED", "true")
    tools = core_tools.get_base_tools()
    tool_names = {t.name for t in tools}

    assert "consultar_memoria_jit" in tool_names
