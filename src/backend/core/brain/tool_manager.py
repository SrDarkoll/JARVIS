import importlib.util
import os
import time as _time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from uuid import uuid4

import requests as http_requests
from core import jarvis_state
from core.brain import brain_state, brain_utils
from core.command_pipeline.execution import (
    ControlledToolBlockedError,
    ToolAuthorizationRequiredError,
    ToolConfirmationRequiredError,
    ToolExecutionService,
    ToolPolicyBlockedError,
)
from core.command_pipeline.models import (
    ActionStep,
    CommandRequest,
    ReceiptStatus,
)
from core.jarvis_config import AUTOCURACION_ACTIVA, BASE_DIR, PLUGINS_DIR, ROOT_DIR, SRC_DIR
from core.jarvis_observability import obs_event, obs_inc, obs_tool
from core.service_container import services
from core.unified_log import write_log
from langchain_core.tools import tool
from services import security_manager
from tools._common import _open_url_or_app
from utils.jarvis_i18n import get_current_language

# Pool global para ejecución paralela de herramientas
tool_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="ToolOp")


def _resultado_parece_error(texto: str) -> bool:
    t = (texto or "").strip().lower()
    if not t:
        return False
    if "access_denied" in t:
        return False
    t = "".join(character for character in unicodedata.normalize("NFKD", t) if not unicodedata.combining(character))
    patrones = [
        "error",
        "could not",
        "could not do",
        "technical issue",
        "forbidden",
        "timeout",
        "traceback",
        "exception",
        "no encontre",
        "no pude",
        "no pudo",
        "no se pudo",
        "no esta disponible",
        "no es accesible",
        "perdio el foco",
    ]
    return any(p in t for p in patrones)


