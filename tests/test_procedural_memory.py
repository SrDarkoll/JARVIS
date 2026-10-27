"""Tests for Procedural and Episodic Memory module."""

from __future__ import annotations

import inspect

import pytest
from modules.memory.models import (
    EpisodicMemoryEntry,
    ProceduralSkill,
    SkillStep,
)
from modules.memory.service import ProceduralMemoryService
from modules.memory.store import ProceduralMemoryStore
from modules.memory.tools import (
    aprender_procedimiento,
    consultar_procedimientos,
    recordar_episodio,
)


@pytest.fixture
def temp_memory_store(tmp_path):
    yield ProceduralMemoryStore(db_path=str(tmp_path / "procedural-memory.db"))


def test_procedural_models():
    step = SkillStep(step_number=1, tool_or_command="abrir_aplicacion", arguments={"app": "spotify"})
    assert step.step_number == 1
    assert step.tool_or_command == "abrir_aplicacion"

    skill = ProceduralSkill(
        skill_id="s1",
        name="modo musica",
        trigger_phrases=("pon musica", "modo musica"),
        steps=(step,),
        description="Abre spotify",
    )
    data = skill.to_dict()
    assert data["name"] == "modo musica"
    assert len(data["steps"]) == 1
    assert data["steps"][0]["action"] == "abrir_aplicacion"

    episode = EpisodicMemoryEntry(
        episode_id="ep1",
        summary="Configuracion de proyecto",
        goal="Iniciar backend",
        outcome="Completado exitosamente",
        tags=("dev", "jarvis"),
    )
    ep_data = episode.to_dict()
    assert ep_data["summary"] == "Configuracion de proyecto"
    assert "dev" in ep_data["tags"]


def test_procedural_store_crud(temp_memory_store):
    store = temp_memory_store
    step = SkillStep(step_number=1, tool_or_command="ajustar_volumen", arguments={"nivel": 50})
    skill = ProceduralSkill(
        skill_id="skill_test_1",
        name="volumen medio",
        trigger_phrases=("volumen medio", "pon volumen a la mitad"),
        steps=(step,),
        description="Ajusta el volumen al 50%",
    )
    store.save_skill(skill)

    found = store.get_skill_by_name("volumen medio")
    assert found is not None
    assert found.name == "volumen medio"
    assert len(found.steps) == 1
    assert found.steps[0].arguments == {"nivel": 50}

    all_skills = store.list_skills()
    assert len(all_skills) >= 1

    # Episode storage and querying
    ep = EpisodicMemoryEntry(
        episode_id="ep_test_1",
        summary="Optimizacion de latencia",
        goal="Bajar latencia TTFA",
        outcome="Reducida a 62ms",
        tags=("latencia", "audio"),
    )
    store.record_episode(ep)

    episodes = store.query_episodes("latencia")
    assert len(episodes) == 1
    assert "Optimizacion de latencia" in episodes[0].summary


def test_procedural_and_episode_ids_are_scoped_by_profile(temp_memory_store):
    store = temp_memory_store
    step = SkillStep(step_number=1, tool_or_command="ajustar_volumen", arguments={"nivel": 25})
    store.save_skill(
        ProceduralSkill(
            skill_id="shared-skill",
            name="Profile A routine",
            trigger_phrases=("routine a",),
            steps=(step,),
            profile_id="profile_a",
        )
    )
    store.save_skill(
        ProceduralSkill(
            skill_id="shared-skill",
            name="Profile B routine",
            trigger_phrases=("routine b",),
            steps=(step,),
            profile_id="profile_b",
        )
    )
    store.record_episode(
        EpisodicMemoryEntry(
            episode_id="shared-episode",
            summary="Profile A episode",
            profile_id="profile_a",
        )
    )
    store.record_episode(
        EpisodicMemoryEntry(
            episode_id="shared-episode",
            summary="Profile B episode",
            profile_id="profile_b",
        )
    )

    assert store.get_skill_by_name("Profile A routine", profile_id="profile_a") is not None
    assert store.get_skill_by_name("Profile B routine", profile_id="profile_b") is not None
    assert [episode.summary for episode in store.query_episodes("", profile_id="profile_a")] == [
        "Profile A episode"
    ]
    assert [episode.summary for episode in store.query_episodes("", profile_id="profile_b")] == [
        "Profile B episode"
    ]

    assert store.delete_skill("shared-skill", profile_id="profile_b") is True
    assert store.get_skill_by_name("Profile A routine", profile_id="profile_a") is not None
    assert store.get_skill_by_name("Profile B routine", profile_id="profile_b") is None


