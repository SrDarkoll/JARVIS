"""Regression tests for language-switch and weather default behavior."""

from __future__ import annotations

import os
import sys


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "src", "backend")


def _ensure_backend_path() -> None:
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)


def test_frontend_stop_words_are_removed():
    """Voice stop-word interception is disabled; commands should reach backend."""
    i18n_path = os.path.join(ROOT, "src", "frontend", "static", "js", "i18n.js")
    with open(i18n_path, encoding="utf-8") as f:
        i18n_js = f.read()
    main_js_path = os.path.join(ROOT, "src", "frontend", "static", "js", "main.js")
    with open(main_js_path, encoding="utf-8") as f:
        main_js = f.read()

    assert "stop_words" not in i18n_js
    assert "cmd_canceled" not in i18n_js
    assert "getStopWords" not in main_js
    assert "StopCancellation" not in main_js


def test_language_routes_init_syncs_runtime_language_state():
    """init_language_routes should sync i18n runtime language from settings."""
    _ensure_backend_path()
    from api import language_routes  # pyright: ignore[reportMissingImports]
    from utils.jarvis_i18n import get_current_language, set_current_language

    prev_lang = get_current_language()
    prev_tts = language_routes._tts_engine
    prev_settings = language_routes._jarvis_settings
    prev_whisper_ref = language_routes._whisper_model_ref

    class DummySettings:
        LANGUAGE = "es"
        LOCALE = "en-US"
        LOCATION = "Madrid"

    dummy_settings = DummySettings()

    try:
        language_routes.init_language_routes(
            {
                "tts_engine": None,
                "jarvis_settings": dummy_settings,
                "whisper_model_ref": {},
            }
        )
        assert get_current_language() == "es"
        assert dummy_settings.LOCALE == "es-ES"
    finally:
        language_routes._tts_engine = prev_tts
        language_routes._jarvis_settings = prev_settings
        language_routes._whisper_model_ref = prev_whisper_ref
        set_current_language(prev_lang)


def test_weather_logic_uses_settings_location_by_default(monkeypatch):
    """Weather logic should use LOCATION when city is omitted."""
    _ensure_backend_path()
    from core.service_container import services  # pyright: ignore[reportMissingImports]
    from core import app_config  # pyright: ignore[reportMissingImports]
    from tools import utilities  # pyright: ignore[reportMissingImports]
    from utils.jarvis_i18n import get_current_language, set_current_language

    weather_called_with: dict[str, object] = {}

    class FakeWeatherResponse:
        status_code = 200

        def json(self):
            return {
                "current_condition": [
                    {
                        "temp_C": "18",
                        "weatherDesc": [{"value": "Sunny"}],
                        "lang_en": [{"value": "Sunny"}],
                    }
                ]
            }

    def fake_weather_get(url: str, *args, **kwargs):
        weather_called_with["url"] = url
        return FakeWeatherResponse()

    def fail_search_impl(query: str):
        raise AssertionError(f"Search fallback should not run when wttr succeeds: {query}")

    prev_get_default_app = app_config.get_default_location
    prev_get_default_util = utilities.get_default_location
    prev_lang = get_current_language()
    monkeypatch.setattr(app_config, "get_default_location", lambda: "Malibu, CA")
    monkeypatch.setattr(utilities, "get_default_location", lambda: "Malibu, CA")
    monkeypatch.setattr(utilities.http_requests, "get", fake_weather_get)
    monkeypatch.setattr("tools.search._buscar_en_internet_impl", fail_search_impl)
    services.weather_cache["temp"] = None
    services.weather_cache["desc"] = None
    services.weather_cache["last_update"] = 0

    try:
        set_current_language("en")
        desc, temp = utilities._obtener_clima_logic()

        assert temp == "18"
        assert desc == "Sunny"
        assert "Malibu" in weather_called_with.get("url", "")
        assert services.weather_cache["temp"] == "18"
    finally:
        monkeypatch.setattr(app_config, "get_default_location", prev_get_default_app)
        monkeypatch.setattr(utilities, "get_default_location", prev_get_default_util)
        set_current_language(prev_lang)


