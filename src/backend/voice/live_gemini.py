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

from core.jarvis_config import LiveVoiceConfig, resolve_live_voice_config
from core.llm_providers import resolve_gemini_api_key
from core.runtime_logger import log_error, log_warning
from core.unified_log import write_log

from voice.live_context import LiveContextPolicy, build_live_context
from voice.live_session import LiveSession, LiveSessionState
from voice.live_transport import (
    LiveTransportController,
    LiveTransportError,
    LiveTransportState,
    await_setup_complete,
    reconnect_delay,
    supervise_transport_tasks,
)

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
        voice_name: str | None = None,
        config: LiveVoiceConfig | None = None,
        connect_factory=None,
    ) -> None:
        self.session = session
        self.api_key = api_key or resolve_gemini_api_key()
        live_env = dict(os.environ)
        if model:
            live_env["JARVIS_GEMINI_LIVE_MODEL"] = model
        if voice_name:
            live_env["JARVIS_GEMINI_LIVE_VOICE"] = voice_name
        self.config = config or resolve_live_voice_config(live_env)
        self.model = f"models/{self.config.model}"
        self.voice_name = self.config.voice_name

        self.system_instruction = system_instruction or (
            "Eres J.A.R.V.I.S., el asistente de IA local para Windows del usuario. "
            "Tienes herramientas del sistema integradas: reproducir_en_spotify, controlar_reproduccion, "
            "ajustar_volumen, obtener_clima, abrir_aplicacion, buscar_en_internet. "
            "REGLA CRÍTICA DE HERRAMIENTAS: Cuando el usuario te pida música (Spotify), volumen, "
            "abrir programas, consultar el clima o buscar información, DEBES LLAMAR INMEDIATAMENTE a la herramienta correspondiente mediante functionCall. "
            "NUNCA simules, finjas o narres en texto que estás haciendo una acción sin invocar la herramienta. LLAMA A LA FUNCIÓN DIRECTAMENTE. "
            "Mantén tus respuestas habladas breves, naturales y directas para voz. No uses Markdown ni expongas tus pensamientos internos."
        )
        self._ws: Any = None
        self._running = False
        self._current_turn_text: list[str] = []
        self._connect_factory = connect_factory
        self.transport = LiveTransportController(
            max_reconnect_attempts=self.config.max_reconnect_attempts,
            stable_connection_seconds=self.config.stable_connection_seconds,
        )

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _resolve_speaker_name(self) -> str:
        """Resolve the display name for the current active profile."""
        pid = str(self.session.profile_id or "default").strip()
        if pid in {"default", "admin", "owner"}:
            return "Administrador"
        try:
            import sqlite3

            from core.jarvis_config import RUNTIME_DIR

            db_path = os.getenv("JARVIS_DB_PATH") or os.path.join(RUNTIME_DIR, "memoria_jarvis.db")
            if os.path.isfile(db_path):
                conn = sqlite3.connect(db_path)
                row = conn.execute("SELECT nombre FROM voice_profiles WHERE profile_id=?", (pid,)).fetchone()
                conn.close()
                if row and row[0]:
                    return str(row[0])
        except Exception:
            pass
        return "Usuario"

    def _build_live_system_instruction(
        self,
        tool_names: tuple[str, ...] = (),
    ) -> str:
        """Build bounded context for the active profile only."""
        try:
            from services.memory_manager import memory_manager

            pid = self.session.profile_id
            speaker_name = self._resolve_speaker_name()
            snapshot = memory_manager.snapshot(pid)
            base_instruction = (
                f"{self.system_instruction} "
                "Use only the supplied active-profile context. "
                "Never claim that a tool succeeded unless its tool response says so. "
                "Keep spoken replies concise and do not expose hidden reasoning."
            )
            return build_live_context(
                base_instruction=base_instruction,
                language=self.session.language,
                profile_name=speaker_name,
                profile_facts=snapshot.facts,
                history=snapshot.history,
                allowed_tool_names=tool_names,
                policy=LiveContextPolicy(
                    max_chars=self.config.context_char_limit,
                    recent_turns=self.config.recent_turns,
                ),
            )
        except Exception as exc:
            log_warning(
                "gemini_live_build_sys_msg_failed",
                error=type(exc).__name__,
            )
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
        """Start the supervised, privacy-first Gemini Live bridge."""
        if not self.is_available():
            await self.session.emit_json({
                "type": "error",
                "code": "gemini_live_unconfigured",
                "message": "Gemini API key is required for Gemini Live mode.",
            })
            return

        connect_factory = self._connect_factory
        if connect_factory is None:
            try:
                import websockets  # type: ignore[reportMissingImports]
            except ImportError:
                log_warning(
                    "gemini_live_websockets_missing",
                    error="ImportError",
                )
                await self.session.emit_json(
                    {
                        "type": "error",
                        "code": "gemini_live_dependency_missing",
                        "message": "Gemini Live transport is unavailable.",
                    }
                )
                return
            connect_factory = websockets.connect

        uri = f"{GEMINI_LIVE_WS_URL}?key={self.api_key}"
        self._running = True
        connection_count = 0

        while self._running:
            try:
                self.transport.transition(
                    LiveTransportState.CONNECTING
                    if connection_count == 0
                    else LiveTransportState.RECONNECTING
                )
                await self.session.set_provider_state(
                    "connecting" if connection_count == 0 else "reconnecting",
                    active_mode="gemini_live",
                )
                async with connect_factory(uri) as ws:
                    self._ws = ws
                    self.transport.mark_connected()
                    self.transport.transition(LiveTransportState.CONFIGURING)
                    await self.session.set_provider_state(
                        "configuring",
                        active_mode="gemini_live",
                    )
                    await self._send_setup()
                    setup_result = await await_setup_complete(
                        ws,
                        timeout_seconds=self.config.setup_timeout_seconds,
                    )
                    self.transport.transition(LiveTransportState.READY)
                    await self.session.set_provider_state(
                        "ready",
                        active_mode="gemini_live",
                    )
                    await self.session.set_state(LiveSessionState.LISTENING)
                    connection_restored = connection_count > 0
                    await self.session.emit_json(
                        {
                            "type": "session_ready",
                            "mode": "gemini_live",
                            "model": self.model,
                            "connection_restored": connection_restored,
                            "context_source": "local",
                        }
                    )

                    if connection_restored:
                        write_log(
                            "LIVE_SESSION",
                            "Gemini Live connection restored with local context",
                            profile_id=self.session.profile_id,
                        )
                    connection_count += 1

                    send_audio_task = asyncio.create_task(self._upstream_audio_loop())
                    send_media_task = asyncio.create_task(self._upstream_media_loop())
                    recv_task = asyncio.create_task(
                        self._downstream_loop(setup_result.pending_messages)
                    )
                    self.session.attach_transport_task(send_audio_task)
                    self.session.attach_transport_task(send_media_task)
                    self.session.attach_transport_task(recv_task)

                    await supervise_transport_tasks(
                        [send_audio_task, send_media_task, recv_task],
                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not self._running:
                    break
                code = (
                    exc.code
                    if isinstance(exc, LiveTransportError)
                    else "gemini_live_connect_failed"
                )
                self.session.diagnostics.record_reconnect()
                try:
                    reconnect_attempt = self.transport.next_reconnect_attempt()
                except LiveTransportError as terminal_error:
                    log_error(
                        "gemini_live_session_exhausted_retries",
                        error=type(exc).__name__,
                    )
                    self._running = False
                    await self.session.set_provider_state(
                        "failed",
                        active_mode="gemini_live",
                        failure_code=terminal_error.code,
                    )
                    raise terminal_error from None

                backoff_delay = reconnect_delay(
                    reconnect_attempt,
                    base_seconds=self.config.reconnect_base_seconds,
                    max_seconds=self.config.reconnect_max_seconds,
                )
                write_log(
                    "LIVE_SESSION",
                    "Gemini Live privacy-first reconnect",
                    code=code,
                    attempt=reconnect_attempt,
                    profile_id=self.session.profile_id,
                )
                await self.session.emit_json(
                    {
                        "type": "session_reconnecting",
                        "code": code,
                        "attempt": reconnect_attempt,
                        "max_attempts": self.config.max_reconnect_attempts,
                        "context_source": "local",
                    }
                )
                await self.session.set_provider_state(
                    "reconnecting",
                    active_mode="gemini_live",
                    failure_code=code,
                )
                await asyncio.sleep(backoff_delay)
            finally:
                self._ws = None

        self._running = False
        self.transport.transition(LiveTransportState.CLOSED)

    async def _send_setup(self) -> None:
        """Send the initial Gemini Bidi setup payload."""
        decls = self._get_function_declarations()
        instruction = self._build_live_system_instruction(
            tuple(str(decl.get("name") or "") for decl in decls)
        )
        setup_payload: dict[str, Any] = {
            "model": self.model,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": self.voice_name,
                        }
                    }
                },
            },
            "systemInstruction": {
                "parts": [{"text": instruction}]
            },
            "contextWindowCompression": {"slidingWindow": {}},
        }
        if decls:
            setup_payload["tools"] = [{"functionDeclarations": decls}]

        setup_msg = {"setup": setup_payload}
        if self._ws:
            await self._ws.send(json.dumps(setup_msg))

    async def _upstream_audio_loop(self) -> None:
        """Continuously sends incoming client audio chunks to Gemini."""
        while self._running and self._ws:
            try:
                chunk = await self.session.next_audio_chunk()
                try:
                    self.session.diagnostics.record_turn_start()
                    b64_data = base64.b64encode(chunk).decode("utf-8")
                    media_msg = {
                        "realtimeInput": {
                            "audio": {
                                "mimeType": "audio/pcm;rate=16000",
                                "data": b64_data,
                            }
                        }
                    }
                    await self._ws.send(json.dumps(media_msg))
                finally:
                    self.session.audio_task_done(chunk)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise LiveTransportError("gemini_live_connect_failed") from None

    async def _upstream_media_loop(self) -> None:
        """Continuously sends incoming screen/camera frames to Gemini Multimodal Live."""
        while self._running and self._ws:
            try:
                frame = await self.session.next_image_frame()
                b64_data, mime_type = frame
                try:
                    if "," in b64_data:
                        b64_data = b64_data.split(",", 1)[1]
                    media_msg = {
                        "realtimeInput": {
                            "video": {
                                "mimeType": mime_type or "image/jpeg",
                                "data": b64_data,
                            }
                        }
                    }
                    await self._ws.send(json.dumps(media_msg))
                finally:
                    self.session.image_task_done(frame)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise LiveTransportError("gemini_live_connect_failed") from None

    async def _send_tool_response(self, call_id: str, result_text: str) -> None:
        """Send a single tool execution result back to Gemini Bidi WebSocket."""
        tool_resp_msg = {
            "toolResponse": {
                "functionResponses": [
                    {
                        "response": {
                            "output": {
                                "result": str(result_text)[:2000],
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

    async def _execute_orchestrated_plan(self, function_calls: list[dict[str, Any]]) -> None:
        """Execute a planned batch of actions sequentially through the LiveActionOrchestrator."""
        plan = self.session.orchestrator.enqueue_batch(function_calls)
        await self.session.set_state(LiveSessionState.PROCESSING)

        for action in list(plan.actions):
            # If session is closed or plan is no longer active, abort
            if not self._running or not plan.is_active:
                break
            # Execute action through the orchestrator
            action_task = asyncio.create_task(
                self.session.orchestrator.execute_action(action, self._send_tool_response)
            )
            action._task = action_task
            self.session.attach_tool_task(action_task)
            try:
                await action_task
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Action execution error: %s", e)

    async def _downstream_loop(
        self,
        initial_messages: tuple[dict[str, Any], ...] = (),
    ) -> None:
        """Continuously receives audio, text, and tool events from Gemini."""
        for message in initial_messages:
            await self._handle_downstream_message(message)
        while self._running and self._ws:
            try:
                raw_msg = await self._ws.recv()
            except asyncio.CancelledError:
                raise
            except Exception:
                raise LiveTransportError("gemini_live_connect_failed") from None

            if isinstance(raw_msg, bytes):
                try:
                    raw_msg = raw_msg.decode("utf-8")
                except UnicodeDecodeError:
                    raise LiveTransportError("gemini_live_protocol_error") from None

            try:
                data = json.loads(raw_msg)
            except (TypeError, ValueError):
                raise LiveTransportError("gemini_live_protocol_error") from None
            if not isinstance(data, dict):
                raise LiveTransportError("gemini_live_protocol_error")
            await self._handle_downstream_message(data)

    async def _handle_downstream_message(self, data: dict[str, Any]) -> None:
        if data.get("goAway") is not None:
            raise LiveTransportError("gemini_live_connection_rotating")
        if data.get("error") is not None:
            raise LiveTransportError("gemini_live_connect_failed")

        # Handle direct toolCall or inside serverContent
        tool_call = data.get("toolCall")
        server_content = data.get("serverContent")
        if not tool_call and server_content:
            model_turn = server_content.get("modelTurn")
            if model_turn:
                for part in model_turn.get("parts", []):
                    if "functionCall" in part:
                        tool_call = {"functionCalls": [part["functionCall"]]}
                        break

        if tool_call and "functionCalls" in tool_call:
            function_calls = tool_call["functionCalls"]
            orchestrator_task = asyncio.create_task(
                self._execute_orchestrated_plan(function_calls)
            )
            self.session.attach_tool_task(orchestrator_task)
            return

        if not server_content:
            return

        if server_content.get("interrupted"):
            write_log(
                "LIVE_SESSION",
                "User interruption detected (barge-in)",
                profile_id=self.session.profile_id,
            )
            await self.session.interrupt()
            return

        model_turn = server_content.get("modelTurn")
        if model_turn:
            self.session.diagnostics.record_first_token()
            await self.session.set_state(LiveSessionState.SPEAKING)
            for part in model_turn.get("parts", []):
                inline_data = part.get("inlineData")
                if inline_data and inline_data.get("data"):
                    try:
                        pcm_bytes = base64.b64decode(
                            inline_data["data"],
                            validate=True,
                        )
                    except (TypeError, ValueError):
                        raise LiveTransportError(
                            "gemini_live_protocol_error"
                        ) from None
                    await self.session.emit_audio_chunk(pcm_bytes)

                raw_text = str(part.get("text") or "").strip()
                if not raw_text or part.get("thought") is True:
                    continue
                self._current_turn_text.append(raw_text)
                write_log(
                    "LIVE_SPEECH",
                    raw_text,
                    profile_id=self.session.profile_id,
                )
                await self.session.emit_json(
                    {
                        "type": "transcript",
                        "role": "assistant",
                        "text": raw_text,
                    }
                )

        if server_content.get("generationComplete"):
            await self.session.emit_json({"type": "generation_complete"})

        if server_content.get("turnComplete"):
            full_reply = " ".join(self._current_turn_text).strip()
            if full_reply:
                try:
                    from core import core_tools
                    from core.unified_log import write_conversation
                    from langchain_core.messages import AIMessage
                    from services.memory_manager import memory_manager

                    pid = self.session.profile_id
                    write_conversation(
                        "JARVIS",
                        full_reply,
                        profile_id=pid,
                        channel="gemini_live",
                    )
                    memory_manager.append_history(
                        pid,
                        [AIMessage(content=full_reply)],
                    )
                    core_tools.guardar_memoria_async(pid)
                except Exception as exc:
                    log_warning(
                        "gemini_live_save_turn_failed",
                        error=type(exc).__name__,
                    )
            self._current_turn_text.clear()

            last_turn = self.session.diagnostics.record_turn_complete()
            self.transport.mark_turn_completed()
            diag_summary = self.session.diagnostics.get_summary()
            ttfa_ms = last_turn.get("ttfa_ms", 0.0)
            ttft_ms = last_turn.get("ttft_ms", 0.0)
            total_ms = last_turn.get("total_ms", 0.0)
            tool_ms = diag_summary["tool_latency"]["p50_ms"]
            await self.session.set_state(LiveSessionState.LISTENING)
            await self.session.emit_json(
                {
                    "type": "turn_complete",
                    "diagnostics": diag_summary,
                    "metrics": {
                        "total_ms": total_ms,
                        "ttft_ms": ttft_ms,
                        "ttfa_ms": ttfa_ms,
                        "tool_ms": tool_ms,
                        "provider": "Gemini Live",
                    },
                }
            )
