from __future__ import annotations

from types import MappingProxyType, SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from core.brain import processor
from core.command_pipeline.models import (
    CommandOutcome,
    CommandRequest,
    CommandResponse,
    ReceiptStatus,
)
from core.command_pipeline.reasoning import ReasoningMode
from core.errors import LLMServiceError
from utils.jarvis_i18n import BACKEND_TRANSLATIONS
from voice import live_delegation


@pytest.fixture(autouse=True)
def _clear_cache():
    live_delegation._RECEIPT_CACHE.clear()
    yield
    live_delegation._RECEIPT_CACHE.clear()


def _fake_registry(monkeypatch, names):
    from core.brain import brain_state

    snapshot = SimpleNamespace(by_name=MappingProxyType({n: object() for n in names}))
    monkeypatch.setattr(brain_state.tool_registry, "snapshot", lambda: snapshot)


def _call(**overrides):
    kwargs = dict(
        user_text="escanea mi red",
        hint="scan",
        profile_id="default",
        language="es",
        session_id="s1",
        call_id="c1",
    )
    kwargs.update(overrides)
    return live_delegation.run_delegated_request(**kwargs)


def test_scope_excludes_critical_confirmable_and_unregistered() -> None:
    scope = live_delegation.delegated_tool_scope(
        {"obtener_clima", "controlar_pc", "delegar_razonamiento"},
        live_allowlist={"obtener_clima", "controlar_pc", "no_existe", "delegar_razonamiento"},
    )
    assert scope == frozenset({"obtener_clima"})


def test_delegated_request_uses_user_words_and_scope(monkeypatch) -> None:
    _fake_registry(monkeypatch, ["obtener_clima"])
    captured: list[CommandRequest] = []

    def fake_process(request):
        captured.append(request)
        return CommandResponse(
            request_id=request.request_id,
            text="listo",
            should_listen=False,
            outcome=CommandOutcome.RESPONDED,
        )

    monkeypatch.setattr(processor, "process_command", fake_process)
    receipt = _call()

    request = captured[0]
    assert request.text == "escanea mi red"
    assert request.channel == "live_delegation"
    assert request.reasoning_mode is ReasoningMode.ALWAYS
    assert request.metadata["live_hint"] == "scan"
    assert request.language == "es"
    assert request.request_id == "live-delegation-s1-c1"
    assert request.allowed_tools is not None
    assert "delegar_razonamiento" not in request.allowed_tools
    assert receipt.user_message == "listo"
    assert receipt.status is ReceiptStatus.SUCCEEDED
    assert receipt.tool_name == "delegar_razonamiento"
    assert receipt.step_id == "c1"
    assert receipt.verified is False


def test_duplicate_call_id_runs_once(monkeypatch) -> None:
    _fake_registry(monkeypatch, [])
    calls = []

    def fake_process(request):
        calls.append(request)
        return CommandResponse(
            request_id=request.request_id,
            text="ok",
            should_listen=False,
            outcome=CommandOutcome.RESPONDED,
        )

    monkeypatch.setattr(processor, "process_command", fake_process)
    first = _call()
    second = _call()
    assert len(calls) == 1
    assert first is second


def test_concurrent_same_call_id_runs_once(monkeypatch) -> None:
    import threading

    _fake_registry(monkeypatch, [])
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow_process(request):
        calls.append(request)
        started.set()
        assert release.wait(5)
        return CommandResponse(
            request_id=request.request_id,
            text="ok",
            should_listen=False,
            outcome=CommandOutcome.RESPONDED,
        )

    monkeypatch.setattr(processor, "process_command", slow_process)
    results = []
    first = threading.Thread(target=lambda: results.append(_call()))
    first.start()
    assert started.wait(5)
    second = threading.Thread(target=lambda: results.append(_call()))
    second.start()
    second.join(0.3)
    assert second.is_alive()  # waiting on the in-flight call
    release.set()
    first.join(5)
    second.join(5)
    assert len(calls) == 1
    assert len(results) == 2
    assert results[0] is results[1]


