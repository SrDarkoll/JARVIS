from __future__ import annotations

import asyncio

import pytest
from api.api_routes import api_bp, init_api_routes
from core.security.browser_profile import configure_profile_sessions
from modules.memory.models import CognitiveNode, NodeType, ProceduralSkill, SkillStep, SynapseEdge
from modules.memory.service import unified_memory
from quart import Quart


@pytest.fixture
def memory_test_app():
    app = Quart(__name__)
    configure_profile_sessions(app)
    init_api_routes(services=None, browser_mode="test", root_dir=".")
    app.register_blueprint(api_bp)
    return app


def test_memory_graph_endpoint_empty_and_populated(memory_test_app, monkeypatch):
    async def _run():
        client = memory_test_app.test_client()
        test_profile = "graph_test_profile"
        async with client.session_transaction() as browser_session:
            browser_session["profile_id"] = test_profile

        # Mock list_cognitive_nodes and list_skills
        node1 = CognitiveNode(
            node_id="node_123",
            title="Python",
            content="Lenguaje de programacion de JARVIS",
            node_type=NodeType.ENTITY,
            tags=("dev", "backend"),
            edges=(
                SynapseEdge(source_title="Python", relation="utiliza", target_title="Quart", weight=1.0),
            ),
            profile_id=test_profile,
        )

        skill1 = ProceduralSkill(
            skill_id="skill_music",
            name="Reproducir Musica",
            trigger_phrases=("pon musica", "play spotify"),
            steps=(SkillStep(step_number=1, tool_or_command="spotify_play", arguments={}),),
            description="Abre y reproduce Spotify",
            profile_id=test_profile,
        )

        monkeypatch.setattr(
            unified_memory.store,
            "list_cognitive_nodes",
            lambda profile_id, limit: [node1] if profile_id == test_profile else [],
        )
        monkeypatch.setattr(
            unified_memory.store,
            "list_skills",
            lambda profile_id, limit: [skill1] if profile_id == test_profile else [],
        )

        res = await client.get(f"/api/memory/graph?profile_id={test_profile}&limit=20")
        assert res.status_code == 200
        data = await res.get_json()

        assert data["ok"] is True
        assert data["profile_id"] == test_profile
        assert data["count"] == 2
        assert len(data["nodes"]) == 2
        assert len(data["links"]) == 1

        node_entry = next(n for n in data["nodes"] if n["id"] == "node_123")
        assert node_entry["title"] == "Python"
        assert node_entry["type"] == "entity"

        skill_entry = next(n for n in data["nodes"] if n["type"] == "skill")
        assert skill_entry["title"] == "Reproducir Musica"

        link_entry = data["links"][0]
        assert link_entry["source"] == "Python"
        assert link_entry["target"] == "Quart"
        assert link_entry["relation"] == "utiliza"

    asyncio.run(_run())


def test_memory_graph_cannot_read_a_different_profile(memory_test_app, monkeypatch):
    async def _run():
        client = memory_test_app.test_client()
        captured_profiles = []
        async with client.session_transaction() as browser_session:
            browser_session["profile_id"] = "active-profile"
        monkeypatch.setattr(
            unified_memory.store,
            "list_cognitive_nodes",
            lambda profile_id, limit: captured_profiles.append(profile_id) or [],
        )
        monkeypatch.setattr(
            unified_memory.store,
            "list_skills",
            lambda profile_id, limit: captured_profiles.append(profile_id) or [],
        )

        res = await client.get("/api/memory/graph?profile_id=other-profile")
        data = await res.get_json()

        assert res.status_code == 200
        assert data["profile_id"] == "active-profile"
        assert captured_profiles == ["active-profile", "active-profile"]

    asyncio.run(_run())


def test_memory_graph_limit_clamping_and_fallback(memory_test_app, monkeypatch):
    async def _run():
        client = memory_test_app.test_client()

        captured_limit = {}

        def mock_list_nodes(profile_id, limit):
            captured_limit["nodes"] = limit
            return []

        monkeypatch.setattr(unified_memory.store, "list_cognitive_nodes", mock_list_nodes)
        monkeypatch.setattr(unified_memory.store, "list_skills", lambda profile_id, limit: [])

        # Non-integer limit defaults safely to 50
        res = await client.get("/api/memory/graph?limit=invalid")
        assert res.status_code == 200
        assert captured_limit["nodes"] == 50

        # Excessive limit clamped to 100
        res = await client.get("/api/memory/graph?limit=999")
        assert res.status_code == 200
        assert captured_limit["nodes"] == 100

        # Zero or negative limit clamped to 1
        res = await client.get("/api/memory/graph?limit=-5")
        assert res.status_code == 200
        assert captured_limit["nodes"] == 1

    asyncio.run(_run())


def test_memory_graph_hides_storage_error_details(memory_test_app, monkeypatch):
    async def _run():
        client = memory_test_app.test_client()
        monkeypatch.setattr(
            unified_memory.store,
            "list_cognitive_nodes",
            lambda **_kwargs: (_ for _ in ()).throw(
                RuntimeError("database_password=super-secret")
            ),
        )

        res = await client.get("/api/memory/graph")
        data = await res.get_json()

        assert res.status_code == 500
        assert data == {"ok": False, "error": "memory_graph_unavailable"}
        assert "super-secret" not in (await res.get_data(as_text=True))

    asyncio.run(_run())
