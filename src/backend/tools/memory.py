"""Memoria persistente: perfiles, serialización, facts, SQLite, memoria entrelazada."""

import json
import os
import re
import threading

from langchain_core.messages import AIMessage, HumanMessage
from services.memory_manager import memory_manager

from tools._common import (
    BASE_DIR,
    DEFAULT_PROFILE_ID,
    SHARED_PROFILE_ID,
    _normalizar_ascii,
    _normalizar_profile_id,
    _perfiles_memoria,
    _profile_scope,
    _texto_limpio_memoria,
    jarvis_state,
    memoria_lock,
)


def _db_path() -> str:
    return os.getenv("JARVIS_DB_PATH") or os.path.join(BASE_DIR, "memoria_jarvis.db")


# ─────────────────────────────────────────
# Facts filtering
# ─────────────────────────────────────────
_MEMORIA_BASURA_PATTERNS = (
    "no hay datos nuevos",
    "no aporta informacion relevante",
    "se mantiene el enfoque",
    "situacion con los vengadores",
    "priorizar situacion",
    "los vengadores",
    "marvel",
    '"type": "human"',
    '"type": "ai"',
)


def _limpiar_facts_memoria(facts: str) -> str:
    raw = str(facts or "").replace("\x00", " ").replace("\r", "\n")
    if not raw:
        return ""
    if len(raw) > 2800:
        raw = raw[-2800:]

    out = []
    seen = set()
    for chunk in re.split(r"[\n|]+", raw):
        ln = _texto_limpio_memoria(chunk.strip(" \t-"))
        if not ln:
            continue
        ln_norm = _normalizar_ascii(ln)
        if len(ln) < 3 or len(ln) > 200:
            continue
        if any(p in ln_norm for p in _MEMORIA_BASURA_PATTERNS):
            continue
        if ln_norm in seen:
            continue
        seen.add(ln_norm)
        out.append(ln)
        if len(out) >= 8:
            break
    if not out:
        return ""
    return "\n".join(f"- {x}" for x in out)


def _line_key_memoria(linea: str) -> str:
    m = re.match(r"^([^:]{2,60})\s*:\s*", str(linea or "").strip())
    if m:
        return _normalizar_ascii(m.group(1))
    return _normalizar_ascii(linea)


# ─────────────────────────────────────────
# Facts extraction
# ─────────────────────────────────────────
def _extraer_facts_de_input(input_text: str) -> list[str]:
    t_raw = _texto_limpio_memoria(input_text)
    t = _normalizar_ascii(t_raw)
    nuevos = []

    if ("admin" in t or "administrador" in t) and (
        "llamame" in t or "dime" in t or "tratame" in t
        or "dirigete" in t or "siempre" in t
    ):
        nuevos.append("Tratamiento preferido: Administrador")

    if "navegador predeterminado" in t or "navegador por defecto" in t:
        nuevos.append("Navegador preferido: Predeterminado del sistema")

    if "spotify" in t and any(
        k in t for k in ["similar", "shuffle", "automi", "aleatorio"]
    ):
        nuevos.append("Spotify: AutoMix con canciones similares")

    return nuevos


def _extraer_facts_compartidos(input_text: str) -> list[str]:
    """Hechos aptos para memoria entrelazada (compartida entre perfiles)."""
    t_raw = _texto_limpio_memoria(input_text)
    t = _normalizar_ascii(t_raw)
    nuevos = []

    if "mi ciudad" in t and any(k in t for k in ["vivo", "vivimos", "estoy en"]):
        nuevos.append("Ubicación operativa: Ciudad Principal")

    if "mi empresa" in t:
        nuevos.append("Contexto general: Entorno principal en Empresa Base")

    if "proyecto" in t and len(t_raw) <= 140:
        nuevos.append(f"Proyecto mencionado: {t_raw[:120]}")

    return nuevos


