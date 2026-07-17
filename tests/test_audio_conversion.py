from __future__ import annotations

import io
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils import audio_conversion
from utils.audio_conversion import (
    AudioConversionError,
    convert_to_ogg_opus,
    convert_to_wav,
)


def _wav_bytes(*, sample_rate: int = 16000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * (sample_rate // 20 * channels))
    return buffer.getvalue()


def _completed(returncode: int = 0):
    return SimpleNamespace(returncode=returncode, stdout=b"", stderr=b"")


@pytest.mark.parametrize("value", [b"", bytearray(), None, "audio"])
def test_conversion_rejects_empty_or_non_bytes_input(value, tmp_path):
    with pytest.raises(AudioConversionError, match=r"^Audio conversion failed\.$"):
        convert_to_wav(value, runtime_dir=tmp_path)


def test_convert_to_wav_uses_hardened_ffmpeg_command_and_cleans_tempdir(
    monkeypatch, tmp_path
):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[-1]).write_bytes(b"RIFF" + b"\x00" * 48)
        return _completed()

    monkeypatch.setattr(audio_conversion.subprocess, "run", fake_run)

    result = convert_to_wav(b"OggS" + b"input", runtime_dir=tmp_path)

    assert result.startswith(b"RIFF")
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:7] == [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
    ]
    assert "pcm_s16le" in command
    assert command[command.index("-ar") + 1] == "16000"
    assert command[command.index("-ac") + 1] == "1"
    assert kwargs == {
        "capture_output": True,
        "timeout": 30,
        "check": False,
        "shell": False,
    }
    assert list(tmp_path.iterdir()) == []


def test_convert_to_ogg_uses_libopus_and_returns_ogg_bytes(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[-1]).write_bytes(b"OggS" + b"\x00" * 32)
        return _completed()

    monkeypatch.setattr(audio_conversion.subprocess, "run", fake_run)

    result = convert_to_ogg_opus(_wav_bytes(), runtime_dir=tmp_path)

    assert result.startswith(b"OggS")
    assert "libopus" in calls[0][0]
    assert calls[0][0][calls[0][0].index("-f") + 1] == "ogg"
    assert list(tmp_path.iterdir()) == []


