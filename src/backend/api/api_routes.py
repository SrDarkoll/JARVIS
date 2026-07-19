"""
APIRoutes: Definition of modular Quart routes.
Helps reduce the size of jarvis_backend.py by moving state and configuration routes.
"""

import os
from datetime import datetime

import psutil
from core.runtime_logger import log_info
from quart import Blueprint, jsonify, make_response, render_template, request, send_from_directory

api_bp = Blueprint("api", __name__)

_services = None
_root_dir = None
_browser_mode = None


def init_api_routes(services, browser_mode, root_dir):
    global _services, _root_dir, _browser_mode
    _services = services
    _root_dir = root_dir
    _browser_mode = browser_mode


@api_bp.route("/", methods=["GET"])
async def index():
    try:
        return await render_template("index.html")
    except Exception as e:
        from core.runtime_logger import log_warning
        log_warning("template_render_failed", error=str(e), template="index.html")
        return jsonify(
            {
                "status": "ok",
                "message": "JARVIS backend online. UI may be unavailable via Quart templates.",
            }
        )


@api_bp.route("/media/<path:filename>", methods=["GET"])
async def media_files(filename: str):
    return await send_from_directory(os.path.join(_root_dir, "media"), filename)


@api_bp.route("/manifest.json", methods=["GET"])
async def manifest_json():
    return await send_from_directory(os.path.join(_root_dir, "src", "frontend", "static"), "manifest.json")


@api_bp.route("/sw.js", methods=["GET"])
async def service_worker():
    response = await make_response(await send_from_directory(os.path.join(_root_dir, "src", "frontend", "static"), "sw.js"))
    response.headers["Cache-Control"] = "no-cache"
    return response


@api_bp.route("/api/health", methods=["GET"])
def health():
    return jsonify(
        {
            "status": "online",
            "timestamp": datetime.now().isoformat(),
            "cpu": psutil.cpu_percent(),
            "ram": psutil.virtual_memory().percent,
        }
    )


@api_bp.route("/api/frontend/log", methods=["POST"])
async def frontend_log():
    data = (await request.get_json(silent=True)) or {}
    message = " ".join(str(data.get("message") or "").split())
    source = " ".join(str(data.get("source") or "ui").split())
    if not message:
        return jsonify({"ok": False, "error": "empty_message"}), 400
    log_info(
        "JARVIS UI",
        source=source[:80],
        ui_message=message[:2000],
        ip=request.remote_addr,
    )
    return jsonify({"ok": True})


@api_bp.route("/api/config/info", methods=["GET"])
def config_info():
    if not _browser_mode:
        return jsonify({"error": "Configuration not initialized"}), 500
    return jsonify(
        {
            "browser_mode": _browser_mode,
            "tts_enabled": True,
            "biometrics_active": True,
        }
    )
