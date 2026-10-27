from __future__ import annotations

import asyncio
import io

import numpy as np
import soundfile as sf
from voice.live_hybrid import HybridLiveStreamer
from voice.live_session import LiveSession


def _wav_bytes(sample_rate: int, channels: int = 1) -> bytes:
    samples = np.full((sample_rate, channels), 1000, dtype=np.int16)
    with io.BytesIO() as output:
        sf.write(output, samples, sample_rate, format="WAV", subtype="PCM_16")
        return output.getvalue()


def test_hybrid_piper_audio_is_resampled_to_browser_rate() -> None:
    async def run() -> None:
        session = LiveSession(
            session_id="hybrid-resample",
            send_text=lambda _text: None,
            send_bytes=lambda _audio: None,
        )

        class FakeTTS:
            @staticmethod
            def sintetizar(_text: str) -> bytes:
                return _wav_bytes(22050)

        streamer = HybridLiveStreamer(session, tts_engine=FakeTTS())
        pcm = await streamer._synthesize_to_pcm("Hola")

        assert len(pcm) == 24000 * 2
        assert np.frombuffer(pcm, dtype="<i2").mean() == 1000

    asyncio.run(run())


def test_hybrid_tts_error_log_omits_spoken_text_and_exception(monkeypatch) -> None:
    from voice import live_hybrid

    diagnostics = []
    monkeypatch.setattr(
        live_hybrid,
        "log_warning",
        lambda *args, **kwargs: diagnostics.append((args, kwargs)),
    )

    class FailingTTS:
        @staticmethod
        def sintetizar(_text: str) -> bytes:
            raise RuntimeError("PRIVATE_TTS_ERROR_123")

    async def run() -> None:
        session = LiveSession(
            session_id="hybrid-private-log",
            send_text=lambda _text: None,
            send_bytes=lambda _audio: None,
        )
        streamer = HybridLiveStreamer(session, tts_engine=FailingTTS())
        assert await streamer._synthesize_to_pcm("PRIVATE_SPOKEN_TEXT_123") == b""

    asyncio.run(run())
    serialized = repr(diagnostics)
    assert "PRIVATE_TTS_ERROR_123" not in serialized
    assert "PRIVATE_SPOKEN_TEXT_123" not in serialized


def test_hybrid_stereo_input_is_mixed_to_mono() -> None:
    async def run() -> None:
        session = LiveSession(
            session_id="hybrid-mono",
            send_text=lambda _text: None,
            send_bytes=lambda _audio: None,
        )

        class FakeTTS:
            @staticmethod
            def sintetizar(_text: str) -> bytes:
                return _wav_bytes(24000, channels=2)

        streamer = HybridLiveStreamer(session, tts_engine=FakeTTS())
        pcm = await streamer._synthesize_to_pcm("Hola")

        assert len(pcm) == 24000 * 2

    asyncio.run(run())
