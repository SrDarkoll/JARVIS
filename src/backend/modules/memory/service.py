"""Unified Interconnected Cognitive Memory Service."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from core import jarvis_state

from modules.memory.consolidator import SleepConsolidationEngine
from modules.memory.hybrid_search import HybridMemorySearchEngine
from modules.memory.jit_cache import JITVoiceMemoryCache
from modules.memory.models import (
    CognitiveNode,
    EpisodicMemoryEntry,
    NodeType,
    ProceduralSkill,
    SkillStep,
    SynapseEdge,
)
from modules.memory.store import ProceduralMemoryStore
from modules.memory.vault import VaultSyncManager

logger = logging.getLogger(__name__)

ActionInvoker = Callable[[str, dict[str, Any], str, str, str], Any]


def _active_profile_id(profile_id: str | None = None) -> str:
    return jarvis_state.normalize_profile_id(
        profile_id,
        jarvis_state.get_active_profile_id(),
    )


class UnifiedCognitiveMemoryService:
    """Unified cognitive memory substrate joining facts, procedures, and episodes."""

    def __init__(
        self,
        store: ProceduralMemoryStore | None = None,
        vault: VaultSyncManager | None = None,
        consolidator: SleepConsolidationEngine | None = None,
        search_engine: HybridMemorySearchEngine | None = None,
        jit_cache: JITVoiceMemoryCache | None = None,
        action_invoker: ActionInvoker | None = None,
    ) -> None:
        self.store = store or ProceduralMemoryStore()
        self.vault = vault or VaultSyncManager(store=self.store)
        self.consolidator = consolidator or SleepConsolidationEngine(store=self.store, vault=self.vault)
        self.search_engine = search_engine or HybridMemorySearchEngine(store=self.store)
        self.jit_cache = jit_cache or JITVoiceMemoryCache(store=self.store, vectorizer=self.search_engine.vectorizer)
        self._action_invoker = action_invoker

    # ─────────────────────────────────────────
    # Cognitive Nodes & Intertwined Knowledge
    # ─────────────────────────────────────────
    def save_cognitive_memory(
        self,
        title: str,
        content: str,
        node_type: NodeType | str = NodeType.FACT,
        tags: list[str] | tuple[str, ...] | None = None,
        relations: list[dict[str, str]] | list[SynapseEdge] | None = None,
        profile_id: str | None = None,
    ) -> CognitiveNode:
        """Save a knowledge node and automatically intertwine it with related concepts."""
        clean_title = str(title or "").strip()
        clean_content = str(content or "").strip()
        if not clean_title or not clean_content:
            raise ValueError("title_and_content_required")
        profile_key = _active_profile_id(profile_id)

        if isinstance(node_type, str):
            try:
                ntype = NodeType(node_type.lower())
            except Exception:
                ntype = NodeType.FACT
        else:
            ntype = node_type

        clean_tags = list(tags or [])
        if not clean_tags:
            # Auto-extract simple keywords as tags
            words = re.findall(r"\b[A-Za-z0-9_\-]{3,}\b", f"{clean_title} {clean_content}".lower())
            stopwords = {"para", "como", "este", "esta", "con", "que", "los", "las", "por", "del", "una", "uno", "the", "and", "for", "with", "this", "that"}
            clean_tags = [w for w in set(words) if w not in stopwords][:6]

        built_edges: list[SynapseEdge] = []
        if relations:
            for r in relations:
                if isinstance(r, SynapseEdge):
                    built_edges.append(r)
                elif isinstance(r, dict):
                    rel = str(r.get("rel") or r.get("relation") or "relacionado_con")
                    tgt = str(r.get("target") or r.get("destino") or "")
                    if tgt:
                        built_edges.append(
                            SynapseEdge(
                                source_title=clean_title,
                                relation=rel,
                                target_title=tgt,
                            )
                        )

        # Auto-link: check if content references existing node titles
        existing_nodes = self.store.list_cognitive_nodes(profile_id=profile_key, limit=30)
        existing_titles = {n.title.lower(): n.title for n in existing_nodes if n.title.lower() != clean_title.lower()}
        for title_lower, original_title in existing_titles.items():
            if title_lower in clean_content.lower() or title_lower in clean_title.lower():
                if not any(e.target_title.lower() == title_lower for e in built_edges):
                    built_edges.append(
                        SynapseEdge(
                            source_title=clean_title,
                            relation="asociado_a",
                            target_title=original_title,
                        )
                    )

        node = CognitiveNode(
            node_id=f"node_{str(uuid.uuid4())[:8]}",
            title=clean_title,
            content=clean_content,
            node_type=ntype,
            tags=tuple(clean_tags),
            edges=tuple(built_edges),
            profile_id=profile_key,
        )
        self.store.save_cognitive_node(node)
        self.jit_cache.update_node(node)
        return node

    def query_jit_memory(self, query: str, profile_id: str | None = None, limit: int = 5) -> str:
        """Use the profile-partitioned exact/lexical in-memory lookup path."""
        return self.jit_cache.query_jit(query, profile_id=_active_profile_id(profile_id), limit=limit)

    def link_concepts(
        self,
        source_title: str,
        relation: str,
        target_title: str,
        profile_id: str | None = None,
    ) -> bool:
        """Create a bidirectional semantic connection between two concepts."""
        s = str(source_title or "").strip()
        r = str(relation or "relacionado_con").strip()
        t = str(target_title or "").strip()
        if not s or not t:
            return False

        edge = SynapseEdge(source_title=s, relation=r, target_title=t)
        self.store.save_edge(edge, profile_id=_active_profile_id(profile_id))
        return True

    def recall_unified_context(self, query: str, profile_id: str | None = None, top_k: int = 6) -> str:
        """Perform an associative hybrid search (vector + graph + lexical) across all intertwined knowledge."""
        q = str(query or "").strip()
        search_results = self.search_engine.search(
            q,
            profile_id=_active_profile_id(profile_id),
            top_k=top_k,
        )

        if not search_results:
            return f"No se encontro informacion entrelazada relacionada con '{query}'."

        lines = [f"=== RED COGNITIVA ENTRELAZADA (Busqueda Hibrida: '{q or 'recientes'}') ==="]
        for item in search_results:
            node = item["node"]
            score = item["score"]
            reasons = ", ".join(item.get("reasons", []))
            type_label = node.node_type.value.upper()
            lines.append(f"\n📌 [{type_label}] {node.title} (Relevancia: {score:.0f}% | {reasons}):")
            lines.append(f"   {node.content}")
            if node.edges:
                edge_descs = [
                    f"→ ({e.relation}) → '{e.target_title}'"
                    if e.source_title.lower() == node.title.lower()
                    else f"← ({e.relation}) ← '{e.source_title}'"
                    for e in node.edges
                ]
                lines.append(f"   🔗 Conexiones: {', '.join(edge_descs)}")

        return "\n".join(lines)

    def get_holistic_context_for_prompt(self, profile_id: str | None = None) -> str:
        """Generate consolidated, rich intertwined memory landscape for system prompt."""
        nodes = self.store.list_cognitive_nodes(profile_id=_active_profile_id(profile_id), limit=12)
        if not nodes:
            return ""

        lines = ["\n--- MEMORIA COGNITIVA ENTRELAZADA Y CONOCIMIENTO ASOCIATIVO ---"]
        for n in nodes:
            lines.append(f"• [{n.node_type.value.upper()}] {n.title}: {n.content}")
            if n.edges:
                conns = [f"[{e.relation} -> {e.target_title}]" for e in n.edges[:3]]
                lines.append(f"  └─ Conexiones: {', '.join(conns)}")

        return "\n".join(lines)

    # ─────────────────────────────────────────
    # Procedural Skills Execution
    # ─────────────────────────────────────────
    def learn_procedure(
        self,
        name: str,
        trigger_phrases: list[str] | str,
        steps: Sequence[dict[str, Any] | str],
        description: str = "",
        profile_id: str | None = None,
    ) -> ProceduralSkill:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("skill_name_required")
        profile_key = _active_profile_id(profile_id)

        if isinstance(trigger_phrases, str):
            triggers = [t.strip() for t in trigger_phrases.split(",") if t.strip()]
        else:
            triggers = [str(t).strip() for t in trigger_phrases if str(t).strip()]
        if not triggers:
            triggers = [clean_name.lower()]

        normalized_steps: list[SkillStep] = []
        for idx, s in enumerate(steps, start=1):
            if isinstance(s, dict):
                normalized_steps.append(
                    SkillStep(
                        step_number=idx,
                        tool_or_command=str(s.get("action") or s.get("tool_or_command") or "").strip(),
                        arguments=dict(s.get("arguments", {})),
                        description=str(s.get("description", "")),
                    )
                )
            else:
                normalized_steps.append(
                    SkillStep(
                        step_number=idx,
                        tool_or_command=str(s).strip(),
                        arguments={},
                        description=str(s).strip(),
                    )
                )

        skill = ProceduralSkill(
            skill_id=f"skill_{str(uuid.uuid4())[:8]}",
            name=clean_name,
            trigger_phrases=tuple(triggers),
            steps=tuple(normalized_steps),
            description=str(description or f"Procedimiento aprendido para '{clean_name}'"),
            profile_id=profile_key,
        )
        self.store.save_skill(skill)

        # Also register as an intertwined cognitive node!
        step_summary = " -> ".join(s.tool_or_command for s in skill.steps)
        self.save_cognitive_memory(
            title=f"Procedimiento: {clean_name}",
            content=f"Pasos: {step_summary}. Activadores: {', '.join(triggers)}",
            node_type=NodeType.SKILL,
            tags=["skill", "workflow", clean_name.lower()],
            profile_id=profile_key,
        )

        return skill

    def find_matching_skill(self, query: str, profile_id: str | None = None) -> ProceduralSkill | None:
        q = str(query or "").strip().lower()
        if not q:
            return None
        profile_key = _active_profile_id(profile_id)

        skill = self.store.get_skill_by_name(q, profile_key)
        if skill:
            return skill

        all_skills = self.store.list_skills(profile_key)
        for s in all_skills:
            if s.name.lower() in q or q in s.name.lower():
                return s
            for trig in s.trigger_phrases:
                if trig.lower() in q or q in trig.lower():
                    return s
        return None

    def _invoke_action(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_input: str,
        source: str,
        profile_id: str,
    ) -> Any:
        invoker = self._action_invoker
        if invoker is None:
            from core.brain import tool_manager

            invoker = tool_manager._invocar_tool_entry
        return invoker(tool_name, arguments, user_input, source, profile_id)

    @staticmethod
    def _nested_action_must_stop(result: Any) -> bool:
        normalized = str(result or "").strip().lower()
        return normalized.startswith("access_denied:") or normalized in {
            "the requested tool is unavailable.",
            "the requested action failed.",
        }

    def execute_procedure(self, name_or_query: str, profile_id: str | None = None) -> str:
        profile_key = _active_profile_id(profile_id)
        skill = self.find_matching_skill(name_or_query, profile_key)
        if not skill:
            return f"No encontre ningun procedimiento aprendido que coincida con '{name_or_query}'."

        results: list[str] = []

        for step in skill.steps:
            tool_name = step.tool_or_command
            try:
                res = self._invoke_action(
                    tool_name,
                    dict(step.arguments),
                    str(name_or_query or skill.name),
                    "routine",
                    profile_key,
                )
                results.append(f"Paso {step.step_number} ({tool_name}): {res}")
                if self._nested_action_must_stop(res):
                    break
            except Exception as exc:
                results.append(f"Paso {step.step_number} ({tool_name}) fallo: {exc}")
                break

        return f"Procedimiento '{skill.name}' ejecutado ({len(results)} pasos):\n" + "\n".join(results)

    def record_episode(
        self,
        summary: str,
        goal: str = "",
        outcome: str = "",
        tags: list[str] | None = None,
        profile_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> EpisodicMemoryEntry:
        profile_key = _active_profile_id(profile_id)
        episode = EpisodicMemoryEntry(
            episode_id=f"ep_{str(uuid.uuid4())[:8]}",
            summary=str(summary or "").strip(),
            goal=str(goal or "").strip(),
            outcome=str(outcome or "").strip(),
            tags=tuple(tags or []),
            profile_id=profile_key,
            details=details or {},
        )
        self.store.record_episode(episode)

        # Also register as an intertwined cognitive node!
        self.save_cognitive_memory(
            title=f"Episodio: {episode.summary[:40]}",
            content=f"Meta: {goal} | Resultado: {outcome}",
            node_type=NodeType.EPISODE,
            tags=["episode", *(tags or [])],
            profile_id=profile_key,
        )

        return episode

    def recall_episodes(
        self,
        query: str,
        profile_id: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        episodes = self.store.query_episodes(
            query,
            profile_id=_active_profile_id(profile_id),
            limit=limit,
        )
        return [e.to_dict() for e in episodes]


# Compatibility aliases and global singleton instances
ProceduralMemoryService = UnifiedCognitiveMemoryService
unified_memory = UnifiedCognitiveMemoryService()
procedural_memory = unified_memory
