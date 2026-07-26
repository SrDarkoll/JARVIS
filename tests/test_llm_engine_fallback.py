from __future__ import annotations

import builtins
import sys
from types import SimpleNamespace

from core.brain import llm_engine
from core.command_pipeline.tool_registry import ToolRegistryService
from langchain_core.messages import AIMessage, HumanMessage


def test_load_chat_openai_uses_openai_compatible_fallback_on_import_runtime_error(monkeypatch):
    original_import = builtins.__import__
    warnings = []
    secret_error = r"provider failed through C:\Users\ramir\private\proxy-token"

    def guarded_import(name, *args, **kwargs):
        if name.startswith("langchain_openai"):
            raise RuntimeError(secret_error)
        return original_import(name, *args, **kwargs)

    monkeypatch.delenv("JARVIS_TEST_MODE", raising=False)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(
        llm_engine,
        "log_warning",
        lambda event, **fields: warnings.append((event, fields)),
    )

    ChatOpenAI = llm_engine._load_chat_openai()
    model = ChatOpenAI(model="qwen/qwen3.6-27b", temperature=0, api_key="test-key", base_url="https://example.invalid")

    assert model is not None
    assert model.model == "qwen/qwen3.6-27b"
    assert hasattr(model, "invoke")
    assert model.bind_tools([], tool_choice="auto") is not None
    assert warnings == [
        ("langchain_openai_import_failed", {"error": "RuntimeError"})
    ]
    assert secret_error not in repr(warnings)


def test_openai_compatible_fallback_invokes_chat_completion(monkeypatch):
    calls = []

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            message = SimpleNamespace(content="fallback ok", tool_calls=None)
            choice = SimpleNamespace(message=message)
            return SimpleNamespace(choices=[choice])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))

    model = llm_engine._OpenAICompatibleChatOpenAI(
        model="qwen/qwen3.6-27b",
        temperature=0,
        api_key="test-key",
        base_url="https://example.invalid",
    )
    response = model.invoke([HumanMessage(content="hello")])

    assert response.content == "fallback ok"
    assert calls[0]["model"] == "qwen/qwen3.6-27b"
    assert calls[0]["messages"] == [{"role": "user", "content": "hello"}]


def test_openai_compatible_fallback_accepts_string_prompt(monkeypatch):
    calls = []

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return "briefing ok"

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))

    model = llm_engine._OpenAICompatibleChatOpenAI(
        model="qwen/qwen3.6-27b",
        temperature=0,
        api_key="test-key",
        base_url="https://example.invalid",
    )

    response = model.invoke("summarize the news")

    assert response.content == "briefing ok"
    assert calls[0]["messages"] == [{"role": "user", "content": "summarize the news"}]


def test_openai_compatible_fallback_accepts_dict_response(monkeypatch):
    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return {"choices": [{"message": {"content": "dict ok"}}]}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))

    model = llm_engine._OpenAICompatibleChatOpenAI(
        model="qwen/qwen3.6-27b",
        temperature=0,
        api_key="test-key",
        base_url="https://example.invalid",
    )

    assert model.invoke("hello").content == "dict ok"


def test_openai_compatible_fallback_serializes_ai_tool_calls():
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call_1",
                "name": "buscar_en_internet",
                "args": {"query": "latest news"},
            }
        ],
    )

    assert llm_engine._message_to_openai(message) == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "buscar_en_internet",
                    "arguments": '{"query": "latest news"}',
                },
            }
        ],
    }