def _fusionar_facts_memoria(last_facts: str, nuevos: list[str]) -> str:
    base = []
    cleaned = _limpiar_facts_memoria(last_facts)
    if cleaned:
        for ln in cleaned.splitlines():
            ln2 = _texto_limpio_memoria(ln.lstrip("- "))
            if ln2:
                base.append(ln2)

    idx_by_key = {}
    merged = []
    for ln in base:
        key = _line_key_memoria(ln)
        idx_by_key[key] = len(merged)
        merged.append(ln)

    for ln in nuevos or []:
        ln2 = _texto_limpio_memoria(ln)
        if not ln2:
            continue
        key = _line_key_memoria(ln2)
        if key in idx_by_key:
            merged[idx_by_key[key]] = ln2
        else:
            idx_by_key[key] = len(merged)
            merged.append(ln2)

    merged = merged[:8]
    if not merged:
        return ""
    return "\n".join(f"- {x}" for x in merged)


# ─────────────────────────────────────────
# History cleaning
# ─────────────────────────────────────────
def _limpiar_historial_memoria(history: list) -> list:
    cleaned = []
    for m in history or []:
        txt = _normalizar_ascii(getattr(m, "content", ""))
        if "vengadores" in txt or "avengers" in txt:
            continue
        if isinstance(m, AIMessage):
            if (
                "situacion con los vengadores" in txt
                or "prioridad con los vengadores" in txt
            ):
                continue
            if "no hay datos nuevos importantes" in txt and "vengadores" in txt:
                continue
        cleaned.append(m)
    return cleaned[-40:]


# ─────────────────────────────────────────
# Serialization
# ─────────────────────────────────────────
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
            out.append(
                {
                    "type": "human" if isinstance(m, HumanMessage) else "ai",
                    "content": (getattr(m, "content", "")),
                }
            )
        except Exception:
            continue
    return out


# ─────────────────────────────────────────
# SQLite persistence
# ─────────────────────────────────────────
def init_sqlite_db():
    try:
        import sqlite3

        db_path = _db_path()
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        # Optimizaciones de rendimiento y concurrencia
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")

        c.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                profile_id TEXT PRIMARY KEY,
                history TEXT,
                facts TEXT
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ERROR DB INIT] {e}")


def cargar_memoria_perfiles() -> dict:
    perfiles = {}
    init_sqlite_db()

    import sqlite3

    db_path = _db_path()
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT profile_id, history, facts FROM profiles")
        rows = c.fetchall()
        for row in rows:
            pid = row[0]
            hist_list = json.loads(row[1]) if row[1] else []
            facts = row[2] or ""
            perfiles[pid] = {
                "history": _limpiar_historial_memoria(_deserializar_history(hist_list)),
                "facts": _limpiar_facts_memoria(facts),
            }
        conn.close()
    except Exception as e:
        print(f"[ERROR DB LOAD] {e}")

    if DEFAULT_PROFILE_ID not in perfiles:
        perfiles[DEFAULT_PROFILE_ID] = {"history": [], "facts": ""}

    memory_manager.load_snapshot(perfiles)
    return perfiles


def guardar_memoria_perfiles():
    try:
        snapshot = memory_manager.get_all_profiles()

        # Aplicamos limpieza final antes de guardar
        for pid in snapshot:
            snapshot[pid]["history"] = _serializar_history(snapshot[pid]["history"])
            snapshot[pid]["facts"] = _limpiar_facts_memoria(snapshot[pid]["facts"])

        import sqlite3

        db_path = _db_path()
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        for pid, data in snapshot.items():
            hist_str = json.dumps(data["history"], ensure_ascii=False)
            facts = data["facts"]
            c.execute(
                """
                INSERT INTO profiles(profile_id, history, facts) VALUES(?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET history=excluded.history, facts=excluded.facts
            """,
                (pid, hist_str, facts),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ERROR DB SAVE] {e}")


