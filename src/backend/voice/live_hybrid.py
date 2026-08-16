"""Hybrid low-latency streaming pipeline for J.A.R.V.I.S.

Provides incremental STT -> Streaming LLM -> Sentence-chunked Piper TTS
for local environments or when Gemini Live is unavailable.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from typing import Any

import soundfile as sf  # type: ignore[reportMissingImports]
from core.runtime_logger import log_error, log_warning

from voice.live_session import LiveSession, LiveSessionState

logger = logging.getLogger(__name__)


class HybridLiveStreamer:
    """Streamer using local or standard LLM + incremental Piper TTS."""

    def __init__(
        self,
        session: LiveSession,
        tts_engine: Any = None,
        command_orchestrator: Any = None,
    ) -> None:
        self.session = session
        self.tts_engine = tts_engine
        self.orchestrator = command_orchestrator
        self._running = False

    async def start(self) -> None:
        """Start the hybrid streamer loop."""
        self._running = True
        await self.session.set_state(LiveSessionState.LISTENING)
        await self.session.emit_json({
            "type": "session_ready",
            "mode": "hybrid_stream",
        })

    async def process_user_text(self, text: str) -> None:
        """Process a spoken or transcribed utterance from the client."""
        if not text.strip() or not self._running:
            return

        task = asyncio.create_task(self._execute_streaming_turn(text))
        self.session.attach_turn_task(task)
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _execute_streaming_turn(self, user_text: str) -> None:
        await self.session.set_state(LiveSessionState.PROCESSING)
        await self.session.emit_json({
            "type": "transcript",
            "role": "user",
            "text": user_text,
            "is_final": True,
        })

        response_text = ""
        if self.orchestrator:
            try:
                from core.command_pipeline.models import CommandRequest
                request = CommandRequest.create(
                    text=user_text,
                    channel="voice",
                    language=self.session.language,
                    profile_id=self.session.profile_id,
                )
                resp = await asyncio.to_thread(self.orchestrator.process, request)
                response_text = getattr(resp, "text", "")
            except Exception as e:
                log_error("hybrid_orchestrator_error", error=str(e))

        if not response_text:
            try:
                from core.brain import processor
                reply_tuple = await asyncio.to_thread(
                    processor.procesar_mensaje,
                    user_text,
                    profile_id=self.session.profile_id,
                )
                response_text = str(reply_tuple[0] or "").strip()
            except Exception as exc:
                log_warning("hybrid_brain_fallback_error", error=str(exc))

        if not response_text:
            response_text = "Entendido, a su disposición."

        await self.session.set_state(LiveSessionState.SPEAKING)
        await self.session.emit_json({
            "type": "transcript",
            "role": "assistant",
            "text": response_text,
            "is_final": True,
        })

        # Incrementally synthesize and stream audio
        await self._stream_synthesized_clauses(response_text)

        await self.session.set_state(LiveSessionState.LISTENING)
        await self.session.emit_json({"type": "turn_complete"})

    async def _stream_synthesized_clauses(self, full_text: str) -> None:
        """Splits text into short clauses and yields PCM chunks rapidly."""
        parts = [p.strip() for p in re.split(r"[.!?\n]+", full_text) if p.strip()]
        if not parts:
            parts = [full_text]

        for clause in parts:
            if not self._running or self.session.state == LiveSessionState.INTERRUPTED:
                break
            pcm_data = await self._synthesize_to_pcm(clause)
            if pcm_data:
                await self.session.emit_audio_chunk(pcm_data)
                # Brief yield to allow event loop and barge-in processing
                await asyncio.sleep(0.01)

    async def _synthesize_to_pcm(self, text: str) -> bytes:
        """Synthesize text via TTS engine and convert to raw 24kHz/16kHz PCM bytes."""
        try:
            tts = self.tts_engine
            if not tts:
                import jarvis_backend
                tts = getattr(jarvis_backend, "tts_engine", None)
            if not tts:
                return b""

            sintetizar_fn = getattr(tts, "sintetizar", None) or getattr(tts, "synthesize", None)
            if not sintetizar_fn:
                return b""

            wav_bytes = await asyncio.to_thread(sintetizar_fn, text)
            if not wav_bytes:
                return b""

            # Extract raw PCM from WAV container
            with io.BytesIO(wav_bytes) as bio:
                data, samplerate = sf.read(bio, dtype="int16")
                return data.tobytes()
        except Exception as e:
            log_warning("hybrid_tts_synthesis_failed", error=str(e), text=text[:30])
            return b""
