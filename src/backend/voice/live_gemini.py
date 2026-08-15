"""Gemini Multimodal Live API client for bidirectional voice streaming in J.A.R.V.I.S.

Connects to Gemini's WebSocket endpoint for sub-second, full-duplex voice interactions.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import Any

from core.llm_providers import resolve_llm_provider_config
from core.runtime_logger import log_error, log_warning

from voice.live_session import LiveSession, LiveSessionState

logger = logging.getLogger(__name__)

GEMINI_LIVE_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)


class GeminiLiveStreamer:
    """Bridges a local LiveSession with the Gemini Multimodal Live WebSocket."""

    def __init__(
        self,
        session: LiveSession,
        api_key: str | None = None,
        model: str | None = None,
        system_instruction: str = "",
        voice_name: str = "Puck",
    ) -> None:
        self.session = session
        cfg = resolve_llm_provider_config()
        self.api_key = api_key or cfg.primary_api_key
        raw_model = (model or os.getenv("JARVIS_GEMINI_LIVE_MODEL") or "models/gemini-3.1-flash-live-preview").strip()
        self.model = raw_model if raw_model.startswith("models/") else f"models/{raw_model}"
        self.system_instruction = system_instruction or (
            "Eres J.A.R.V.I.S., el asistente de IA local para Windows del usuario. "
            "Tienes herramientas del sistema integradas: reproducir_en_spotify, controlar_reproduccion, "
            "ajustar_volumen, obtener_clima, abrir_aplicacion, buscar_en_internet. "
            "REGLA CRÍTICA DE HERRAMIENTAS: Cuando el usuario te pida música (Spotify), volumen, "
            "abrir programas, consultar el clima o buscar información, DEBES LLAMAR INMEDIATAMENTE a la herramienta correspondiente mediante functionCall. "
            "NUNCA simules, finjas o narres en texto que estás haciendo una acción sin invocar la herramienta. LLAMA A LA FUNCIÓN DIRECTAMENTE. "
            "Mantén tus respuestas habladas breves, naturales y directas para voz. No uses Markdown ni expongas tus pensamientos internos."
        )
        self.voice_name = voice_name
        self._ws: Any = None
        self._running = False
        self._current_turn_text: list[str] = []

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _build_live_system_instruction(self) -> str:
        """Build dynamic system instruction with full profile memory and recent conversation context."""
        try:
            from core.brain import prompts
            from langchain_core.messages import HumanMessage
            from services.memory_manager import memory_manager

            pid = self.session.profile_id
            sys_msg = prompts.get_system_msg("", profile_id=pid).content

            # Retrieve recent conversation history turns
            history = memory_manager.get_history(pid)
            hist_text = ""
            if history:
                turns = []
                for m in history[-12:]:
                    sender = "Usuario" if isinstance(m, HumanMessage) else "JARVIS"
                    content = str(getattr(m, "content", "") or "").strip()
                    if content:
                        turns.append(f"{sender}: {content}")
                if turns:
                    hist_text = "\n\n--- CONVERSACIONES PASADAS Y MEMORIA RECIENTE ---\n" + "\n".join(turns)

            directives = (
                "\n\n--- DIRECTIVAS CRÍTICAS DE VOZ EN VIVO Y HERRAMIENTAS ---\n"
                "1. Eres J.A.R.V.I.S., el asistente de IA local para Windows del Administrador.\n"
                "2. Conoces toda la información, memoria permanente y conversaciones pasadas del usuario listadas arriba. NUNCA busques en internet sobre tus conversaciones pasadas con el usuario; usa la sección de memoria y conversaciones de arriba.\n"
                "3. Si el usuario te pide una acción del sistema (abrir juegos/apps como Counter-Strike, Spotify, volumen, clima, buscar en internet), "
                "DEBES llamar inmediatamente a la función correspondiente mediante toolCall. NUNCA simules o digas que hiciste la acción sin llamar a la herramienta.\n"
                "4. Mantén tus respuestas habladas breves, concisas, naturales y directas para voz. No uses Markdown ni expongas pensamientos internos."
            )
            return f"{sys_msg}{hist_text}{directives}"
        except Exception as e:
            log_warning("gemini_live_build_sys_msg_failed", error=str(e))
            return self.system_instruction

    def _get_function_declarations(self) -> list[dict]:
        """Extract tool declarations from core tools for Gemini Live."""
        try:
            from core import core_tools
            from core.brain.llm_engine import _tool_schema_from_langchain_tool

            # Conversational/LLM text generator tools are excluded because Gemini Live handles dialogue natively.
            excluded = {"frase_motivacional"}

            tools = core_tools.get_base_tools()
            decls = []
            for t in tools:
                schema = _tool_schema_from_langchain_tool(t)
                fn = schema.get("function")
                if fn and fn.get("name") and fn["name"] not in excluded:
                    decls.append({
                        "name": fn["name"],
                        "description": str(fn.get("description", "") or "")[:500],
                        "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                    })
            return decls
        except Exception as e:
            log_warning("gemini_live_tools_declaration_failed", error=str(e))
            return []

    async def start(self) -> None:
        """Start the bidirectional bridge."""
        if not self.is_available():
            await self.session.emit_json({
                "type": "error",
                "code": "gemini_live_unconfigured",
                "message": "Gemini API key is required for Gemini Live mode.",
            })
            return

        try:
            import websockets  # type: ignore[reportMissingImports]
        except ImportError:
            log_warning("gemini_live_websockets_missing", msg="websockets package not available")
            await self.session.emit_json({
                "type": "error",
                "code": "websockets_unavailable",
                "message": "The websockets library is required for live audio streaming.",
            })
            return

        uri = f"{GEMINI_LIVE_WS_URL}?key={self.api_key}"
        self._running = True

        try:
            async with websockets.connect(uri) as ws:
                self._ws = ws
                await self._send_setup()
                await self.session.set_state(LiveSessionState.LISTENING)
                await self.session.emit_json({
                    "type": "session_ready",
                    "mode": "gemini_live",
                    "model": self.model,
                })

                send_task = asyncio.create_task(self._upstream_loop())
                recv_task = asyncio.create_task(self._downstream_loop())
                self.session.attach_task(recv_task)

                done, pending = await asyncio.wait(
                    [send_task, recv_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log_error("gemini_live_session_error", error=str(e))
            await self.session.emit_json({
                "type": "error",
                "code": "gemini_live_connection_failed",
                "message": f"Gemini Live stream closed: {e}",
            })
        finally:
            self._running = False
            self._ws = None

    async def _send_setup(self) -> None:
        """Send the initial Gemini Bidi setup payload."""
        decls = self._get_function_declarations()
        instruction = self._build_live_system_instruction()
        setup_payload: dict[str, Any] = {
            "model": self.model,
            "generation_config": {
                "response_modalities": ["AUDIO"],
                "speech_config": {
                    "voice_config": {
                        "prebuilt_voice_config": {
                            "voice_name": self.voice_name,
                        }
                    }
                },
            },
            "system_instruction": {
                "parts": [{"text": instruction}]
            },
        }
        if decls:
            setup_payload["tools"] = [{"function_declarations": decls}]

        setup_msg = {"setup": setup_payload}
        await self._ws.send(json.dumps(setup_msg))

    async def _upstream_loop(self) -> None:
        """Continuously sends incoming client audio chunks to Gemini."""
        while self._running and self._ws:
            try:
                chunk = await self.session._input_audio_queue.get()
                try:
                    b64_data = base64.b64encode(chunk).decode("utf-8")
                    media_msg = {
                        "realtime_input": {
                            "audio": {
                                "mime_type": "audio/pcm;rate=16000",
                                "data": b64_data,
                            }
                        }
                    }
                    await self._ws.send(json.dumps(media_msg))
                finally:
                    self.session._input_audio_queue.task_done()
            except (asyncio.CancelledError, Exception):
                break

    async def _handle_function_call(self, name: str, args: dict, call_id: str) -> None:
        """Execute a requested tool and send the result back to Gemini."""
        print(f"\n[GEMINI LIVE TOOL] >> INVOCANDO: {name}(args={args}) [call_id={call_id}]", flush=True)
        await self.session.set_state(LiveSessionState.PROCESSING)
        await self.session.emit_json({
            "type": "tool_executing",
            "tool": name,
            "args": args,
        })

        tool_result = "Acción ejecutada."
        try:
            from core.brain.tool_manager import _invocar_tool_entry
            res = await asyncio.wait_for(
                asyncio.to_thread(
                    _invocar_tool_entry,
                    name,
                    args,
                    f"Live voice command: {name}",
                    source="gemini_live",
                    profile_id=self.session.profile_id,
                ),
                timeout=7.0,
            )
            if res:
                tool_result = str(res)
            print(f"[GEMINI LIVE TOOL] << RESULTADO: {tool_result}\n", flush=True)
        except TimeoutError:
            log_warning("gemini_live_tool_timeout", tool=name)
            tool_result = f"La herramienta {name} tardó demasiado tiempo en responder."
            print(f"[GEMINI LIVE TOOL] << TIMEOUT: {tool_result}\n", flush=True)
        except Exception as e:
            log_error("gemini_live_tool_execution_failed", tool=name, error=str(e))
            tool_result = f"Error al ejecutar {name}: {e}"
            print(f"[GEMINI LIVE TOOL] << ERROR: {tool_result}\n", flush=True)

        tool_resp_msg = {
            "tool_response": {
                "function_responses": [
                    {
                        "response": {
                            "output": {
                                "result": tool_result[:2000],
                            }
                        },
                        "id": call_id,
                    }
                ]
            }
        }
        if self._ws and self._running:
            try:
                await self._ws.send(json.dumps(tool_resp_msg))
            except Exception as exc:
                log_warning("gemini_live_tool_response_send_failed", error=str(exc))

    async def _downstream_loop(self) -> None:
        """Continuously receives audio, text, and tool events from Gemini."""
        while self._running and self._ws:
            try:
                raw_msg = await self._ws.recv()
            except (asyncio.CancelledError, Exception):
                break

            if isinstance(raw_msg, bytes):
                raw_msg = raw_msg.decode("utf-8")

            try:
                data = json.loads(raw_msg)
            except Exception:
                continue

            # Handle direct toolCall or inside serverContent
            tool_call = data.get("toolCall")
            server_content = data.get("serverContent")
            if not tool_call and server_content:
                model_turn = server_content.get("modelTurn")
                if model_turn:
                    for p in model_turn.get("parts", []):
                        if "functionCall" in p:
                            tool_call = {"functionCalls": [p["functionCall"]]}
                            break

            if tool_call and "functionCalls" in tool_call:
                for fn_call in tool_call["functionCalls"]:
                    fn_name = fn_call.get("name", "")
                    fn_args = fn_call.get("args") or {}
                    fn_id = fn_call.get("id", "")
                    asyncio.create_task(self._handle_function_call(fn_name, fn_args, fn_id))
                continue

            if not server_content:
                continue

            # Check for turn completion / interruption
            if server_content.get("interrupted"):
                print("\n[GEMINI LIVE] << Interrupción de usuario (Barge-in) detectada.", flush=True)
                await self.session.interrupt()
                continue

            model_turn = server_content.get("modelTurn")
            if model_turn:
                await self.session.set_state(LiveSessionState.SPEAKING)
                for part in model_turn.get("parts", []):
                    # Handle audio data
                    inline_data = part.get("inlineData")
                    if inline_data and inline_data.get("data"):
                        pcm_bytes = base64.b64decode(inline_data["data"])
                        await self.session.emit_audio_chunk(pcm_bytes)

                    # Handle text transcript and internal thinking
                    raw_text = str(part.get("text") or "").strip()
                    if raw_text:
                        is_thought = (
                            part.get("thought") is True
                            or (raw_text.startswith("**") and any(k in raw_text for k in ("Initiating", "Decided", "Planning", "Executing")))
                            or any(k in raw_text for k in ("I've processed", "persona's constraints", "Confirmation is being formulated", "I've decided on the"))
                        )

                        if is_thought:
                            # Log internal reasoning cleanly to console
                            print(f"\n[GEMINI LIVE RAZONAMIENTO] {raw_text}", flush=True)
                        else:
                            # Log spoken response to console and send to UI
                            self._current_turn_text.append(raw_text)
                            print(f"\n[JARVIS] << {raw_text}", flush=True)
                            await self.session.emit_json({
                                "type": "transcript",
                                "role": "assistant",
                                "text": raw_text,
                            })

            if server_content.get("turnComplete"):
                full_reply = " ".join(self._current_turn_text).strip()
                if full_reply:
                    try:
                        from core import core_tools
                        from core.unified_log import write_conversation
                        from langchain_core.messages import AIMessage
                        from services.memory_manager import memory_manager

                        pid = self.session.profile_id
                        write_conversation("JARVIS", full_reply, profile_id=pid, channel="gemini_live")
                        memory_manager.append_history(pid, [AIMessage(content=full_reply)])
                        core_tools.guardar_memoria_async(pid)
                    except Exception as e:
                        log_warning("gemini_live_save_turn_failed", error=str(e))
                self._current_turn_text.clear()
                print("[GEMINI LIVE] << Fin de turno de respuesta.\n", flush=True)
                await self.session.set_state(LiveSessionState.LISTENING)
                await self.session.emit_json({"type": "turn_complete"})