# ─────────────────────────────────────────
# Compatibility API
# ─────────────────────────────────────────
def cargar_memoria():
    perfiles = cargar_memoria_perfiles()
    perfil = perfiles.get(DEFAULT_PROFILE_ID, {"history": [], "facts": ""})
    return perfil.get("history", []), perfil.get("facts", "")


def guardar_memoria(history, facts=""):
    guardar_memoria_async(history, facts, DEFAULT_PROFILE_ID)


def guardar_memoria_async(*args):
    try:
        profile_id = jarvis_state.get_active_profile_id()
        if len(args) == 1 and isinstance(args[0], str):
            profile_id = args[0]
        elif len(args) >= 2:
            history, facts = args[0], args[1]
            if len(args) >= 3 and isinstance(args[2], str):
                profile_id = args[2]
            pid = _normalizar_profile_id(profile_id)
            if history is not None:
                snapshot = list(history or [])[-40:]
                memory_manager.set_profile_history(pid, snapshot)
            if facts is not None:
                memory_manager.set_facts(pid, _limpiar_facts_memoria(facts or ""))

        threading.Thread(target=guardar_memoria_perfiles, daemon=True).start()
    except Exception as e:
        print(f"[ERROR MEMORIA ASYNC] {e}")


# ─────────────────────────────────────────
# Profile context
# ─────────────────────────────────────────
def _obtener_contexto_perfil(profile_id: str):
    pid = _normalizar_profile_id(profile_id)
    with memoria_lock:
        pdata = _perfiles_memoria.get(pid)
        if not pdata:
            pdata = {"history": [], "facts": ""}
            _perfiles_memoria[pid] = pdata
    return pid, pdata.get("history", []), pdata.get("facts", "")


def _obtener_contexto_memoria_entrelazada(profile_id: str):
    pid = _normalizar_profile_id(profile_id)
    with memoria_lock:
        own = _perfiles_memoria.get(pid) or {"history": [], "facts": ""}
        shared = _perfiles_memoria.get(SHARED_PROFILE_ID) or {
            "history": [],
            "facts": "",
        }
    return {
        "profile_id": pid,
        "scope": _profile_scope(pid),
        "private_facts": own.get("facts", "") or "",
        "shared_facts": shared.get("facts", "") or "",
        "private_history_len": len(own.get("history", []) or []),
        "shared_history_len": len(shared.get("history", []) or []),
    }


def _sincronizar_memoria_entrelazada(
    profile_id: str, user_input: str, ai_reply: str
) -> None:
    pid = _normalizar_profile_id(profile_id)
    if pid == SHARED_PROFILE_ID:
        return
    if pid != DEFAULT_PROFILE_ID:
        return

    shared_new = _extraer_facts_compartidos(f"{user_input}\n{ai_reply}")
    if not shared_new:
        return

    with memoria_lock:
        shared = _perfiles_memoria.get(SHARED_PROFILE_ID)
        if not shared:
            shared = {"history": [], "facts": ""}
            _perfiles_memoria[SHARED_PROFILE_ID] = shared
        shared["facts"] = _fusionar_facts_memoria(shared.get("facts", ""), shared_new)

    threading.Thread(target=guardar_memoria_perfiles, daemon=True).start()


def extraer_datos_criticos(input_text, last_facts):
    try:
        nuevos = _extraer_facts_de_input(input_text)
        return _fusionar_facts_memoria(last_facts, nuevos)
    except Exception:
        return _limpiar_facts_memoria(last_facts)


# ─────────────────────────────────────────
# Bootstrap: cargar memoria al importar
# ─────────────────────────────────────────
try:
    loaded = cargar_memoria_perfiles()
except Exception as e:
    print(f"[MEMORY] Error cargando perfiles al importar: {e}")
    loaded = None
if SHARED_PROFILE_ID not in jarvis_state._perfiles_memoria:
    with memory_manager.lock:
        jarvis_state._perfiles_memoria[SHARED_PROFILE_ID] = {"history": [], "facts": ""}

