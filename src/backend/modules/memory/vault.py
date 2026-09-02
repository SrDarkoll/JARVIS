"""Markdown / Obsidian Knowledge Vault Synchronizer and Link Extractor."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any

from core import jarvis_config, jarvis_state

from modules.memory.models import CognitiveNode, NodeType, SynapseEdge
from modules.memory.store import ProceduralMemoryStore

logger = logging.getLogger(__name__)


def _default_vault_path() -> str:
    return os.getenv("JARVIS_VAULT_DIR") or os.path.join(
        jarvis_config.RUNTIME_DIR,
        "vault",
    )


def _active_profile_id(profile_id: str | None = None) -> str:
    return jarvis_state.normalize_profile_id(
        profile_id,
        jarvis_state.get_active_profile_id(),
    )


class VaultSyncManager:
    """Manages reading, writing, and bi-directional wikilink indexing in Markdown notes."""

    def __init__(
        self,
        vault_dir: str | None = None,
        store: ProceduralMemoryStore | None = None,
    ) -> None:
        self.vault_dir = vault_dir or _default_vault_path()
        self.store = store or ProceduralMemoryStore()
        os.makedirs(self.vault_dir, exist_ok=True)

    def _profile_vault_dir(self, profile_id: str) -> str:
        if profile_id in {"default", jarvis_state.DEFAULT_PROFILE_ID}:
            return self.vault_dir
        readable = re.sub(r"[^a-z0-9._-]+", "_", profile_id).strip("._-") or "profile"
        digest = hashlib.sha256(profile_id.encode("utf-8")).hexdigest()[:12]
        return os.path.join(self.vault_dir, ".profiles", f"{readable[:32]}-{digest}")

    def scan_and_index_vault(self, profile_id: str | None = None) -> dict[str, int]:
        """Scan all markdown notes in vault, extract wikilinks [[...]], and save to cognitive graph."""
        profile_key = _active_profile_id(profile_id)
        profile_vault_dir = self._profile_vault_dir(profile_key)
        nodes_created = 0
        edges_created = 0

        if not os.path.exists(profile_vault_dir):
            return {"notes": 0, "edges": 0}

        for root, directories, files in os.walk(profile_vault_dir):
            if os.path.normcase(root) == os.path.normcase(self.vault_dir):
                directories[:] = [directory for directory in directories if directory != ".profiles"]
            for file in files:
                if not file.endswith(".md"):
                    continue

                path = os.path.join(root, file)
                try:
                    with open(path, encoding="utf-8") as f:
                        raw_text = f.read()

                    note_title = os.path.splitext(file)[0]
                    # Parse tags (YAML or #tag)
                    tags = re.findall(r"#([a-zA-Z0-9_\-]+)", raw_text)
                    if "tags:" in raw_text:
                        yaml_tags_match = re.search(r"tags:\s*\[(.*?)\]", raw_text)
                        if yaml_tags_match:
                            extracted = [t.strip() for t in yaml_tags_match.group(1).split(",") if t.strip()]
                            tags.extend(extracted)

                    # Extract wikilinks [[Target Concept]] or [[Target Concept|Alias]]
                    raw_links = re.findall(r"\[\[(.*?)\]\]", raw_text)
                    edges: list[SynapseEdge] = []
                    for link in raw_links:
                        target = link.split("|")[0].strip()
                        if target and target.lower() != note_title.lower():
                            edges.append(
                                SynapseEdge(
                                    source_title=note_title,
                                    relation="enlace_wiki",
                                    target_title=target,
                                )
                            )
                            edges_created += 1

                    # Clean content snippet
                    content_clean = re.sub(r"---.*?---", "", raw_text, flags=re.DOTALL).strip()
                    if not content_clean:
                        content_clean = f"Nota de bóveda: {note_title}"

                    node = CognitiveNode(
                        node_id=f"vault_{note_title.lower().replace(' ', '_')}",
                        title=note_title,
                        content=content_clean[:4000],
                        node_type=NodeType.ENTITY,
                        tags=tuple(set(tags)),
                        edges=tuple(edges),
                        metadata={"source_file": file, "path": path},
                        profile_id=profile_key,
                    )
                    self.store.save_cognitive_node(node)
                    nodes_created += 1
                except Exception as exc:
                    logger.warning("Error indexing vault note '%s': %s", file, exc)

        return {"notes": nodes_created, "edges": edges_created}

    def create_or_update_note(
        self,
        title: str,
        content: str,
        links: list[str] | None = None,
        tags: list[str] | None = None,
        profile_id: str | None = None,
    ) -> str:
        """Create or update a Markdown note with clean Obsidian-compatible wikilinks."""
        profile_key = _active_profile_id(profile_id)
        profile_vault_dir = self._profile_vault_dir(profile_key)
        os.makedirs(profile_vault_dir, exist_ok=True)
        clean_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(title or "").strip()).strip(". ")
        if not clean_title:
            raise ValueError("title_required")

        filename = f"{clean_title}.md"
        filepath = os.path.abspath(os.path.join(profile_vault_dir, filename))
        if os.path.commonpath((filepath, os.path.abspath(profile_vault_dir))) != os.path.abspath(
            profile_vault_dir
        ):
            raise ValueError("invalid_vault_path")

        body_lines = []
        # Header frontmatter
        body_lines.append("---")
        body_lines.append(f"title: \"{clean_title}\"")
        if tags:
            clean_tags = [str(t).strip().lstrip("#") for t in tags if str(t).strip()]
            body_lines.append(f"tags: [{', '.join(clean_tags)}]")
        body_lines.append("---\n")

        body_lines.append(f"# {clean_title}\n")
        body_lines.append(str(content or "").strip())

        # Wikilinks block
        if links:
            body_lines.append("\n\n### Enlaces Relacionados")
            for link in links:
                l_clean = str(link).strip().strip("[]")
                if l_clean:
                    body_lines.append(f"- [[{l_clean}]]")

        full_md = "\n".join(body_lines) + "\n"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(full_md)

        # Immediate indexing into graph
        self.scan_and_index_vault(profile_id=profile_key)
        return filepath

    def search_vault(self, query: str, profile_id: str | None = None) -> list[dict[str, Any]]:
        """Fast full-text scan in vault Markdown notes."""
        profile_key = _active_profile_id(profile_id)
        profile_vault_dir = self._profile_vault_dir(profile_key)
        results: list[dict[str, Any]] = []
        q = str(query or "").strip().lower()
        if not q or not os.path.exists(profile_vault_dir):
            return results

        for root, directories, files in os.walk(profile_vault_dir):
            if os.path.normcase(root) == os.path.normcase(self.vault_dir):
                directories[:] = [directory for directory in directories if directory != ".profiles"]
            for file in files:
                if not file.endswith(".md"):
                    continue

                path = os.path.join(root, file)
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()

                    if q in text.lower() or q in file.lower():
                        title = os.path.splitext(file)[0]
                        # Extract first preview paragraph
                        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("---") and not line.startswith("#")]
                        preview = lines[0] if lines else "Nota de bóveda."
                        results.append({
                            "title": title,
                            "filename": file,
                            "path": path,
                            "preview": preview[:160],
                        })
                except Exception:
                    pass

        return results
