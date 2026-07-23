import os
import re
import threading
import time as _time
from collections.abc import Iterator
from datetime import datetime, timedelta

from core import core_tools, jarvis_state
from core.brain import (
    brain_state,
    brain_utils,
    history_manager,
    keywords,
    music_engine,
    prompts,
    router,
    security_engine,
    social_engine,
    tool_manager,
)
from core.errors import (
    CHAT_UNAVAILABLE_MESSAGE,
    LLM_UNCONFIGURED_MESSAGE,
    LLMServiceError,
    LLMUnavailableError,
)
from core.jarvis_observability import obs_event, obs_inc
from core.jarvis_state import DEFAULT_PROFILE_ID
from core.service_container import services
from engines.memory_rag import rag_motor
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from modules.spotify.followup import (
    SpotifySelectionStatus,
    pending_spotify_selections,
)
from modules.spotify.state import get_last_requested_track
from utils.jarvis_i18n import get_current_language
from utils.jarvis_text import reparar_unicode


def _invocar_tool_wrapper(
    tool_name: str, args: dict, user_input: str, source: str = "wrapper"
) -> str:
    """Wrapper to invoke tools from the router."""
    return str(
        tool_manager._invocar_tool_entry(
            tool_name,
            args,
            user_input,
            source,
            jarvis_state.get_active_profile_id(),
        )
    )


def necesita_tools(text: str) -> bool:
    t = text.lower().strip().rstrip(".?!")
    if any(k in t for k in keywords.KEYWORDS_CON_TOOLS):
        return True
    if t in keywords.KEYWORDS_SIN_TOOLS:
        return False
    if len(t.split()) <= 4 and any(k in t for k in keywords.KEYWORDS_SIN_TOOLS):
        return False
    return True


