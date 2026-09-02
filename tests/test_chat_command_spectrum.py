from __future__ import annotations

import asyncio
import threading

import pytest
from api.chat_routes import ChatRoutesConfig, chat_bp, init_chat_routes
from quart import Quart


def _run_async(awaitable):
    return asyncio.run(awaitable)


class _SyncResponse:
    def __init__(self, response):
        self._response = response

    def __getattr__(self, name):
        return getattr(self._response, name)

    def get_json(self, *args, **kwargs):
        return _run_async(self._response.get_json(*args, **kwargs))

    def get_data(self, *args, **kwargs):
        return _run_async(self._response.get_data(*args, **kwargs))


class _SyncClient:
    def __init__(self, client):
        self._client = client

    def post(self, *args, **kwargs):
        return _SyncResponse(_run_async(self._client.post(*args, **kwargs)))

    def get(self, *args, **kwargs):
        return _SyncResponse(_run_async(self._client.get(*args, **kwargs)))


@pytest.fixture
def sync_client():
    app = Quart(__name__)
    app.config["TESTING"] = True
    init_chat_routes(
        ChatRoutesConfig(
            ip_last_call={},
            ip_last_call_lock=threading.Lock(),
            chat_limit_seconds=0.0,
        )
    )
    app.register_blueprint(chat_bp)
    return _SyncClient(app.test_client())


def test_chat_general_conversation_spanish(sync_client, monkeypatch):
    from core import jarvis_brain

    monkeypatch.setattr(
        jarvis_brain,
        "procesar_mensaje",
        lambda msg, profile_id="": ("Hola Administrador, todos los sistemas se encuentran en línea.", False),
    )
    resp = sync_client.post(
        "/api/chat",
        json={"message": "Hola JARVIS, ¿cuál es tu estado?", "profile_id": "admin"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "Hola Administrador" in data["response"]
    assert data["should_listen"] is False


def test_chat_general_conversation_english(sync_client, monkeypatch):
    from core import jarvis_brain

    monkeypatch.setattr(
        jarvis_brain,
        "procesar_mensaje",
        lambda msg, profile_id="": ("Greetings. All core subroutines are fully operational.", False),
    )
    resp = sync_client.post(
        "/api/chat",
        json={"message": "Hello Jarvis, give me a status report", "profile_id": "admin"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "Greetings" in data["response"]


def test_chat_spotify_playback_command(sync_client, monkeypatch):
    from core import jarvis_brain

    monkeypatch.setattr(
        jarvis_brain,
        "procesar_mensaje",
        lambda msg, profile_id="": ("Reproduciendo 'I Wonder' de Kanye West en Spotify Desktop.", False),
    )
    resp = sync_client.post(
        "/api/chat",
        json={"message": "Pon la canción I Wonder de Kanye West", "profile_id": "admin"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "Reproduciendo 'I Wonder'" in data["response"]


def test_chat_spotify_queue_command(sync_client, monkeypatch):
    from core import jarvis_brain

    monkeypatch.setattr(
        jarvis_brain,
        "procesar_mensaje",
        lambda msg, profile_id="": ("Añadido a la cola en Spotify Desktop: 'Stronger' de Kanye West.", False),
    )
    resp = sync_client.post(
        "/api/chat",
        json={"message": "Añade Stronger de Kanye West a la cola de reproducción", "profile_id": "admin"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "Añadido a la cola" in data["response"]


def test_chat_spotify_like_command(sync_client, monkeypatch):
    from core import jarvis_brain

    monkeypatch.setattr(
        jarvis_brain,
        "procesar_mensaje",
        lambda msg, profile_id="": ("Guardé 'I Wonder' en tus Me Gusta de Spotify Desktop.", False),
    )
    resp = sync_client.post(
        "/api/chat",
        json={"message": "Dale like a la canción actual", "profile_id": "admin"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "Guardé" in data["response"]


def test_chat_system_routine_command(sync_client, monkeypatch):
    from core import jarvis_brain

    monkeypatch.setattr(
        jarvis_brain,
        "procesar_mensaje",
        lambda msg, profile_id="": ("Iniciando rutina de trabajo: abriendo editor y cargando métricas.", False),
    )
    resp = sync_client.post(
        "/api/chat",
        json={"message": "Iniciar rutina de trabajo", "profile_id": "admin"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "rutina de trabajo" in data["response"]


def test_chat_memory_storage_and_recall(sync_client, monkeypatch):
    from core import jarvis_brain

    monkeypatch.setattr(
        jarvis_brain,
        "procesar_mensaje",
        lambda msg, profile_id="": ("Entendido, he guardado en memoria que tu proyecto principal es J.A.R.V.I.S.", False),
    )
    resp = sync_client.post(
        "/api/chat",
        json={"message": "Recuerda que mi proyecto principal es J.A.R.V.I.S.", "profile_id": "admin"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "guardado en memoria" in data["response"]


def test_chat_empty_message_returns_error(sync_client):
    resp = sync_client.post(
        "/api/chat",
        json={"message": "   ", "profile_id": "admin"},
    )
    assert resp.status_code in (400, 422)
    data = resp.get_json()
    assert "error" in data


def test_chat_message_length_limit(sync_client):
    huge_message = "x" * 5000
    resp = sync_client.post(
        "/api/chat",
        json={"message": huge_message, "profile_id": "admin"},
    )
    assert resp.status_code in (400, 413, 422)


def test_chat_unicode_and_emojis_support(sync_client, monkeypatch):
    from core import jarvis_brain

    monkeypatch.setattr(
        jarvis_brain,
        "procesar_mensaje",
        lambda msg, profile_id="": ("¡Saludos! 🚀 Sistemas funcionando al 100% ⚡", False),
    )
    resp = sync_client.post(
        "/api/chat",
        json={"message": "Hola Jarvis 🤖 ¿cómo va todo? 🔥", "profile_id": "admin"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "🚀" in data["response"]


def test_chat_streaming_endpoint(sync_client, monkeypatch):
    from core import jarvis_brain

    def fake_stream_events(message, profile_id=""):
        yield {"type": "token", "content": "Hola"}
        yield {"type": "token", "content": " Administrador"}
        yield {"type": "final", "response": "Hola Administrador", "should_listen": False}

    monkeypatch.setattr(
        jarvis_brain,
        "stream_procesar_mensaje_events",
        fake_stream_events,
    )
    resp = sync_client.post(
        "/api/chat/stream",
        json={"message": "Dame una respuesta paso a paso", "profile_id": "admin"},
    )
    assert resp.status_code == 200
    body = resp.get_data().decode("utf-8")
    assert "data: " in body
    assert "Hola" in body
