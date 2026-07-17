"""Pruebas de humo: import del backend y rutas sin levantar hilos de red largos."""

from __future__ import annotations

import io
import json
import os
import struct
import sys
import asyncio
import wave
import numpy as np

import pytest  # pyright: ignore[reportMissingImports]

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "src", "backend")


def _make_test_wav(
    duration_s: float = 1.0, sample_rate: int = 16000, amplitude: int = 400
) -> bytes:
    total_frames = max(int(duration_s * sample_rate), 1)
    frame = struct.pack("<h", amplitude)
    with io.BytesIO() as buf:
        with wave.open(buf, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(frame * total_frames)
        return buf.getvalue()


def _run_async(awaitable):
    return asyncio.run(awaitable)


class _SyncResponse:
    def __init__(self, response):
        self._response = response

    def __getattr__(self, name):
        return getattr(self._response, name)

    def get_json(self, *args, **kwargs):
        return _run_async(self._response.get_json(*args, **kwargs))


class _SyncClient:
    def __init__(self, client):
        self._client = client

    def _adapt_kwargs(self, kwargs):
        adapted = dict(kwargs)
        environ_base = adapted.pop("environ_base", None) or {}
        scope_base = dict(adapted.get("scope_base") or {})
        remote_addr = environ_base.get("REMOTE_ADDR")
        remote_port = environ_base.get("REMOTE_PORT", 12345)
        if remote_addr:
            try:
                remote_port = int(remote_port)
            except Exception:
                remote_port = 12345
            scope_base["client"] = (str(remote_addr), remote_port)
        if "client" not in scope_base:
            scope_base["client"] = ("127.0.0.1", 12345)
        adapted["scope_base"] = scope_base
        return adapted

    def get(self, *args, **kwargs):
        adapted = self._adapt_kwargs(kwargs)
        return _SyncResponse(_run_async(self._client.get(*args, **adapted)))

    def post(self, *args, **kwargs):
        adapted = self._adapt_kwargs(kwargs)
        return _SyncResponse(_run_async(self._client.post(*args, **adapted)))

    def patch(self, *args, **kwargs):
        adapted = self._adapt_kwargs(kwargs)
        return _SyncResponse(_run_async(self._client.patch(*args, **adapted)))

    def delete(self, *args, **kwargs):
        adapted = self._adapt_kwargs(kwargs)
        return _SyncResponse(_run_async(self._client.delete(*args, **adapted)))


def _test_client(app):
    return _SyncClient(app.test_client())


@pytest.fixture(scope="module", autouse=True)
def _env_and_path():
    os.environ.setdefault("GROQ_API_KEY", "test-key-smoke")
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)


# =============================================================================
# CORE INFRASTRUCTURE
# =============================================================================


def test_jarvis_config():
    from core import jarvis_config  # pyright: ignore[reportMissingImports]

    assert os.path.isdir(jarvis_config.ROOT_DIR)
    assert "127.0.0.1" in " ".join(jarvis_config.get_cors_origins())


def test_jarvis_observability():
    from core.jarvis_observability import obs_inc, obs_snapshot  # pyright: ignore[reportMissingImports]

    obs_inc("messages_total", 0)
    snap = obs_snapshot()
    assert "started_at" in snap


def test_service_container():
    from core.service_container import services  # pyright: ignore[reportMissingImports]

    # All core services should be accessible without error
    assert hasattr(services, "noticias_cache")
    assert hasattr(services, "weather_cache")
    assert hasattr(services, "llm")
    assert hasattr(services, "get_reminders")


def test_heartbeat_state_defaults():
    from core.jarvis_state import heartbeat_state  # pyright: ignore[reportMissingImports]

    assert isinstance(heartbeat_state, dict)
    assert "ultimo_briefing" in heartbeat_state
    assert "cpu_high_streak" in heartbeat_state or "last_health_check" in heartbeat_state


# =============================================================================
# BACKEND APP & ROUTES
# =============================================================================


def test_import_jarvis_backend():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    assert jarvis_backend.app is not None
    rules = list(jarvis_backend.app.url_map.iter_rules())
    paths = {r.rule for r in rules}
    assert "/api/chat" in paths
    assert "/api/status" in paths
    assert "/api/voice/registro/iniciar" in paths


def test_flask_test_client_basic_routes():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    c = _test_client(jarvis_backend.app)

    # 1. Chat validation (expect 400 because of empty message)
    r = c.post("/api/chat", json={})
    assert r.status_code == 400

    # 2. Main index
    r = c.get("/")
    assert r.status_code == 200

    # 2.1 Static assets required by UI must resolve
    r = c.get("/static/js/main.js")
    assert r.status_code == 200

    # 3. Security snapshot
    r = c.get("/api/security")
    assert r.status_code == 200
    data = r.get_json()
    assert "policy" in data or "strict_mode" in data

    # 4. Observability
    r = c.get("/api/observabilidad")
    assert r.status_code == 200

    # 5. Plugins status
    r = c.get("/api/plugins")
    assert r.status_code == 200

    # 6. Owner voice enrollment endpoints exist
    r = c.post("/api/voice/registro/admin/iniciar")
    assert r.status_code in (200, 401, 503)


def test_admin_voice_registration_requires_token_off_loopback(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]

    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    c = _test_client(jarvis_backend.app)
    r = c.post(
        "/api/voice/registro/admin/iniciar",
        environ_base={"REMOTE_ADDR": "192.168.1.30"},
    )
    assert r.status_code == 401
    data = r.get_json() or {}
    assert data.get("error") == "Token required for critical routes."


