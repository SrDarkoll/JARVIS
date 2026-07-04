import asyncio
import json
import time
import traceback

from core import jarvis_brain
from core.jarvis_observability import obs_event
from langchain_core.messages import HumanMessage
from quart import Blueprint, Response, jsonify, request
from utils.jarvis_text import normalizar_tratamiento_admin

chat_bp = Blueprint("chat", __name__)

# Injected dependencies
_ip_last_call = None
_ip_last_call_lock = None
chat_limit_seconds = None


class ChatRoutesConfig:
    def __init__(self, ip_last_call, ip_last_call_lock, chat_limit_seconds):
        self.ip_last_call = ip_last_call
        self.ip_last_call_lock = ip_last_call_lock
        self.chat_limit_seconds = chat_limit_seconds


def init_chat_routes(config: ChatRoutesConfig):
    global _ip_last_call, _ip_last_call_lock, chat_limit_seconds
    _ip_last_call = config.ip_last_call
    _ip_last_call_lock = config.ip_last_call_lock
    chat_limit_seconds = config.chat_limit_seconds


def _rate_limit_response(ip: str, bucket: str):
    if _ip_last_call is None or _ip_last_call_lock is None or chat_limit_seconds is None:
        return None
    key = (ip.lower().strip(), bucket)
    now = time.time()
    with _ip_last_call_lock:
        last = _ip_last_call.get(key, 0.0)
        elapsed = now - last
        if elapsed < chat_limit_seconds:
            return jsonify(
                {"error": "Too many requests", "retry_after": round(max(0.0, chat_limit_seconds - elapsed), 3)}
            ), 429
        _ip_last_call[key] = now
    return None


async def _read_json_payload():
    data = await request.get_json(silent=True)
    if data is None:
        raw_body = await request.get_data(cache=True)
        if raw_body and raw_body.strip():
            return None, (jsonify({"error": "Invalid JSON payload"}), 400)
        return {}, None
    if not isinstance(data, dict):
        return None, (jsonify({"error": "Invalid JSON payload"}), 400)
    return data, None


@chat_bp.route("/api/chat", methods=["GET", "POST"])
async def api_chat():
    if request.method == "GET":
        return jsonify(
            {
                "status": "error",
                "message": "Chat requires POST. Use the main interface.",
            }
        ), 405

    try:
        data, payload_error = await _read_json_payload()
        if payload_error:
            return payload_error
        user_input = (data.get("message") or "").strip()
        if not user_input:
            return jsonify({"error": "No message"}), 400
        if len(user_input) > 4000:
            return jsonify({"error": "Message too large"}), 413
        ip = request.remote_addr or "unknown"
        limited = _rate_limit_response(ip, "chat")
        if limited:
            return limited
        print(f"\n[LORD] {user_input}")
        profile_id = (data.get("profile_id") or "web_default").strip()
        obs_event(
            "api_chat_in",
            user_input=user_input[:220],
            ip=ip,
            profile_id=profile_id,
        )
        reply, should_listen = await asyncio.to_thread(
            jarvis_brain.procesar_mensaje,
            user_input,
            profile_id=profile_id,
        )
        obs_event(
            "api_chat_out",
            reply=(reply or "")[:220],
            should_listen=bool(should_listen),
            ip=ip,
            profile_id=profile_id,
        )
        return jsonify({"response": reply, "should_listen": should_listen})
    except Exception as e:
        print(f"[ERROR] {e}")
        traceback.print_exc()
        try:
            payload = (await request.get_json(silent=True)) or {}
            ui = payload.get("message", "")
            fallback_profile = (payload.get("profile_id") or "web_default").strip()
            if jarvis_brain.llm is None:
                raise RuntimeError("no llm")
            messages = [
                jarvis_brain.get_system_msg(ui, profile_id=fallback_profile),
                HumanMessage(content=ui),
            ]
            reply = (await asyncio.to_thread(jarvis_brain.llm.invoke, messages)).content
            return jsonify({"response": normalizar_tratamiento_admin(reply), "should_listen": False})
        except Exception as e:
            print(f"[WARN /api/chat] LLM invoke error: {e}")
            return jsonify(
                {
                    "response": "I apologize, Administrator. Systems are rebooting.",
                    "should_listen": False,
                }
            ), 200


@chat_bp.route("/api/chat/stream", methods=["POST"])
async def api_chat_stream():
    data, payload_error = await _read_json_payload()
    if payload_error:
        return payload_error
    user_input = (data.get("message") or "").strip()
    if not user_input:
        return jsonify({"error": "No message"}), 400
    if len(user_input) > 4000:
        return jsonify({"error": "Message too large"}), 413
    ip = request.remote_addr or "unknown"
    limited = _rate_limit_response(ip, "chat_stream")
    if limited:
        return limited
    profile_id = (data.get("profile_id") or "web_default").strip()
    obs_event(
        "api_chat_stream_in",
        user_input=user_input[:220],
        ip=ip,
        profile_id=profile_id,
    )

    def _next_stream_event(iterator):
        try:
            return next(iterator), False
        except StopIteration:
            return None, True

    async def generate():
        iterator = jarvis_brain.stream_procesar_mensaje_events(
            user_input,
            profile_id=profile_id,
        )
        try:
            while True:
                evt, done = await asyncio.to_thread(_next_stream_event, iterator)
                if done:
                    break
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as ex:
            yield f"data: {json.dumps({'type': 'error', 'message': str(ex)}, ensure_ascii=False)}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "close",
        },
    )
