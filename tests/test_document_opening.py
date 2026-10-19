from pathlib import Path

import pytest
from core.brain import router
from core.command_pipeline.models import CommandRequest, ReceiptStatus


@pytest.mark.parametrize(("text", "name"), [
    ("Abre el archivo RA1 del escritorio", "RA1"),
    ("Abre la presentación que empieza por RA1", "RA1"),
    ("Open the document report.pdf", "report.pdf"),
    ("Abre informe.pptx", "informe.pptx"),
])
def test_explicit_document_request_has_a_separate_plan(text, name):
    request = CommandRequest.create(text=text, profile_id="admin", channel="chat", language="es")
    plan = router.plan_hybrid(request)
    assert plan is not None
    assert len(plan.steps) == 1
    assert plan.steps[0].tool_name == "abrir_documento"
    assert dict(plan.steps[0].arguments) == {"nombre": name}


def test_document_tool_is_catalogued_without_enabling_shell_tools(monkeypatch):
    from tools import get_base_tools
    from core.security.tool_policy import get_tool_policy
    from voice.live_gemini import LIVE_VOICE_TOOL_ALLOWLIST
    monkeypatch.setenv("JARVIS_CORE_MODE", "true")
    monkeypatch.setenv("JARVIS_SYSTEM_TOOLS_ENABLED", "false")
    names = {tool.name for tool in get_base_tools()}
    assert "abrir_documento" in names
    assert "ejecutar_comando_terminal" not in names
    assert get_tool_policy("abrir_documento").risk_level == "elevated"
    assert "abrir_documento" in LIVE_VOICE_TOOL_ALLOWLIST


@pytest.mark.parametrize(("mode", "expected"), [
    ("accepted", ReceiptStatus.ACCEPTED), ("ambiguous", ReceiptStatus.PARTIAL),
    ("missing", ReceiptStatus.FAILED), ("denied", ReceiptStatus.BLOCKED),
])
def test_document_tool_never_claims_the_application_opened(monkeypatch, mode, expected):
    from tools import documents
    from tools.desktop_document_resolver import DocumentResolutionError
    launches = []
    monkeypatch.setattr(documents, "verificar_autorizacion", lambda _: mode != "denied")
    def resolve(_):
        if mode == "ambiguous":
            raise DocumentResolutionError("document_ambiguous")
        return None if mode == "missing" else Path("C:/safe/RA1.pptx")
    monkeypatch.setattr(documents, "resolve_local_document", resolve)
    monkeypatch.setattr(documents, "_launch_document", lambda target: launches.append(target))
    result = documents.abrir_documento.invoke({"nombre": "RA1"})
    assert result.status is expected
    assert not result.verified
    assert len(launches) == int(mode == "accepted")


def test_document_opening_requires_explicit_current_intent():
    from core.brain import tool_manager
    for channel in ("gemini_live", "router", "planner", "stream"):
        assert not tool_manager._tool_permitida_por_contexto("abrir_documento", "gracias", channel)
        assert tool_manager._tool_permitida_por_contexto("abrir_documento", "Abre el archivo RA1", channel)
