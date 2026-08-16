import json
import os
import sys
import threading
from types import SimpleNamespace

from core import core_tools
from core.brain import brain_state, tool_manager
from core.jarvis_config import (
    BRIEFING_ENABLED,
    GOOGLE_API_KEY,
    GROQ_API_KEY,
    GROQ_MODEL,
    GROQ_VISION_MODEL,
    PLUGINS_ENABLED,
    VISION_ENABLED,
)
from core.llm_providers import provider_base_url, resolve_llm_provider_config
from core.runtime_logger import log_warning
from core.service_container import services
from langchain_core.messages import AIMessage
from utils.jarvis_i18n import get_bt


def _message_role(message) -> str:
    message_type = str(getattr(message, "type", "") or "").lower()
    if message_type == "system":
        return "system"
    if message_type == "human":
        return "user"
    if message_type == "tool":
        return "tool"
    return "assistant"


def _serialize_tool_calls_for_openai(tool_calls) -> list[dict]:
    serialized = []
    for call in tool_calls or []:
        if isinstance(call, dict):
            call_id = call.get("id")
            name = call.get("name")
            args = call.get("args")
        else:
            call_id = getattr(call, "id", None)
            name = getattr(call, "name", None)
            args = getattr(call, "args", None)
        if not name:
            continue
        arguments = args if isinstance(args, str) else json.dumps(args or {})
        serialized.append(
            {
                "id": call_id or f"call_{len(serialized) + 1}",
                "type": "function",
                "function": {
                    "name": str(name),
                    "arguments": arguments,
                },
            }
        )
    return serialized


def _message_to_openai(message) -> dict:
    if isinstance(message, str):
        return {"role": "user", "content": message}

    if isinstance(message, dict):
        return dict(message)

    payload = {
        "role": _message_role(message),
        "content": str(getattr(message, "content", "") or ""),
    }
    tool_call_id = getattr(message, "tool_call_id", None)
    if tool_call_id:
        payload["tool_call_id"] = tool_call_id
    tool_calls = _serialize_tool_calls_for_openai(getattr(message, "tool_calls", None))
    if tool_calls:
        payload["tool_calls"] = tool_calls
    return payload


def _tool_schema_from_langchain_tool(tool) -> dict:
    if isinstance(tool, dict):
        return tool

    name = str(getattr(tool, "name", "") or "").strip()
    description = str(getattr(tool, "description", "") or "").strip()
    args_schema = getattr(tool, "args_schema", None)
    parameters = None

    if args_schema is not None:
        if hasattr(args_schema, "model_json_schema"):
            parameters = args_schema.model_json_schema()
        elif hasattr(args_schema, "schema"):
            parameters = args_schema.schema()

    if not parameters:
        properties = {}
        required = []
        for arg_name, arg_meta in (getattr(tool, "args", None) or {}).items():
            if isinstance(arg_meta, dict):
                prop = {
                    key: value for key, value in arg_meta.items() if key in {"type", "description", "enum", "items"}
                }
                prop.setdefault("type", "string")
            else:
                prop = {"type": "string"}
            properties[str(arg_name)] = prop
            if isinstance(arg_meta, dict) and arg_meta.get("required"):
                required.append(str(arg_name))
        parameters = {"type": "object", "properties": properties}
        if required:
            parameters["required"] = required

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def _decode_tool_arguments(raw_args) -> dict:
    if isinstance(raw_args, dict):
        return raw_args
    if not raw_args:
        return {}
    try:
        parsed = json.loads(raw_args)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:
        return {"raw": str(raw_args)}


def _extract_tool_calls(raw_tool_calls) -> list[dict]:
    calls = []
    for raw_call in raw_tool_calls or []:
        raw_function = getattr(raw_call, "function", None)
        if isinstance(raw_call, dict):
            raw_function = raw_call.get("function")
            call_id = raw_call.get("id")
        else:
            call_id = getattr(raw_call, "id", None)

        if isinstance(raw_function, dict):
            name = raw_function.get("name")
            arguments = raw_function.get("arguments")
        else:
            name = getattr(raw_function, "name", None)
            arguments = getattr(raw_function, "arguments", None)

        if not name:
            continue
        calls.append(
            {
                "id": call_id or f"call_{len(calls) + 1}",
                "name": str(name),
                "args": _decode_tool_arguments(arguments),
            }
        )
    return calls


