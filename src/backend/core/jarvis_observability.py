import json
import os
import queue
import threading
from datetime import datetime

from core.jarvis_config import OBS_DIR, OBS_LOG_FILE
from core.unified_log import redact_data, write_log

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
    def __init__(
        self,
        *,
        max_bytes: int | None = None,
        backup_count: int | None = None,
    ):
        super().__init__(name="LogWorker", daemon=True)
        self.queue = queue.Queue()
        self.stop_signal = False
        self.max_bytes = max(
            1,
            int(
                max_bytes
                if max_bytes is not None
                else os.getenv("JARVIS_OBS_LOG_MAX_BYTES", str(5 * 1024 * 1024))
            ),
        )
        self.backup_count = max(
            0,
            int(
                backup_count
                if backup_count is not None
                else os.getenv("JARVIS_OBS_LOG_BACKUP_COUNT", "3")
            ),
        )

    def _rotate_if_needed(self, filepath: str, incoming_bytes: int) -> None:
        try:
            current_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
            if current_size + incoming_bytes <= self.max_bytes:
                return
            if self.backup_count == 0:
                with open(filepath, "w", encoding="utf-8"):
                    pass
                return
            oldest = f"{filepath}.{self.backup_count}"
            if os.path.exists(oldest):
                os.remove(oldest)
            for index in range(self.backup_count - 1, 0, -1):
                source = f"{filepath}.{index}"
                if os.path.exists(source):
                    os.replace(source, f"{filepath}.{index + 1}")
            if os.path.exists(filepath):
                os.replace(filepath, f"{filepath}.1")
        except OSError:
            return

    def run(self):
        while not self.stop_signal:
            try:
                # We wait for tasks (blocking with timeout to check stop_signal)
                item = self.queue.get(timeout=1.0)
                if item is None:
                    break

                try:
                    filepath, line = item
                    if not filepath:
                        continue
                    encoded_size = len((line + "\n").encode("utf-8"))
                    self._rotate_if_needed(filepath, encoded_size)
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

    def stop(self) -> None:
        self.stop_signal = True
        self.queue.put(None)


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
    safe_payload = redact_data(payload or {})
    if isinstance(safe_payload, dict):
        event.update(safe_payload)
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
    write_log("EVENT", event_type, **(payload or {}))


def obs_tool(
    tool_name: str,
    ok: bool,
    elapsed_ms: float,
    source: str,
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
            normalized_line = (line or "").strip()
            if not normalized_line:
                continue
            try:
                out.append(json.loads(normalized_line))
            except Exception:
                out.append({"raw": normalized_line})
        return out
    except Exception:
        return []
