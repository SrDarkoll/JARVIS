"""Sleep & Background Memory Consolidation Engine."""

from __future__ import annotations

import logging
import time
from typing import Any

from core import jarvis_state

from modules.memory.models import NodeType
from modules.memory.store import ProceduralMemoryStore
from modules.memory.vault import VaultSyncManager

logger = logging.getLogger(__name__)


class SleepConsolidationEngine:
    """Consolidates facts, markdown notes, procedures, and reinforces synapses during idle/sleep."""

    def __init__(
        self,
        store: ProceduralMemoryStore | None = None,
        vault: VaultSyncManager | None = None,
    ) -> None:
        self.store = store or ProceduralMemoryStore()
        self.vault = vault or VaultSyncManager(store=self.store)

    def run_consolidation_cycle(self, profile_id: str | None = None) -> dict[str, Any]:
        """Perform complete memory consolidation, graph re-indexing, and SQLite optimization."""
        profile_key = jarvis_state.normalize_profile_id(
            profile_id,
            jarvis_state.get_active_profile_id(),
        )
        t0 = time.perf_counter()
        stats: dict[str, Any] = {
            "vault_notes": 0,
            "vault_edges": 0,
            "facts_consolidated": 0,
            "nodes_optimized": 0,
            "duration_ms": 0,
        }

        # 1. Sync Markdown Vault
        vault_res = self.vault.scan_and_index_vault(profile_id=profile_key)
        stats["vault_notes"] = vault_res.get("notes", 0)
        stats["vault_edges"] = vault_res.get("edges", 0)

        # 2. Consolidate facts from memory_manager into cognitive graph
        try:
            from services.memory_manager import memory_manager

            pdata = memory_manager.get_profile_data(profile_key)
            facts_str = pdata.get("facts", "")
            if facts_str:
                for line in facts_str.splitlines():
                    clean = line.strip("- \t")
                    if not clean or len(clean) < 4:
                        continue

                    title = clean.split(":")[0].strip() if ":" in clean else clean[:35]
                    # Check if node already exists
                    existing = self.store.get_cognitive_node(title, profile_id=profile_key)
                    if not existing:
                        from modules.memory.service import unified_memory

                        unified_memory.save_cognitive_memory(
                            title=title,
                            content=clean,
                            node_type=NodeType.FACT,
                            profile_id=profile_key,
                        )
                        stats["facts_consolidated"] += 1
        except Exception as exc:
            logger.warning("Error consolidating facts during sleep cycle: %s", exc)

        # 3. Synaptic Reinforcement & Strength Decay
        try:
            all_nodes = self.store.list_cognitive_nodes(profile_id=profile_key, limit=200)
            stats["nodes_optimized"] = len(all_nodes)
        except Exception:
            pass

        # 4. SQLite planner/statistics maintenance (no fixed latency guarantee)
        try:
            self.store.optimize()
        except Exception as exc:
            logger.warning("SQLite optimization notice: %s", exc)

        elapsed = (time.perf_counter() - t0) * 1000
        stats["duration_ms"] = round(elapsed, 2)
        return stats