def _get_mapping_value(value, key, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _extract_chat_response_content(response) -> tuple[str, list[dict]]:
    if isinstance(response, str):
        return response, []

    choices = _get_mapping_value(response, "choices", None) or []
    if not choices:
        content = _get_mapping_value(response, "content", "")
        if content:
            return str(content), []
        return str(response or ""), []

    first_choice = choices[0]
    message = _get_mapping_value(first_choice, "message", None)
    if message is None:
        delta = _get_mapping_value(first_choice, "delta", None)
        content = _get_mapping_value(delta, "content", "") if delta is not None else ""
        return str(content or ""), []

    content = _get_mapping_value(message, "content", "") or ""
    tool_calls = _extract_tool_calls(_get_mapping_value(message, "tool_calls", None))
    return str(content), tool_calls


class _OpenAICompatibleChatOpenAI:
    """Small fallback for OpenAI-compatible providers when langchain_openai cannot import."""

    def __init__(
        self,
        *,
        model: str,
        temperature: float = 0,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        _client=None,
        _tools: list | None = None,
        _tool_choice: str | None = None,
        _extra_body: dict | None = None,
        **_kwargs,
    ):
        self.model = model
        self.temperature = temperature
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self._tools = list(_tools or [])
        self._tool_choice = _tool_choice
        self._extra_body = dict(_extra_body or {})

        if _client is not None:
            self._client = _client
            return

        from openai import OpenAI  # noqa: PLC0415

        client_kwargs = {"api_key": api_key or "missing"}
        if base_url:
            client_kwargs["base_url"] = base_url
        if timeout:
            client_kwargs["timeout"] = timeout
        self._client = OpenAI(**client_kwargs)

    def bind_tools(self, tools, tool_choice: str = "auto", **kwargs):
        extra_body = dict(self._extra_body)
        if kwargs.get("extra_body"):
            extra_body.update(kwargs["extra_body"])
        return self.__class__(
            model=self.model,
            temperature=self.temperature,
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            _client=self._client,
            _tools=list(tools or []),
            _tool_choice=tool_choice,
            _extra_body=extra_body,
        )

    def _completion_kwargs(self, messages, *, stream: bool = False) -> dict:
        if isinstance(messages, str):
            normalized_messages = [{"role": "user", "content": messages}]
        else:
            normalized_messages = [_message_to_openai(message) for message in messages]

        kwargs = {
            "model": self.model,
            "messages": normalized_messages,
            "temperature": self.temperature,
        }
        if stream:
            kwargs["stream"] = True
        if self._tools:
            kwargs["tools"] = [_tool_schema_from_langchain_tool(tool) for tool in self._tools]
            if self._tool_choice:
                kwargs["tool_choice"] = self._tool_choice
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body
        return kwargs

    def invoke(self, messages):
        response = self._client.chat.completions.create(**self._completion_kwargs(messages))
        content, tool_calls = _extract_chat_response_content(response)
        return AIMessage(content=content, tool_calls=tool_calls)

    def stream(self, messages):
        response = self._client.chat.completions.create(**self._completion_kwargs(messages, stream=True))
        for chunk in response:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", "") if delta is not None else ""
            if content:
                yield SimpleNamespace(content=content)


def _load_chat_openai():
    if (os.getenv("JARVIS_TEST_MODE") or "").strip().lower() in {"1", "true", "yes"}:

        class _TestModeChatOpenAI:
            def __new__(cls, *args, **kwargs):
                return None

        return _TestModeChatOpenAI

    try:
        from langchain_openai import ChatOpenAI  # noqa: PLC0415

        return ChatOpenAI
    except Exception as exc:
        log_warning("langchain_openai_import_failed", error=type(exc).__name__)
        return _OpenAICompatibleChatOpenAI


def init_brain(app_ref):
    """Initializes LLM, plugins, and tool maps."""
    brain_state._app_ref = app_ref

    # Dependency injection in core_tools (shim)
    core_tools.inject_dependencies(
        {
            "_invocar_tool": tool_manager._invocar_tool_entry,
            "_recargar_plugins_runtime": tool_manager._recargar_plugins_runtime,
            "noticias_cache": services.noticias_cache,
            "weather_cache": services.weather_cache,
        }
    )

    # Load base tools
    base_tools = core_tools.get_base_tools()

    # Clean key configuration (avoids issues with literal quotes in .env)
    g_key = (
        (getattr(sys.modules[__name__], "GROQ_API_KEY", None) or GROQ_API_KEY or "")
        .strip()
        .replace('"', "")
        .replace("'", "")
    )
    google_key = (
        (getattr(sys.modules[__name__], "GOOGLE_API_KEY", None) or GOOGLE_API_KEY or "")
        .strip()
        .replace('"', "")
        .replace("'", "")
    )

    provider_env = dict(os.environ)
    provider_env["GROQ_API_KEY"] = g_key
    provider_env["GOOGLE_API_KEY"] = google_key
    provider_env["JARVIS_GROQ_MODEL"] = GROQ_MODEL
    provider_config = resolve_llm_provider_config(provider_env)
    bt = get_bt()
    print(bt["log_brain_init"].format(model=provider_config.primary_model or "unconfigured"))

    ChatOpenAI = _load_chat_openai()
    brain_state.llm = None
    brain_state.llm_vision = None
    brain_state.llm_fallback = None
    brain_state.llm_primary_provider = ""
    brain_state.llm_fallback_provider = ""

    if provider_config.configured:
        brain_state.llm = ChatOpenAI(
            model=provider_config.primary_model,
            temperature=0,
            api_key=provider_config.primary_api_key,
            base_url=provider_base_url(provider_config.primary_provider),
        )
        brain_state.llm_primary_provider = provider_config.primary_provider
        if VISION_ENABLED:
            vision_model = (
                os.getenv("JARVIS_GEMINI_VISION_MODEL", "gemini-2.5-flash")
                if provider_config.primary_provider == "gemini"
                else GROQ_VISION_MODEL
            )
            brain_state.llm_vision = ChatOpenAI(
                model=vision_model,
                temperature=0,
                api_key=provider_config.primary_api_key,
                base_url=provider_base_url(provider_config.primary_provider),
            )
        if provider_config.fallback_provider:
            brain_state.llm_fallback = ChatOpenAI(
                model=provider_config.fallback_model,
                temperature=0,
                api_key=provider_config.fallback_api_key,
                base_url=provider_base_url(provider_config.fallback_provider),
            )
            brain_state.llm_fallback_provider = provider_config.fallback_provider
    else:
        log_warning(
            "llm_api_key_missing",
            error="API keys missing for both Google Gemini and Groq",
        )

    services.llm = brain_state.llm
    services.llm_vision = brain_state.llm_vision
    services.llm_fallback = brain_state.llm_fallback

    # Load plugins
    plugin_tools = tool_manager._cargar_plugins_dinamicos(app_ref, base_tools) if PLUGINS_ENABLED else []

    # Final tooling construction
    _rebuild_tooling(base_tools, plugin_tools)

    bt = get_bt()
    print(
        bt["log_tools_ready"].format(
            base=len(base_tools),
            plugins=len(plugin_tools),
            total=len(brain_state.tools_list),
        )
    )

    # Launch news briefing after initialization
    if BRIEFING_ENABLED and brain_state.llm is not None and hasattr(core_tools, "generar_resumen_noticias"):
        threading.Thread(target=core_tools.generar_resumen_noticias, daemon=True).start()


def _rebuild_tooling(base_tools: list, plugin_tools: list) -> None:
    new_base_tools = list(base_tools)
    new_tools = new_base_tools + list(plugin_tools)

    with brain_state.PLUGIN_LOCK:
        new_llm_with_tools = (
            brain_state.llm.bind_tools(
                new_tools,
                tool_choice="auto",
            )
            if brain_state.llm is not None
            else None
        )
        registry = brain_state.tool_registry.replace(new_tools)
        brain_state._BASE_TOOLS = new_base_tools
        brain_state.tools_list = list(registry.tools)
        brain_state.tool_map = dict(registry.by_name)
        brain_state.llm_with_tools = new_llm_with_tools
