"""Inbound Telegram bot: startup wiring, idempotent start, and voice notes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def _manager(monkeypatch):
    from services import telegram_manager as telegram_module

    monkeypatch.setattr(telegram_module, "TELEGRAM_ENABLED", True)
    monkeypatch.setattr(telegram_module, "TELEGRAM_AVAILABLE", True)
    monkeypatch.setattr(telegram_module, "TELEGRAM_TOKEN", "token")
    monkeypatch.setattr(telegram_module, "TELEGRAM_CHAT_ID", "123")
    return telegram_module, telegram_module.TelegramManager()


def test_start_is_idempotent(monkeypatch):
    telegram_module, manager = _manager(monkeypatch)
    created = []

    class FakeThread:
        def __init__(self, target, daemon):
            created.append(target)
            self._alive = False

        def start(self):
            self._alive = True

        def is_alive(self):
            return self._alive

    monkeypatch.setattr(telegram_module.threading, "Thread", FakeThread)

    manager.start()
    manager.start()

    assert len(created) == 1


class _Voice:
    def __init__(self, payload: bytes):
        self._payload = payload

    async def get_file(self):
        payload = self._payload

        class _File:
            async def download_to_drive(self, path):
                with open(path, "wb") as handle:
                    handle.write(payload)

        return _File()


class _Message:
    def __init__(self, voice):
        self.voice = voice
        self.chat_id = 123
        self.replies: list[str] = []

    async def reply_text(self, text):
        self.replies.append(text)


def _voice_update(payload: bytes = b"OggSvoice"):
    message = _Message(_Voice(payload))
    return SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=123)), message


def _silence_sync_hub(monkeypatch):
    from core import device_sync

    monkeypatch.setattr(device_sync.sync_hub, "broadcast_sync", lambda *a, **k: None)


def test_voice_note_uses_transcription_service(monkeypatch):
    telegram_module, manager = _manager(monkeypatch)
    _silence_sync_hub(monkeypatch)
    converted = []
    monkeypatch.setattr(
        telegram_module,
        "convert_to_wav",
        lambda data: converted.append(data) or b"RIFFwav",
    )

    transcribed = []

    class Transcriber:
        def transcribe(self, audio_bytes, transcript_hint, transcript_confidence=None, *, route_mode="secure", language="en"):
            transcribed.append((audio_bytes, transcript_hint))
            return SimpleNamespace(text="hola jarvis", source="local")

    received = []

    class Brain:
        def procesar_mensaje(self, text, profile_id):
            received.append((text, profile_id))
            return "respuesta", False

    manager.inject_dependencies(Brain(), None, None, None, transcriber=Transcriber())
    update, message = _voice_update(b"OggSvoice")

    asyncio.run(manager._voz_handler(update, None))

    assert converted == [b"OggSvoice"]
    assert transcribed == [(b"RIFFwav", "")]
    assert received == [("hola jarvis", "telegram_user")]
    assert message.replies == ["📝 hola jarvis\n\nrespuesta"]


def test_voice_note_without_transcriber_replies_unavailable(monkeypatch):
    _telegram_module, manager = _manager(monkeypatch)
    _silence_sync_hub(monkeypatch)

    class Brain:
        def procesar_mensaje(self, text, profile_id):
            raise AssertionError("brain must not be called")

    manager.inject_dependencies(Brain(), None, None, None)
    update, message = _voice_update()

    asyncio.run(manager._voz_handler(update, None))

    assert len(message.replies) == 1
    assert "voz" in message.replies[0] or "voice" in message.replies[0]


@pytest.mark.parametrize("enabled", [True, False])
def test_backend_starts_telegram_bot_only_when_enabled(monkeypatch, enabled):
    import jarvis_backend
    from services.telegram_manager import telegram_manager

    calls = []
    monkeypatch.setattr(
        telegram_manager,
        "inject_dependencies",
        lambda brain, whisper_model, tts_engine, obs_event, *, transcriber=None: calls.append(
            ("inject", brain, transcriber)
        ),
    )
    monkeypatch.setattr(telegram_manager, "start", lambda: calls.append(("start",)))

    jarvis_backend._start_telegram_bot(enabled=enabled)

    if enabled:
        assert calls == [
            ("inject", jarvis_backend.jarvis_brain, jarvis_backend.transcription_service),
            ("start",),
        ]
    else:
        assert calls == []
