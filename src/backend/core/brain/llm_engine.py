import threading
import os
from core import core_tools
from core.brain import brain_state, tool_manager
from core.runtime_logger import log_warning
from core.jarvis_config import (
    MINIMAX_MODEL, MINIMAX_API_KEY, MINIMAX_VISION_MODEL, MINIMAX_BASE_URL,
    GROQ_API_KEY, GROQ_MODEL
)
from core.service_container import services


def _load_chat_openai():
    if (os.getenv("JARVIS_TEST_MODE") or "").strip().lower() in {"1", "true", "yes"}:
        class _TestModeChatOpenAI:
            def __new__(cls, *args, **kwargs):
                return None

        return _TestModeChatOpenAI

    try:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI
    except Exception as e:
        log_warning("langchain_openai_import_failed", error=str(e))

        class _UnavailableChatOpenAI:
            def __new__(cls, *args, **kwargs):
                return None

        return _UnavailableChatOpenAI


def init_brain(app_ref):
    """Initializes LLM, plugins, and tool maps."""
    brain_state._app_ref = app_ref
    from utils.jarvis_i18n import get_bt
    bt = get_bt()
    print(bt["log_brain_init"].format(model=MINIMAX_MODEL))

    # Dependency injection in core_tools (shim)
    core_tools.inject_dependencies({
        "_invocar_tool": tool_manager._invocar_tool_entry,
        "_recargar_plugins_runtime": tool_manager._recargar_plugins_runtime,
        "noticias_cache": services.noticias_cache,
        "weather_cache": services.weather_cache,
    })

    # Load base tools
    base_tools = core_tools.get_base_tools()

    # Clean key configuration (avoids issues with literal quotes in .env)
    m_key = (MINIMAX_API_KEY or "").strip().replace('"', "").replace("'", "")
    g_key = (GROQ_API_KEY or "").strip().replace('"', "").replace("'", "")


    # Configure Primary LLM (MiniMax)
    ChatOpenAI = _load_chat_openai()
    # Use m_key to ensure no quotes are passed to the API
    brain_state.llm = ChatOpenAI(
        model=MINIMAX_MODEL,
        temperature=0,
        api_key=m_key,
        base_url=MINIMAX_BASE_URL,
    )

    brain_state.llm_vision = ChatOpenAI(
        model=MINIMAX_VISION_MODEL,
        temperature=0,
        api_key=m_key,
        base_url=MINIMAX_BASE_URL,
    )
    if MINIMAX_VISION_MODEL == "MiniMax-Text-01":
        print("[WARN] JARVIS_MINIMAX_VISION_MODEL = MiniMax-Text-01 (text, not vision). Screen analysis will not work. Configure a VL model in .env.")

    # Configure Fallback LLM (Groq)
    if g_key and brain_state.llm is not None:
        brain_state.llm_fallback = ChatOpenAI(
            model=GROQ_MODEL,
            temperature=0,
            api_key=g_key,
            base_url="https://api.groq.com/openai/v1",
        )

    services.llm = brain_state.llm
    services.llm_vision = brain_state.llm_vision
    services.llm_fallback = brain_state.llm_fallback

    # Load plugins
    plugin_tools = tool_manager._cargar_plugins_dinamicos(app_ref, base_tools)

    # Final tooling construction
    _rebuild_tooling(base_tools, plugin_tools)

    from utils.jarvis_i18n import get_bt
    bt = get_bt()
    print(bt["log_tools_ready"].format(base=len(base_tools), plugins=len(plugin_tools), total=len(brain_state.tools_list)))

    # Launch news briefing after initialization
    if brain_state.llm is not None and hasattr(core_tools, "generar_resumen_noticias"):
        threading.Thread(target=core_tools.generar_resumen_noticias, daemon=True).start()

def _rebuild_tooling(base_tools: list, plugin_tools: list) -> None:
    brain_state._BASE_TOOLS = list(base_tools)
    brain_state.tools_list = list(base_tools) + list(plugin_tools)
    brain_state.tool_map = {t.name: t for t in brain_state.tools_list if getattr(t, "name", "")}
    if brain_state.llm is not None:
        brain_state.llm_with_tools = brain_state.llm.bind_tools(
        brain_state.tools_list,
        tool_choice="auto",
    )
    if hasattr(brain_state.llm, "model") and "MiniMax-M2" in str(brain_state.llm.model):
        try:
            brain_state.llm_with_tools = brain_state.llm.bind_tools(
                brain_state.tools_list,
                tool_choice="auto",
                extra_body={"reasoning_split": True},
            )
        except Exception as e:
            log_warning("llm_reasoning_split_not_supported", error=str(e))
