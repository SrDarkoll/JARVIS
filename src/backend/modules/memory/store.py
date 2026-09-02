"""SQLite persistence layer for Unified Interconnected Cognitive Memory."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Generator

from core import jarvis_config

from modules.memory.models import (
    CognitiveNode,
    EpisodicMemoryEntry,
    NodeType,
    ProceduralSkill,
    SkillStep,
    SynapseEdge,
)


def _default_db_path() -> str:
    return os.getenv("JARVIS_DB_PATH") or os.path.join(
        jarvis_config.MEMORY_DIR,
        "memoria_jarvis.db",
    )


class ProceduralMemoryStore:
    """Thread-safe SQLite store for unified cognitive nodes, edges, skills, and episodes."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _default_db_path()
        self._lock = threading.RLock()
        self._init_tables()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass
        try:
            yield conn
        finally:
            conn.close()

    def _migrate_table_pk(self, conn: sqlite3.Connection, table: str, expected_pk: tuple[str, ...]) -> None:
        pk_cols = ", ".join(expected_pk)
        if table == "cognitive_nodes":
            create_sql = f"""
                CREATE TABLE {table}_migrated (
                    node_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    tags TEXT,
                    metadata_json TEXT,
                    profile_id TEXT NOT NULL,
                    strength REAL DEFAULT 1.0,
                    access_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY ({pk_cols})
                );
            """
        elif table == "cognitive_edges":
            create_sql = f"""
                CREATE TABLE {table}_migrated (
                    edge_id TEXT NOT NULL,
                    source_title TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    target_title TEXT NOT NULL,
                    weight REAL DEFAULT 1.0,
                    profile_id TEXT NOT NULL,
                    PRIMARY KEY ({pk_cols})
                );
            """
        elif table == "procedural_skills":
            create_sql = f"""
                CREATE TABLE {table}_migrated (
                    skill_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    trigger_phrases TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    description TEXT,
                    profile_id TEXT NOT NULL,
                    success_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY ({pk_cols})
                );
            """
        elif table == "episodic_memory":
            create_sql = f"""
                CREATE TABLE {table}_migrated (
                    episode_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    goal TEXT,
                    outcome TEXT,
                    tags TEXT,
                    profile_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    details_json TEXT,
                    PRIMARY KEY ({pk_cols})
                );
            """
        else:
            return

        conn.execute(create_sql)
        conn.execute(f"INSERT OR IGNORE INTO {table}_migrated SELECT * FROM {table}")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {table}_migrated RENAME TO {table}")

    def _init_tables(self) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cognitive_nodes (
                    node_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    tags TEXT,
                    metadata_json TEXT,
                    profile_id TEXT NOT NULL,
                    strength REAL DEFAULT 1.0,
                    access_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (profile_id, node_id)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cognitive_edges (
                    edge_id TEXT NOT NULL,
                    source_title TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    target_title TEXT NOT NULL,
                    weight REAL DEFAULT 1.0,
                    profile_id TEXT NOT NULL,
                    PRIMARY KEY (profile_id, edge_id)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS procedural_skills (
                    skill_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    trigger_phrases TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    description TEXT,
                    profile_id TEXT NOT NULL,
                    success_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (profile_id, skill_id)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodic_memory (
                    episode_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    goal TEXT,
                    outcome TEXT,
                    tags TEXT,
                    profile_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    details_json TEXT,
                    PRIMARY KEY (profile_id, episode_id)
                );
                """
            )
            expected_pks = {
                "cognitive_nodes": ("profile_id", "node_id"),
                "cognitive_edges": ("profile_id", "edge_id"),
                "procedural_skills": ("profile_id", "skill_id"),
                "episodic_memory": ("profile_id", "episode_id"),
            }
            for table, expected_pk in expected_pks.items():
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
                if rows:
                    actual_pk = tuple(row[1] for row in sorted(rows, key=lambda row: row[5]) if row[5])
                    if actual_pk != expected_pk:
                        self._migrate_table_pk(conn, table, expected_pk)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cog_title ON cognitive_nodes (title, profile_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_edge_source ON cognitive_edges (source_title, profile_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_edge_target ON cognitive_edges (target_title, profile_id);"
            )
            conn.commit()

    # ─────────────────────────────────────────
    # Cognitive Nodes & Synapse Edges CRUD
    # ─────────────────────────────────────────
    def save_cognitive_node(self, node: CognitiveNode) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cognitive_nodes (
                    node_id, title, content, node_type, tags, metadata_json,
                    profile_id, strength, access_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.node_id,
                    node.title.strip(),
                    node.content.strip(),
                    node.node_type.value if isinstance(node.node_type, NodeType) else str(node.node_type),
                    json.dumps(list(node.tags)),
                    json.dumps(node.metadata),
                    node.profile_id,
                    node.strength,
                    node.access_count,
                    node.created_at.isoformat(),
                    node.updated_at.isoformat(),
                ),
            )
            # Save associated edges
            for edge in node.edges:
                edge_id = f"{edge.source_title.lower()}::{edge.relation.lower()}::{edge.target_title.lower()}"
                conn.execute(
                    """
                    INSERT OR REPLACE INTO cognitive_edges (
                        edge_id, source_title, relation, target_title, weight, profile_id
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge_id,
                        edge.source_title.strip(),
                        edge.relation.strip(),
                        edge.target_title.strip(),
                        edge.weight,
                        node.profile_id,
                    ),
                )
            conn.commit()

    def save_edge(self, edge: SynapseEdge, profile_id: str = "default") -> None:
        with self._lock, self._get_connection() as conn:
            edge_id = f"{edge.source_title.lower()}::{edge.relation.lower()}::{edge.target_title.lower()}"
            conn.execute(
                """
                INSERT OR REPLACE INTO cognitive_edges (
                    edge_id, source_title, relation, target_title, weight, profile_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    edge_id,
                    edge.source_title.strip(),
                    edge.relation.strip(),
                    edge.target_title.strip(),
                    edge.weight,
                    profile_id.strip(),
                ),
            )
            conn.commit()

    def get_cognitive_node(self, title: str, profile_id: str = "default") -> CognitiveNode | None:
        with self._lock, self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM cognitive_nodes
                WHERE LOWER(title) = LOWER(?) AND profile_id = ?
                LIMIT 1
                """,
                (title.strip(), profile_id.strip()),
            ).fetchone()
            if not row:
                return None
            edges = self._get_edges_for_title(conn, str(row["title"]), profile_id)
            return self._row_to_cognitive_node(row, edges)

    def _get_edges_for_title(self, conn: sqlite3.Connection, title: str, profile_id: str) -> list[SynapseEdge]:
        rows = conn.execute(
            """
            SELECT source_title, relation, target_title, weight FROM cognitive_edges
            WHERE (LOWER(source_title) = LOWER(?) OR LOWER(target_title) = LOWER(?))
              AND profile_id = ?
            """,
            (title.strip(), title.strip(), profile_id.strip()),
        ).fetchall()
        return [
            SynapseEdge(
                source_title=str(r["source_title"]),
                relation=str(r["relation"]),
                target_title=str(r["target_title"]),
                weight=float(r["weight"] or 1.0),
            )
            for r in rows
        ]

    def list_cognitive_nodes(self, profile_id: str = "default", limit: int = 50) -> list[CognitiveNode]:
        with self._lock, self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM cognitive_nodes
                WHERE profile_id = ?
                ORDER BY updated_at DESC, access_count DESC
                LIMIT ?
                """,
                (profile_id.strip(), limit),
            ).fetchall()
            result = []
            for r in rows:
                edges = self._get_edges_for_title(conn, str(r["title"]), profile_id)
                result.append(self._row_to_cognitive_node(r, edges))
            return result

    def query_cognitive_nodes(self, query: str, profile_id: str = "default", limit: int = 10) -> list[CognitiveNode]:
        with self._lock, self._get_connection() as conn:
            q = f"%{query.strip().lower()}%"
            rows = conn.execute(
                """
                SELECT * FROM cognitive_nodes
                WHERE profile_id = ?
                  AND (LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(tags) LIKE ? OR LOWER(node_type) LIKE ?)
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (profile_id.strip(), q, q, q, q, limit),
            ).fetchall()
            result = []
            for r in rows:
                edges = self._get_edges_for_title(conn, str(r["title"]), profile_id)
                result.append(self._row_to_cognitive_node(r, edges))
            return result

    def get_intertwined_subgraph(
        self,
        query: str,
        profile_id: str = "default",
        max_nodes: int = 8,
    ) -> tuple[list[CognitiveNode], list[SynapseEdge]]:
        """Retrieve matching root nodes plus 1-hop connected associative neighbors."""
        matched = self.query_cognitive_nodes(query, profile_id=profile_id, limit=max_nodes)
        if not matched:
            return [], []

        node_map: dict[str, CognitiveNode] = {n.title.lower(): n for n in matched}
        all_edges: list[SynapseEdge] = []

        with self._lock, self._get_connection() as conn:
            for node in matched:
                edges = self._get_edges_for_title(conn, node.title, profile_id)
                all_edges.extend(edges)
                # Expand 1-hop neighbors
                for e in edges:
                    neighbor_title = e.target_title if e.source_title.lower() == node.title.lower() else e.source_title
                    if neighbor_title.lower() not in node_map and len(node_map) < max_nodes:
                        row = conn.execute(
                            "SELECT * FROM cognitive_nodes WHERE LOWER(title) = LOWER(?) AND profile_id = ? LIMIT 1",
                            (neighbor_title.strip(), profile_id.strip()),
                        ).fetchone()
                        if row:
                            neighbor_edges = self._get_edges_for_title(conn, str(row["title"]), profile_id)
                            node_map[neighbor_title.lower()] = self._row_to_cognitive_node(row, neighbor_edges)

        return list(node_map.values()), all_edges

    @staticmethod
    def _row_to_cognitive_node(row: sqlite3.Row, edges: list[SynapseEdge]) -> CognitiveNode:
        tags = tuple(json.loads(row["tags"] or "[]"))
        meta = json.loads(row["metadata_json"] or "{}")
        created = datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(UTC)
        updated = datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else datetime.now(UTC)
        try:
            ntype = NodeType(str(row["node_type"]))
        except Exception:
            ntype = NodeType.FACT

        return CognitiveNode(
            node_id=str(row["node_id"]),
            title=str(row["title"]),
            content=str(row["content"]),
            node_type=ntype,
            tags=tags,
            edges=tuple(edges),
            metadata=meta,
            profile_id=str(row["profile_id"]),
            strength=float(row["strength"] or 1.0),
            access_count=int(row["access_count"] or 0),
            created_at=created,
            updated_at=updated,
        )

    # ─────────────────────────────────────────
    # Procedural Skills CRUD
    # ─────────────────────────────────────────
    def save_skill(self, skill: ProceduralSkill) -> None:
        with self._lock, self._get_connection() as conn:
            steps_data = [
                {
                    "step_number": s.step_number,
                    "tool_or_command": s.tool_or_command,
                    "arguments": s.arguments,
                    "description": s.description,
                }
                for s in skill.steps
            ]
            conn.execute(
                """
                INSERT OR REPLACE INTO procedural_skills (
                    skill_id, name, trigger_phrases, steps_json, description,
                    profile_id, success_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    skill.skill_id,
                    skill.name,
                    json.dumps(list(skill.trigger_phrases)),
                    json.dumps(steps_data),
                    skill.description,
                    skill.profile_id,
                    skill.success_count,
                    skill.created_at.isoformat(),
                    skill.updated_at.isoformat(),
                ),
            )
            conn.commit()

    def get_skill_by_name(self, name: str, profile_id: str = "default") -> ProceduralSkill | None:
        with self._lock, self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM procedural_skills
                WHERE LOWER(name) = LOWER(?) AND profile_id = ?
                LIMIT 1
                """,
                (name.strip(), profile_id.strip()),
            ).fetchone()
            if not row:
                return None
            return self._row_to_skill(row)

    def list_skills(self, profile_id: str = "default", limit: int | None = None) -> list[ProceduralSkill]:
        with self._lock, self._get_connection() as conn:
            if limit is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM procedural_skills
                    WHERE profile_id = ?
                    ORDER BY success_count DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (profile_id.strip(), int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM procedural_skills
                    WHERE profile_id = ?
                    ORDER BY success_count DESC, updated_at DESC
                    """,
                    (profile_id.strip(),),
                ).fetchall()
            return [self._row_to_skill(r) for r in rows]

    def delete_skill(self, skill_id: str, profile_id: str | None = None) -> bool:
        with self._lock, self._get_connection() as conn:
            if profile_id:
                cur = conn.execute(
                    "DELETE FROM procedural_skills WHERE skill_id = ? AND profile_id = ?",
                    (skill_id.strip(), profile_id.strip()),
                )
            else:
                cur = conn.execute("DELETE FROM procedural_skills WHERE skill_id = ?", (skill_id.strip(),))
            conn.commit()
            return cur.rowcount > 0

    # ─────────────────────────────────────────
    # Episodic Memory CRUD
    # ─────────────────────────────────────────
    def record_episode(self, episode: EpisodicMemoryEntry) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO episodic_memory (
                    episode_id, summary, goal, outcome, tags, profile_id, created_at, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode.episode_id,
                    episode.summary,
                    episode.goal,
                    episode.outcome,
                    json.dumps(list(episode.tags)),
                    episode.profile_id,
                    episode.created_at.isoformat(),
                    json.dumps(episode.details),
                ),
            )
            conn.commit()

    def query_episodes(self, query: str = "", profile_id: str = "default", limit: int = 10) -> list[EpisodicMemoryEntry]:
        with self._lock, self._get_connection() as conn:
            q = f"%{query.strip().lower()}%"
            rows = conn.execute(
                """
                SELECT * FROM episodic_memory
                WHERE profile_id = ?
                  AND (LOWER(summary) LIKE ? OR LOWER(goal) LIKE ? OR LOWER(outcome) LIKE ? OR LOWER(tags) LIKE ?)
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (profile_id.strip(), q, q, q, q, limit),
            ).fetchall()
            return [self._row_to_episode(r) for r in rows]

    def optimize(self) -> None:
        """Run SQLite vacuum and pragma optimize."""
        with self._lock, self._get_connection() as conn:
            conn.execute("PRAGMA optimize;")

    @staticmethod
    def _row_to_skill(row: sqlite3.Row) -> ProceduralSkill:
        triggers = tuple(json.loads(row["trigger_phrases"] or "[]"))
        steps_raw = json.loads(row["steps_json"] or "[]")
        steps = tuple(
            SkillStep(
                step_number=int(s.get("step_number", idx + 1)),
                tool_or_command=str(s.get("tool_or_command", "")),
                arguments=dict(s.get("arguments", {})),
                description=str(s.get("description", "")),
            )
            for idx, s in enumerate(steps_raw)
        )
        created = datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(UTC)
        updated = datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else datetime.now(UTC)
        return ProceduralSkill(
            skill_id=str(row["skill_id"]),
            name=str(row["name"]),
            trigger_phrases=triggers,
            steps=steps,
            description=str(row["description"] or ""),
            profile_id=str(row["profile_id"]),
            success_count=int(row["success_count"] or 0),
            created_at=created,
            updated_at=updated,
        )

    @staticmethod
    def _row_to_episode(row: sqlite3.Row) -> EpisodicMemoryEntry:
        tags = tuple(json.loads(row["tags"] or "[]"))
        details = json.loads(row["details_json"] or "{}")
        created = datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(UTC)
        return EpisodicMemoryEntry(
            episode_id=str(row["episode_id"]),
            summary=str(row["summary"]),
            goal=str(row["goal"] or ""),
            outcome=str(row["outcome"] or ""),
            tags=tags,
            profile_id=str(row["profile_id"]),
            created_at=created,
            details=details,
        )
