import re

from core import jarvis_state

_COMANDOS_REPETIR_MUSICA = {
    "otra vez", "de nuevo", "repite", "repitela", "repitelo", "repetir",
    "repeat", "again", "otra", "la misma", "mismo tema",
}

_MUSICA_GENERICA_PATTERNS = [
    "una canción", "una cancion", "alguna canción", "alguna cancion",
    "pon música", "pon musica", "pon algo", "algo de música", "algo de musica",
    "pon una rola", "una rola", "una de música", "una de musica",
]

def _es_comando_repetir_musica(user_input: str) -> bool:
    t = (user_input or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t in _COMANDOS_REPETIR_MUSICA

def _es_peticion_musica_generica(user_input: str) -> bool:
    t = (user_input or "").strip().lower()
    t = re.sub(r"[!?.,;:]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t: return False
    if t in {"spotify", "música", "musica", "canción", "cancion", "rola"}:
        return True
    if not any(p in t for p in _MUSICA_GENERICA_PATTERNS):
        return False
    if any(k in t for k in [' "', " feat ", " ft ", " by ", " de ", " del "]):
        return False
    words = re.findall(r"[a-z0-9áéíóúñü]+", t)
    return 1 <= len(words) <= 8

def _es_posible_titulo_cancion(user_input: str) -> bool:
    t = (user_input or "").strip().lower()
    if not t or "?" in t or "http" in t: return False
    if _es_comando_repetir_musica(t): return False
    if len(t) > 90: return False
    words = re.findall(r"[a-zA-Z0-9áéíóúñü]+", t)
    if len(words) < 2 or len(words) > 8: return False
    bloqueantes = [
        "clima", "noticias", "volumen", "apaga", "reinicia", "hiberna", "bloquea",
        "youtube", "navegador", "archivo", "recordatorio", "hora", "fecha", "quien",
        "qué", "que", "como", "cómo", "por que", "por qué", "busca", "buscar",
        "abre", "lee", "dime", "porcentaje", "emergencia", "urgencia", "ayuda",
        "ayúdame", "ayudame", "déjalo", "dejalo", "güey", "wey", "me ayudas", "no me ayudas",
    ]
    if any(b in t for b in bloqueantes): return False
    return True

def _contexto_musica_activo(historial: list | None = None) -> bool:
    from langchain_core.messages import AIMessage, HumanMessage
    historial = historial if historial is not None else jarvis_state.chat_history
    if not historial: return False
    ultimo_user, ultimo_ai = "", ""
    for m in reversed(historial):
        txt = (getattr(m, "content", "") or "").lower()
        if not txt: continue
        if not ultimo_user and isinstance(m, HumanMessage): ultimo_user = txt
        elif not ultimo_ai and isinstance(m, AIMessage): ultimo_ai = txt
        if ultimo_user and ultimo_ai: break
    senales = ["spotify", "canci", "musica", "música", "reprodu", "shuffle", "cola", "play", "automi"]
    return any(s in ultimo_user for s in senales) or any(s in ultimo_ai for s in senales)