def test_llm_failure_returns_failed_receipt(monkeypatch) -> None:
    _fake_registry(monkeypatch, [])

    def boom(request):
        raise LLMServiceError

    monkeypatch.setattr(processor, "process_command", boom)
    receipt = _call()
    assert receipt.status is ReceiptStatus.FAILED
    assert receipt.diagnostic_code == "llm_unavailable"
    assert receipt.user_message == BACKEND_TRANSLATIONS["es"]["delegation_unavailable"]


def test_other_exception_returns_delegation_failed(monkeypatch) -> None:
    _fake_registry(monkeypatch, [])

    def boom(request):
        raise ValueError("secret detail")

    monkeypatch.setattr(processor, "process_command", boom)
    receipt = _call()
    assert receipt.diagnostic_code == "delegation_failed"
    assert "secret" not in receipt.user_message


def test_late_result_message_by_channel() -> None:
    assert "Telegram" in live_delegation.late_result_message("es", telegram_ready=True)
    assert "historial" in live_delegation.late_result_message("es", telegram_ready=False)


def test_deliver_late_result_sends_text_only(monkeypatch) -> None:
    sent = []

    class FakeManager:
        def send_message_sync(self, text, audio=True):
            sent.append((text, audio))
            return True

    import services.telegram_manager as tm

    monkeypatch.setattr(tm, "telegram_manager", FakeManager())
    monkeypatch.setattr(live_delegation, "telegram_ready", lambda: True)
    assert live_delegation.deliver_late_result("hola") is True
    assert sent == [("hola", False)]

    monkeypatch.setattr(live_delegation, "telegram_ready", lambda: False)
    sent.clear()
    assert live_delegation.deliver_late_result("hola") is False
    assert sent == []

    class Raising:
        def send_message_sync(self, text, audio=True):
            raise RuntimeError("x")

    monkeypatch.setattr(tm, "telegram_manager", Raising())
    monkeypatch.setattr(live_delegation, "telegram_ready", lambda: True)
    assert live_delegation.deliver_late_result("hola") is False


def test_telegram_ready_reads_config_at_call_time(monkeypatch) -> None:
    from core import jarvis_config

    monkeypatch.setattr(jarvis_config, "TELEGRAM_ENABLED", True)
    monkeypatch.setattr(jarvis_config, "TELEGRAM_TOKEN", "t")
    monkeypatch.setattr(jarvis_config, "TELEGRAM_CHAT_ID", 5)
    assert live_delegation.telegram_ready() is True
    monkeypatch.setattr(jarvis_config, "TELEGRAM_CHAT_ID", 0)
    assert live_delegation.telegram_ready() is False


def test_planner_messages_carry_hint_before_user_message(monkeypatch) -> None:
    monkeypatch.setattr(processor.memory_manager, "next_message_count", lambda _pid: 2)
    monkeypatch.setattr(processor.prompts, "get_system_msg", lambda *_a, **_k: SystemMessage(content="sys"))
    request = CommandRequest.create(
        text="escanea mi red",
        profile_id="default",
        channel="live_delegation",
        metadata={"live_hint": "scan"},
    )
    messages = processor._build_planner_messages(request, [])
    assert isinstance(messages[-2], SystemMessage)
    assert "untrusted" in messages[-2].content
    assert "scan" in messages[-2].content
    assert isinstance(messages[-1], HumanMessage)
    assert messages[-1].content == "escanea mi red"

    plain = CommandRequest.create(text="hola", profile_id="default", channel="chat")
    assert len(processor._build_planner_messages(plain, [])) == 2


def test_delegation_declaration_shape() -> None:
    decl = live_delegation.delegation_declaration("en")
    assert decl["name"] == "delegar_razonamiento"
    assert decl["parameters"]["required"] == ["peticion"]
    assert decl["description"] == BACKEND_TRANSLATIONS["en"]["delegation_tool_description"]


@pytest.mark.parametrize(("raw", "expected"), [("1", 3.0), ("99", 14.0), ("x", 12.0), ("8", 8.0)])
def test_delegation_timeout_bounds(monkeypatch, raw, expected) -> None:
    monkeypatch.setenv("JARVIS_LIVE_DELEGATION_TIMEOUT", raw)
    assert live_delegation.delegation_timeout_seconds() == expected