def test_procedural_service_learn_and_execute(temp_memory_store):
    invocations = []

    def invoke_action(tool_name, args, user_input, source, profile_id):
        invocations.append((tool_name, args, user_input, source, profile_id))
        return {
            "abrir_aplicacion": "VS Code abierto",
            "consultar_estado_proactivo": "Estado normal",
        }[tool_name]

    service = ProceduralMemoryService(
        store=temp_memory_store,
        action_invoker=invoke_action,
    )

    skill = service.learn_procedure(
        name="rutina inicio",
        trigger_phrases="iniciar dia, comenzar trabajo",
        steps=[
            {"action": "abrir_aplicacion", "arguments": {"app": "vscode"}},
            {"action": "consultar_estado_proactivo", "arguments": {}},
        ],
        description="Abre VS Code y revisa el estado",
        profile_id="profile_a",
    )
    assert skill.name == "rutina inicio"
    assert len(skill.steps) == 2

    # Match by trigger
    matched = service.find_matching_skill("por favor iniciar dia", profile_id="profile_a")
    assert matched is not None
    assert matched.name == "rutina inicio"

    result = service.execute_procedure("rutina inicio", profile_id="profile_a")
    assert "ejecutado (2 pasos)" in result
    assert "VS Code abierto" in result
    assert [call[0] for call in invocations] == [
        "abrir_aplicacion",
        "consultar_estado_proactivo",
    ]
    assert all(call[3] == "routine" for call in invocations)
    assert all(call[4] == "profile_a" for call in invocations)


def test_procedural_service_stops_after_nested_action_is_blocked(temp_memory_store):
    invocations = []

    def invoke_action(tool_name, args, user_input, source, profile_id):
        invocations.append((tool_name, args, user_input, source, profile_id))
        return "ACCESS_DENIED: Necesito confirmacion explicita."

    service = ProceduralMemoryService(
        store=temp_memory_store,
        action_invoker=invoke_action,
    )
    service.learn_procedure(
        name="rutina protegida",
        trigger_phrases="rutina protegida",
        steps=[
            {"action": "controlar_pc", "arguments": {"accion": "apagar"}},
            {"action": "abrir_aplicacion", "arguments": {"app": "notepad"}},
        ],
        profile_id="profile_a",
    )

    result = service.execute_procedure("rutina protegida", profile_id="profile_a")

    assert "ACCESS_DENIED" in result
    assert [call[0] for call in invocations] == ["controlar_pc"]


def test_procedural_service_has_no_direct_tool_invocation():
    source = inspect.getsource(ProceduralMemoryService.execute_procedure)
    assert ".invoke(" not in source


def test_langchain_memory_tools():
    out_learn = aprender_procedimiento.invoke({
        "nombre": "modo trabajo",
        "frases_clave": "vamos a trabajar, modo trabajo",
        "pasos": "abrir_aplicacion\najustar_volumen",
        "descripcion": "Prepara el espacio de trabajo",
    })
    assert "aprendido exitosamente" in out_learn

    out_consult = consultar_procedimientos.invoke({"consulta": "modo trabajo"})
    assert "modo trabajo" in out_consult

    out_episode = recordar_episodio.invoke({"consulta": "inexistente_xyz"})
    assert "No encontre recuerdos" in out_episode


def test_procedural_tools_in_base_catalog(monkeypatch):
    from core import core_tools

    monkeypatch.setenv("JARVIS_COGNITIVE_MEMORY_ENABLED", "true")
    tools = core_tools.get_base_tools()
    tool_names = {t.name for t in tools}

    assert "aprender_procedimiento" in tool_names
    assert "consultar_procedimientos" in tool_names
    assert "ejecutar_procedimiento" in tool_names
    assert "recordar_episodio" in tool_names
