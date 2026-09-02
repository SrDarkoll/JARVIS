"""LangChain tool adapters for Unified Interconnected Cognitive Memory."""

from __future__ import annotations

import json

from core import jarvis_state
from langchain_core.tools import tool

from modules.memory.service import unified_memory


def _active_profile_id() -> str:
    return jarvis_state.get_active_profile_id()


@tool
def guardar_memoria_entrelazada(
    concepto: str,
    contenido: str,
    tipo: str = "fact",
    relaciones: str = "",
    etiquetas: str = "",
) -> str:
    """Guarda un nuevo nodo de conocimiento en la memoria cognitiva unificada y lo entrelaza con conceptos existentes o relaciones especificadas."""
    try:
        tags = [t.strip() for t in etiquetas.split(",") if t.strip()] if etiquetas else None
        rels = []
        if relaciones:
            for r in relaciones.split(";"):
                parts = r.split(":")
                if len(parts) == 2:
                    rels.append({"rel": parts[0].strip(), "target": parts[1].strip()})
                elif len(parts) == 1 and parts[0].strip():
                    rels.append({"rel": "relacionado_con", "target": parts[0].strip()})

        node = unified_memory.save_cognitive_memory(
            title=concepto,
            content=contenido,
            node_type=tipo,
            tags=tags,
            relations=rels,
            profile_id=_active_profile_id(),
        )
        edge_count = len(node.edges)
        return f"Memoria guardada: [{node.node_type.value.upper()}] '{node.title}' entrelazada con {edge_count} conexion(es) asociativa(s)."
    except Exception as exc:
        return f"Error guardando memoria entrelazada: {exc}"


@tool
def consultar_memoria_unificada(consulta: str = "") -> str:
    """Realiza una busqueda asociativa profunda en toda la red cognitiva entrelazada (hechos, habilidades, proyectos, preferencias y episodios pasados con sus conexiones)."""
    try:
        return unified_memory.recall_unified_context(
            consulta,
            profile_id=_active_profile_id(),
        )
    except Exception as exc:
        return f"Error consultando memoria unificada: {exc}"


@tool
def enlazar_conceptos(origen: str, relacion: str, destino: str) -> str:
    """Crea una conexion semantica sinaptica directa entre dos conceptos de la memoria."""
    try:
        ok = unified_memory.link_concepts(
            origen,
            relacion,
            destino,
            profile_id=_active_profile_id(),
        )
        if ok:
            return f"Enlace creado: '{origen}' --({relacion})--> '{destino}'."
        return "No se pudo crear el enlace."
    except Exception as exc:
        return f"Error enlazando conceptos: {exc}"


@tool
def aprender_procedimiento(
    nombre: str,
    frases_clave: str,
    pasos: str,
    descripcion: str = "",
) -> str:
    """Aprende y guarda un nuevo procedimiento o rutina de varios pasos y lo entrelaza en la memoria."""
    try:
        raw_pasos = pasos.strip()
        if raw_pasos.startswith("[") and raw_pasos.endswith("]"):
            try:
                parsed_steps = json.loads(raw_pasos)
            except Exception:
                parsed_steps = [s.strip() for s in raw_pasos.strip("[]").split(",") if s.strip()]
        else:
            parsed_steps = [s.strip() for s in raw_pasos.split("\n") if s.strip()]

        skill = unified_memory.learn_procedure(
            name=nombre,
            trigger_phrases=frases_clave,
            steps=parsed_steps,
            description=descripcion,
            profile_id=_active_profile_id(),
        )
        return f"Procedimiento '{skill.name}' aprendido exitosamente con {len(skill.steps)} pasos. Activadores: {', '.join(skill.trigger_phrases)}."
    except Exception as exc:
        return f"Error aprendiendo procedimiento: {exc}"


@tool
def consultar_procedimientos(consulta: str = "") -> str:
    """Lista todos los procedimientos y recetas aprendidos por JARVIS, o busca uno especifico."""
    try:
        skills = unified_memory.store.list_skills(profile_id=_active_profile_id())
        if not skills:
            return "Aun no tengo ningun procedimiento personalizado aprendido en mi memoria."

        q = consulta.strip().lower()
        if q:
            matched = [s for s in skills if q in s.name.lower() or any(q in t.lower() for t in s.trigger_phrases)]
            if not matched:
                return f"No encontre procedimientos aprendidos que coincidan con '{consulta}'."
            skills = matched

        lines = [f"Procedimientos aprendidos ({len(skills)}):"]
        for s in skills:
            triggers = ", ".join(s.trigger_phrases)
            lines.append(f"- **{s.name}** ({len(s.steps)} pasos): {s.description} [Activadores: {triggers}] (Uso: {s.success_count})")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error consultando procedimientos: {exc}"