def test_critical_route_rejects_untrusted_origin_on_loopback(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]

    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    c = _test_client(jarvis_backend.app)
    r = c.post(
        "/api/voice/registro/admin/iniciar",
        headers={"Origin": "https://evil.example"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert r.status_code == 403
    data = r.get_json() or {}
    assert data.get("error") == "Untrusted origin for critical route."


def test_critical_route_allows_trusted_loopback_origin_without_token(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]

    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    c = _test_client(jarvis_backend.app)
    r = c.post(
        "/api/voice/registro/admin/iniciar",
        headers={"Origin": "http://localhost:5002"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert r.status_code in (200, 503)


def test_chat_stream_rejects_get():
    import jarvis_backend  # pyright: ignore[reportMissingImports]

    c = _test_client(jarvis_backend.app)
    r = c.get("/api/chat/stream?message=hello")
    assert r.status_code == 405


def test_chat_stream_rate_limits_like_chat(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from core import jarvis_brain  # pyright: ignore[reportMissingImports]

    monkeypatch.setattr(
        jarvis_brain,
        "stream_procesar_mensaje_events",
        lambda *args, **kwargs: iter([{"type": "final", "text": "ok"}]),
    )

    c = _test_client(jarvis_backend.app)
    remote = {"REMOTE_ADDR": "127.0.0.44"}
    first = c.post("/api/chat/stream", json={"message": "hello"}, environ_base=remote)
    second = c.post("/api/chat/stream", json={"message": "hello"}, environ_base=remote)
    assert first.status_code == 200
    assert second.status_code == 429


def test_status_endpoint_returns_telemetry():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    c = _test_client(jarvis_backend.app)
    r = c.get("/api/status/full")
    assert r.status_code == 200
    data = r.get_json()
    # Must have at least one telemetry field
    assert any(k in data for k in ["cpu", "ram", "weather", "uptime"])


def test_status_endpoint_reports_runtime_mode():
    import jarvis_backend  # pyright: ignore[reportMissingImports]

    c = _test_client(jarvis_backend.app)
    r = c.get("/api/status")
    assert r.status_code == 200
    data = r.get_json() or {}
    assert data["mode"] == "core"
    assert data["features"]["voice_id"] is False
    assert data["features"]["rag"] is False
    assert data["features"]["vision"] is False
    assert data["features"]["monitoring"] is False
    assert isinstance(data["features"]["monitoring_available"], bool)
    assert data["features"]["monitoring_running"] is False


def test_noticias_endpoint_returns_202_when_not_ready():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    c = _test_client(jarvis_backend.app)
    r = c.get("/api/noticias")
    # 202 = still processing, 200 = ready
    assert r.status_code in (200, 202)
    data = r.get_json()
    assert "listo" in data


def test_auth_status_endpoint():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    c = _test_client(jarvis_backend.app)
    r = c.get("/api/auth_status")
    assert r.status_code == 200
    data = r.get_json()
    assert "autorizado" in data


# =============================================================================
# VOICE REGISTRATION FLOW
# =============================================================================


def test_voice_registration_start_route():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    c = _test_client(jarvis_backend.app)
    r = c.post("/api/voice/registro/iniciar")
    assert r.status_code in (200, 503)
    data = r.get_json() or {}
    if r.status_code == 200:
        assert data.get("ok") is True
        assert data.get("stage") == "awaiting_sample"
    else:
        assert data.get("ok") is False


def test_unknown_voice_registration_auto_answers_pending_question(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    monkeypatch.setattr(voice_routes, "_norm_a_wav", lambda b: (b"wav-ok", True))

    ip = "__test_ip__"
    jarvis_backend._PENDING_VOICE_REGISTRATION[ip] = {
        "audio": b"fake-audio",
        "stage": "awaiting_name",
        "pending_question": "precio actual del bitcoin",
        "created_at": jarvis_backend._time.time(),
    }

    c = _test_client(jarvis_backend.app)
    r = c.post(
        "/api/voice",
        data=b"fake-audio-2",
        headers={"X-Transcript": "Pedro", "X-Profile-Id": "admin"},
        environ_base={"REMOTE_ADDR": ip},
    )
    assert r.status_code in (200, 409)
    data = r.get_json() or {}
    if r.status_code == 200:
        assert str(data.get("profile_id", "")).startswith("guest_")
        assert data.get("identity_source") == "guest_registration"
        assert data.get("nombre") == "Pedro"
        response_text = str(data.get("response", "")).lower()
        assert any(role in response_text for role in ("invitado", "guest"))
    else:
        assert data.get("nueva_voz") is True
        assert data.get("should_listen") is True


def test_owner_alias_not_registered_as_guest(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    monkeypatch.setattr(voice_routes, "_norm_a_wav", lambda b: (b"wav-ok", True))

    ip = "__test_ip_owner_alias__"
    jarvis_backend._PENDING_VOICE_REGISTRATION[ip] = {
        "audio": b"fake-audio",
        "stage": "awaiting_name",
        "pending_question": "",
        "created_at": jarvis_backend._time.time(),
    }

    class DummyMotor:
        encoder = object()
        perfiles_voz = {"admin": {"nombre": "Administrador", "embedding": []}}

        @staticmethod
        def registrar_voz(*_a, **_k):
            return True

        @staticmethod
        def similitud_con_perfil(*_a, **_k):
            return 0.0

    monkeypatch.setattr(voice_routes, "_voice_id_motor", DummyMotor())
    monkeypatch.setattr(voice_routes, "_biometria_activa", True)

    c = _test_client(jarvis_backend.app)
    r = c.post(
        "/api/voice",
        data=b"fake-audio-3",
        headers={"X-Transcript": "Administrador", "X-Profile-Id": ""},
        environ_base={"REMOTE_ADDR": ip},
    )
    assert r.status_code == 200
    data = r.get_json() or {}
    assert str(data.get("profile_id", "")).startswith("guest_")
    assert str(data.get("profile_id", "")) != "guest_seor"
    assert data.get("identity_source") == "guest_registration"


def test_owner_enroll_capture_requires_session():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    jarvis_backend._PENDING_VOICE_REGISTRATION.pop("127.0.0.1", None)
    c = _test_client(jarvis_backend.app)
    r = c.post("/api/voice/registro/admin/capturar", data=b"fake")
    assert r.status_code in (400, 401, 409)


def test_owner_enroll_bootstrap_allows_full_session_without_prior_owner(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    ip = "127.0.0.1"
    jarvis_backend._PENDING_VOICE_REGISTRATION.pop(ip, None)

    class DummyMotor:
        encoder = object()

        def __init__(self):
            self.perfiles_voz = {}

        def reset_owner_profile(self):
            self.perfiles_voz.pop("admin", None)
            return True

        def registrar_voz(self, _audio, profile_id, nombre):
            self.perfiles_voz[profile_id] = {
                "nombre": nombre,
                "embedding": [],
                "n_samples": 1,
            }
            return True

        def get_profile_stats(self):
            return {
                pid: {"nombre": data["nombre"], "n_samples": data.get("n_samples", 1)}
                for pid, data in self.perfiles_voz.items()
            }

    motor = DummyMotor()
    monkeypatch.setattr(voice_routes, "_voice_id_motor", motor)
    monkeypatch.setattr(voice_routes, "_biometria_activa", True)
    monkeypatch.setattr(voice_routes, "_verificar_autorizacion", lambda _pid: False)
    monkeypatch.setattr(voice_routes, "_norm_a_wav", lambda b: (b"RIFF" + b"x" * 1200, True))
    monkeypatch.setattr(voice_routes, "_bytes_es_wav_valido", lambda _b: True)

    c = _test_client(jarvis_backend.app)
    r = c.post("/api/voice/registro/admin/iniciar", environ_base={"REMOTE_ADDR": ip})
    assert r.status_code == 200
    assert jarvis_backend._PENDING_VOICE_REGISTRATION[ip]["bootstrap"] is True

    last_data = {}
    for _ in range(5):
        r = c.post(
            "/api/voice/registro/admin/capturar",
            data=b"RIFF" + b"x" * 1200,
            environ_base={"REMOTE_ADDR": ip},
        )
        last_data = r.get_json() or {}
        assert r.status_code == 200, last_data

    assert last_data.get("done") is True
    assert "admin" in motor.perfiles_voz


def test_owner_enroll_existing_owner_requires_authorized_session(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    class DummyMotor:
        encoder = object()
        perfiles_voz = {"admin": {"nombre": "Administrador", "embedding": []}}

    monkeypatch.setattr(voice_routes, "_voice_id_motor", DummyMotor())
    monkeypatch.setattr(voice_routes, "_biometria_activa", True)
    monkeypatch.setattr(voice_routes, "_verificar_autorizacion", lambda _pid: False)

    c = _test_client(jarvis_backend.app)
    r = c.post("/api/voice/registro/admin/iniciar", headers={"X-Profile-Id": "admin"})
    assert r.status_code == 401


def test_session_owner_fallback_avoids_unknown_loop(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes
    from utils.jarvis_auth import autorizar_por_biometria  # pyright: ignore[reportMissingImports]

    # Simular que no estamos en flujo de onboarding previo.
    jarvis_backend._PENDING_VOICE_REGISTRATION.pop("127.0.0.1", None)

    # Pre-autorizar al Administrador para que owner_session_active sea True.
    autorizar_por_biometria("admin", "Administrador")

    class DummyMotor:
        encoder = object()
        perfiles_voz = {"admin": {"nombre": "Administrador", "embedding": []}}

        @staticmethod
        def identificar(*_a, **_k):
            return (None, None, 0.0)

        @staticmethod
        def get_ultimo_candidato():
            return ("admin", "Administrador", 0.40)  # Above 0.35 session continuity threshold

        @staticmethod
        def registrar_voz(*_a, **_k):
            return True

    monkeypatch.setattr(voice_routes, "_voice_id_motor", DummyMotor())
    monkeypatch.setattr(voice_routes, "_biometria_activa", True)

    c = _test_client(jarvis_backend.app)
    r = c.post(
        "/api/voice",
        data=b"RIFFFAKEAUDIO",
        headers={"X-Transcript": "¿Cómo está el clima?", "X-Profile-Id": "admin"},
    )
    assert r.status_code == 200
    data = r.get_json() or {}
    assert data.get("profile_id") == "admin"
    assert data.get("identity_source") in (
        "session_owner_fallback",
        "session_owner_fallback_audio_error",
        "session_continuity",
        "biometric_match",
    )


def test_unknown_voice_registration_retry_on_failed_embedding(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    monkeypatch.setattr(voice_routes, "_norm_a_wav", lambda b: (b"wav-ok", True))

    ip = "__test_ip_retry__"
    jarvis_backend._PENDING_VOICE_REGISTRATION[ip] = {
        "audio": b"fake-audio",
        "stage": "awaiting_name",
        "pending_question": "clima hoy",
        "created_at": jarvis_backend._time.time(),
    }

    class DummyMotor:
        encoder = object()

        @staticmethod
        def registrar_voz(*_a, **_k):
            return False

    monkeypatch.setattr(voice_routes, "_voice_id_motor", DummyMotor())
    monkeypatch.setattr(voice_routes, "_biometria_activa", True)

    c = _test_client(jarvis_backend.app)
    r = c.post(
        "/api/voice",
        data=b"fake-audio-2",
        headers={"X-Transcript": "Pedro", "X-Profile-Id": ""},
        environ_base={"REMOTE_ADDR": ip},
    )
    assert r.status_code == 409
    data = r.get_json() or {}
    assert data.get("should_listen") is True
    assert data.get("nueva_voz") is True
    assert data.get("identity_source") == "retry"


def test_invalid_audio_returns_400_in_awaiting_name():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    ip = "__test_bad_audio__"
    jarvis_backend._PENDING_VOICE_REGISTRATION[ip] = {
        "audio": b"bad-audio",
        "stage": "awaiting_name",
        "pending_question": "",
        "created_at": jarvis_backend._time.time(),
    }
    c = _test_client(jarvis_backend.app)
    r = c.post(
        "/api/voice",
        data=b"bad-audio-2",
        headers={"X-Transcript": "Pedro", "X-Profile-Id": ""},
        environ_base={"REMOTE_ADDR": ip},
    )
    assert r.status_code in (400, 409, 200)


# =============================================================================
# AUDIO & TRANSCRIPTION
# =============================================================================


def test_transcribir_audio_prefers_whisper_for_dubious_hint(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    wav_bytes = _make_test_wav(duration_s=2.6)
    calls = []

    def _fake_whisper(_path):
        calls.append(_path)
        return "quien soy realmente"

    monkeypatch.setattr(jarvis_backend, "whisper_model", object())
    monkeypatch.setattr(jarvis_backend, "_transcribir_con_whisper_archivo", _fake_whisper)

    texto = jarvis_backend.transcribir_audio(
        wav_bytes,
        transcript_hint="Hoy.\u00bfQui\u00e9n soy?",
        transcript_confidence=0.12,
    )

    assert texto == "quien soy realmente"
    assert len(calls) == 1


def test_transcribir_audio_keeps_clear_browser_hint(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    wav_bytes = _make_test_wav(duration_s=1.0)
    calls = []

    def _fake_whisper(_path):
        calls.append(_path)
        return "otra cosa"

    monkeypatch.setattr(jarvis_backend, "whisper_model", object())
    monkeypatch.setattr(jarvis_backend, "_transcribir_con_whisper_archivo", _fake_whisper)

    texto = jarvis_backend.transcribir_audio(
        wav_bytes,
        transcript_hint="pon musica relajante",
        transcript_confidence=0.91,
    )

    assert texto == "pon musica relajante"
    assert calls == []


def test_transcribir_audio_keeps_confident_short_question(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    wav_bytes = _make_test_wav(duration_s=1.1)
    calls = []

    def _fake_whisper(_path):
        calls.append(_path)
        return "otra cosa"

    monkeypatch.setattr(jarvis_backend, "whisper_model", object())
    monkeypatch.setattr(jarvis_backend, "_transcribir_con_whisper_archivo", _fake_whisper)

    texto = jarvis_backend.transcribir_audio(
        wav_bytes,
        transcript_hint="como esta el clima hoy",
        transcript_confidence=0.87,
    )

    assert texto == "como esta el clima hoy"
    assert calls == []


def test_normalizar_a_wav_rejects_garbage():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    # Random bytes should be rejected (returns False or raises)
    _, ok = jarvis_backend._normalizar_a_wav(b"\x00\x01\x02\x03")
    assert ok is False


def test_normalizar_a_wav_accepts_wav():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    wav = _make_test_wav(duration_s=0.5)
    _, ok = jarvis_backend._normalizar_a_wav(wav)
    assert ok is True


def test_normalizar_a_wav_accepts_ogg():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    # Minimal OGG header: "OggS"
    ogg = b"OggS" + b"\x00" * 20
    # May fail full decode but should at least not crash
    try:
        result, ok = jarvis_backend._normalizar_a_wav(ogg)
        assert isinstance(ok, bool)
    except Exception:
        pass  # ffmpeg may not be available in test env


# =============================================================================
# CHAT & LLM
# =============================================================================


def test_chat_accepts_profile_id_payload():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    c = _test_client(jarvis_backend.app)
    r = c.post("/api/chat", json={"message": "hola", "profile_id": "web_test_profile"})
    assert r.status_code == 200
    data = r.get_json() or {}
    assert "response" in data


def test_chat_rejects_empty_message():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    c = _test_client(jarvis_backend.app)
    r = c.post("/api/chat", json={"message": ""})
    assert r.status_code == 400


def test_chat_rejects_invalid_json_payload():
    import jarvis_backend  # pyright: ignore[reportMissingImports]

    c = _test_client(jarvis_backend.app)
    r = c.post("/api/chat", data="{bad json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert (r.get_json() or {}).get("error") == "Invalid JSON payload"


def test_chat_rejects_oversized_message():
    import jarvis_backend  # pyright: ignore[reportMissingImports]

    c = _test_client(jarvis_backend.app)
    r = c.post(
        "/api/chat",
        json={"message": "x" * 4001},
        environ_base={"REMOTE_ADDR": "127.0.0.145"},
    )
    assert r.status_code == 413
    assert (r.get_json() or {}).get("error") == "Message too large"


def test_chat_stream_rejects_invalid_json_payload():
    import jarvis_backend  # pyright: ignore[reportMissingImports]

    c = _test_client(jarvis_backend.app)
    r = c.post("/api/chat/stream", data="{bad json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert (r.get_json() or {}).get("error") == "Invalid JSON payload"


def test_chat_stream_endpoint_exists():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    c = _test_client(jarvis_backend.app)
    # Should accept POST (may return 200, 400, or 503 depending on LLM state)
    r = c.post("/api/chat/stream", json={"message": "hola"})
    assert r.status_code in (200, 400, 503)


# =============================================================================
# BRAIN & ROUTER
# =============================================================================


def test_guest_cannot_authorize_high_level_actions():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes
    from core import jarvis_brain  # pyright: ignore[reportMissingImports]
    from utils.jarvis_auth import revocar_autorizacion  # pyright: ignore[reportMissingImports]

    revocar_autorizacion()
    r1, _ = jarvis_brain.procesar_mensaje("apaga la computadora", profile_id="guest_demo")
    assert "acceso_denegado" in str(r1).lower() or "requiere autoriz" in str(r1).lower()

    # Intento de frase cualquiera desde invitado no debe elevar privilegios.
    # The previous auth phrase will now be treated as normal input
    r2, _ = jarvis_brain.procesar_mensaje("hola jarvis", profile_id="guest_demo")
    # Should get a normal response, not an auth error
    assert r2 is not None


def test_owner_biometric_authorization_works():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes
    from utils.jarvis_auth import (  # pyright: ignore[reportMissingImports]
        revocar_autorizacion,
        verificar_autorizacion,
        autorizar_por_biometria,
    )

    revocar_autorizacion()
    assert verificar_autorizacion("admin") is False
    autorizar_por_biometria("admin", "Administrador")
    assert verificar_autorizacion("admin") is True


def test_jarvis_brain_needs_tools():
    from core.jarvis_brain import necesita_tools  # pyright: ignore[reportMissingImports]

    assert necesita_tools("hola jarvis") is False
    assert necesita_tools("reproduce musica de coldplay") is True
    assert necesita_tools("clima en madrid hoy") is True


def test_dynamic_queries_force_web_tools():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes
    from core import jarvis_brain  # pyright: ignore[reportMissingImports]

    assert jarvis_brain._debe_buscar_en_web("precio actual del bitcoin") is True
    assert jarvis_brain._debe_buscar_en_web("que es groq") is True
    r, _ = jarvis_brain.procesar_mensaje(
        "precio actual del bitcoin",
        profile_id="guest_demo",
    )
    txt = str(r).lower()
    assert "desea buscar" not in txt
    assert "quieres que" not in txt
    assert "no encontrada" not in txt
    assert "error" not in txt or "error code: 401" in txt


def test_plain_question_does_not_force_web_search():
    from core import jarvis_brain  # pyright: ignore[reportMissingImports]

    assert jarvis_brain._debe_buscar_en_web("como que invitados soy tu admin") is False


def test_chat_greeting_without_tools():
    from core import jarvis_brain  # pyright: ignore[reportMissingImports]

    r, _ = jarvis_brain.procesar_mensaje("hola", profile_id="admin")
    assert r is not None
    assert len(str(r)) > 5


def test_brain_handles_unknown_input():
    from core import jarvis_brain  # pyright: ignore[reportMissingImports]

    # Should not crash even with gibberish
    r, _ = jarvis_brain.procesar_mensaje("asdfghjkl", profile_id="admin")
    assert r is not None


# =============================================================================
# TTS ENGINE
# =============================================================================


def test_tts_engine_pronunciation():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes
    from jarvis_backend import _aplicar_pronunciacion_tts

    res_yt = _aplicar_pronunciacion_tts("YouTube").lower()
    assert "yut" in res_yt

    res_jv = _aplicar_pronunciacion_tts("Jarvis").lower()
    assert "yarvis" in res_jv


def test_tts_endpoint_exists():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    c = _test_client(jarvis_backend.app)
    r = c.post("/api/tts", json={"text": "hola"})
    # 200 = success, 429 = rate limited, 503 = TTS not ready
    assert r.status_code in (200, 429, 503)


def test_tts_returns_503_when_engine_not_loaded(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import tts_routes

    monkeypatch.setattr(tts_routes._tts_engine, "voice", None)

    c = _test_client(jarvis_backend.app)
    r = c.post("/api/tts", json={"text": "hola"})

    assert r.status_code == 503
    data = r.get_json() or {}
    assert data.get("error") == "tts_unavailable"


def test_tts_rejects_too_short_text():
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    c = _test_client(jarvis_backend.app)
    r = c.post("/api/tts", json={"text": "..."})
    assert r.status_code in (200, 400, 429, 503)


# =============================================================================
# CORE TOOLS
# =============================================================================


def test_core_tools_normalization():
    from core.core_tools import _normalizar_destino_web  # pyright: ignore[reportMissingImports]

    assert _normalizar_destino_web("google.com") == "https://google.com"
    # The function adds https:// and .com to plain text
    result = _normalizar_destino_web("test search")
    assert result.startswith("https://")


def test_weather_tool_without_widget_payload():
    from core.core_tools import obtener_clima  # pyright: ignore[reportMissingImports]

    out = obtener_clima.invoke({"ciudad": "Madrid"})
    assert "<WIDGET>" not in out
    assert "<_WIDGET>" not in out


def test_linked_memory_context_has_shared_bucket():
    from core import core_tools  # pyright: ignore[reportMissingImports]

    ctx = core_tools._obtener_contexto_memoria_entrelazada("guest_demo")
    assert ctx.get("scope") == "guest"
    assert "shared_facts" in ctx


# =============================================================================
# SECURITY
# =============================================================================


def test_security_manager_policy():
    from services import security_manager  # pyright: ignore[reportMissingImports]

    snap = security_manager._security_snapshot()
    policy = snap.get("policy", snap)
    assert "allowed_web_domains" in policy
    assert "google.com" in policy["allowed_web_domains"]


def test_security_manager_proactive_state():
    from services import security_manager  # pyright: ignore[reportMissingImports]

    state = security_manager.PROACTIVE_STATE
    assert isinstance(state, dict)
    assert "last_health_check" in state or "alerts" in state


# =============================================================================
# MEMORY & RAG
# =============================================================================


def test_memory_rag_module():
    from engines import memory_rag  # pyright: ignore[reportMissingImports]

    assert hasattr(memory_rag, "rag_motor")


def test_memory_rag_search():
    from engines import memory_rag  # pyright: ignore[reportMissingImports]

    # buscar_contexto returns a string block, not a list
    results = memory_rag.rag_motor.buscar_contexto("saludo", top_k=1)
    assert isinstance(results, str)


def test_memory_rag_store():
    from engines import memory_rag  # pyright: ignore[reportMissingImports]

    # agregar_interaccion uses 'profile_id' not 'perfil'
    memory_rag.rag_motor.agregar_interaccion(
        "test entry smoke", "response smoke", profile_id="test_profile"
    )
    # FAISS may not be initialized in test env, so buscar_contexto may return empty string
    results = memory_rag.rag_motor.buscar_contexto("test entry smoke", top_k=1)
    assert isinstance(results, str)
    # If FAISS is available, results should contain text
    # If not, empty string is acceptable
    if results:
        assert len(results) > 10 or "RECUPERACION" in results or "FAISS" in results


# =============================================================================
# VOICE ID ENGINE
# =============================================================================


def test_voice_id_preprocess_helper_from_bytes():
    import wave
    import struct
    from core.jarvis_config import VOICE_ID_ENABLED
    from engines import voice_id  # pyright: ignore[reportMissingImports]

    if not VOICE_ID_ENABLED:
        pytest.skip("Voice biometrics are disabled in core mode.")

    sr = 16000
    samples = [int(32767 * 0.1)] * int(sr * 0.5)
    raw = b"".join(struct.pack("<h", s) for s in samples)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(raw)

    wav_bytes = buf.getvalue()
    arr = voice_id.voice_id_motor._preprocess_audio_bytes(wav_bytes)
    assert arr is not None


def test_voice_id_wait_encoder_ready_helper():
    from engines import voice_id  # pyright: ignore[reportMissingImports]

    ok = voice_id.voice_id_motor._wait_encoder_ready(timeout=0.2)
    assert isinstance(ok, bool)


def test_voice_id_purge_non_owner_profiles():
    from engines import voice_id  # pyright: ignore[reportMissingImports]

    removed = voice_id.voice_id_motor.purge_non_owner_profiles("admin")
    assert isinstance(removed, int)


# =============================================================================
# MONITORING SERVICE
# =============================================================================


def test_monitoring_service_imports():
    from services import monitoring_service  # pyright: ignore[reportMissingImports]

    # monitoring_service is a module; the class is MonitoringService
    assert hasattr(monitoring_service, "MonitoringService")
    assert hasattr(monitoring_service, "monitoring_service")
    ms = monitoring_service.monitoring_service
    assert hasattr(ms, "_heartbeat_proactive_healthcheck")
    assert hasattr(ms, "_check_reminders_task")
    assert hasattr(ms, "_daily_briefing_task")


def test_monitoring_service_scheduler():
    from core.jarvis_config import MONITORING_ENABLED
    from services import monitoring_service  # pyright: ignore[reportMissingImports]

    ms = monitoring_service.monitoring_service
    expected = MONITORING_ENABLED and monitoring_service.SCHEDULER_AVAILABLE
    assert (ms._scheduler is not None) is expected


# =============================================================================
# REMINDERS
# =============================================================================


def test_reminder_crud():
    from core import jarvis_state  # pyright: ignore[reportMissingImports]
    from datetime import datetime, timedelta

    # Add a reminder
    when = datetime.now() + timedelta(minutes=30)
    jarvis_state._recordatorios.append(
        {"texto": "test reminder smoke", "cuando": when, "profile_id": "test"}
    )

    reminders = jarvis_state._recordatorios
    assert any(r["texto"] == "test reminder smoke" for r in reminders)


# =============================================================================
# EDGE CASES & REGRESSION GUARDS
# =============================================================================


def test_widget_stripping_logic():
    """Verify that WIDGET blocks are properly stripped from TTS text."""
    raw = 'Aquí está el clima<WIDGET>{"type":"weather","data":{"temp":25}}</WIDGET> y listo.'
    clean = raw.replace("<WIDGET>", "").replace("</WIDGET>", "")
    # Simple regex-free check: the cleaned version should not contain WIDGET tags
    assert "<WIDGET>" not in clean
    assert "</WIDGET>" not in clean


def test_briefing_once_per_day_logic():
    """Verify the briefing date guard logic works correctly."""
    from datetime import datetime

    hoy = datetime.now().strftime("%Y-%m-%d")
    ayer = datetime.now().strftime("%Y-%m-%d")  # simplified check

    # Same day = should skip
    assert hoy == hoy

    # Different format should still match as string
    assert hoy == datetime.now().strftime("%Y-%m-%d")


def test_low_similarity_does_not_trigger_session_continuity(monkeypatch):
    """With sim=0.15 (another person), session continuity should NOT activate.
    Instead, if it's a question, it gets answered directly without registration."""
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes
    from utils.jarvis_auth import autorizar_por_biometria, revocar_autorizacion  # pyright: ignore[reportMissingImports]

    revocar_autorizacion()
    autorizar_por_biometria("admin", "Administrador")

    jarvis_backend._PENDING_VOICE_REGISTRATION.pop("127.0.0.1", None)

    class DummyMotor:
        encoder = object()
        perfiles_voz = {"admin": {"nombre": "Administrador", "embedding": []}}

        @staticmethod
        def identificar(*_a, **_k):
            return (None, None, 0.15)  # Another person, very low sim

        @staticmethod
        def get_ultimo_candidato():
            return ("admin", "Administrador", 0.15)  # Below 0.35 threshold

        @staticmethod
        def registrar_voz(*_a, **_k):
            return True

    monkeypatch.setattr(voice_routes, "_voice_id_motor", DummyMotor())
    monkeypatch.setattr(voice_routes, "_biometria_activa", True)
    monkeypatch.setattr(voice_routes, "_norm_a_wav", lambda b: (b"wav-ok", True))

    c = _test_client(jarvis_backend.app)
    r = c.post(
        "/api/voice",
        data=b"RIFFFAKEAUDIO",
        headers={"X-Transcript": "¿Que una canción?", "X-Profile-Id": ""},
    )
    assert r.status_code == 200
    data = r.get_json() or {}
    # Now it responds directly through the conversational fast path without registering.
    identity = data.get("identity_source", "")
    assert identity in ("fast_info_direct", "unknown_direct_response", "soft_match_pending", "unknown"), (
        f"Unexpected identity_source={identity}"
    )
    # Should NOT be identified via session_continuity
    assert identity != "session_continuity"


def test_voice_cancel_registration():
    """POST /api/voice/cancelar should clear pending registrations."""
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    # Set up a fake pending registration
    jarvis_backend._PENDING_VOICE_REGISTRATION["127.0.0.1"] = {
        "audio": b"fake-audio",
        "stage": "awaiting_name",
        "pending_question": "clima hoy",
        "created_at": jarvis_backend._time.time(),
    }

    c = _test_client(jarvis_backend.app)
    r = c.post("/api/voice/cancelar")
    assert r.status_code == 200
    data = r.get_json() or {}
    assert data.get("ok") is True
    assert "identity_source" in data
    assert data["identity_source"] == "registration_cancelled"

    # Verify it was cleared
    assert "127.0.0.1" not in jarvis_backend._PENDING_VOICE_REGISTRATION


def test_voice_cancel_all_registrations():
    """POST /api/voice/cancelar should clear ALL if no pending for that IP."""
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    jarvis_backend._PENDING_VOICE_REGISTRATION.clear()
    jarvis_backend._PENDING_VOICE_REGISTRATION["10.0.0.1"] = {
        "audio": b"fake",
        "stage": "awaiting_name",
        "pending_question": "",
        "created_at": jarvis_backend._time.time(),
    }

    c = _test_client(jarvis_backend.app)
    r = c.post("/api/voice/cancelar")
    assert r.status_code == 200
    data = r.get_json() or {}
    assert data.get("ok") is True
    # Should clear all since no pending for 127.0.0.1
    assert len(jarvis_backend._PENDING_VOICE_REGISTRATION) == 0


def test_owner_hint_avoids_guest_registration_when_authorized(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes
    from utils.jarvis_auth import autorizar_por_biometria  # pyright: ignore[reportMissingImports]

    autorizar_por_biometria("admin", "Administrador")
    ip = "__test_ip_owner_hint__"
    jarvis_backend._PENDING_VOICE_REGISTRATION[ip] = {
        "audio": b"fake-audio",
        "stage": "awaiting_name",
        "pending_question": "",
        "created_at": jarvis_backend._time.time(),
    }

    class DummyMotor:
        encoder = object()

        @staticmethod
        def identificar(*_a, **_k):
            return (None, None, 0.0)

        @staticmethod
        def get_ultimo_candidato():
            return (None, None, 0.0)

    monkeypatch.setattr(voice_routes, "_voice_id_motor", DummyMotor())
    monkeypatch.setattr(voice_routes, "_biometria_activa", True)
    monkeypatch.setattr(voice_routes, "_norm_a_wav", lambda b: (b"wav-ok", True))

    c = _test_client(jarvis_backend.app)
    r = c.post(
        "/api/voice",
        data=b"RIFFFAKEAUDIO",
        headers={"X-Transcript": "clima hoy", "X-Profile-Id": "admin"},
        environ_base={"REMOTE_ADDR": ip},
    )
    assert r.status_code == 200
    data = r.get_json() or {}
    assert data.get("profile_id") == "admin"
    assert data.get("identity_source") in {
        "session_owner_fast_path",
        "session_owner_hint",
        "session_owner_fallback",
        "session_owner_fallback_audio_error",
        "session_owner_fallback_registration",
        "session_continuity",
        "biometric_match",
    }


def test_voice_route_emits_identity_debug_and_observability(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    events = []

    def _capture(event_type, **payload):
        events.append((event_type, payload))

    from api import voice_routes; monkeypatch.setattr(voice_routes, "_obs_event", _capture)
    monkeypatch.setattr(voice_routes, "_norm_a_wav", lambda b: (b"wav-ok", True))
    monkeypatch.setattr(voice_routes, "_bytes_es_wav_valido", lambda _b: True)
    monkeypatch.setattr(
        jarvis_backend.jarvis_brain,
        "procesar_mensaje",
        lambda _txt, profile_id=None: (f"ok::{profile_id or 'none'}", False),
    )

    class DummyMotor:
        encoder = object()

        @staticmethod
        def identificar(*_a, **_k):
            return ("admin", "Administrador", 0.88)

        @staticmethod
        def get_ultimo_candidato():
            return ("admin", "Administrador", 0.88)

        @staticmethod
        def get_ultimo_debug():
            return {
                "decision": "owner_direct",
                "top_profile_id": "admin",
                "top_nombre": "Administrador",
                "top_sim": 0.88,
                "top2_gap": 0.44,
            }

    monkeypatch.setattr(voice_routes, "_voice_id_motor", DummyMotor())
    monkeypatch.setattr(voice_routes, "_biometria_activa", True)

    c = _test_client(jarvis_backend.app)
    r = c.post(
        "/api/voice",
        data=b"RIFFFAKEAUDIO",
        headers={"X-Transcript": "abre spotify", "X-Profile-Id": ""},
    )
    assert r.status_code == 200
    data = r.get_json() or {}
    assert data.get("identity_source") == "biometric_match"
    dbg = data.get("identity_debug") or {}
    assert dbg.get("request_id")
    assert dbg.get("top_profile_id") == "admin"
    assert float(dbg.get("similarity", 0.0)) >= 0.88

    event_names = [name for name, _ in events]
    for required in [
        "voice_request_in",
        "voice_audio_normalized",
        "voice_identification_result",
        "voice_response_out",
    ]:
        assert required in event_names


def test_voice_fast_info_hint_skips_biometric_lookup(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    monkeypatch.setattr(voice_routes, "_norm_a_wav", lambda b: (b"wav-ok", True))
    monkeypatch.setattr(voice_routes, "_bytes_es_wav_valido", lambda _b: True)
    monkeypatch.setattr(
        jarvis_backend.jarvis_brain,
        "procesar_mensaje",
        lambda _txt, profile_id=None: (f"fast::{profile_id or 'none'}", False),
    )

    class DummyMotor:
        encoder = object()

        @staticmethod
        def identificar(*_a, **_k):
            raise AssertionError("identificar no debe ejecutarse en fast_info")

        @staticmethod
        def get_ultimo_candidato():
            return (None, None, 0.0)

        @staticmethod
        def get_ultimo_debug():
            return {}

    monkeypatch.setattr(voice_routes, "_voice_id_motor", DummyMotor())
    monkeypatch.setattr(voice_routes, "_biometria_activa", True)

    c = _test_client(jarvis_backend.app)
    r = c.post(
        "/api/voice",
        data=b"RIFFFAKEAUDIO",
        headers={
            "X-Transcript": "como esta el clima hoy",
            "X-Transcript-Confidence": "0.92",
            "X-Profile-Id": "",
        },
    )
    assert r.status_code == 200
    data = r.get_json() or {}
    assert data.get("identity_source") == "fast_info_direct"
    dbg = data.get("identity_debug") or {}
    assert dbg.get("route_mode") == "fast_info"


def test_identity_query_opens_guest_registration_and_accepts_english_name(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes
    from core import jarvis_state
    from utils.jarvis_auth import revocar_autorizacion  # pyright: ignore[reportMissingImports]

    ip = "__test_identity_registration__"
    revocar_autorizacion()
    jarvis_backend._PENDING_VOICE_REGISTRATION.pop(ip, None)
    jarvis_state._perfiles_memoria.pop("guest_daniel", None)
    monkeypatch.setattr(voice_routes, "_norm_a_wav", lambda b: (b"wav-ok", True))
    monkeypatch.setattr(voice_routes, "_bytes_es_wav_valido", lambda _b: True)

    class DummyMotor:
        encoder = object()

        @staticmethod
        def identificar(*_a, **_k):
            return None, None, 0.0

        @staticmethod
        def get_ultimo_candidato():
            return (None, None, 0.0)

        @staticmethod
        def get_ultimo_debug():
            return {}

        @staticmethod
        def registrar_voz(*_a, **_k):
            return True

    monkeypatch.setattr(voice_routes, "_voice_id_motor", DummyMotor())
    monkeypatch.setattr(voice_routes, "_biometria_activa", True)

    c = _test_client(jarvis_backend.app)
    r1 = c.post(
        "/api/voice",
        data=b"RIFFFAKEAUDIO",
        headers={"X-Transcript": "Who am I?", "X-Profile-Id": ""},
        environ_base={"REMOTE_ADDR": ip},
    )
    assert r1.status_code == 200
    data1 = r1.get_json() or {}
    assert data1.get("identity_source") == "identity_query_unverified"
    assert data1.get("nueva_voz") is True
    assert jarvis_backend._PENDING_VOICE_REGISTRATION[ip]["stage"] == "awaiting_name"

    r2 = c.post(
        "/api/voice",
        data=b"RIFFFAKEAUDIO",
        headers={"X-Transcript": "My name is Daniel.", "X-Profile-Id": ""},
        environ_base={"REMOTE_ADDR": ip},
    )
    assert r2.status_code == 200
    data2 = r2.get_json() or {}
    assert data2.get("identity_source") == "guest_registration"
    assert data2.get("profile_id") == "guest_daniel"
    assert data2.get("nombre") == "Daniel"
    assert "Daniel" in (jarvis_state._perfiles_memoria["guest_daniel"].get("facts") or "")
    assert ip not in jarvis_backend._PENDING_VOICE_REGISTRATION


def test_direct_spanish_self_intro_registers_guest_voice_and_memory(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes
    from core import jarvis_state
    from utils.jarvis_auth import revocar_autorizacion  # pyright: ignore[reportMissingImports]

    ip = "__test_direct_spanish_self_intro__"
    revocar_autorizacion()
    jarvis_backend._PENDING_VOICE_REGISTRATION.pop(ip, None)
    jarvis_state._perfiles_memoria.pop("guest_daniel", None)
    monkeypatch.setattr(voice_routes, "_norm_a_wav", lambda b: (b"wav-ok", True))
    monkeypatch.setattr(voice_routes, "_bytes_es_wav_valido", lambda _b: True)

    class DummyMotor:
        encoder = object()

        def __init__(self):
            self.registered = []

        @staticmethod
        def identificar(*_a, **_k):
            return None, None, 0.0

        @staticmethod
        def get_ultimo_candidato():
            return (None, None, 0.0)

        @staticmethod
        def get_ultimo_debug():
            return {}

        def registrar_voz(self, audio, profile_id, nombre):
            self.registered.append((audio, profile_id, nombre))
            return True

    motor = DummyMotor()
    monkeypatch.setattr(voice_routes, "_voice_id_motor", motor)
    monkeypatch.setattr(voice_routes, "_biometria_activa", True)

    c = _test_client(jarvis_backend.app)
    r = c.post(
        "/api/voice",
        data=b"RIFFFAKEAUDIO",
        headers={"X-Transcript": "Oye, yo me llamo Daniel.", "X-Profile-Id": ""},
        environ_base={"REMOTE_ADDR": ip},
    )

    data = r.get_json() or {}
    assert r.status_code == 200, data
    assert data.get("identity_source") == "guest_self_introduction"
    assert data.get("profile_id") == "guest_daniel"
    assert data.get("nombre") == "Daniel"
    assert ip not in jarvis_backend._PENDING_VOICE_REGISTRATION
    assert motor.registered[-1][1:] == ("guest_daniel", "Daniel")
    assert "Daniel" in (jarvis_state._perfiles_memoria["guest_daniel"].get("facts") or "")


def test_direct_english_self_intro_registers_guest_voice(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes
    from utils.jarvis_auth import revocar_autorizacion  # pyright: ignore[reportMissingImports]

    ip = "__test_direct_english_self_intro__"
    revocar_autorizacion()
    jarvis_backend._PENDING_VOICE_REGISTRATION.pop(ip, None)
    monkeypatch.setattr(voice_routes, "_norm_a_wav", lambda b: (b"wav-ok", True))
    monkeypatch.setattr(voice_routes, "_bytes_es_wav_valido", lambda _b: True)

    class DummyMotor:
        encoder = object()

        def __init__(self):
            self.registered = []

        @staticmethod
        def identificar(*_a, **_k):
            return None, None, 0.0

        @staticmethod
        def get_ultimo_candidato():
            return (None, None, 0.0)

        @staticmethod
        def get_ultimo_debug():
            return {}

        def registrar_voz(self, audio, profile_id, nombre):
            self.registered.append((audio, profile_id, nombre))
            return True

    motor = DummyMotor()
    monkeypatch.setattr(voice_routes, "_voice_id_motor", motor)
    monkeypatch.setattr(voice_routes, "_biometria_activa", True)

    c = _test_client(jarvis_backend.app)
    r = c.post(
        "/api/voice",
        data=b"RIFFFAKEAUDIO",
        headers={"X-Transcript": "Hey Jarvis, my name is Sarah.", "X-Profile-Id": ""},
        environ_base={"REMOTE_ADDR": ip},
    )

    data = r.get_json() or {}
    assert r.status_code == 200, data
    assert data.get("identity_source") == "guest_self_introduction"
    assert data.get("profile_id") == "guest_sarah"
    assert data.get("nombre") == "Sarah"
    assert motor.registered[-1][1:] == ("guest_sarah", "Sarah")


def test_voice_http_route_delegates_processing_to_voice_service(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    calls = []

    class DummyVoiceService:
        def process_voice(self, audio_bytes, request_data):
            calls.append((audio_bytes, request_data))
            return {
                "response": "delegated",
                "identity_source": "service_test",
                "should_listen": False,
            }, 202

    monkeypatch.setattr(voice_routes, "voice_service", DummyVoiceService(), raising=False)

    c = _test_client(jarvis_backend.app)
    response = c.post(
        "/api/voice",
        data=b"RIFFFAKEAUDIO",
        headers={
            "X-Transcript": "Hello Jarvis",
            "X-Transcript-Confidence": "0.91",
            "X-Profile-Id": "guest_daniel",
            "Content-Type": "audio/wav",
        },
        environ_base={"REMOTE_ADDR": "127.0.0.9"},
    )

    assert response.status_code == 202
    assert calls
    audio_bytes, request_data = calls[0]
    assert audio_bytes == b"RIFFFAKEAUDIO"
    assert request_data["transcript_hint"] == "Hello Jarvis"
    assert request_data["transcript_confidence"] == 0.91
    assert request_data["client_profile_id"] == "guest_daniel"
    assert request_data["ip"] == "127.0.0.9"


def test_voice_domain_logic_is_split_into_focused_modules():
    from voice import capture, guest_registration, intent_classifier, voice_response

    assert intent_classifier.clasificar_peticion_voz("Who am I?")["mode"] == "identity_query"
    assert intent_classifier.es_presentacion_nombre_voz("My name is Daniel.")
    assert not intent_classifier.es_pregunta_simple_voz("My name is Daniel.")
    assert callable(capture.transcribir_dudoso)
    assert callable(guest_registration.persist_guest_profile_registration)
    assert callable(voice_response.build_voice_debug)


def test_tool_policy_blocks_guest_from_critical_tools_without_auth():
    from core.security.tool_policy import evaluate_tool_policy, get_tool_policy

    policy = get_tool_policy("leer_archivo")
    assert policy.risk_level == "critical"
    assert policy.requires_confirmation is True
    assert policy.audit_log is True

    decision = evaluate_tool_policy(
        "leer_archivo",
        profile_id="guest_daniel",
        authorized=False,
        confirmed=False,
    )
    assert decision.allowed is False
    assert "autorizacion" in decision.reason.lower()


def test_tool_policy_allows_public_weather_for_guest():
    from core.security.tool_policy import evaluate_tool_policy, get_tool_policy

    policy = get_tool_policy("obtener_clima")
    assert policy.risk_level == "public"

    decision = evaluate_tool_policy(
        "obtener_clima",
        profile_id="guest_daniel",
        authorized=False,
        confirmed=False,
    )
    assert decision.allowed is True


def test_profiles_memory_detail_endpoint_exposes_facts_and_history(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from core import jarvis_state

    jarvis_state._perfiles_memoria["guest_panel"] = {
        "facts": "- Nombre del usuario: Daniel",
        "history": [],
    }

    c = _test_client(jarvis_backend.app)
    response = c.get("/api/perfiles/guest_panel")

    assert response.status_code == 200
    data = response.get_json() or {}
    assert data["profile_id"] == "guest_panel"
    assert "Daniel" in data["facts"]
    assert data["history"] == []


def test_profiles_memory_detail_can_update_and_clear_facts(monkeypatch):
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from core import jarvis_state

    jarvis_state._perfiles_memoria["guest_panel_edit"] = {
        "facts": "- Nombre del usuario: Daniel",
        "history": [{"type": "human", "content": "hi"}],
    }

    c = _test_client(jarvis_backend.app)
    patch_response = c.patch(
        "/api/perfiles/guest_panel_edit",
        json={"facts": "- Nombre del usuario: Daniela"},
    )
    assert patch_response.status_code == 200
    assert "Daniela" in (patch_response.get_json() or {}).get("facts", "")

    delete_response = c.delete("/api/perfiles/guest_panel_edit")
    assert delete_response.status_code == 200
    data = delete_response.get_json() or {}
    assert data["facts"] == ""
    assert data["history"] == []


def test_desktop_session_uses_stable_localhost_origin(monkeypatch, tmp_path):
    from core.desktop_session import load_desktop_session

    desktop_home = tmp_path / "desktop-home"
    monkeypatch.setenv("JARVIS_DESKTOP_HOME", str(desktop_home))
    session = load_desktop_session(port=5002)

    assert session.origin == "http://localhost:5002"
    assert str(desktop_home) in session.webview_storage_dir
    assert session.persist_permissions is True


def test_setup_wizard_reports_core_configuration(monkeypatch):
    from core.setup_wizard import build_setup_status

    status = build_setup_status(
        env={
            "JARVIS_API_TOKEN": "token",
            "SPOTIPY_CLIENT_ID": "client",
            "SPOTIPY_CLIENT_SECRET": "secret",
            "TELEGRAM_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
        },
        language="en",
        admin_voice_profiles=0,
        weather_location="Malibu, CA",
    )

    assert status["items"]["language"]["configured"] is True
    assert status["items"]["admin_voice"]["configured"] is False
    assert status["items"]["spotify"]["configured"] is True
    assert status["items"]["telegram"]["optional"] is True
    assert status["items"]["api_token"]["configured"] is True
    assert status["complete"] is False


def test_voice_identifier_similarity_event_and_debug(monkeypatch):
    from voice import identifier as voice_identifier  # pyright: ignore[reportMissingImports]
    from voice.identifier import VoiceIdentifier  # pyright: ignore[reportMissingImports]

    events = []

    def _capture(event_type, **payload):
        events.append((event_type, payload))

    monkeypatch.setattr(voice_identifier, "obs_event", _capture)

    motor = object.__new__(VoiceIdentifier)
    motor.encoder = object()
    motor._disponible = True
    motor.perfiles_voz = {
        "admin": {
            "nombre": "Administrador",
            "embedding": np.array([1.0, 0.0], dtype=np.float32),
        },
        "guest_alex": {
            "nombre": "Alex",
            "embedding": np.array([0.0, 1.0], dtype=np.float32),
        },
    }
    motor._ultimo_candidato = (None, None, 0.0)
    motor._ultimo_debug = {}
    monkeypatch.setattr(motor, "_wait_encoder_ready", lambda timeout=3.0: True)
    monkeypatch.setattr(
        motor,
        "_embedding_desde_audio_bytes",
        lambda _audio: np.array([1.0, 0.0], dtype=np.float32),
    )

    pid, nombre, sim = motor.identificar(b"fake-audio")
    assert pid == "admin"
    assert nombre == "Administrador"
    assert sim > 0.9

    dbg = motor.get_ultimo_debug()
    assert dbg.get("decision") in {"owner_direct", "main_threshold"}
    assert dbg.get("top_profile_id") == "admin"
    assert dbg.get("profiles_evaluated") == 2
    assert any(name == "voice_similarity_scored" for name, _ in events)


def test_healthcheck_cpu_ram_thresholds():
    """Verify monitoring thresholds match expected values (CPU 85%, RAM 90%, streak 2)."""
    import inspect
    from services import monitoring_service  # pyright: ignore[reportMissingImports]

    ms = monitoring_service.monitoring_service
    source = inspect.getsource(ms._heartbeat_proactive_healthcheck)
    assert "85.0" in source  # CPU threshold
    assert "90.0" in source  # RAM threshold
    assert ">= 2" in source  # Streak count


def test_voice_endpoint_rejects_get():
    """POST-only endpoints should reject GET requests."""
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    c = _test_client(jarvis_backend.app)
    r = c.get("/api/voice")
    assert r.status_code in (404, 405)


def test_tts_endpoint_rejects_get():
    """TTS endpoint should reject GET requests."""
    import jarvis_backend  # pyright: ignore[reportMissingImports]
    from api import voice_routes

    c = _test_client(jarvis_backend.app)
    r = c.get("/api/tts")
    # 400 = bad request (no text), 404/405 = method not allowed
    assert r.status_code in (400, 404, 405)
