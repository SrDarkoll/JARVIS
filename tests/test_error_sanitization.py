from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from api import tts_routes
from core.brain import processor, prompts
from quart import Quart


def _run(awaitable):
    return asyncio.run(awaitable)


def _tts_app() -> Quart:
    app = Quart(__name__)
    app.register_blueprint(tts_routes.tts_bp)
    return app


async def _request_json(app: Quart, method: str, path: str, **kwargs):
    client = app.test_client()
    response = await getattr(client, method)(path, **kwargs)
    return response.status_code, await response.get_json()


def _configure_tts(monkeypatch, synthesize):
    api_lock = threading.Lock()
    engine = SimpleNamespace(
        voice=object(),
        tts_lock=threading.Lock(),
        tts_pronun_map={},
        update_reglas=lambda rules, _replace: rules,
        reset_reglas=lambda: {},
    )
    monkeypatch.setattr(tts_routes, "_tts_engine", engine)
    monkeypatch.setattr(tts_routes, "_tts_api_lock", api_lock)
    monkeypatch.setattr(tts_routes, "_synthesize_audio", synthesize)
    monkeypatch.setattr(tts_routes, "IP_LAST_CALL", {})
    return api_lock


def test_tts_generic_failure_returns_sanitized_json_and_releases_lock(monkeypatch):
    secret_text = "read aloud API_TOKEN=top-secret"
    secret_error = r"provider failed at C:\Users\ramir\private\voice.onnx"
    events = []

    def fail_synthesis(_text):
        raise ValueError(secret_error)

    api_lock = _configure_tts(monkeypatch, fail_synthesis)
    monkeypatch.setattr(
        tts_routes,
        "obs_event",
        lambda event, **fields: events.append((event, fields)),
    )

    status, payload = _run(
        _request_json(
            _tts_app(), "post", "/api/tts", json={"text": secret_text}
        )
    )

    assert status == 500
    assert payload == {"error": "tts_failed", "message": "Voice synthesis failed."}
    rendered = repr(payload)
    assert secret_text not in rendered
    assert secret_error not in rendered
    assert "Traceback" not in rendered
    assert events == [("tts_api_error", {"error": "ValueError"})]
    assert api_lock.acquire(blocking=False) is True
    api_lock.release()


def test_tts_voice_runtime_failure_returns_controlled_503(monkeypatch):
    secret_error = r"Piper voice unavailable at C:\Users\ramir\private\voice.onnx"
    events = []

    def fail_synthesis(_text):
        raise RuntimeError(secret_error)

    _configure_tts(monkeypatch, fail_synthesis)
    monkeypatch.setattr(
        tts_routes,
        "obs_event",
        lambda event, **fields: events.append((event, fields)),
    )

    status, payload = _run(
        _request_json(_tts_app(), "post", "/api/tts", json={"text": "hello"})
    )

    assert status == 503
    assert payload == {
        "error": "tts_unavailable",
        "message": "Voice engine is unavailable.",
    }
    assert secret_error not in repr(payload)
    assert events == [("tts_unavailable", {"error": "RuntimeError"})]


def test_tts_generation_degrades_before_route_initialization(monkeypatch):
    monkeypatch.setattr(tts_routes, "_tts_engine", None)
    monkeypatch.setattr(tts_routes, "_tts_api_lock", None)
    monkeypatch.setattr(tts_routes, "IP_LAST_CALL", {})

    status, payload = _run(
        _request_json(_tts_app(), "post", "/api/tts", json={"text": "hello"})
    )

    assert status == 503
    assert payload == {
        "error": "tts_unavailable",
        "message": "Voice engine is unavailable.",
    }


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("get", "/api/tts/pronunciation", {}),
        ("post", "/api/tts/pronunciation", {"json": {"rules": {"a": "b"}}}),
        ("post", "/api/tts/pronunciation/reset", {}),
    ],
)
def test_tts_pronunciation_routes_degrade_when_engine_is_absent(
    monkeypatch, method, path, kwargs
):
    monkeypatch.setattr(tts_routes, "_tts_engine", None)

    status, payload = _run(_request_json(_tts_app(), method, path, **kwargs))

    assert status == 503
    assert payload == {
        "error": "tts_unavailable",
        "message": "Voice engine is unavailable.",
    }


def test_prompt_exception_logs_include_class_only(monkeypatch):
    from core import core_tools

    rag_secret = r"RAG failed at C:\Users\ramir\private\memory"
    auth_secret = "auth provider leaked TOKEN=top-secret"
    warnings = []

    monkeypatch.setattr(prompts, "RAG_ENABLED", True)
    monkeypatch.setattr(
        core_tools,
        "_obtener_contexto_memoria_entrelazada",
        lambda _pid: {"private_facts": "", "shared_facts": ""},
    )
    monkeypatch.setattr(
        prompts.rag_motor,
        "buscar_contexto",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(rag_secret)),
    )
    monkeypatch.setattr(
        prompts,
        "get_auth_snapshot",
        lambda: (_ for _ in ()).throw(ValueError(auth_secret)),
    )
    monkeypatch.setattr(prompts, "verificar_autorizacion", lambda _pid: True)
    monkeypatch.setattr(prompts, "es_guest", lambda _pid: True)
    monkeypatch.setattr(
        prompts,
        "log_warning",
        lambda event, **fields: warnings.append((event, fields)),
    )

    result = prompts.get_system_msg("private prompt")

    assert result.content
    assert warnings == [
        ("rag_context_retrieval_failed", {"error": "RuntimeError"}),
        ("auth_snapshot_read_failed", {"error": "ValueError"}),
    ]
    rendered = repr(warnings)
    assert rag_secret not in rendered
    assert auth_secret not in rendered


def test_volume_adjustment_error_is_sanitized(monkeypatch):
    from core import core_tools

    secret_error = r"volume backend failed at C:\Users\ramir\private\device"
    events = []

    monkeypatch.setattr(processor, "_cargar_contexto_perfil", lambda pid: pid)
    monkeypatch.setattr(processor.social_engine, "_respuesta_rapida_social", lambda *_: None)
    monkeypatch.setattr(
        processor.social_engine,
        "_respuesta_seguimiento_contextual",
        lambda *_: None,
    )
    monkeypatch.setattr(processor.history_manager, "_get_history_for_profile", lambda _pid: [])
    monkeypatch.setattr(processor.brain_utils, "parse_reminder", lambda _text: (None, None))
    monkeypatch.setattr(
        processor.brain_utils,
        "parsear_comando_volumen",
        lambda _text: ("absolute", 50),
    )
    monkeypatch.setattr(
        core_tools,
        "_ajustar_volumen_absoluto",
        lambda _value: (_ for _ in ()).throw(OSError(secret_error)),
    )
    monkeypatch.setattr(
        processor,
        "obs_event",
        lambda event, **fields: events.append((event, fields)),
    )

    reply, should_listen = processor._preflight(
        "set volume to 50 and API_TOKEN=top-secret",
        "admin",
        allow_compound=False,
    )

    assert reply == "Could not adjust the system volume."
    assert should_listen is False
    assert events == [("volume_adjustment_error", {"error": "OSError"})]
    assert secret_error not in reply
    assert "API_TOKEN" not in repr(events)
