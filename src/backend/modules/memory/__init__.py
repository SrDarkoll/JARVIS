"""Unified Interconnected Cognitive Memory module."""

from modules.memory.consolidator import SleepConsolidationEngine
from modules.memory.embeddings import LocalSemanticVectorizer, local_vectorizer
from modules.memory.hybrid_search import HybridMemorySearchEngine
from modules.memory.jit_cache import JITVoiceMemoryCache, jit_voice_cache
from modules.memory.models import (
    CognitiveNode,
    EpisodicMemoryEntry,
    NodeType,
    ProceduralSkill,
    SkillStep,
    SynapseEdge,
)
from modules.memory.service import (
    ProceduralMemoryService,
    UnifiedCognitiveMemoryService,
    procedural_memory,
    unified_memory,
)
from modules.memory.store import ProceduralMemoryStore
from modules.memory.tools import (
    aprender_procedimiento,
    buscar_en_boveda,
    busqueda_semantica_hibrida,
    consultar_memoria_jit,
    consultar_memoria_unificada,
    consultar_procedimientos,
    crear_nota_boveda,
    ejecutar_consolidacion_memoria,
    ejecutar_procedimiento,
    enlazar_conceptos,
    guardar_memoria_entrelazada,
    recordar_episodio,
    sincronizar_boveda_conocimiento,
)
from modules.memory.vault import VaultSyncManager

__all__ = [
    "CognitiveNode",
    "EpisodicMemoryEntry",
    "HybridMemorySearchEngine",
    "JITVoiceMemoryCache",
    "LocalSemanticVectorizer",
    "NodeType",
    "ProceduralMemoryService",
    "ProceduralMemoryStore",
    "ProceduralSkill",
    "SkillStep",
    "SleepConsolidationEngine",
    "SynapseEdge",
    "UnifiedCognitiveMemoryService",
    "VaultSyncManager",
    "aprender_procedimiento",
    "buscar_en_boveda",
    "busqueda_semantica_hibrida",
    "consultar_memoria_jit",
    "consultar_memoria_unificada",
    "consultar_procedimientos",
    "crear_nota_boveda",
    "ejecutar_consolidacion_memoria",
    "ejecutar_procedimiento",
    "enlazar_conceptos",
    "guardar_memoria_entrelazada",
    "jit_voice_cache",
    "local_vectorizer",
    "procedural_memory",
    "recordar_episodio",
    "sincronizar_boveda_conocimiento",
    "unified_memory",
]