def _cargar_plugins_dinamicos(app_ref, base_tools: list) -> list:
    existentes = {getattr(t, "name", "") for t in base_tools}
    loaded_tools = []
    loaded = {}
    errors = {}

    if not os.path.isdir(PLUGINS_DIR):
        return []

    for fname in sorted(os.listdir(PLUGINS_DIR)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        plugin_name = os.path.splitext(fname)[0]
        plugin_path = os.path.join(PLUGINS_DIR, fname)
        try:
            spec = importlib.util.spec_from_file_location(f"jarvis_plugin_{plugin_name}", plugin_path)
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            provider = getattr(module, "get_tools", None) or getattr(module, "register", None)
            if not callable(provider):
                errors[plugin_name] = "Does not export get_tools or register."
                continue

            context = {
                "tool": tool,
                "app": app_ref,
                "BASE_DIR": BASE_DIR,
                "SRC_DIR": SRC_DIR,
                "ROOT_DIR": ROOT_DIR,
                "http_requests": http_requests,
            }

            try:
                declared = provider(context)
            except TypeError:
                declared = provider()

            if declared is None:
                declared = []
            if not isinstance(declared, list):
                declared = [declared]

            tool_names = []
            for _t in declared:
                tname = getattr(_t, "name", "")
                if not tname or not hasattr(_t, "invoke"):
                    continue
                if tname in existentes:
                    continue
                existentes.add(tname)
                loaded_tools.append(_t)
                tool_names.append(tname)

            if tool_names:
                loaded[plugin_name] = tool_names
                obs_event("plugin_loaded", plugin=plugin_name, tools=tool_names)
        except Exception as e:
            errors[plugin_name] = str(e)
            obs_event("plugin_error", plugin=plugin_name, error=str(e)[:200])

    with brain_state.PLUGIN_LOCK:
        brain_state.PLUGIN_STATE["last_reload"] = datetime.now().isoformat(timespec="seconds")
        brain_state.PLUGIN_STATE["loaded"] = loaded
        brain_state.PLUGIN_STATE["errors"] = errors
        brain_state.PLUGIN_STATE["tools"] = loaded_tools
    return loaded_tools


def _recargar_plugins_runtime() -> str:
    try:
        from core.brain.llm_engine import _rebuild_tooling

        if not brain_state._BASE_TOOLS or brain_state._app_ref is None:
            return "Plugin system not initialized."
        nuevos = _cargar_plugins_dinamicos(brain_state._app_ref, brain_state._BASE_TOOLS)
        _rebuild_tooling(brain_state._BASE_TOOLS, nuevos)
        return f"Plugins reloaded: {len(nuevos)} active tools."
    except Exception as e:
        obs_event("plugin_reload_error", error=str(e)[:200])
        return f"Error reloading plugins: {e}"


def _tool_permitida_por_contexto(tool_name: str, user_input: str, source: str = "router") -> bool:
    """Anti-hallucination guard: blocks critical tools if user context does not request them."""
    import re

    if source in {"auth_resume", "routine", "control_panel", "router", "router_dynamic", "router_directo", "gemini_live", "live_voice"}:
        return True
    t = (user_input or "").lower()
    t = re.sub(r"[^\wáéíóúñü\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return False
    if "routine" in t:
        return True
    if tool_name == "ajustar_volumen":
        return any(
            k in t
            for k in [
                "volume",
                "volumen",
                "up",
                "sube",
                "down",
                "baja",
                "mute",
                "silence",
                "silencio",
                "silencia",
                "do not disturb",
                "no molestar",
            ]
        )
    if tool_name == "borrar_memoria":
        return any(
            k in t
            for k in [
                "clear memory",
                "reset memory",
                "forget everything",
                "borra memoria",
                "borrar memoria",
                "olvida todo",
            ]
        )
    if tool_name == "matar_proceso":
        return any(
            k in t
            for k in [
                "kill process",
                "end process",
                "close process",
                "kill",
                "mata proceso",
                "termina proceso",
                "cierra proceso",
            ]
        )
    if tool_name == "controlar_pc":
        return any(
            k in t
            for k in [
                "turn off",
                "apaga",
                "apagar",
                "restart",
                "reinicia",
                "reiniciar",
                "hibernate",
                "hiberna",
                "hibernar",
                "lock",
                "bloquea",
                "bloquear",
                "cancela",
                "cancelar",
            ]
        )
    if tool_name == "abrir_aplicacion":
        return any(
            k in t
            for k in [
                "open",
                "start",
                "launch",
                "application",
                "app",
                "abre",
                "abrir",
                "inicia",
                "ejecuta",
                "lanza",
                "aplicacion",
                "programa",
            ]
        )

    if tool_name == "buscar_en_internet":
        from core.brain import social_engine

        if social_engine._debe_buscar_en_web(t):
            return True
        return any(
            k in t
            for k in [
                "search",
                "find",
                "investigate",
                "find out",
                "google",
                "consult",
                "on the internet",
                "on the web",
                "news",
                "price",
                "current",
                "today",
                "version",
                "api",
                "sdk",
                "framework",
                "function",
                "works",
                "work",
                "operation",
                "how it works",
                "what is",
                "who is",
                "which is",
                "tell me",
                "I need to know",
                "I need information",
                "I need info",
            ]
        )

    return True


class _ToolReportedFailure(RuntimeError):
    pass


def _blocked_error(reason: str) -> ControlledToolBlockedError:
    public_reason = str(reason or "").strip()
    normalized_reason = public_reason.lower()
    if "authoriz" in normalized_reason or "autoriz" in normalized_reason:
        return ToolAuthorizationRequiredError(public_reason)
    if "confirm" in normalized_reason:
        return ToolConfirmationRequiredError(public_reason)
    return ToolPolicyBlockedError(public_reason)


def _invoke_tool_once(request: CommandRequest, step: ActionStep) -> Any:
    tool_name = step.tool_name
    args = dict(step.arguments)
    user_input = request.text
    source = request.channel
    profile_id = request.profile_id
    configured_tool_map = request.metadata.get("_tool_map")
    if isinstance(configured_tool_map, dict):
        tool = configured_tool_map.get(tool_name)
    else:
        registry = brain_state.tool_registry.snapshot()
        tool = registry.by_name.get(tool_name)
        if tool is None:
            # Compatibility for tests and legacy plugins that still mutate
            # brain_state.tool_map directly.
            tool = brain_state.tool_map.get(tool_name)
        if tool is None:
            from core import core_tools
            tool = getattr(core_tools, tool_name, None)
            if tool is None:
                for base_t in core_tools.get_base_tools():
                    if getattr(base_t, "name", "") == tool_name:
                        tool = base_t
                        break

    started = _time.perf_counter()
    obs_inc("tool_calls_total", 1)
    outcome = "error"
    result_preview = ""

    def _finish(value: Any, status: str) -> Any:
        nonlocal outcome, result_preview
        outcome = status
        result_preview = str(value or "")[:2000]
        return value

    print(f"\n[TOOL] >> INICIO: {tool_name} | args={args} | canal={source}", flush=True)
    write_log(
        "TOOL",
        f"START {tool_name}",
        source=source,
        profile_id=profile_id,
        args=args,
    )

    try:
        # Anti-hallucination guard by context
        if not _tool_permitida_por_contexto(tool_name, user_input, source):
            outcome = "blocked"
            result_preview = "context_guard_blocked"
            raise ToolPolicyBlockedError()

        # Security guard: profile_id travels explicitly or via ContextVar.
        guard_fn = getattr(security_manager, "_security_guard", None)
        if callable(guard_fn):
            try:
                allowed, reason = guard_fn(
                    tool_name,
                    args,
                    user_input,
                    source,
                    profile_id=profile_id,
                )
            except Exception as exc:
                outcome = "blocked"
                result_preview = f"security_guard_failed:{type(exc).__name__}"
                elapsed = (_time.perf_counter() - started) * 1000.0
                obs_tool(
                    tool_name,
                    False,
                    elapsed,
                    source,
                    user_input=user_input,
                    error=result_preview,
                )
                raise ToolPolicyBlockedError() from exc
            if not allowed:
                outcome = "blocked"
                blocked_error = _blocked_error(reason)
                result_preview = blocked_error.diagnostic_code
                elapsed = (_time.perf_counter() - started) * 1000.0
                obs_tool(
                    tool_name,
                    False,
                    elapsed,
                    source,
                    user_input=user_input,
                    error=result_preview,
                )
                raise blocked_error

        from core.brain import security_engine

        if security_engine._tool_requiere_autorizacion(tool_name):
            from utils.jarvis_auth import verificar_autorizacion

            if not verificar_autorizacion(profile_id):
                from core.brain.history_manager import _registrar_accion_pendiente_auth

                _registrar_accion_pendiente_auth(profile_id, tool_name, args, user_input)
                outcome = "blocked"
                result_preview = "tool_authorization_required"
                raise ToolAuthorizationRequiredError()

        # Actual tool execution
        if tool is None:
            outcome = "unavailable"
            result_preview = "tool_unavailable"
            raise LookupError("tool_unavailable")
        with jarvis_state.active_profile(profile_id):
            result = tool.invoke(args)
        result_txt = str(result)

        ok = not _resultado_parece_error(result_txt)
        if not ok and AUTOCURACION_ACTIVA:
            healed = _intentar_autocuracion(tool_name, args, user_input, result_txt)
            if healed:
                obs_inc("autocure_success", 1)
                result = healed
                ok = True

        if not ok:
            security_manager._proactive_register_tool_error(
                tool_name,
                result_txt or "tool_reported_failure",
            )
            outcome = "error"
            result_preview = result_txt or "tool_reported_failure"
            raise _ToolReportedFailure(result_txt or "tool_reported_failure")

        elapsed = (_time.perf_counter() - started) * 1000.0
        obs_tool(tool_name, ok, elapsed, source, user_input=user_input, error="" if ok else result_txt)
        return _finish(result, "ok")
    except (ControlledToolBlockedError, LookupError, _ToolReportedFailure):
        raise
    except Exception as exc:
        error_type = type(exc).__name__
        if AUTOCURACION_ACTIVA:
            healed = _intentar_autocuracion(
                tool_name,
                args,
                user_input,
                error_type,
            )
            if healed:
                obs_inc("autocure_success", 1)
                return _finish(healed, "ok")
        diagnostic = f"tool_execution_failed:{error_type}"
        security_manager._proactive_register_tool_error(tool_name, diagnostic)
        elapsed = (_time.perf_counter() - started) * 1000.0
        obs_tool(
            tool_name,
            False,
            elapsed,
            source,
            user_input=user_input,
            error=diagnostic,
        )
        outcome = "error"
        result_preview = diagnostic
        raise RuntimeError("tool_execution_failed") from exc
    finally:
        elapsed = (_time.perf_counter() - started) * 1000.0
        print(f"[TOOL] << FIN: {tool_name} | estado={outcome} | tiempo={round(elapsed, 2)}ms | resultado={result_preview[:160]}\n", flush=True)
        write_log(
            "TOOL",
            f"END {tool_name}",
            status=outcome,
            source=source,
            profile_id=profile_id,
            elapsed_ms=round(elapsed, 2),
            result=result_preview,
        )


_tool_execution_service = ToolExecutionService(_invoke_tool_once)
services.tool_execution = _tool_execution_service


def _receipt_value(receipt) -> Any:
    if receipt.result is not None:
        return receipt.result
    if receipt.status is ReceiptStatus.BLOCKED:
        return f"ACCESS_DENIED: {receipt.user_message}"
    return receipt.user_message


def _invocar_tool_entry(
    tc_name: str,
    args: dict,
    user_input: str,
    source: str = "unknown",
    profile_id: str | None = None,
    request_id: str | None = None,
    step_id: str | None = None,
) -> Any:
    request = CommandRequest.create(
        text=user_input or tc_name,
        profile_id=profile_id or jarvis_state.get_active_profile_id(),
        channel=source,
        request_id=request_id,
        language=get_current_language(),
    )
    step = ActionStep(
        step_id=step_id or f"legacy-{uuid4()}",
        tool_name=tc_name,
        arguments=args,
    )
    return _receipt_value(_tool_execution_service.execute(request, step))


def _invocar_tool(tc: dict, tool_map: dict, context: dict) -> Any:
    tool_name = str(tc.get("name") or "").strip()
    user_input = str(context.get("user_input") or tool_name)
    request = CommandRequest.create(
        text=user_input,
        profile_id=(context.get("profile_id") or jarvis_state.get_active_profile_id()),
        channel=context.get("source", "unknown"),
        request_id=context.get("request_id"),
        language=get_current_language(),
        metadata={"_tool_map": tool_map},
    )
    step = ActionStep(
        step_id=str(context.get("step_id") or tc.get("id") or f"legacy-{uuid4()}"),
        tool_name=tool_name,
        arguments=tc.get("args") or {},
    )
    return _receipt_value(_tool_execution_service.execute(request, step))


def _intentar_autocuracion(tool_name: str, args: dict, user_input: str, motivo: str = "") -> str | None:
    obs_inc("autocure_attempts", 1)
    obs_event("autocure_attempt", tool=tool_name, args=args, motivo=(motivo or "")[:200])
    try:
        if tool_name == "abrir_navegador":
            destino = brain_utils._normalizar_destino_web((args or {}).get("destino", ""))
            if brain_utils._abrir_en_navegador_sistema(destino):
                return f"Browser opened by backup route: {destino}"

        if tool_name == "abrir_youtube":
            import webbrowser

            query = (args or {}).get("query", "")
            from urllib.parse import quote_plus

            url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
            webbrowser.open(url)
            return f"YouTube opened by autocuration: {query}"

        if tool_name == "reproducir_en_spotify":
            _open_url_or_app("spotify:")
            _time.sleep(0.8)
            res = brain_state.tool_map["reproducir_en_spotify"].invoke(args)
            if not _resultado_parece_error(str(res)):
                return str(res)

        if tool_name == "ajustar_volumen":
            from core import core_tools

            valor = (args or {}).get("valor", 50)
            try:
                core_tools._ajustar_volumen_absoluto(int(valor))
                return f"Volume adjusted to {valor}% by autocuration."
            except Exception:
                pass
    except Exception:
        pass
    return None
