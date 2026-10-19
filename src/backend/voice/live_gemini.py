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

from voice.live_context import LiveContextPolicy, build_live_context, resolve_live_language
from voice.live_session import LiveSession, LiveSessionState
from voice.live_task_context import (
    CONTEXT_INSTRUCTION,
    CONTEXT_TOOL_NAME,
    context_error_response,
    context_tool_declaration,
    parse_context_proposal,
)
from voice.live_transport import (
    LiveResponseWatchdog,
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

LIVE_VOICE_TOOL_ALLOWLIST = frozenset(
    {
        "abrir_aplicacion",
        "abrir_documento",
        "abrir_navegador",
        "abrir_youtube",
        "agregar_a_cola_spotify",
        "ajustar_volumen",
        "buscar_en_internet",
        "buscar_en_wikipedia",
        "cancion_actual_spotify",
        "controlar_reproduccion",
        "controlar_ventana",
        "dar_like_spotify",
        "ejecutar_rutina",
        "evaluar_expresion_matematica",
        "listar_ventanas",
        "modo_no_molestar",
        "obtener_clima",
        "obtener_deportes_espn",
        "poner_recordatorio",
        "quitar_like_spotify",
        "reproducir_en_spotify",
        "reproducir_en_youtube",
        "reproducir_mix_spotify",
        "ver_procesos_pesados",
    }
)
MAX_FUNCTION_CALLS_PER_MESSAGE = 8
MAX_FUNCTION_CALL_ARGS_CHARS = 16_000


def _merge_transcript_text(current: str, incoming: str) -> str:
    existing = " ".join(str(current or "").split())
    update = " ".join(str(incoming or "").split())
    if not update:
        return existing
    if not existing:
        return update
    if update == existing or existing.endswith(update) or existing.startswith(update):
        return existing
    if update.startswith(existing):
        return update
    separator = "" if update[0] in ".,!?;:" else " "
    return f"{existing}{separator}{update}"


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
            "Usa herramientas sólo cuando el usuario solicite una acción, directamente o como seguimiento inequívoco de una tarea. "
            "Conversar, explicar, mencionar Spotify, citar una canción o corregir el idioma no solicita reproducción. "
            "Reproducir AHORA, añadir a la COLA y guardar/quitar de FAVORITOS son operaciones distintas: "
            "nunca sustituyas reproducir por agregar_a_cola_spotify. Usa cola o favoritos sólo si se solicitan. "
            "No conviertas 'es que', 'solo quiero hablar' ni ruido/voz de fondo en comandos. "
            "Respeta las negaciones. Si la intención es ambigua, pregunta antes de actuar. "
            "Cuando sí se solicite una acción, llama a su herramienta y confirma sólo lo que su resultado verifique. "
            "Mantén tus respuestas habladas breves, naturales y directas para voz. No uses Markdown ni expongas tus pensamientos internos."
        )
        self._ws: Any = None
        self._running = False
        self._current_turn_text = ""
        self._model_turn_text = ""
        self._current_user_text = ""
        self._user_text_ready = asyncio.Event()
        self._turn_generation = 0
        self._task_input_turn: int | None = None
        self._output_turn_generation: int | None = None
        self._output_active = False
        self._assistant_finalized = False
        self._typed_input_pending = False
        self._input_send_lock = asyncio.Lock()
        self.response_watchdog = LiveResponseWatchdog(timeout_seconds=self.config.response_timeout_seconds)
        self._tool_call_names: dict[str, str] = {}
        self._declared_tool_names: set[str] = set()
        self._cancelled_tool_call_ids: set[str] = set()
        self._connect_factory = connect_factory
        self.transport = LiveTransportController(
            max_reconnect_attempts=self.config.max_reconnect_attempts,
            stable_connection_seconds=self.config.stable_connection_seconds,
        )

    def is_available(self) -> bool:
        return bool(self.api_key) and not self.config.model_configuration_error

    def _reset_turn_transcripts(self) -> None:
        self._current_turn_text = ""
        self._model_turn_text = ""
        self._current_user_text = ""
        self._task_input_turn = None
        self._turn_generation += 1
        self._user_text_ready.clear()
        self._output_turn_generation = None
        self._output_active = False
        self._assistant_finalized = False
        self._typed_input_pending = False
        self.response_watchdog.reset()

    def accept_user_transcript(self, text: str) -> None:
        self._current_user_text = _merge_transcript_text(
            self._current_user_text, text,
        )[:self.session.max_user_text_chars]
        if self._current_user_text:
            self.session.language = resolve_live_language(self._current_user_text, self.session.language)
            self.session.orchestrator.language = self.session.language
            if self._task_input_turn is None:
                self._task_input_turn = self.session.orchestrator.task_context.begin_turn()
            self.session.orchestrator.task_context.update_turn_text(self._task_input_turn, self._current_user_text)
            self._user_text_ready.set()
            self.session.diagnostics.record_turn_start()
            self.response_watchdog.input_received()

    async def send_user_text(self, text: str) -> bool:
        """Submit one explicit typed turn on the existing native conversation."""
        if not isinstance(text, str) or not text.strip() or len(text) > self.session.max_user_text_chars:
            return False
        async with self._input_send_lock:
            if (not self._running or not self._ws or self.session._closed
                    or self.session.handshake_state != "ready" or self.session.input_paused):
                return False
            await self.session.interrupt(clear_input_audio=True)
            self._reset_turn_transcripts()
            self.accept_user_transcript(text.strip())
            self._typed_input_pending = True
            payload = {"clientContent": {
                "turns": [{"role": "user", "parts": [{"text": text.strip()}]}], "turnComplete": True,
            }}
            try:
                await self._ws.send(json.dumps(payload))
            except Exception as exc:
                log_warning("gemini_live_text_send_failed", error=type(exc).__name__)
                # A failed send can be ambiguous; never automatically replay it.
                return False
            self._persist_live_transcript("user", text.strip())
            await self.session.emit_json({
                "type": "transcript", "role": "user", "text": text.strip(),
                "is_final": True, "source": "live_typed_text",
            })
            await self.session.set_state(LiveSessionState.PROCESSING)
            return True

    def _begin_output(self) -> None:
        if not self._output_active:
            if self._assistant_finalized:
                self._current_turn_text = ""
                self._model_turn_text = ""
            self._output_active = True
            self._assistant_finalized = False
            self._output_turn_generation = self._turn_generation
        self.response_watchdog.output_received()

    async def _finalize_assistant_transcript(self) -> None:
        if self._assistant_finalized:
            return
        full_reply = (self._current_turn_text or self._model_turn_text).strip()
        if full_reply:
            await self.session.emit_json({
                "type": "transcript", "role": "assistant", "text": full_reply,
                "is_final": True, "source": "gemini_live",
            })
            self._persist_live_transcript("assistant", full_reply)
        self._assistant_finalized = True

    def _resolve_speaker_name(self) -> str:
        """Resolve the display name for the current active profile."""
        pid = str(self.session.profile_id or "default").strip()
        if pid in {"default", "admin", "owner", "web_default"}:
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
                f"{self.system_instruction} {CONTEXT_INSTRUCTION} "
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
            return build_live_context(
                base_instruction=f"{self.system_instruction} {CONTEXT_INSTRUCTION}",
                language=self.session.language,
                profile_name="Usuario",
                profile_facts="",
                history=(),
                allowed_tool_names=tool_names,
                policy=LiveContextPolicy(max_chars=self.config.context_char_limit, recent_turns=0),
            )

    def _get_function_declarations(self) -> list[dict]:
        """Extract tool declarations from core tools for Gemini Live."""
        try:
            from core import core_tools
            from core.brain.llm_engine import _tool_schema_from_langchain_tool
            from core.security.tool_policy import get_tool_policy

            tools = core_tools.get_base_tools()
            decls = []
            profile_id = str(self.session.profile_id or "").strip().lower()
            owner_profile = profile_id in {"", "default", "admin", "owner", "web_default"}
            for t in tools:
                schema = _tool_schema_from_langchain_tool(t)
                fn = schema.get("function")
                name = str((fn or {}).get("name") or "")
                policy = get_tool_policy(name)
                if (
                    fn
                    and name in LIVE_VOICE_TOOL_ALLOWLIST
                    and policy.risk_level in {"public", "elevated"}
                    and (owner_profile or policy.risk_level == "public")
                ):
                    decls.append({
                        "name": name,
                        "description": str(fn.get("description", "") or "")[:500],
                        "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                    })
            contextual = context_tool_declaration({declaration["name"] for declaration in decls})
            if contextual:
                decls.append(contextual)
            return decls
        except Exception as exc:
            log_warning(
                "gemini_live_tools_declaration_failed",
                error=type(exc).__name__,
            )
            return []

    def _persist_live_transcript(self, role: str, text: str) -> None:
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return
        try:
            from core import core_tools
            from core.unified_log import write_conversation
            from langchain_core.messages import AIMessage, HumanMessage
            from services.memory_manager import memory_manager

            pid = self.session.profile_id
            message = (
                HumanMessage(content=normalized)
                if role == "user"
                else AIMessage(content=normalized)
            )
            write_conversation(
                "USUARIO" if role == "user" else "JARVIS",
                normalized,
                profile_id=pid,
                channel="gemini_live",
            )
            memory_manager.append_history(pid, [message])
            core_tools.guardar_memoria_async(pid)
        except Exception as exc:
            log_warning(
                "gemini_live_save_transcript_failed",
                error=type(exc).__name__,
                role=role,
            )

    async def start(self) -> None:
        """Start the supervised, privacy-first Gemini Live bridge."""
        if self.config.model_configuration_error:
            await self.session.emit_json({
                "type": "error",
                "code": self.config.model_configuration_error,
                "message": "The configured conversational Live model is invalid.",
            })
            return
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
                            "input_transcription_enabled": self.config.input_transcription_enabled,
                            "output_transcription_enabled": self.config.output_transcription_enabled,
                            "browser_transcription_required": not self.config.input_transcription_enabled,
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
                    response_watchdog_task = asyncio.create_task(self._response_watchdog_loop())
                    self.session.attach_transport_task(send_audio_task)
                    self.session.attach_transport_task(send_media_task)
                    self.session.attach_transport_task(recv_task)
                    self.session.attach_transport_task(response_watchdog_task)

                    await supervise_transport_tasks(
                        [send_audio_task, send_media_task, recv_task, response_watchdog_task],
                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not self._running:
                    break
                self._reset_turn_transcripts()
                self.session.orchestrator.task_context.invalidate()
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
        self._declared_tool_names = {
            str(decl.get("name") or "") for decl in decls if decl.get("name")
        }
        instruction = self._build_live_system_instruction(
            tuple(str(decl.get("name") or "") for decl in decls)
        )
        setup_payload: dict[str, Any] = {
            "model": self.model,
            "realtimeInputConfig": {
                "automaticActivityDetection": {
                    "disabled": False,
                    "startOfSpeechSensitivity": "START_SENSITIVITY_HIGH",
                    "endOfSpeechSensitivity": "END_SENSITIVITY_LOW",
                    "prefixPaddingMs": 40,
                    "silenceDurationMs": 700,
                },
            },
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
        }
        if self.config.context_compression_enabled:
            setup_payload["contextWindowCompression"] = {
                "triggerTokens": self.config.context_compression_trigger_tokens,
                "slidingWindow": {
                    "targetTokens": self.config.context_compression_target_tokens,
                },
            }
        if self.config.input_transcription_enabled:
            setup_payload["inputAudioTranscription"] = {}
        if self.config.output_transcription_enabled:
            setup_payload["outputAudioTranscription"] = {}
        if decls:
            setup_payload["tools"] = [{
                "functionDeclarations": [{**decl, "behavior": "BLOCKING"} for decl in decls],
            }]

        setup_msg = {"setup": setup_payload}
        if self._ws:
            await self._ws.send(json.dumps(setup_msg))

    async def _upstream_audio_loop(self) -> None:
        """Continuously sends incoming client audio chunks to Gemini."""
        while self._running and self._ws:
            try:
                chunk = await self.session.next_audio_chunk()
                try:
                    b64_data = base64.b64encode(chunk).decode("utf-8")
                    media_msg = {
                        "realtimeInput": {
                            "audio": {
                                "mimeType": "audio/pcm;rate=16000",
                                "data": b64_data,
                            }
                        }
                    }
                    async with self._input_send_lock:
                        await self._ws.send(json.dumps(media_msg))
                finally:
                    self.session.audio_task_done(chunk)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise LiveTransportError("gemini_live_connect_failed") from None

    async def _response_watchdog_loop(self) -> None:
        while self._running and self._ws:
            if self.response_watchdog.expired(busy_tools=self.session.has_active_tool_work):
                raise LiveTransportError("gemini_live_turn_timeout")
            await asyncio.sleep(0.25)

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
        if call_id in self._cancelled_tool_call_ids:
            logger.info("gemini_live_tool_response_suppressed code=provider_call_cancelled")
            return
        tool_name = self._tool_call_names.get(call_id, "unknown_tool")
        bounded_result = str(result_text)
        try:
            structured_result = json.loads(bounded_result)
        except (TypeError, ValueError):
            bounded_result = bounded_result[:2000]
        else:
            if isinstance(structured_result, dict):
                if isinstance(structured_result.get("message"), str):
                    structured_result["message"] = structured_result["message"][:2000]
                bounded_result = json.dumps(structured_result, ensure_ascii=False)
            else:
                bounded_result = bounded_result[:2000]
        tool_resp_msg = {
            "toolResponse": {
                "functionResponses": [
                    {
                        "id": call_id,
                        "name": tool_name,
                        "response": {"result": bounded_result},
                    }
                ]
            }
        }
        if self._ws and self._running:
            try:
                await self._ws.send(json.dumps(tool_resp_msg))
            except Exception as exc:
                log_warning(
                    "gemini_live_tool_response_send_failed",
                    error=type(exc).__name__,
                )

    async def _execute_orchestrated_plan(
        self, function_calls: list[dict[str, Any]], *, user_text: str | None = None,
        turn_generation: int | None = None, task_turn_id: int | None = None,
    ) -> None:
        """Execute a planned batch of actions sequentially through the LiveActionOrchestrator."""
        if not self._running or self.session._closed:
            return
        original_text = self._current_user_text if user_text is None else user_text
        generation = self._turn_generation if turn_generation is None else turn_generation
        music_tools = {"reproducir_en_spotify", "reproducir_mix_spotify"}
        if not original_text:
            try:
                await asyncio.wait_for(self._user_text_ready.wait(), timeout=1.5)
            except TimeoutError:
                pass
            if generation == self._turn_generation:
                original_text = self._current_user_text

        if not self._running or self.session._closed:
            return

        from core.command_pipeline.task_context import ContextResolutionError

        context = self.session.orchestrator.task_context
        contextual_calls = [call for call in function_calls if call.get("name") == CONTEXT_TOOL_NAME]
        input_turn = task_turn_id if task_turn_id is not None else self._task_input_turn
        if input_turn is None and original_text:
            input_turn = context.begin_turn(original_text)
            self._task_input_turn = input_turn
        if not original_text or input_turn != context.current_turn:
            logger.info("gemini_live_context_rejected code=context_input_unavailable")
            for call in function_calls:
                await self._send_tool_response(call["id"], context_error_response("context_input_unavailable", self.session.language))
            return
        if contextual_calls:
            for call in contextual_calls:
                try:
                    if len(function_calls) != 1:
                        raise ContextResolutionError("context_ambiguous")
                    proposal = parse_context_proposal(call["args"])
                    await self.session.set_state(LiveSessionState.PROCESSING)
                    await self.session.orchestrator.execute_contextual_call(
                        call["id"], **proposal, turn_id=input_turn, user_text=original_text,
                        allowed_tools=self._declared_tool_names - {CONTEXT_TOOL_NAME},
                        send_response_fn=self._send_tool_response,
                        language=resolve_live_language(original_text, self.session.language),
                    )
                except ContextResolutionError as error:
                    logger.info("gemini_live_context_rejected code=%s", error)
                    await self._send_tool_response(call["id"], context_error_response(str(error), self.session.language))
            return

        from core.brain.tool_manager import _tool_permitida_por_contexto

        grounded_calls = []
        for call in function_calls:
            if call.get("name") in music_tools and (
                not _tool_permitida_por_contexto(call["name"], original_text, "gemini_live")
            ):
                hint = context.followup_hint(input_turn)
                if hint:
                    response = json.loads(context_error_response("context_followup_required", self.session.language))
                    response.update(hint)
                    await self._send_tool_response(call["id"], json.dumps(response))
                    continue
                message = (
                    "No ejecuté música: no pude vincular tu mensaje con una solicitud válida."
                    if resolve_live_language(original_text, self.session.language) == "es"
                    else "I did not play music: I could not link your message to a valid request."
                )
                logger.info("gemini_live_context_rejected code=live_music_intent_required")
                await self._send_tool_response(call["id"], json.dumps({
                    "ok": False, "state": "blocked", "diagnostic_code": "live_music_intent_required",
                    "message": message, "verified": False,
                }))
            else:
                grounded_calls.append(call)
        if not grounded_calls:
            return
        try:
            plan = self.session.orchestrator.enqueue_batch(
                grounded_calls, user_text=original_text, turn_id=input_turn,
                language=resolve_live_language(original_text, self.session.language),
            )
        except ContextResolutionError as error:
            for call in grounded_calls:
                await self._send_tool_response(call["id"], context_error_response(str(error), self.session.language))
            return
        cancelled_before_start = {
            action.call_id
            for action in plan.actions
            if action.call_id in self._cancelled_tool_call_ids
        }
        if cancelled_before_start:
            await self.session.orchestrator.cancel_actions_by_call_ids(
                cancelled_before_start,
                reason="provider_tool_call_cancellation",
            )
        await self.session.set_state(LiveSessionState.PROCESSING)

        for action in list(plan.actions):
            # If session is closed or plan is no longer active, abort
            if not self._running or self.session._closed or not plan.is_active:
                break
            if action.call_id in self._cancelled_tool_call_ids:
                continue
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
            except Exception as exc:
                logger.debug("Action execution error: %s", type(exc).__name__)

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

        activity = data.get("voiceActivity")
        if isinstance(activity, dict):
            activity_type = activity.get("type")
            if activity_type == "ACTIVITY_START" and not self.response_watchdog.speech_active:
                self._typed_input_pending = False
                self._current_user_text = ""
                self._user_text_ready.clear()
                self._turn_generation += 1
                self.session.orchestrator.task_context.revoke_proposals()
                self._task_input_turn = None
                self.response_watchdog.speech_started()
                self.session.diagnostics.record_turn_start()
            elif activity_type == "ACTIVITY_END":
                self.response_watchdog.speech_ended()

        cancellation = data.get("toolCallCancellation")
        if isinstance(cancellation, dict):
            call_ids = [
                str(call_id)[:200]
                for call_id in cancellation.get("ids", [])
                if str(call_id or "").strip()
            ]
            self._cancelled_tool_call_ids.update(call_ids)
            cancelled = await self.session.orchestrator.cancel_actions_by_call_ids(
                call_ids,
                reason="provider_tool_call_cancellation",
                include_running=False,
            )
            await self.session.emit_json(
                {
                    "type": "action_cancelled",
                    "source": "gemini_live",
                    "call_ids": call_ids,
                    "actions": [action.to_dict() for action in cancelled],
                }
            )
            return

        # Handle direct toolCall or inside serverContent
        tool_call = data.get("toolCall")
        server_content = data.get("serverContent")
        if server_content is not None and not isinstance(server_content, dict):
            raise LiveTransportError("gemini_live_protocol_error")
        interrupted = bool((server_content or {}).get("interrupted"))
        if interrupted:
            speech_active = self.response_watchdog.speech_active
            typed_pending = self._typed_input_pending
            keep_input = speech_active or typed_pending
            current_input = self._current_user_text if keep_input else ""
            current_task_turn = self._task_input_turn if keep_input else None
            self._reset_turn_transcripts()
            self._typed_input_pending = typed_pending
            if keep_input:
                self._task_input_turn = current_task_turn
                if speech_active:
                    self.response_watchdog.speech_started()
                if current_input:
                    self.accept_user_transcript(current_input)
            await self.session.interrupt(clear_input_audio=False)
        # Transcription can arrive in the same envelope as a function call.
        # Capture it before scheduling work and snapshot it across async turns.
        input_transcription = (server_content or {}).get("inputTranscription")
        if isinstance(input_transcription, dict) and not self._typed_input_pending:
            text = " ".join(str(input_transcription.get("text") or "").split())
            if text:
                self.accept_user_transcript(text)
                await self.session.emit_json({
                    "type": "transcript", "role": "user", "text": self._current_user_text,
                    "is_final": input_transcription.get("finished", True) is not False,
                    "source": "gemini_live",
                })
                self._persist_live_transcript("user", text)
        if interrupted:
            return
        if not tool_call and server_content:
            model_turn = server_content.get("modelTurn")
            if model_turn:
                for part in model_turn.get("parts", []):
                    if "functionCall" in part:
                        tool_call = {"functionCalls": [part["functionCall"]]}
                        break

        if tool_call and "functionCalls" in tool_call:
            function_calls = []
            rejected_calls = []
            raw_calls = tool_call.get("functionCalls")
            if not isinstance(raw_calls, list):
                raise LiveTransportError("gemini_live_protocol_error")
            for raw_call in raw_calls[:MAX_FUNCTION_CALLS_PER_MESSAGE]:
                if not isinstance(raw_call, dict):
                    continue
                call_id = str(raw_call.get("id") or "").strip()[:200]
                name = str(raw_call.get("name") or "").strip()
                args = raw_call.get("args") or {}
                if call_id and name:
                    self._tool_call_names[call_id] = name
                    while len(self._tool_call_names) > 256:
                        self._tool_call_names.pop(next(iter(self._tool_call_names)))
                try:
                    args_size = len(json.dumps(args, ensure_ascii=False))
                except (TypeError, ValueError):
                    args_size = MAX_FUNCTION_CALL_ARGS_CHARS + 1
                if (
                    call_id
                    and name in self._declared_tool_names
                    and isinstance(args, dict)
                    and args_size <= MAX_FUNCTION_CALL_ARGS_CHARS
                ):
                    function_calls.append(
                        {"id": call_id, "name": name, "args": args}
                    )
                elif call_id:
                    rejected_calls.append(call_id)
            for call_id in rejected_calls:
                await self._send_tool_response(
                    call_id,
                    json.dumps(
                        {
                            "ok": False,
                            "state": "unavailable",
                            "diagnostic_code": "live_tool_not_declared",
                        }
                    ),
                )
            if function_calls:
                orchestrator_task = asyncio.create_task(
                    self._execute_orchestrated_plan(
                        function_calls, user_text=self._current_user_text,
                        turn_generation=self._turn_generation,
                        task_turn_id=self._task_input_turn,
                    )
                )
                self.session.attach_tool_task(orchestrator_task)

        if not server_content:
            return

        if not isinstance(server_content, dict):
            raise LiveTransportError("gemini_live_protocol_error")

        interim_input = server_content.get("interimInputTranscription")
        if isinstance(interim_input, dict):
            text = " ".join(str(interim_input.get("text") or "").split())
            if text:
                await self.session.emit_json(
                    {
                        "type": "transcript",
                        "role": "user",
                        "text": text,
                        "is_final": False,
                        "source": "gemini_live",
                    }
                )

        output_transcription = server_content.get("outputTranscription")
        if isinstance(output_transcription, dict):
            text = str(output_transcription.get("text") or "")
            if text.strip():
                self._begin_output()
            self._current_turn_text = _merge_transcript_text(
                self._current_turn_text,
                text,
            )
            if self._current_turn_text:
                await self.session.emit_json(
                    {
                        "type": "transcript",
                        "role": "assistant",
                        "text": self._current_turn_text,
                        "is_final": False,
                        "source": "gemini_live",
                    }
                )

        model_turn = server_content.get("modelTurn")
        if model_turn:
            output_started = False
            for part in model_turn.get("parts", []):
                pcm_bytes = b""
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

                raw_text = str(part.get("text") or "").strip()
                if part.get("thought") is True:
                    raw_text = ""
                if not pcm_bytes and not raw_text:
                    continue
                self._begin_output()
                if not output_started:
                    self.session.diagnostics.record_first_token()
                    await self.session.set_state(LiveSessionState.SPEAKING)
                    output_started = True
                if pcm_bytes:
                    await self.session.emit_audio_chunk(pcm_bytes)
                if not raw_text:
                    continue
                self._model_turn_text = _merge_transcript_text(
                    self._model_turn_text,
                    raw_text,
                )
                if not self.config.output_transcription_enabled:
                    await self.session.emit_json(
                        {
                            "type": "transcript",
                            "role": "assistant",
                            "text": self._model_turn_text,
                            "is_final": False,
                            "source": "gemini_live",
                        }
                    )

        if server_content.get("generationComplete"):
            await self._finalize_assistant_transcript()
            self._output_active = False
            if self._output_turn_generation is None:
                self._output_turn_generation = self._turn_generation
            if self._output_turn_generation == self._turn_generation:
                self.response_watchdog.generation_complete()
                await self.session.set_state(LiveSessionState.LISTENING)
            await self.session.emit_json({"type": "generation_complete"})

        if server_content.get("waitingForInput"):
            self.response_watchdog.waiting_for_input()
            await self.session.set_state(LiveSessionState.LISTENING)

        if server_content.get("turnComplete"):
            if self._typed_input_pending and self._output_turn_generation is None:
                # A terminal event from interrupted output is not the new text's reply.
                return
            await self._finalize_assistant_transcript()
            if self._output_turn_generation in {None, self._turn_generation}:
                self._reset_turn_transcripts()

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