def test_conversion_retries_matroska_then_tolerant_mode(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if len(commands) == 3:
            Path(command[-1]).write_bytes(b"RIFF" + b"\x00" * 48)
            return _completed()
        return _completed(returncode=1)

    monkeypatch.setattr(audio_conversion.subprocess, "run", fake_run)

    assert convert_to_wav(b"\x1a\x45\xdf\xa3data", runtime_dir=tmp_path).startswith(
        b"RIFF"
    )
    assert len(commands) == 3
    assert commands[1][commands[1].index("-f") + 1] == "matroska"
    assert "-err_detect" in commands[2]
    assert "ignore_err" in commands[2]
    assert "+genpts+discardcorrupt" in commands[2]


@pytest.mark.parametrize(
    "raised",
    [FileNotFoundError("private ffmpeg path"), subprocess.TimeoutExpired("ffmpeg", 30)],
)
def test_conversion_hides_ffmpeg_missing_and_timeout_details(
    monkeypatch, tmp_path, raised
):
    def fake_run(command, **kwargs):
        raise raised

    monkeypatch.setattr(audio_conversion.subprocess, "run", fake_run)

    with pytest.raises(AudioConversionError) as exc_info:
        convert_to_wav(b"OggSdata", runtime_dir=tmp_path)

    assert str(exc_info.value) == "Audio conversion failed."
    assert "private" not in str(exc_info.value)


def test_conversion_translates_runtime_temp_failure(monkeypatch, tmp_path):
    def fail_tempdir(*args, **kwargs):
        raise OSError("private runtime path")

    monkeypatch.setattr(audio_conversion.tempfile, "TemporaryDirectory", fail_tempdir)

    with pytest.raises(AudioConversionError) as exc_info:
        convert_to_wav(b"OggSdata", runtime_dir=tmp_path)

    assert str(exc_info.value) == "Audio conversion failed."


def test_conversion_rejects_empty_or_wrong_magic_output_and_cleans_files(
    monkeypatch, tmp_path
):
    temp_parents = []

    def fake_run(command, **kwargs):
        output_path = Path(command[-1])
        temp_parents.append(output_path.parent)
        output_path.write_bytes(b"not-a-wave")
        return _completed()

    monkeypatch.setattr(audio_conversion.subprocess, "run", fake_run)

    with pytest.raises(AudioConversionError):
        convert_to_wav(b"OggSdata", runtime_dir=tmp_path)

    assert len(temp_parents) == 3
    assert all(not path.exists() for path in temp_parents)


def test_pipeline_keeps_optimized_wav_fast_path(monkeypatch):
    from voice import pipeline

    optimized = _wav_bytes()

    def unexpected_conversion(*args, **kwargs):
        raise AssertionError("FFmpeg must not run for an optimized WAV")

    monkeypatch.setattr(pipeline, "convert_to_wav", unexpected_conversion)

    assert pipeline.normalizar_a_wav(optimized) == (optimized, True)


def test_pipeline_returns_original_audio_when_conversion_fails(monkeypatch):
    from voice import pipeline

    original = b"OggSbroken"

    def fail_conversion(*args, **kwargs):
        raise AudioConversionError("Audio conversion failed.")

    monkeypatch.setattr(pipeline, "convert_to_wav", fail_conversion)

    assert pipeline.normalizar_a_wav(original) == (original, False)


def test_voice_identifier_returns_none_when_conversion_fails(monkeypatch):
    from voice import identifier

    calls = []

    def fail_conversion(*args, **kwargs):
        calls.append((args, kwargs))
        raise AudioConversionError("Audio conversion failed.")

    monkeypatch.setattr(identifier, "convert_to_wav", fail_conversion)
    motor = object.__new__(identifier.VoiceIdentifier)

    assert motor._preprocess_audio_bytes(b"OggS" + b"x" * 128) is None
    assert len(calls) == 1


def test_telegram_sends_converted_ogg_and_checks_http_status(monkeypatch):
    from services import telegram_manager as telegram_module

    class Brain:
        @staticmethod
        def _limpiar_metadatos_voz(text):
            return text

    class Tts:
        @staticmethod
        def sintetizar(text):
            return _wav_bytes()

    class Response:
        def __init__(self):
            self.raise_calls = 0

        def raise_for_status(self):
            self.raise_calls += 1

    response = Response()
    posted = {}

    def fake_post(url, **kwargs):
        posted.update(url=url, **kwargs)
        posted["voice_bytes"] = kwargs["files"]["voice"][1].read()
        return response

    monkeypatch.setattr(telegram_module, "TELEGRAM_TOKEN", "token")
    monkeypatch.setattr(telegram_module, "TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(
        telegram_module, "convert_to_ogg_opus", lambda data: b"OggSencoded"
    )
    monkeypatch.setattr(telegram_module.http_requests, "post", fake_post)
    manager = telegram_module.TelegramManager()
    manager._brain = Brain()
    manager._tts_engine = Tts()

    assert manager.send_message_sync("hello", audio=True) is True
    assert posted["voice_bytes"] == b"OggSencoded"
    assert posted["files"]["voice"][2] == "audio/ogg"
    assert response.raise_calls == 1


def test_telegram_logs_only_exception_type(monkeypatch):
    from services import telegram_manager as telegram_module

    captured = []

    class Brain:
        @staticmethod
        def _limpiar_metadatos_voz(text):
            return text

    class Tts:
        @staticmethod
        def sintetizar(text):
            return _wav_bytes()

    def fail_conversion(data):
        raise AudioConversionError("private path and ffmpeg stderr")

    monkeypatch.setattr(telegram_module, "TELEGRAM_TOKEN", "token")
    monkeypatch.setattr(telegram_module, "TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(telegram_module, "convert_to_ogg_opus", fail_conversion)
    monkeypatch.setattr(
        telegram_module,
        "log_error",
        lambda event, **fields: captured.append((event, fields)),
    )
    manager = telegram_module.TelegramManager()
    manager._brain = Brain()
    manager._tts_engine = Tts()

    assert manager.send_message_sync("hello", audio=True) is False
    assert captured == [
        ("telegram_send_failed", {"error": "AudioConversionError"})
    ]
