"""
TTS Routes: audio synthesis and pronunciation management.
The engine instance is injected via init_tts_routes().
"""

import re
import time as _time

from core.jarvis_observability import obs_event
from quart import Blueprint, jsonify, make_response, request

tts_bp = Blueprint("tts", __name__)

_tts_engine = None
_tts_lock = None
_tts_api_lock = None
_tts_max_chars = 420
_synthesize_audio = None


class TTSRoutesConfig:
    def __init__(
        self,
        engine,
        tts_lock,
        tts_api_lock,
        tts_max_chars,
        synthesize_audio_fn,
    ):
        self.engine = engine
        self.tts_lock = tts_lock
        self.tts_api_lock = tts_api_lock
        self.tts_max_chars = tts_max_chars
        self.synthesize_audio_fn = synthesize_audio_fn


def init_tts_routes(config: TTSRoutesConfig):
    global _tts_engine, _tts_lock, _tts_api_lock, _tts_max_chars, _synthesize_audio
    _tts_engine = config.engine
    _tts_lock = config.tts_lock
    _tts_api_lock = config.tts_api_lock
    _tts_max_chars = config.tts_max_chars
    _synthesize_audio = config.synthesize_audio_fn


def _normalize_tts_map(data):
    from utils.jarvis_text import reparar_unicode

    source = data or {}
    out = {}
    for k, v in source.items():
        key = reparar_unicode(str(k or "")).strip().lower()
        val = reparar_unicode(str(v or "")).strip()
        if key and val:
            out[key] = val
    return out


IP_LAST_CALL = {}
TTS_LIMIT_SECONDS = 0.35


def _check_rate_limit(ip, limit, endpoint):
    ip_norm = (str(ip or "unknown")).strip().lower()
    if ip_norm in {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}:
        return True, 0.0
    key = (ip_norm, endpoint)
    now = _time.time()
    last = IP_LAST_CALL.get(key, 0.0)
    elapsed = now - last
    if elapsed < limit:
        return False, max(0.0, limit - elapsed)
    IP_LAST_CALL[key] = now
    return True, 0.0


def _tts_unavailable_response():
    return jsonify({"error": "tts_unavailable", "message": "Voice engine is unavailable."}), 503


def _tts_failed_response():
    return jsonify({"error": "tts_failed", "message": "Voice synthesis failed."}), 500


@tts_bp.route("/api/tts", methods=["GET", "POST"])
async def generate_tts():
    from utils.jarvis_text import reparar_unicode

    ip = request.remote_addr or "unknown"
    allowed, retry_after = _check_rate_limit(ip, TTS_LIMIT_SECONDS, "tts")
    if not allowed:
        return jsonify({"error": "rate_limit", "retry_after": round(retry_after, 3)}), 429

    api_lock = _tts_api_lock
    if api_lock is None or not callable(getattr(api_lock, "acquire", None)):
        return _tts_unavailable_response()
    if not api_lock.acquire(timeout=10):
        return jsonify({"error": "tts_busy", "retry_after": 1.2}), 429

    try:
        text = ""
        if request.method == "POST":
            text = ((await request.get_json(silent=True)) or {}).get("text", "")
        else:
            text = request.args.get("text", "")
        text = str(text or "").strip()
        if not text:
            return jsonify({"error": "No text"}), 400

        if _tts_engine is None or getattr(_tts_engine, "voice", None) is None:
            return _tts_unavailable_response()
        if not callable(_synthesize_audio):
            return _tts_unavailable_response()

        text = reparar_unicode(text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > _tts_max_chars:
            text = text[:_tts_max_chars].rsplit(" ", 1)[0].strip()

        t0 = _time.time()
        audio_bytes = _synthesize_audio(text)
        dur = _time.time() - t0
        obs_event("tts_ok", duration_ms=int(dur * 1000), chars=len(text))

        resp = await make_response(audio_bytes)
        resp.headers["Content-Type"] = "audio/wav"
        resp.headers["Content-Length"] = len(audio_bytes)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Connection"] = "close"
        return resp
    except RuntimeError as exc:
        obs_event("tts_unavailable", error=type(exc).__name__)
        return _tts_unavailable_response()
    except Exception as exc:
        obs_event("tts_api_error", error=type(exc).__name__)
        return _tts_failed_response()
    finally:
        api_lock.release()


@tts_bp.route("/api/tts/pronunciation", methods=["GET"])
@tts_bp.route("/api/tts/pronunciacion", methods=["GET"])
def get_tts_pronunciation():
    if _tts_engine is None or getattr(_tts_engine, "tts_lock", None) is None:
        return _tts_unavailable_response()
    with _tts_engine.tts_lock:
        return jsonify({"rules": dict(_tts_engine.tts_pronun_map)})


@tts_bp.route("/api/tts/pronunciation", methods=["POST"])
@tts_bp.route("/api/tts/pronunciacion", methods=["POST"])
async def update_tts_pronunciation():
    if _tts_engine is None or not callable(getattr(_tts_engine, "update_reglas", None)):
        return _tts_unavailable_response()

    data = (await request.get_json(silent=True)) or {}
    rules_input = data.get("rules", {})
    if not isinstance(rules_input, dict):
        return jsonify({"error": "Object required"}), 400
    normalized_rules = _normalize_tts_map(rules_input)
    if not normalized_rules:
        return jsonify({"error": "No rules"}), 400
    return jsonify(
        {
            "message": "Updated",
            "rules": _tts_engine.update_reglas(normalized_rules, bool(data.get("replace", False))),
        }
    )


@tts_bp.route("/api/tts/pronunciation/reset", methods=["POST"])
@tts_bp.route("/api/tts/pronunciacion/reset", methods=["POST"])
def reset_tts_pronunciation():
    if _tts_engine is None or not callable(getattr(_tts_engine, "reset_reglas", None)):
        return _tts_unavailable_response()
    return jsonify({"message": "Reset", "rules": _tts_engine.reset_reglas()})