@tool
def ejecutar_procedimiento(nombre: str) -> str:
    """Ejecuta un procedimiento aprendido previamente por su nombre o frase activadora."""
    try:
        return unified_memory.execute_procedure(
            nombre,
            profile_id=_active_profile_id(),
        )
    except Exception as exc:
        return f"Error ejecutando procedimiento '{nombre}': {exc}"


@tool
def recordar_episodio(consulta: str) -> str:
    """Busca en la memoria episodica de largo plazo hitos pasados, soluciones tecnicas previas o acuerdos anteriores."""
    try:
        episodes = unified_memory.recall_episodes(
            consulta,
            profile_id=_active_profile_id(),
            limit=5,
        )
        if not episodes:
            return f"No encontre recuerdos episodicos coincidentes con '{consulta}'."

        lines = [f"Recuerdos episodicos encontrados ({len(episodes)}):"]
        for ep in episodes:
            lines.append(f"- [{ep['ts']}] **{ep['summary']}** | Meta: {ep.get('goal', '--')} | Resultado: {ep.get('outcome', '--')}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error recordando episodio: {exc}"


@tool
def sincronizar_boveda_conocimiento() -> str:
    """Escanea la bóveda de notas en Markdown (tipo Obsidian), extrae wikilinks [[...]] y etiquetas, e indexa todo en la red cognitiva entrelazada."""
    try:
        res = unified_memory.vault.scan_and_index_vault(
            profile_id=_active_profile_id(),
        )
        return f"Bóveda sincronizada exitosamente: {res.get('notes', 0)} notas indexadas, {res.get('edges', 0)} conexiones [[wikilink]] entrelazadas."
    except Exception as exc:
        return f"Error sincronizando bóveda: {exc}"


@tool
def ejecutar_consolidacion_memoria() -> str:
    """Ejecuta el ciclo de consolidación profunda de memoria (sleep cycle): sincroniza la bóveda, consolida hechos/conversaciones del día, refuerza sinapsis y optimiza la base de datos para respuestas sub-milisegundo."""
    try:
        stats = unified_memory.consolidator.run_consolidation_cycle(
            profile_id=_active_profile_id(),
        )
        return (
            f"Ciclo de consolidación completado en {stats.get('duration_ms', 0)}ms: "
            f"{stats.get('vault_notes', 0)} notas de bóveda, {stats.get('vault_edges', 0)} enlaces, "
            f"{stats.get('facts_consolidated', 0)} hechos consolidados, {stats.get('nodes_optimized', 0)} nodos optimizados en SQLite."
        )
    except Exception as exc:
        return f"Error ejecutando consolidación de memoria: {exc}"


@tool
def crear_nota_boveda(
    titulo: str,
    contenido: str,
    enlaces: str = "",
    etiquetas: str = "",
) -> str:
    """Crea o actualiza una nota estructurada en Markdown en la bóveda de conocimiento con enlaces bidireccionales [[Concepto]] y etiquetas."""
    try:
        links_list = [link_item.strip() for link_item in enlaces.split(",") if link_item.strip()] if enlaces else None
        tags_list = [t.strip() for t in etiquetas.split(",") if t.strip()] if etiquetas else None
        path = unified_memory.vault.create_or_update_note(
            title=titulo,
            content=contenido,
            links=links_list,
            tags=tags_list,
            profile_id=_active_profile_id(),
        )
        return f"Nota '{titulo}' guardada exitosamente en la bóveda: {path} y entrelazada en el grafo cognitivo."
    except Exception as exc:
        return f"Error creando nota en la bóveda: {exc}"


@tool
def buscar_en_boveda(consulta: str) -> str:
    """Busca directamente en el texto de los archivos Markdown de la bóveda de conocimiento."""
    try:
        results = unified_memory.vault.search_vault(
            consulta,
            profile_id=_active_profile_id(),
        )
        if not results:
            return f"No se encontraron notas en la bóveda que coincidan con '{consulta}'."

        lines = [f"Notas encontradas en la bóveda ({len(results)}):"]
        for r in results:
            lines.append(f"- **{r['title']}** (`{r['filename']}`): {r['preview']}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error buscando en la bóveda: {exc}"


@tool
def busqueda_semantica_hibrida(consulta: str, limite: int = 5) -> str:
    """Nombre compatible para búsqueda híbrida experimental: hashing léxico determinista, coincidencia textual y expansión del grafo."""
    try:
        return unified_memory.recall_unified_context(
            consulta,
            profile_id=_active_profile_id(),
            top_k=limite,
        )
    except Exception as exc:
        return f"Error en búsqueda semántica híbrida: {exc}"


@tool
def consultar_memoria_jit(consulta: str, limite: int = 5) -> str:
    """Consulta una partición de memoria RAM pre-calentada; exacta por hash o aproximada mediante ranking léxico acotado."""
    try:
        return unified_memory.query_jit_memory(
            consulta,
            profile_id=_active_profile_id(),
            limit=limite,
        )
    except Exception as exc:
        return f"Error en consulta JIT de memoria: {exc}"
