from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from core import unified_log
from core.brain import processor, security_engine, tool_manager
from services.memory_manager import memory_manager


@pytest.fixture
def readable_log(tmp_path: Path):
    log_path = tmp_path / "log.txt"
    unified_log.configure_unified_log(
        log_path,
        enabled=True,
        max_bytes=1024 * 1024,
        backup_count=1,
    )
    yield log_path
    unified_log.reset_unified_log_for_tests()


def test_processor_records_user_and_jarvis_turns(
    readable_log: Path,
    monkeypatch,
):
    profile_id = "log_test"
    memory_manager.set_profile_history(profile_id, [])
    monkeypatch.setattr(processor.core_tools, "guardar_memoria_async", lambda *_args: None)
    monkeypatch.setattr(processor.rag_motor, "agregar_interaccion", lambda **_kwargs: None)
    monkeypatch.setattr(processor, "obs_event", lambda *_args, **_kwargs: None)

    reply, should_listen = processor.procesar_mensaje(
        "quien eres",
        profile_id=profile_id,
    )

    assert reply
    assert should_listen is False
    history = memory_manager.get_history(profile_id)
    assert isinstance(history[0], HumanMessage)
    assert isinstance(history[1], AIMessage)
    content = readable_log.read_text(encoding="utf-8")
    assert "[CONVERSATION] USUARIO(log_test): quien eres" in content
    assert f"[CONVERSATION] JARVIS(log_test): {reply}" in content


def test_tool_manager_records_start_and_successful_end(
    readable_log: Path,
    monkeypatch,
):
    class FakeTool:
        def invoke(self, args):
            return f"played:{args['song']}"

    monkeypatch.setattr(tool_manager, "_tool_permitida_por_contexto", lambda *_args: True)
    monkeypatch.setattr(
        tool_manager.security_manager,
        "_security_guard",
        lambda *_args, **_kwargs: (True, "ok"),
    )
    monkeypatch.setattr(
        security_engine,
        "_tool_requiere_autorizacion",
        lambda _name: False,
    )
    monkeypatch.setattr(tool_manager, "AUTOCURACION_ACTIVA", False)
    monkeypatch.setattr(tool_manager, "obs_inc", lambda *_args: None)
    monkeypatch.setattr(tool_manager, "obs_tool", lambda *_args, **_kwargs: None)

    result = tool_manager._invocar_tool(
        {"name": "demo_player", "args": {"song": "Monster"}},
        {"demo_player": FakeTool()},
        {
            "user_input": "pon Monster",
            "source": "test",
            "profile_id": "admin",
        },
    )

    assert result == "played:Monster"
    content = readable_log.read_text(encoding="utf-8")
    assert "[TOOL] START demo_player" in content
    assert 'args={"song": "Monster"}' in content
    assert "[TOOL] END demo_player" in content
    assert "status=ok" in content
    assert "elapsed_ms=" in content
    assert "result=played:Monster" in content


def test_tool_manager_records_context_block_as_an_end_state(
    readable_log: Path,
    monkeypatch,
):
    monkeypatch.setattr(tool_manager, "_tool_permitida_por_contexto", lambda *_args: False)
    monkeypatch.setattr(tool_manager, "obs_inc", lambda *_args: None)

    result = tool_manager._invocar_tool(
        {"name": "blocked_tool", "args": {}},
        {},
        {"user_input": "hello", "source": "test", "profile_id": "guest"},
    )

    assert result.startswith("ACCESS_DENIED")
    content = readable_log.read_text(encoding="utf-8")
    assert "[TOOL] START blocked_tool" in content
    assert "[TOOL] END blocked_tool" in content
    assert "status=blocked" in content


def test_observability_event_is_mirrored_as_readable_event(
    readable_log: Path,
):
    from core.jarvis_observability import obs_event

    obs_event("voice_transcribed", profile_id="admin", source="groq")

    content = readable_log.read_text(encoding="utf-8")
    assert "[EVENT] voice_transcribed" in content
    assert "profile_id=admin" in content
    assert "source=groq" in content
