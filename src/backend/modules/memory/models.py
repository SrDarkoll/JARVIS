"""Data models and contracts for Unified Interconnected Cognitive Memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class NodeType(StrEnum):
    FACT = "fact"
    SKILL = "skill"
    EPISODE = "episode"
    PREFERENCE = "preference"
    PROJECT = "project"
    ENTITY = "entity"


@dataclass(frozen=True, slots=True)
class SynapseEdge:
    """A semantic relationship connecting two cognitive nodes."""

    source_title: str
    relation: str
    target_title: str
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source_title,
            "rel": self.relation,
            "target": self.target_title,
            "weight": self.weight,
        }


@dataclass(frozen=True, slots=True)
class CognitiveNode:
    """An intertwined cognitive memory node."""

    node_id: str
    title: str
    content: str
    node_type: NodeType = NodeType.FACT
    tags: tuple[str, ...] = ()
    edges: tuple[SynapseEdge, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    profile_id: str = "default"
    strength: float = 1.0
    access_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "title": self.title,
            "content": self.content,
            "type": self.node_type.value,
            "tags": list(self.tags),
            "edges": [e.to_dict() for e in self.edges],
            "profile_id": self.profile_id,
            "access_count": self.access_count,
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M"),
        }


@dataclass(frozen=True, slots=True)
class SkillStep:
    """Single step in a procedural workflow."""

    step_number: int
    tool_or_command: str
    arguments: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass(frozen=True, slots=True)
class ProceduralSkill:
    """A learned procedural workflow with trigger phrases and sequential actions."""

    skill_id: str
    name: str
    trigger_phrases: tuple[str, ...]
    steps: tuple[SkillStep, ...]
    description: str = ""
    profile_id: str = "default"
    success_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "trigger_phrases": list(self.trigger_phrases),
            "description": self.description,
            "steps": [
                {
                    "step": s.step_number,
                    "action": s.tool_or_command,
                    "arguments": s.arguments,
                    "description": s.description,
                }
                for s in self.steps
            ],
            "profile_id": self.profile_id,
            "success_count": self.success_count,
        }


@dataclass(frozen=True, slots=True)
class EpisodicMemoryEntry:
    """An episodic record of past accomplishments, decisions, or milestones."""

    episode_id: str
    summary: str
    goal: str = ""
    outcome: str = ""
    tags: tuple[str, ...] = ()
    profile_id: str = "default"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "summary": self.summary,
            "goal": self.goal,
            "outcome": self.outcome,
            "tags": list(self.tags),
            "profile_id": self.profile_id,
            "ts": self.created_at.strftime("%Y-%m-%d %H:%M"),
            "details": self.details,
        }
