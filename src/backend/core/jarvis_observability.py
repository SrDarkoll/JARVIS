import json
import os
import queue
import threading
from datetime import datetime

from core.jarvis_config import OBS_DIR, OBS_LOG_FILE

os.makedirs(OBS_DIR, exist_ok=True)

OBS_LOCK = threading.Lock()
OBS_METRICS: dict = {
    "started_at": datetime.now().isoformat(timespec="seconds"),
    "messages_total": 0,
    "router_hits": 0,
    "tool_calls_total": 0,
    "tool_ok_total": 0,
    "tool_error_total": 0,
    "autocure_attempts": 0,
    "autocure_success": 0,
    "security_blocked_total": 0,
    "security_warning_total": 0,
    "proactive_alerts_total": 0,
    "tool_stats": {},
    "plugins_loaded": 0,
}

# --- Background Logger ---
class LogWorker(threading.Thread):
    def __init__(self):
        super().__init__(name="LogWorker", daemon=True)
        self.queue = queue.Queue()
        self.stop_signal = False

    def run(self):
        while not self.stop_signal:
            try:
                # We wait for tasks (blocking with timeout to check stop_signal)
                item = self.queue.get(timeout=1.0)
                if item is None: break

                filepath, line = item
                if not filepath:
                    continue
                try:
                    with open(filepath, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except Exception as e:
                    print(f"[ERROR BACKGROUND LOG] Could not write to {filepath}: {e}")
                finally:
                    self.queue.task_done()
            except queue.Empty:
                continue

    def log(self, filepath, line):
        self.queue.put((filepath, line))

_log_worker = LogWorker()
_log_worker.start()
# -------------------------

def obs_inc(metric: str, delta: int = 1) -> None:
    with OBS_LOCK:
        OBS_METRICS[metric] = int(OBS_METRICS.get(metric, 0)) + int(delta)


def obs_event(event_type: str, **payload) -> None:
    event = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "event": event_type,
    }
    event.update(payload or {})
    try:
        line = json.dumps(event, ensure_ascii=False, default=str)
    except Exception:
        line = json.dumps(
            {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "event": event_type,
                "payload_error": "serialization_failed",
            },
            ensure_ascii=False,
        )

    # Send to background worker
    _log_worker.log(OBS_LOG_FILE, line)


def obs_tool(
    tool_name: str,
    ok: bool,
    elapsed_ms: float,
    source: str,
    user_input: str = "",
    error: str = "",
) -> None:
    with OBS_LOCK:
        tool_stats = OBS_METRICS.setdefault("tool_stats", {})
        stat = tool_stats.setdefault(tool_name, {"ok": 0, "error": 0, "ms_total": 0.0})
        if ok:
            stat["ok"] += 1
            OBS_METRICS["tool_ok_total"] = int(OBS_METRICS.get("tool_ok_total", 0)) + 1
        else:
            stat["error"] += 1
            OBS_METRICS["tool_error_total"] = int(OBS_METRICS.get("tool_error_total", 0)) + 1
        stat["ms_total"] = round(float(stat.get("ms_total", 0.0)) + float(elapsed_ms), 2)

    obs_event(
        "tool_result",
        tool=tool_name,
        ok=bool(ok),
        elapsed_ms=round(float(elapsed_ms), 2),
        source=source,
        user_input=(user_input or "")[:180],
        error=(error or "")[:300],
    )


def obs_snapshot() -> dict:
    with OBS_LOCK:
        snap = json.loads(json.dumps(OBS_METRICS, ensure_ascii=False, default=str))
    return snap


def obs_tail(limit: int = 80) -> list[dict]:
    if not os.path.exists(OBS_LOG_FILE):
        return []
    try:
        with open(OBS_LOG_FILE, encoding="utf-8") as f:
            lines = f.readlines()[-max(1, int(limit)) :]
        out = []
        for line in lines:
            line = (line or "").strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                out.append({"raw": line})
        return out
    except Exception:
        return []
