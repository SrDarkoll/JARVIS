from __future__ import annotations

from api import status_routes
from core.brain import brain_state


def _capabilities(monkeypatch, env: dict[str, str], *, llm=None):
    for name in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "JARVIS_LLM_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(brain_state, "llm", llm)
    return status_routes._runtime_capabilities(
        monitoring={
            "configured": False,
            "available": False,
            "running": False,
        },
        speech_to_text={
            "provider": "auto",
            "groq_configured": False,
            "local_enabled": True,
            "local_state": "not_loaded",
        },
        admin_voice_profiles=0,
    )


def test_status_reports_active_gemini_provider(monkeypatch):
    capabilities = _capabilities(
        monkeypatch,
        {
            "GEMINI_API_KEY": "configured",
            "GROQ_API_KEY": "fallback",
        },
        llm=object(),
    )

    assert capabilities["llm"]["state"] == "available"
    assert capabilities["llm"]["code"] == "gemini_ready"
    assert capabilities["llm"]["detail"] == "Fallback provider: groq"


def test_status_accepts_groq_as_the_only_provider(monkeypatch):
    capabilities = _capabilities(
        monkeypatch,
        {"GROQ_API_KEY": "configured"},
        llm=object(),
    )

    assert capabilities["llm"]["state"] == "available"
    assert capabilities["llm"]["code"] == "groq_ready"


def test_status_uses_provider_neutral_missing_key_code(monkeypatch):
    capabilities = _capabilities(monkeypatch, {}, llm=None)

    assert capabilities["llm"]["state"] == "unconfigured"
    assert capabilities["llm"]["code"] == "llm_key_missing"