def test_init_brain_configures_groq_as_primary_provider(monkeypatch):
    from core.brain import brain_state  # pyright: ignore[reportMissingImports]

    calls = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.model = kwargs["model"]

        def bind_tools(self, tools, tool_choice="auto", **_kwargs):
            self.bound_tools = list(tools)
            self.tool_choice = tool_choice
            return self

    class FakeThread:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def start(self):
            return None

    monkeypatch.setattr(llm_engine, "_load_chat_openai", lambda: FakeChatOpenAI)
    monkeypatch.setattr(llm_engine, "GOOGLE_API_KEY", "", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    monkeypatch.setattr(llm_engine, "GROQ_API_KEY", "groq-key")
    monkeypatch.setattr(llm_engine, "GROQ_MODEL", "qwen/qwen3.6-27b")
    monkeypatch.setattr(llm_engine, "GROQ_VISION_MODEL", "qwen/qwen3.6-27b")
    monkeypatch.setattr(llm_engine, "VISION_ENABLED", True)
    monkeypatch.setattr(llm_engine, "PLUGINS_ENABLED", True)
    monkeypatch.setattr(llm_engine, "BRIEFING_ENABLED", True)
    monkeypatch.setattr(llm_engine.core_tools, "inject_dependencies", lambda _deps: None)
    monkeypatch.setattr(llm_engine.core_tools, "get_base_tools", lambda: [])
    monkeypatch.setattr(llm_engine.tool_manager, "_cargar_plugins_dinamicos", lambda _app, _base: [])
    monkeypatch.setattr(llm_engine.threading, "Thread", FakeThread)

    monkeypatch.setattr(brain_state, "llm", None)
    monkeypatch.setattr(brain_state, "llm_vision", None)
    monkeypatch.setattr(brain_state, "llm_fallback", None)
    monkeypatch.setattr(brain_state, "llm_with_tools", None)

    llm_engine.init_brain(app_ref=None)

    assert calls[0]["model"] == "qwen/qwen3.6-27b"
    assert calls[0]["api_key"] == "groq-key"
    assert calls[0]["base_url"] == "https://api.groq.com/openai/v1"
    assert calls[1]["model"] == "qwen/qwen3.6-27b"
    assert calls[1]["api_key"] == "groq-key"
    assert brain_state.llm_fallback is None


def test_init_brain_configures_gemini_as_primary_and_groq_as_fallback(monkeypatch):
    from core.brain import brain_state

    calls = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.model = kwargs["model"]

        def bind_tools(self, tools, tool_choice="auto", **_kwargs):
            return self

    monkeypatch.setattr(llm_engine, "_load_chat_openai", lambda: FakeChatOpenAI)
    monkeypatch.setattr(llm_engine, "GOOGLE_API_KEY", "google-key", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    monkeypatch.setattr(llm_engine, "GROQ_API_KEY", "groq-key")
    monkeypatch.setattr(llm_engine, "GROQ_MODEL", "qwen/qwen3.6-27b")
    monkeypatch.setattr(llm_engine, "VISION_ENABLED", False, raising=False)
    monkeypatch.setattr(llm_engine, "PLUGINS_ENABLED", False, raising=False)
    monkeypatch.setattr(llm_engine, "BRIEFING_ENABLED", False, raising=False)
    monkeypatch.setattr(llm_engine.core_tools, "inject_dependencies", lambda _deps: None)
    monkeypatch.setattr(llm_engine.core_tools, "get_base_tools", lambda: [])

    monkeypatch.setattr(brain_state, "llm", None)
    monkeypatch.setattr(brain_state, "llm_vision", None)
    monkeypatch.setattr(brain_state, "llm_fallback", None)
    monkeypatch.setattr(brain_state, "llm_with_tools", None)

    llm_engine.init_brain(app_ref=None)

    assert brain_state.llm is not None
    assert brain_state.llm.model == "gemini-3.5-flash"
    assert brain_state.llm_fallback is not None
    assert brain_state.llm_fallback.model == "qwen/qwen3.6-27b"


def test_init_brain_core_mode_skips_optional_initializers(monkeypatch):
    from core.brain import brain_state  # pyright: ignore[reportMissingImports]

    calls = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.model = kwargs["model"]

        def bind_tools(self, tools, tool_choice="auto", **_kwargs):
            return self

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("optional initializer should not run in core mode")

    monkeypatch.setattr(llm_engine, "_load_chat_openai", lambda: FakeChatOpenAI)
    monkeypatch.setattr(llm_engine, "GOOGLE_API_KEY", "", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    monkeypatch.setattr(llm_engine, "GROQ_API_KEY", "groq-key")
    monkeypatch.setattr(llm_engine, "GROQ_MODEL", "qwen/qwen3.6-27b")
    monkeypatch.setattr(llm_engine, "VISION_ENABLED", False, raising=False)
    monkeypatch.setattr(llm_engine, "PLUGINS_ENABLED", False, raising=False)
    monkeypatch.setattr(llm_engine, "BRIEFING_ENABLED", False, raising=False)
    monkeypatch.setattr(llm_engine.core_tools, "inject_dependencies", lambda _deps: None)
    monkeypatch.setattr(llm_engine.core_tools, "get_base_tools", lambda: [])
    monkeypatch.setattr(llm_engine.tool_manager, "_cargar_plugins_dinamicos", fail_if_called)
    monkeypatch.setattr(llm_engine.threading, "Thread", fail_if_called)
    monkeypatch.setattr(brain_state, "llm", None)
    monkeypatch.setattr(brain_state, "llm_vision", None)
    monkeypatch.setattr(brain_state, "llm_fallback", None)
    monkeypatch.setattr(brain_state, "llm_with_tools", None)

    llm_engine.init_brain(app_ref=None)

    assert [call["model"] for call in calls] == ["qwen/qwen3.6-27b"]
    assert brain_state.llm is not None
    assert brain_state.llm_vision is None
    assert brain_state.llm_fallback is None


def test_rebuild_tooling_replaces_registry_under_plugin_lock(monkeypatch):
    from core.brain import brain_state  # pyright: ignore[reportMissingImports]

    events = []

    class RecordingLock:
        active = False

        def __enter__(self):
            self.active = True
            events.append("lock_enter")
            return self

        def __exit__(self, *_args):
            events.append("lock_exit")
            self.active = False

    lock = RecordingLock()

    class Tool:
        def __init__(self, name):
            self.name = name

    class BoundModel:
        def bind_tools(self, tools, tool_choice="auto"):
            assert lock.active is True
            events.append(("bind", [tool.name for tool in tools], tool_choice))
            return ("bound", tuple(tool.name for tool in tools))

    monkeypatch.setattr(brain_state, "PLUGIN_LOCK", lock)
    monkeypatch.setattr(brain_state, "llm", BoundModel())
    monkeypatch.setattr(brain_state, "_BASE_TOOLS", ["stale"])
    monkeypatch.setattr(brain_state, "tools_list", ["stale"])
    monkeypatch.setattr(brain_state, "tool_map", {"stale": object()})
    monkeypatch.setattr(brain_state, "llm_with_tools", "stale")
    monkeypatch.setattr(
        brain_state,
        "tool_registry",
        ToolRegistryService(),
    )

    base = [Tool("base")]
    plugins = [Tool("plugin")]
    llm_engine._rebuild_tooling(base, plugins)

    assert events == [
        "lock_enter",
        ("bind", ["base", "plugin"], "auto"),
        "lock_exit",
    ]
    assert brain_state._BASE_TOOLS == base
    assert brain_state.tools_list == [*base, *plugins]
    assert brain_state.tool_map == {
        "base": base[0],
        "plugin": plugins[0],
    }
    assert brain_state.llm_with_tools == (
        "bound",
        ("base", "plugin"),
    )
    snapshot = brain_state.tool_registry.snapshot()
    assert snapshot.version == 1
    assert snapshot.tools == tuple([*base, *plugins])
    assert dict(snapshot.by_name) == brain_state.tool_map


def test_rebuild_tooling_clears_stale_bound_model_without_llm(monkeypatch):
    from core.brain import brain_state  # pyright: ignore[reportMissingImports]

    monkeypatch.setattr(brain_state, "llm", None)
    monkeypatch.setattr(brain_state, "llm_with_tools", "stale")

    llm_engine._rebuild_tooling([], [])

    assert brain_state.llm_with_tools is None
