"""
API routes for language switching in J.A.R.V.I.S.
Handles hot-swapping of: TTS voice model, Whisper language, backend translations, and UI locale.
"""

import threading

from quart import Blueprint, jsonify, request
from utils.jarvis_i18n import (
    LANGUAGE_CONFIG,
    get_model_path,
    set_current_language,
)
from core.app_config import get_default_location, set_active_language, get_app_config
from core.api_contracts import validate_language_request, validate_language_response
from core.runtime_logger import log_info, log_warning
from core.jarvis_observability import obs_event

language_bp = Blueprint("language", __name__)

# These will be injected via init_language_routes
_tts_engine = None
_jarvis_settings = None
_whisper_model_ref = None


def init_language_routes(config: dict):
    """Inject dependencies for language route handlers."""
    global _tts_engine, _jarvis_settings, _whisper_model_ref
    _tts_engine = config.get("tts_engine")
    _jarvis_settings = config.get("jarvis_settings")
    _whisper_model_ref = config.get("whisper_model_ref")

    startup_lang = "en"
    if _jarvis_settings:
        startup_lang = str(getattr(_jarvis_settings, "LANGUAGE", "en") or "en").strip().lower()
    if startup_lang not in LANGUAGE_CONFIG:
        startup_lang = "en"

    set_current_language(startup_lang)
    if _jarvis_settings:
        _jarvis_settings.LANGUAGE = startup_lang
        _jarvis_settings.LOCALE = LANGUAGE_CONFIG[startup_lang]["locale"]

    if _tts_engine:
        startup_model = get_model_path(startup_lang)
        current_model = str(getattr(_tts_engine, "model_path", "") or "")
        if current_model and current_model != startup_model:
            try:
                _tts_engine.reload_model(startup_model)
            except Exception as e:
                log_warning(
                    "TTS startup language sync failed",
                    error=str(e),
                    language=startup_lang,
                    model=startup_model,
                )

    try:
        set_active_language(startup_lang)
    except Exception as e:
        log_warning("AppConfig language sync failed on init", error=str(e), language=startup_lang)


@language_bp.route("/api/language", methods=["GET"])
async def get_language():
    """Returns the current language config and available languages."""
    current = getattr(_jarvis_settings, "LANGUAGE", get_app_config().localization.language)
    if current not in LANGUAGE_CONFIG:
        current = "en"
    available = {
        code: {"name": cfg["name"], "locale": cfg["locale"]}
        for code, cfg in LANGUAGE_CONFIG.items()
    }
    return jsonify({
        "current": current,
        "locale": LANGUAGE_CONFIG.get(current, {}).get("locale", "en-US"),
        "available": available,
    })


@language_bp.route("/api/language", methods=["POST"])
async def set_language():
    """
    Hot-swap the entire language stack:
    1. Update jarvis_settings.LANGUAGE and LOCALE in memory
    2. Reload TTS engine with the correct voice model
    3. Update Whisper transcription language
    4. Return the new config so the frontend can update the UI
    """
    data = await request.get_json(silent=True) or {}
    req_contract = validate_language_request(data)
    if not req_contract.ok:
        obs_event("api_contract_violation", endpoint="/api/language", side="request", error=req_contract.error)
        return jsonify({"ok": False, "error": req_contract.error}), 400

    lang = (data.get("language") or "").strip().lower()

    if lang not in LANGUAGE_CONFIG:
        return jsonify({
            "ok": False,
            "error": f"Unsupported language: {lang}",
            "available": list(LANGUAGE_CONFIG.keys()),
        }), 400

    cfg = LANGUAGE_CONFIG[lang]
    old_lang = getattr(_jarvis_settings, "LANGUAGE", "en")

    # 1. Update centralized language state (single source of truth)
    set_current_language(lang)
    try:
        set_active_language(lang)
    except Exception as e:
        log_warning("AppConfig language sync failed", error=str(e), language=lang)

    # Also update jarvis_settings for backward compatibility
    if _jarvis_settings:
        _jarvis_settings.LANGUAGE = lang
        _jarvis_settings.LOCALE = cfg["locale"]

    # 2. Hot-swap TTS voice model
    tts_ok = False
    if _tts_engine:
        new_model = get_model_path(lang)
        try:
            tts_ok = bool(_tts_engine.reload_model(new_model))
        except Exception as e:
            tts_ok = False
            log_warning("TTS model swap failed", language=lang, model=new_model, error=str(e))
        if not tts_ok:
            log_warning("TTS model swap returned false", language=lang, model=new_model)

    # 3. Whisper reads from get_current_language() at transcription time

    # 4. Refresh weather immediately in the new language
    try:
        from tools.utilities import _obtener_clima_logic

        location = get_default_location()
        threading.Thread(target=lambda: _obtener_clima_logic(location), daemon=True).start()
    except Exception as e:
        log_warning("Weather refresh trigger failed", language=lang, error=str(e))

    log_info(
        "Language switched",
        previous=old_lang,
        current=lang,
        tts_swapped=tts_ok,
    )

    payload = {
        "ok": True,
        "language": lang,
        "locale": cfg["locale"],
        "name": cfg["name"],
        "tts_swapped": tts_ok,
    }
    res_contract = validate_language_response(payload)
    if not res_contract.ok:
        obs_event("api_contract_violation", endpoint="/api/language", side="response", error=res_contract.error)
        return jsonify({"ok": False, "error": res_contract.error}), 500

    return jsonify(payload)
