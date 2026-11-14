from pathlib import Path

import pytest
from core.brain import llm_engine
from core.llm_session import install_session_id

SESSION = "s" * 32


def _setup(monkeypatch, *, vision=False, env=None):
    from core.brain import brain_state

    calls = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.model = kwargs["model"]
            self.kwargs = kwargs

        def bind_tools(self, tools, tool_choice="auto", **_kwargs):
            return self

    for name in (
        "DEEPSEEK_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "JARVIS_LLM_PROVIDER",
        "JARVIS_OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)

    monkeypatch.setattr(llm_engine, "_load_chat_openai", lambda: FakeChatOpenAI)
    monkeypatch.setattr(llm_engine, "install_session_id", lambda _home: SESSION)
    monkeypatch.setattr(llm_engine, "GOOGLE_API_KEY", env.get("GOOGLE_API_KEY", ""), raising=False)
    monkeypatch.setattr(llm_engine, "GROQ_API_KEY", env.get("GROQ_API_KEY", ""))
    monkeypatch.setattr(llm_engine, "GROQ_MODEL", "qwen/qwen3.6-27b")
    monkeypatch.setattr(llm_engine, "VISION_ENABLED", vision, raising=False)
    monkeypatch.setattr(llm_engine, "PLUGINS_ENABLED", False, raising=False)
    monkeypatch.setattr(llm_engine, "BRIEFING_ENABLED", False, raising=False)
    monkeypatch.setattr(llm_engine.core_tools, "inject_dependencies", lambda _deps: None)
    monkeypatch.setattr(llm_engine.core_tools, "get_base_tools", lambda: [])
    for slot, value in (
        ("llm", None),
        ("llm_vision", None),
        ("llm_fallback", None),
        ("llm_fallbacks", ()),
        ("llm_chain_providers", ()),
        ("llm_with_tools", None),
    ):
        monkeypatch.setattr(brain_state, slot, value)
    return calls, brain_state


_ALL_KEYS = {
    "DEEPSEEK_API_KEY": "d" * 24,
    "GOOGLE_API_KEY": "g" * 24,
    "GROQ_API_KEY": "q" * 24,
}


def test_init_brain_builds_deepseek_primary_with_chain(monkeypatch):
    calls, brain_state = _setup(monkeypatch, env=_ALL_KEYS)

    llm_engine.init_brain(app_ref=None)

    assert calls[0]["model"] == "deepseek-v4.1-flash"
    assert calls[0]["base_url"] == "https://opencode.ai/inference/go/openai/v1"
    assert calls[0]["timeout"] == 20.0
    assert calls[0]["default_headers"] == {
        "User-Agent": "JARVIS/0.1.0",
        "x-opencode-session": SESSION,
    }
    assert [m.model for m in brain_state.llm_fallbacks] == [
        "gemini-3.5-flash-lite",
        "qwen/qwen3.6-27b",
    ]
    assert brain_state.llm_fallback is brain_state.llm_fallbacks[0]
    assert brain_state.llm_chain_providers == ("deepseek", "gemini", "groq")


def test_gemini_client_has_no_custom_headers(monkeypatch):
    calls, _state = _setup(monkeypatch, env=_ALL_KEYS)

    llm_engine.init_brain(app_ref=None)

    gemini_calls = [c for c in calls if c["model"] == "gemini-3.5-flash-lite"]
    assert gemini_calls
    assert all("default_headers" not in c for c in gemini_calls)


def test_vision_uses_first_gemini_or_groq_slot(monkeypatch):
    from core.llm_providers import provider_base_url, resolve_gemini_vision_model

    calls, brain_state = _setup(
        monkeypatch,
        vision=True,
        env={"DEEPSEEK_API_KEY": "d" * 24, "GOOGLE_API_KEY": "g" * 24},
    )

    llm_engine.init_brain(app_ref=None)

    assert brain_state.llm_vision is not None
    assert brain_state.llm_vision.model == resolve_gemini_vision_model({})
    assert brain_state.llm_vision.kwargs["base_url"] == provider_base_url("gemini")
    assert "default_headers" not in brain_state.llm_vision.kwargs


def test_vision_is_none_when_chain_has_only_deepseek(monkeypatch):
    _calls, brain_state = _setup(monkeypatch, vision=True, env={"DEEPSEEK_API_KEY": "d" * 24})

    llm_engine.init_brain(app_ref=None)

    assert brain_state.llm is not None
    assert brain_state.llm_vision is None


def test_fallback_models_bridges_legacy_slot(monkeypatch):
    from core.brain import brain_state

    marker = object()
    monkeypatch.setattr(brain_state, "llm_fallbacks", ())
    monkeypatch.setattr(brain_state, "llm_fallback", marker)
    assert brain_state.fallback_models() == (marker,)

    monkeypatch.setattr(brain_state, "llm_fallback", None)
    assert brain_state.fallback_models() == ()

    other = object()
    monkeypatch.setattr(brain_state, "llm_fallbacks", (other,))
    assert brain_state.fallback_models() == (other,)


def test_install_session_id_persists_and_regenerates_invalid(tmp_path, monkeypatch):
    first = install_session_id(tmp_path)
    assert len(first) == 32
    assert install_session_id(tmp_path) == first

    (tmp_path / "llm_session_id").write_text("bad", encoding="utf-8")
    regenerated = install_session_id(tmp_path)
    assert len(regenerated) == 32
    int(regenerated, 16)

    def boom(self, *_args, **_kwargs):
        raise OSError("read-only")

    (tmp_path / "llm_session_id").unlink()
    monkeypatch.setattr(Path, "write_text", boom)
    fallback = install_session_id(tmp_path)
    assert len(fallback) == 32
    int(fallback, 16)


def test_activate_rollback_restores_chain_slots(monkeypatch):
    from core.brain import brain_state
    from core.service_container import services

    previous_fallbacks = (object(),)
    monkeypatch.setattr(brain_state, "llm", object())
    monkeypatch.setattr(brain_state, "llm_fallbacks", previous_fallbacks)
    monkeypatch.setattr(brain_state, "llm_chain_providers", ("groq",))
    monkeypatch.setattr(services, "llm_fallbacks", previous_fallbacks)
    monkeypatch.setattr(
        llm_engine,
        "_rebind_current_tools",
        lambda: (_ for _ in ()).throw(RuntimeError("bind failed")),
    )
    candidate = llm_engine.LLMRuntimeCandidate(
        primary=object(),
        fallback=object(),
        fallbacks=(object(), object()),
        primary_provider="deepseek",
        providers=("deepseek", "gemini", "groq"),
    )

    with pytest.raises(llm_engine.LLMReconfigurationError):
        llm_engine.activate_llm_runtime(candidate)

    assert brain_state.llm_fallbacks is previous_fallbacks
    assert brain_state.llm_chain_providers == ("groq",)
    assert services.llm_fallbacks is previous_fallbacks