def test_router_english_weather_and_date_are_localized(monkeypatch):
    _ensure_backend_path()
    from core.brain import processor, router  # pyright: ignore[reportMissingImports]
    from utils.jarvis_i18n import get_current_language, set_current_language

    prev_lang = get_current_language()
    captured: dict[str, object] = {}

    def fake_tool(tool_name, args, user_input, source="wrapper"):
        captured["tool_name"] = tool_name
        captured["args"] = args
        captured["source"] = source
        return "Weather routed."

    try:
        set_current_language("en")
        monkeypatch.setattr(router, "get_default_location", lambda: "Malibu, CA")
        monkeypatch.setattr(processor, "_invocar_tool_wrapper", fake_tool)

        assert router._router_hibrido("What day is it?").startswith("Today is ")
        assert router._router_hibrido("What's the weather today?") == "Weather routed."
        assert captured["tool_name"] == "obtener_clima"
        assert captured["args"] == {"ciudad": "Malibu, CA"}
    finally:
        set_current_language(prev_lang)


def test_frontend_voice_i18n_is_runtime_driven():
    """Frontend voice language should be computed dynamically."""
    main_js_path = os.path.join(ROOT, "src", "frontend", "static", "js", "main.js")
    with open(main_js_path, encoding="utf-8") as f:
        main_js = f.read()

    assert "function getRecognitionLang(" in main_js
    assert "applyRecognitionLanguage(newLang);" in main_js
    assert "passiveRecognition.lang = getRecognitionLang();" in main_js
    assert "activeRecognition.lang = getRecognitionLang();" in main_js
    assert "const STOP_WORDS = t('stop_words');" not in main_js
    assert "passiveRecognition.lang = 'es-ES';" not in main_js
    assert "activeRecognition.lang = 'es-ES';" not in main_js


def test_frontend_does_not_cancel_stop_transcripts():
    """The frontend should not intercept stop transcripts at all."""
    main_js_path = os.path.join(ROOT, "src", "frontend", "static", "js", "main.js")
    with open(main_js_path, encoding="utf-8") as f:
        main_js = f.read()

    assert "getStopCancellationMatch" not in main_js
    assert "isStopCancellationTranscript" not in main_js
    assert "CHECK STOP WORDS" not in main_js
    assert "buildStopRegex()" not in main_js
    assert "cancelVoiceRegistration();" not in main_js


def test_backend_stop_para_aliases_are_removed_from_prompts_and_keywords():
    keywords_path = os.path.join(ROOT, "src", "backend", "core", "brain", "keywords.py")
    with open(keywords_path, encoding="utf-8") as f:
        keywords_py = f.read()
    i18n_path = os.path.join(ROOT, "src", "backend", "utils", "jarvis_i18n.py")
    with open(i18n_path, encoding="utf-8") as f:
        i18n_py = f.read()

    assert '"stop"' not in keywords_py
    assert '"para"' not in keywords_py
    assert '"social_stop": ["stop"' not in i18n_py
    assert '"social_stop": ["stop", "para"' not in i18n_py
    assert '"Para", "det' not in i18n_py
    assert '"Stop", "halt"' not in i18n_py


