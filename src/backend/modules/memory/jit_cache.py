"""Profile-partitioned in-memory cache for exact and bounded lexical lookups."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from modules.memory.embeddings import LocalSemanticVectorizer, local_vectorizer
from modules.memory.models import CognitiveNode, ProceduralSkill
from modules.memory.store import ProceduralMemoryStore

logger = logging.getLogger(__name__)


def _profile_key(profile_id: str | None) -> str:
    return str(profile_id or "").strip().lower() or "default"


@dataclass(slots=True)
class _ProfileCache:
    nodes_by_id: dict[str, CognitiveNode] = field(default_factory=dict)
    nodes_by_title: dict[str, CognitiveNode] = field(default_factory=dict)
    node_vectors: list[tuple[CognitiveNode, list[float]]] = field(default_factory=list)
    skills: dict[str, ProceduralSkill] = field(default_factory=dict)
    warmed_at: float = 0.0


class JITVoiceMemoryCache:
    """Pre-warmed cache with O(1) exact lookup and lexical ranking fallback."""

    def __init__(
        self,
        store: ProceduralMemoryStore | None = None,
        vectorizer: LocalSemanticVectorizer | None = None,
        max_lru_entries: int = 256,
    ) -> None:
        self.store = store or ProceduralMemoryStore()
        self.vectorizer = vectorizer or local_vectorizer
        self.max_lru_entries = max_lru_entries
        self._lock = threading.RLock()

        # Each profile owns an independent in-memory partition.
        self._profiles: dict[str, _ProfileCache] = {}
        self._lru_query_cache: dict[str, tuple[float, str]] = {}

    def warm_up(self, profile_id: str = "default") -> int:
        """Pre-load all cognitive nodes, edges and skills from SQLite into RAM."""
        start_t = time.perf_counter()
        profile_key = _profile_key(profile_id)
        with self._lock:
            all_nodes = self.store.list_cognitive_nodes(profile_id=profile_key, limit=500)
            all_skills = self.store.list_skills(profile_id=profile_key, limit=200)

            partition = _ProfileCache(
                nodes_by_id={n.node_id.lower(): n for n in all_nodes},
                nodes_by_title={n.title.lower(): n for n in all_nodes},
                skills={s.skill_id.lower(): s for s in all_skills},
            )
            partition.skills.update({s.name.lower(): s for s in all_skills})

            # Pre-compute vectors in RAM
            for n in all_nodes:
                text = f"{n.title} {' '.join(n.tags)} {n.content}"
                vec = self.vectorizer.encode(text)
                partition.node_vectors.append((n, vec))

            partition.warmed_at = time.time()
            self._profiles[profile_key] = partition
            self._clear_lru_for_profile(profile_key)
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            logger.debug(
                "JITVoiceMemoryCache pre-warmed profile %s with %d nodes & %d skills in %.2f ms",
                profile_key,
                len(all_nodes),
                len(all_skills),
                elapsed_ms,
            )
            return len(all_nodes)

    def invalidate(self, profile_id: str | None = None) -> None:
        """Clear cache state triggering re-warm on next request."""
        with self._lock:
            if profile_id is None:
                self._profiles.clear()
                self._lru_query_cache.clear()
                return

            profile_key = _profile_key(profile_id)
            self._profiles.pop(profile_key, None)
            self._clear_lru_for_profile(profile_key)

    def update_node(self, node: CognitiveNode) -> None:
        """Atomically update a single node in the in-memory RAM cache."""
        profile_key = _profile_key(node.profile_id)
        with self._lock:
            partition = self._profiles.setdefault(profile_key, _ProfileCache())
            node_id = node.node_id.lower()
            previous = partition.nodes_by_id.get(node_id)
            if previous is not None and previous.title.lower() != node.title.lower():
                partition.nodes_by_title.pop(previous.title.lower(), None)
            partition.nodes_by_id[node_id] = node
            partition.nodes_by_title[node.title.lower()] = node

            # Update or append vector
            text = f"{node.title} {' '.join(node.tags)} {node.content}"
            new_vec = self.vectorizer.encode(text)
            partition.node_vectors = [
                (cached_node, vector)
                for cached_node, vector in partition.node_vectors
                if cached_node.node_id.lower() != node_id
            ]
            partition.node_vectors.append((node, new_vec))
            self._clear_lru_for_profile(profile_key)

    def get_fast_node(self, title_or_id: str, profile_id: str = "default") -> CognitiveNode | None:
        """Sub-microsecond exact O(1) hash lookup."""
        with self._lock:
            key = str(title_or_id or "").strip().lower()
            partition = self._profiles.get(_profile_key(profile_id))
            if partition is None:
                return None
            return partition.nodes_by_id.get(key) or partition.nodes_by_title.get(key)

    def query_jit(
        self,
        query: str,
        profile_id: str = "default",
        limit: int = 5,
    ) -> str:
        """Search one profile's bounded RAM partition without latency guarantees."""
        q = str(query or "").strip()
        profile_key = _profile_key(profile_id)
        if not q:
            with self._lock:
                partition = self._profiles.get(profile_key)
                if partition is None or partition.warmed_at == 0.0:
                    self.warm_up(profile_id=profile_key)
                    partition = self._profiles[profile_key]
                top_nodes = list(partition.nodes_by_id.values())[:limit]
            if not top_nodes:
                return "Memoria JIT vacia."
            return self._format_nodes_summary(top_nodes, "Recientes en RAM")

        cache_key = f"{profile_key}:{q.lower()}:{limit}"
        with self._lock:
            # Check LRU RAM cache
            if cache_key in self._lru_query_cache:
                _, cached_text = self._lru_query_cache[cache_key]
                return cached_text

            # Ensure cache is warm
            partition = self._profiles.get(profile_key)
            if partition is None or partition.warmed_at == 0.0:
                self.warm_up(profile_id=profile_key)
                partition = self._profiles[profile_key]

            # 1. Exact Title / Keyword Match
            q_lower = q.lower()
            exact_hit = partition.nodes_by_title.get(q_lower)
            if exact_hit:
                subgraph = [exact_hit]
                for edge in exact_hit.edges:
                    tgt_title = (
                        edge.target_title
                        if edge.source_title.lower() == exact_hit.title.lower()
                        else edge.source_title
                    )
                    connected = partition.nodes_by_title.get(tgt_title.lower())
                    if connected and connected not in subgraph:
                        subgraph.append(connected)
                result_text = self._format_nodes_summary(subgraph[:limit], f"Coincidencia exacta JIT: '{q}'")
                self._cache_lru(cache_key, result_text)
                return result_text

            # 2. In-RAM Vector Similarity Scoring
            query_vec = self.vectorizer.encode(q)
            scored: list[tuple[CognitiveNode, float]] = []
            for node, n_vec in partition.node_vectors:
                sim = self.vectorizer.cosine_similarity(query_vec, n_vec)
                # Substring bonus
                if q_lower in node.title.lower() or q_lower in node.content.lower():
                    sim += 0.35
                scored.append((node, sim))

            scored.sort(key=lambda x: x[1], reverse=True)
            matched_nodes = [node for node, score in scored[:limit] if score > 0.08]

            if not matched_nodes:
                return f"No se encontro informacion en la memoria JIT para '{query}'."

            result_text = self._format_nodes_summary(matched_nodes, f"Búsqueda Léxica JIT RAM: '{q}'")
            self._cache_lru(cache_key, result_text)
            return result_text

    def _clear_lru_for_profile(self, profile_id: str) -> None:
        prefix = f"{profile_id}:"
        stale_keys = [key for key in self._lru_query_cache if key.startswith(prefix)]
        for key in stale_keys:
            self._lru_query_cache.pop(key, None)

    def _cache_lru(self, key: str, value: str) -> None:
        if len(self._lru_query_cache) >= self.max_lru_entries:
            # Remove oldest entry
            oldest_k = min(self._lru_query_cache.keys(), key=lambda k: self._lru_query_cache[k][0])
            self._lru_query_cache.pop(oldest_k, None)
        self._lru_query_cache[key] = (time.time(), value)

    def _format_nodes_summary(self, nodes: list[CognitiveNode], header: str) -> str:
        lines = [f"=== MEMORIA JIT EN RAM ({header}) ==="]
        for n in nodes:
            type_tag = n.node_type.value.upper()
            lines.append(f"\n⚡ [{type_tag}] {n.title}:")
            lines.append(f"   {n.content}")
            if n.edges:
                conn_strs = [
                    f"-> ({e.relation}) -> '{e.target_title}'"
                    if e.source_title.lower() == n.title.lower()
                    else f"<- ({e.relation}) <- '{e.source_title}'"
                    for e in n.edges[:3]
                ]
                lines.append(f"   🔗 {', '.join(conn_strs)}")
        return "\n".join(lines)


# Global singleton instance
jit_voice_cache = JITVoiceMemoryCache()