def _llm_calls_disabled_for_tests() -> bool:
    return (os.getenv("JARVIS_TEST_MODE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _invoke_model(model, messages: list, *, event: str):
    try:
        return model.invoke(messages)
    except Exception as exc:
        obs_event(event, error=type(exc).__name__)
        raise LLMServiceError from None


def _finalize_reply(
    reply: str, messages: list, user_input: str, pid: str, path: str
) -> tuple[str, bool]:
    # Post-check: if STRICT_WEB_SEARCH and dynamic topic without tool → force retry
    from core.brain.brain_utils import _respuesta_necesita_web_forzarla
    if _respuesta_necesita_web_forzarla(user_input, reply, messages):
        obs_event("strict_web_forced_retry", user_input=user_input[:100], path=path)
        from core.brain import tool_manager
        web_result = tool_manager._invocar_tool_entry(
            "buscar_en_internet",
            {"query": user_input},
            user_input,
            "strict_web_retry",
            pid,
        )
        reply = brain_utils._compactar_resumen_busqueda(str(web_result))
        obs_event("strict_web_retry_ok", reply=reply[:120])

    should_listen = any(
        "ACCESO_DENEGADO" in str(m.content)
        for m in messages
        if isinstance(m, ToolMessage)
    )
    if security_engine._es_bloqueo_autorizacion(reply):
        should_listen = True
    if not should_listen:
        should_listen = reply.strip().endswith("?")

    reply = brain_utils._formatear_reply_por_perfil(reply, pid)
    reply = core_tools._limpiar_respuesta(reply)

    tool_results_summary = [
        str(m.content or "").strip()[:600]
        for m in messages
        if isinstance(m, ToolMessage)
    ]

    history_manager._append_to_profile_history(
        pid,
        HumanMessage(content=user_input),
        AIMessage(content=reply),
        tool_results=tool_results_summary if tool_results_summary else None,
    )

    from core.core_tools import guardar_memoria_async

    guardar_memoria_async(
        history_manager._get_history_for_profile(pid), jarvis_state.DATOS_CURIOSOS, pid
    )

    obs_event(
        "reply_sent", path=path, should_listen=bool(should_listen), reply=reply[:220]
    )
    print(f"[JARVIS] {reply}")

    rag_motor.agregar_interaccion(user_msg=user_input, ai_msg=reply, profile_id=pid)

    return reply, should_listen


def _cargar_contexto_perfil(profile_id: str) -> str:
    from core.core_tools import _obtener_contexto_perfil

    with jarvis_state.memoria_lock:
        pid, chat_history_ref, datos_ref = _obtener_contexto_perfil(profile_id)
        jarvis_state.chat_history[:] = chat_history_ref
        jarvis_state.DATOS_CURIOSOS = datos_ref
    return pid


def _preflight_compuesto(user_input: str, profile_id: str) -> tuple[str | None, bool]:
    segments = router._split_compound_intents(user_input)
    if len(segments) < 2:
        return None, False

    results: list[tuple[str, str]] = []
    should_listen = False
    for segment in segments[:5]:
        reply, sl = _preflight(segment, profile_id, allow_compound=False)
        if reply is None:
            continue
        results.append((segment, str(reply)))
        should_listen = should_listen or bool(sl)

    if len(results) < 2:
        return None, False
    return router._format_compound_results(results), should_listen


def _resolve_pending_spotify_selection(
    user_input: str,
    profile_id: str,
) -> tuple[str | None, bool]:
    resolution = pending_spotify_selections.resolve(profile_id, user_input)
    if resolution is None or resolution.status is SpotifySelectionStatus.UNRELATED:
        return None, False

    english = get_current_language().startswith("en")
    if resolution.status is SpotifySelectionStatus.CANCELLED:
        return (
            "Spotify selection cancelled."
            if english
            else "Selección de Spotify cancelada."
        ), False
    if resolution.status is SpotifySelectionStatus.CLARIFY:
        choices = "; ".join(
            f"{index + 1}: {item.title} de {item.artist}"
            for index, item in enumerate(resolution.choices)
        )
        return (
            f"I could not identify the selection. Say first, second, or the title: {choices}. Which one?"
            if english
            else f"No pude identificar la selección. Di primera, segunda o el título: {choices}. ¿Cuál?"
        ), True

    candidate = resolution.candidate
    if candidate is None:
        return None, False
    query = " ".join(
        part
        for part in (
            candidate.title,
            f"de {candidate.artist}" if candidate.artist else "",
        )
        if part
    )
    result = tool_manager._invocar_tool_entry(
        "reproducir_en_spotify",
        {"cancion": query},
        user_input,
        "spotify_clarification",
        profile_id,
    )
    return str(result), False


def _preflight(
    user_input: str,
    profile_id: str,
    *,
    allow_compound: bool = True,
) -> tuple[str | None, bool]:
    from core.core_tools import (
        _ajustar_volumen_absoluto,
        _ajustar_volumen_relativo,
        agregar_recordatorio,
    )

    pid = _cargar_contexto_perfil(profile_id)

    user_input_norm = reparar_unicode(str(user_input or "")).strip()
    if allow_compound:
        compound_reply, compound_should_listen = _preflight_compuesto(
            user_input_norm, pid
        )
        if compound_reply is not None:
            return compound_reply, compound_should_listen

    spotify_reply, spotify_should_listen = _resolve_pending_spotify_selection(
        user_input_norm,
        pid,
    )
    if spotify_reply is not None:
        return spotify_reply, spotify_should_listen

    # 2. Cached briefing
    if any(k in user_input_norm.lower() for k in ["daily news", "news briefing", "briefing", "news"]):
        nc = services.noticias_cache
        if nc and nc.get("resumen") and nc.get("listo"):
            resumen = re.sub(r"<think>.*?</think>", "", nc["resumen"], flags=re.DOTALL)
            resumen = re.sub(r"\s+", " ", resumen).strip()
            return f"Daily news briefing: {resumen}", False
        return "The briefing is still being generated, Administrator. One moment.", False

    # 4. Quick responses
    social = social_engine._respuesta_rapida_social(user_input_norm, pid)
    if social is not None:
        return social, False

    contextual = social_engine._respuesta_seguimiento_contextual(
        user_input_norm, history_manager._get_history_for_profile(pid)
    )
    if contextual is not None:
        return contextual, False

    # 3. Reminders
    reminder_text, reminder_minutes = brain_utils.parse_reminder(user_input_norm)
    if reminder_text and reminder_minutes:
        agregar_recordatorio(reminder_text, reminder_minutes)
        reminder_time = (datetime.now() + timedelta(minutes=reminder_minutes)).strftime("%H:%M")
        return (
            f"Understood, Administrator. I will remind you of '{reminder_text}' at {reminder_time}."
            if pid == DEFAULT_PROFILE_ID
            else f"Understood. I will remind you of '{reminder_text}' at {reminder_time}."
        ), False

    # 4. Volume + Specific error log
    m_vol, v_vol = brain_utils.parsear_comando_volumen(user_input_norm)
    if m_vol and v_vol is not None:
        try:
            res = (
                _ajustar_volumen_relativo(v_vol)
                if m_vol == "relative"
                else _ajustar_volumen_absoluto(v_vol)
            )
            return str(res), False
        except Exception as exc:
            obs_event("volume_adjustment_error", error=type(exc).__name__)
            return "Could not adjust the system volume.", False

    # 5. Hybrid router
    router_reply = router._router_hibrido(user_input_norm)
    if router_reply is not None:
        return str(router_reply), security_engine._es_bloqueo_autorizacion(
            str(router_reply)
        )

    # 6. Music fast-path
    ultima = get_last_requested_track()
    if music_engine._es_comando_repetir_musica(user_input_norm):
        if ultima:
            res = tool_manager._invocar_tool_entry(
                "reproducir_en_spotify",
                {"cancion": ultima},
                user_input_norm,
                "fast_repeat",
                pid,
            )
            return str(res), False
        return (
            "I don't have a previous song to repeat, Administrator."
            if pid == DEFAULT_PROFILE_ID
            else "I don't have a previous song to repeat."
        ), False

    if music_engine._es_peticion_musica_generica(user_input_norm):
        return (
            "Please provide a title or artist and I will play it, Administrator."
            if pid == DEFAULT_PROFILE_ID
            else "Please provide a title or artist and I will play it."
        ), False

    if music_engine._es_posible_titulo_cancion(
        user_input_norm
    ) and music_engine._contexto_musica_activo(
        history_manager._get_history_for_profile(pid)
    ):
        res = tool_manager._invocar_tool_entry(
            "reproducir_en_spotify",
            {"cancion": user_input_norm},
            user_input_norm,
            "fast_music",
            pid,
        )
        return str(res), False

    return None, False


def procesar_mensaje(
    user_input: str, profile_id: str = DEFAULT_PROFILE_ID, *, count_inbound: bool = True
) -> tuple[str, bool]:
    if count_inbound:
        obs_inc("messages_total", 1)
    pid = _cargar_contexto_perfil(profile_id)
    with jarvis_state.active_profile(pid):
        reply, sl = _preflight(user_input, pid)
        if reply is not None:
            return _finalize_reply(reply, [], user_input, pid, "preflight")

        if social_engine._debe_buscar_en_web(user_input):
            dyn = router._router_hibrido(user_input)
            if dyn is not None:
                return _finalize_reply(
                    str(dyn),
                    [],
                    user_input,
                    pid,
                    "dynamic_router",
                )

        return _ejecutar_cerebro_llm(user_input, pid)


def _ejecutar_cerebro_llm(user_input: str, pid: str) -> tuple[str, bool]:
    from core.core_tools import extraer_datos_criticos

    with jarvis_state.memoria_lock:
        msg_counter = int(jarvis_state._msg_counter_by_profile.get(pid, 0)) + 1
        jarvis_state._msg_counter_by_profile[pid] = msg_counter
        if msg_counter == 1 or msg_counter % 3 == 0:
            jarvis_state.DATOS_CURIOSOS = extraer_datos_criticos(
                user_input, jarvis_state.DATOS_CURIOSOS
            )
            core_tools.DATOS_CURIOSOS = jarvis_state.DATOS_CURIOSOS

    messages = (
        [prompts.get_system_msg(user_input, profile_id=pid)]
        + history_manager._get_history_for_profile(pid)[-10:]
        + [HumanMessage(content=user_input)]
    )

    if _llm_calls_disabled_for_tests():
        return "Brain not initialized.", False
    if brain_state.llm is None:
        raise LLMUnavailableError

    if not necesita_tools(user_input):
        response = _invoke_model(
            brain_state.llm,
            messages,
            event="llm_direct_invoke_failed",
        )
        return _finalize_reply(
            brain_utils._limpiar_thinking(response.content),
            messages,
            user_input,
            pid,
            "no_tools",
        )

    if brain_state.llm_with_tools is None:
        response = _invoke_model(
            brain_state.llm,
            messages,
            event="llm_plain_fallback_failed",
        )
        return _finalize_reply(
            brain_utils._limpiar_thinking(response.content),
            messages,
            user_input,
            pid,
            "tools_unavailable_fallback",
        )

    try:
        response = _invoke_model(
            brain_state.llm_with_tools,
            messages,
            event="llm_tool_invoke_failed",
        )
        messages.append(response)

        # Shortcut for Spotify if it's the only tool
        if response.tool_calls and len(response.tool_calls) == 1:
            tc0 = response.tool_calls[0]
            if tc0.get("name") == "reproducir_en_spotify":
                result = tool_manager._invocar_tool_entry(
                    tc0["name"], tc0.get("args") or {}, user_input, "llm_shortcut", pid
                )
                return _finalize_reply(
                    str(result), messages, user_input, pid, "llm_shortcut"
                )

        iterations = 0
        while response.tool_calls and iterations < 3:
            iterations += 1
            tcs = response.tool_calls

            if len(tcs) == 1:
                tc = tcs[0]
                result = tool_manager._invocar_tool(tc, brain_state.tool_map, {"user_input": user_input, "source": "llm_loop", "profile_id": pid})
                messages.append(ToolMessage(content=core_tools._limpiar_respuesta(brain_utils._formatear_reply_por_perfil(str(result), pid)), tool_call_id=tc["id"]))
            else:
                futures = []
                for tc in tcs:
                    futures.append((tc, tool_manager.tool_executor.submit(
                        tool_manager._invocar_tool, tc, brain_state.tool_map, {"user_input": user_input, "source": "llm_parallel_loop", "profile_id": pid}
                    )))

                for tc, f in futures:
                    try:
                        result = f.result(timeout=25)
                        messages.append(ToolMessage(content=core_tools._limpiar_respuesta(brain_utils._formatear_reply_por_perfil(str(result), pid)), tool_call_id=tc["id"]))
                    except Exception as exc:
                        obs_event(
                            "parallel_tool_failed",
                            error=type(exc).__name__,
                            tool=str(tc.get("name") or "")[:80],
                        )
                        messages.append(
                            ToolMessage(
                                content="Parallel tool execution failed.",
                                tool_call_id=tc["id"],
                            )
                        )

            response = _invoke_model(
                brain_state.llm_with_tools,
                messages,
                event="llm_tool_followup_failed",
            )
            messages.append(response)
        reply = brain_utils._limpiar_thinking(response.content or "")
    except Exception as exc:
        obs_event("llm_brain_error", error=type(exc).__name__)
        fallback = brain_state.llm_fallback or brain_state.llm
        if fallback is None:
            raise LLMServiceError from None
        fallback_response = _invoke_model(
            fallback,
            messages,
            event="llm_fallback_invoke_failed",
        )
        reply = brain_utils._limpiar_thinking(fallback_response.content or "")

    return _finalize_reply(reply, messages, user_input, pid, "final")


def stream_procesar_mensaje_events(
    user_input: str, profile_id: str = DEFAULT_PROFILE_ID
) -> Iterator[dict]:
    # (Streaming with 100% parity including heartbeats)
    user_input = reparar_unicode(str(user_input or "")).strip()
    yield {"type": "status", "text": "connecting to AI cores"}

    stop_heartbeat = threading.Event()

    def _heartbeat():
        while not stop_heartbeat.is_set():
            _time.sleep(8)
            if not stop_heartbeat.is_set():
                obs_event("brain_heartbeat_pulse")

    threading.Thread(target=_heartbeat, daemon=True).start()
    context_token = None

    try:
        pid = _cargar_contexto_perfil(profile_id)
        context_token = jarvis_state.set_active_profile_id(pid)

        reply, sl = _preflight(user_input, pid)
        if reply is not None:
            final, fsl = _finalize_reply(reply, [], user_input, pid, "early")
            yield {"type": "done", "response": final, "should_listen": fsl}
            return

        if not necesita_tools(user_input):
            yield {"type": "status", "text": "generating direct response"}
            messages = (
                [prompts.get_system_msg(user_input, profile_id=pid)]
                + history_manager._get_history_for_profile(pid)[-10:]
                + [HumanMessage(content=user_input)]
            )
            if _llm_calls_disabled_for_tests():
                final = "Brain not initialized."
                yield {"type": "done", "response": final, "should_listen": False}
                return
            if brain_state.llm is None:
                raise LLMUnavailableError

            acc = []
            in_thinking = False
            for chunk in brain_state.llm.stream(messages):
                c = getattr(chunk, "content", "") or ""
                if not c:
                    continue
                acc.append(c)

                # Detect thinking start
                if "<think>" in c:
                    in_thinking = True
                    print("\n[JARVIS THINKING]", end="", flush=True)

                if in_thinking:
                    print(".", end="", flush=True)
                    if "</think>" in c:
                        in_thinking = False
                        print("[END OF THINKING]")
                    continue

                yield {"type": "token", "text": c}
            final = brain_utils._formatear_reply_por_perfil(
                brain_utils._limpiar_thinking("".join(acc)), pid
            )
            _finalize_reply(final, messages, user_input, pid, "no_tools_stream")
            yield {"type": "done", "response": final, "should_listen": False}
            return

        yield {"type": "status", "text": "resolving tools..."}
        reply, sl = _ejecutar_cerebro_llm(user_input, pid)
        yield {"type": "done", "response": reply, "should_listen": sl}

    except LLMUnavailableError:
        obs_event("llm_stream_unavailable", error="LLMUnavailableError")
        yield {
            "type": "error",
            "code": "llm_unconfigured",
            "message": LLM_UNCONFIGURED_MESSAGE,
        }
    except Exception as exc:
        obs_event("llm_stream_failed", error=type(exc).__name__)
        yield {
            "type": "error",
            "code": "chat_unavailable",
            "message": CHAT_UNAVAILABLE_MESSAGE,
        }
    finally:
        if context_token is not None:
            jarvis_state.reset_active_profile_id(context_token)
        stop_heartbeat.set()