def test_voice_transcription_uses_active_language_and_keeps_clear_hint():
    _ensure_backend_path()
    from voice import service as voice_service  # pyright: ignore[reportMissingImports]
    from utils.jarvis_i18n import get_current_language, set_current_language

    calls: list[dict[str, object]] = []

    class DummySegment:
        text = "today is the day"
        start = 0.0
        end = 1.0

    class DummyWhisper:
        def transcribe(self, _path, **kwargs):
            calls.append(kwargs)
            return [DummySegment()], None

    prev_lang = get_current_language()
    try:
        set_current_language("en")

        clear_hint = voice_service._transcribir_dudoso(
            b"fake-audio",
            transcript_hint="How are you today?",
            whisper_model=DummyWhisper(),
            transcript_confidence=0.12,
            route_mode="fast_info",
        )
        assert clear_hint == "How are you today?"
        assert calls == []

        fallback = voice_service._transcribir_dudoso(
            b"fake-audio",
            transcript_hint="Jarvis",
            whisper_model=DummyWhisper(),
            transcript_confidence=0.01,
            route_mode="secure",
        )
        assert fallback == "today is the day"
        assert calls[-1]["language"] == "en"
    finally:
        set_current_language(prev_lang)


def test_voice_name_introduction_is_not_simple_question():
    _ensure_backend_path()
    from voice import service as voice_service  # pyright: ignore[reportMissingImports]
    from voice.pipeline import normalizar_nombre_invitado  # pyright: ignore[reportMissingImports]

    assert voice_service._es_presentacion_nombre_voz("My name is Daniel.")
    assert not voice_service._es_pregunta_simple_voz("My name is Daniel.")
    assert normalizar_nombre_invitado("My name is Daniel.") == "Daniel"


def test_spotify_playback_message_is_localized():
    _ensure_backend_path()
    from modules.spotify import messages as spotify_messages
    from utils.jarvis_i18n import get_current_language, set_current_language

    prev_lang = get_current_language()
    try:
        set_current_language("en")
        assert (
            spotify_messages.playback_success_message("Killer Queen", "Queen")
            == "Playing 'Killer Queen' by Queen."
        )

        set_current_language("es")
        assert (
            spotify_messages.playback_success_message("Killer Queen", "Queen")
            == "Reproduciendo 'Killer Queen' de Queen."
        )
    finally:
        set_current_language(prev_lang)


def test_spanish_social_identity_queries_do_not_become_web_search():
    _ensure_backend_path()
    from core.brain import social_engine  # pyright: ignore[reportMissingImports]
    from utils.jarvis_auth import autorizar_por_biometria, revocar_autorizacion
    from utils.jarvis_i18n import get_current_language, set_current_language

    prev_lang = get_current_language()
    try:
        set_current_language("es")
        autorizar_por_biometria("admin", "Administrador")

        assert social_engine._debe_buscar_en_web("¿Quién eres?") is False
        assistant_reply = social_engine._respuesta_rapida_social(
            "¿Quién eres?",
            "guest_unverified",
        )
        assert assistant_reply is not None
        assert "J.A.R.V.I.S." in assistant_reply
        assert "traduce" not in assistant_reply.lower()

        status_reply = social_engine._respuesta_rapida_social(
            "¿Cómo estás?",
            "guest_unverified",
        )
        assert status_reply is not None
        assert "Invitado" in status_reply
        assert "Administrador" not in status_reply
    finally:
        revocar_autorizacion()
        set_current_language(prev_lang)


def test_router_routes_spanish_nba_questions_to_nba_tool(monkeypatch):
    _ensure_backend_path()
    from core.brain import processor, router  # pyright: ignore[reportMissingImports]
    from utils.jarvis_i18n import get_current_language, set_current_language

    prev_lang = get_current_language()
    captured: dict[str, object] = {}

    def fake_tool(tool_name, args, user_input, source="wrapper"):
        captured["tool_name"] = tool_name
        captured["args"] = args
        captured["source"] = source
        captured["user_input"] = user_input
        return "Resumen NBA localizado."

    try:
        set_current_language("es")
        monkeypatch.setattr(processor, "_invocar_tool_wrapper", fake_tool)

        reply = router._router_hibrido("¿Quién está jugando ahorita en la NBA los partidos?")

        assert reply == "Resumen NBA localizado."
        assert captured["tool_name"] == "obtener_deportes_espn"
        assert captured["source"] == "router_directo"
    finally:
        set_current_language(prev_lang)
