import json
import os
import re
import sqlite3
import threading

from core import jarvis_config, jarvis_state
from langchain_core.messages import AIMessage, HumanMessage

_BASE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BASE)
DB_PATH = os.getenv("JARVIS_DB_PATH") or os.path.join(
    jarvis_config.MEMORY_DIR,
    "memoria_jarvis.db",
)

DEFAULT_PROFILE_ID = jarvis_state.DEFAULT_PROFILE_ID
memoria_lock = jarvis_state.memoria_lock
_perfiles_memoria = jarvis_state._perfiles_memoria
_msg_counter_by_profile = jarvis_state._msg_counter_by_profile
db_lock = threading.Lock()

def init_db():
    with db_lock:
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            # Optimizations for performance and concurrency
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")

            c.execute('''
                CREATE TABLE IF NOT EXISTS profiles (
                    profile_id TEXT PRIMARY KEY,
                    history TEXT,
                    facts TEXT
                )
            ''')
            conn.commit()
            conn.close()
        except Exception as e:
            from core.jarvis_observability import obs_event
            obs_event("db_init_error", error=str(e)[:300])
            print(f"[ERROR DB INIT] {e}")

def _normalizar_profile_id(profile_id: str | None) -> str:
    pid = (profile_id or DEFAULT_PROFILE_ID).strip().lower()
    pid = re.sub(r"[^a-z0-9_.-]+", "_", pid)
    pid = pid.strip("._-") or DEFAULT_PROFILE_ID
    return pid[:64]

def _deserializar_history(items: list) -> list:
    restored = []
    for m in items or []:
        try:
            contenido = (m or {}).get("content", "")
            mtype = (m or {}).get("type", "ai")
            if mtype == "human":
                restored.append(HumanMessage(content=contenido))
            else:
                restored.append(AIMessage(content=contenido))
        except Exception:
            continue
    return restored[-40:]

def _serializar_history(history: list) -> list:
    out = []
    for m in (history or [])[-40:]:
        try:
            out.append({
                "type": "human" if isinstance(m, HumanMessage) else "ai",
                "content": getattr(m, "content", ""),
            })
        except Exception:
            continue
    return out

def cargar_memoria_perfiles(normalizar_tratamiento_admin_func) -> dict:
    perfiles = {}
    init_db()

    # Load from DB
    with db_lock:
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT profile_id, history, facts FROM profiles")
            rows = c.fetchall()
            for row in rows:
                pid = row[0]
                try:
                    hist_list = json.loads(row[1]) if row[1] else []
                except Exception as e:
                    from core.jarvis_observability import obs_event
                    obs_event("db_load_json_error", error=str(e)[:300])
                    hist_list = []
                facts = row[2] or ""
                perfiles[pid] = {
                    "history": _deserializar_history(hist_list),
                    "facts": normalizar_tratamiento_admin_func(facts),
                }
            conn.close()
        except Exception as e:
            from core.jarvis_observability import obs_event
            obs_event("db_load_error", error=str(e)[:300])
            print(f"[ERROR DB LOAD] {e}")

    # Fallback to JSON if DB is completely empty for the default user
    if DEFAULT_PROFILE_ID not in perfiles:
        perfiles[DEFAULT_PROFILE_ID] = {"history": [], "facts": ""}

    if len(perfiles) <= 1 and not perfiles[DEFAULT_PROFILE_ID]["history"]:
        mem_file = os.path.join(_ROOT, 'memoria_jarvis_profiles.json')
        legacy_file = os.path.join(_ROOT, 'memoria_jarvis.json')
        if os.path.exists(mem_file):
            try:
                with open(mem_file, encoding="utf-8") as f:
                    data = json.load(f) or {}
                raw_profiles = data.get("profiles", {})
                for pid_raw, pdata in raw_profiles.items():
                    pid = _normalizar_profile_id(pid_raw)
                    perfiles[pid] = {
                        "history": _deserializar_history((pdata or {}).get("history", [])),
                        "facts": normalizar_tratamiento_admin_func((pdata or {}).get("facts", "")),
                    }
            except Exception as e:
                from core.jarvis_observability import obs_event
                obs_event("json_load_error", error=str(e)[:300])
        elif os.path.exists(legacy_file):
            try:
                with open(legacy_file, encoding="utf-8") as f:
                    old = json.load(f) or {}
                perfiles[DEFAULT_PROFILE_ID] = {
                    "history": _deserializar_history(old.get("history", [])),
                    "facts": normalizar_tratamiento_admin_func(old.get("facts", "")),
                }
            except Exception as e:
                from core.jarvis_observability import obs_event
                obs_event("legacy_json_load_error", error=str(e)[:300])

    return perfiles

def guardar_memoria_perfiles(normalizar_tratamiento_admin_func):
    try:
        with memoria_lock:
            snapshot = {}
            for pid, pdata in _perfiles_memoria.items():
                snapshot[pid] = {
                    "history": _serializar_history((pdata or {}).get("history", [])),
                    "facts": normalizar_tratamiento_admin_func((pdata or {}).get("facts", "")),
                }

        init_db()
        with db_lock:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            for pid, data in snapshot.items():
                hist_str = json.dumps(data["history"], ensure_ascii=False)
                facts = data["facts"]
                c.execute('''
                    INSERT INTO profiles(profile_id, history, facts) VALUES(?, ?, ?)
                    ON CONFLICT(profile_id) DO UPDATE SET history=excluded.history, facts=excluded.facts
                ''', (pid, hist_str, facts))
            conn.commit()
            conn.close()
    except Exception as e:
        from core.jarvis_observability import obs_event
        obs_event("db_save_error", error=str(e)[:300])
        print(f"[ERROR DB SAVE] {e}")

def guardar_memoria_async(args, normalizar_tratamiento_admin_func):
    try:
        profile_id = jarvis_state.get_active_profile_id()
        if len(args) == 1 and isinstance(args[0], str):
            profile_id = args[0]
        elif len(args) >= 2:
            history, facts = args[0], args[1]
            if len(args) >= 3 and isinstance(args[2], str):
                profile_id = args[2]
            pid = _normalizar_profile_id(profile_id)
            with memoria_lock:
                _perfiles_memoria[pid] = {
                    "history": list(history or [])[-40:],
                    "facts": normalizar_tratamiento_admin_func(facts or ""),
                }
        threading.Thread(target=guardar_memoria_perfiles, args=(normalizar_tratamiento_admin_func,), daemon=True).start()
    except Exception as e:
        print(f"[ERROR MEMORY ASYNC] {e}")

def cargar_memoria(normalizar_tratamiento_admin_func):
    perfiles = cargar_memoria_perfiles(normalizar_tratamiento_admin_func)
    perfil = perfiles.get(DEFAULT_PROFILE_ID, {"history": [], "facts": ""})
    return perfil.get("history", []), perfil.get("facts", "")
