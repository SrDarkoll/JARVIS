"""Hybrid search combining lexical feature hashing, text matching, and graph expansion."""

from __future__ import annotations

import logging
from typing import Any

from modules.memory.embeddings import LocalSemanticVectorizer, local_vectorizer
from modules.memory.models import CognitiveNode
from modules.memory.store import ProceduralMemoryStore

logger = logging.getLogger(__name__)


class HybridMemorySearchEngine:
    """Executes multi-channel retrieval with Reciprocal Rank Fusion (RRF)."""

    def __init__(
        self,
        store: ProceduralMemoryStore | None = None,
        vectorizer: LocalSemanticVectorizer | None = None,
    ) -> None:
        self.store = store or ProceduralMemoryStore()
        self.vectorizer = vectorizer or local_vectorizer

    def search(
        self,
        query: str,
        profile_id: str = "default",
        top_k: int = 5,
        graph_expand: bool = True,
    ) -> list[dict[str, Any]]:
        """Perform hybrid search over cognitive nodes and connected graph edges."""
        q = str(query or "").strip()
        if not q:
            recent_nodes = self.store.list_cognitive_nodes(profile_id=profile_id, limit=top_k)
            return [
                {
                    "node": node,
                    "score": 1.0,
                    "reasons": ["reciente"],
                }
                for node in recent_nodes
            ]

        query_vec = self.vectorizer.encode(q)
        all_nodes = self.store.list_cognitive_nodes(profile_id=profile_id, limit=100)
        if not all_nodes:
            return []

        # Channel 1: deterministic lexical feature-hash similarity ranking
        vector_scores: list[tuple[CognitiveNode, float]] = []
        for node in all_nodes:
            node_text = f"{node.title} {' '.join(node.tags)} {node.content}"
            node_vec = self.vectorizer.encode(node_text)
            sim = self.vectorizer.cosine_similarity(query_vec, node_vec)
            if sim > 0.05:
                vector_scores.append((node, sim))

        vector_scores.sort(key=lambda x: x[1], reverse=True)

        # Channel 2: Lexical Exact / Substring Ranking
        lexical_nodes = self.store.query_cognitive_nodes(q, profile_id=profile_id, limit=30)
        lexical_ranked = {node.node_id: idx for idx, node in enumerate(lexical_nodes)}

        # Channel 3: Graph Synaptic Traversal
        graph_neighbor_boost: dict[str, list[str]] = {}
        if graph_expand and vector_scores:
            top_roots = [n for n, _ in vector_scores[:3]]
            for root in top_roots:
                for edge in root.edges:
                    tgt = edge.target_title if edge.source_title.lower() == root.title.lower() else edge.source_title
                    graph_neighbor_boost.setdefault(tgt.lower(), []).append(f"conectado a '{root.title}' vía ({edge.relation})")

        # Reciprocal Rank Fusion (RRF)
        rrf_scores: dict[str, float] = {}
        node_map: dict[str, CognitiveNode] = {n.node_id: n for n in all_nodes}
        reasons_map: dict[str, list[str]] = {}

        # Accumulate feature-hash rank
        for rank, (node, sim) in enumerate(vector_scores):
            nid = node.node_id
            rrf_scores[nid] = rrf_scores.get(nid, 0.0) + (1.0 / (60.0 + rank))
            reasons_map.setdefault(nid, []).append(f"similitud léxica hash ({sim:.2f})")

        # Accumulate Lexical Rank
        for nid, rank in lexical_ranked.items():
            rrf_scores[nid] = rrf_scores.get(nid, 0.0) + (1.2 / (60.0 + rank))
            reasons_map.setdefault(nid, []).append(f"coincidencia textual (pos #{rank + 1})")

        # Accumulate Graph Boost
        for node in all_nodes:
            nid = node.node_id
            if node.title.lower() in graph_neighbor_boost:
                rrf_scores[nid] = rrf_scores.get(nid, 0.0) + 0.02
                reasons_map.setdefault(nid, []).extend(graph_neighbor_boost[node.title.lower()])

        # Sort combined results
        sorted_results = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

        results: list[dict[str, Any]] = []
        for nid, score in sorted_results[:top_k]:
            if nid in node_map:
                results.append({
                    "node": node_map[nid],
                    "score": round(score * 100, 2),
                    "reasons": reasons_map.get(nid, []),
                })

        return results
