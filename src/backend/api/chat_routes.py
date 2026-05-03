import json
import traceback
import asyncio
from quart import Blueprint, request, jsonify, Response
from core import jarvis_brain
from core.jarvis_observability import obs_event, obs_inc
from utils.jarvis_text import normalizar_tratamiento_admin
from langchain_core.messages import HumanMessage

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
        data = await request.get_json(force=True)
        user_input = data.get("message", "")
        if not user_input:
            return jsonify({"error": "No message"}), 400
        ip = request.remote_addr or "unknown"
        key = (ip.lower().strip(), "chat")
        now = __import__("time").time()
        with _ip_last_call_lock:
            last = _ip_last_call.get(key, 0.0)
            elapsed = now - last
            if elapsed < chat_limit_seconds:
                return jsonify(
                    {"error": "Too many requests", "retry_after": round(max(0.0, chat_limit_seconds - elapsed), 3)}
                ), 429
            _ip_last_call[key] = now
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


@chat_bp.route("/api/chat/stream", methods=["GET", "POST"])
async def api_chat_stream():
    data = (await request.get_json(force=True)) if request.method == "POST" else request.args
    data = data or {}
    user_input = (data.get("message") or "").strip()
    if not user_input:
        return jsonify({"error": "No message"}), 400
    profile_id = (data.get("profile_id") or "web_default").strip()
    obs_event(
        "api_chat_stream_in",
        user_input=user_input[:220],
        ip=request.remote_addr,
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
