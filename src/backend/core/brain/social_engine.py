import re

from core import jarvis_state
from langchain_core.messages import AIMessage
from utils.jarvis_auth import get_auth_snapshot
from utils.jarvis_i18n import BACKEND_TRANSLATIONS, get_current_language
from utils.jarvis_text import reparar_unicode

DEFAULT_PROFILE_ID = jarvis_state.DEFAULT_PROFILE_ID

KEYWORDS_WEB_DINAMICAS = [
    "today",
    "current",
    "latest",
    "recent",
    "price",
    "quote",
    "how much",
    "news",
    "result",
    "score",
    "match",
    "weather",
    "temperature",
    "forecast",
    "who is",
    "what happened",
    "when is",
    "launch",
    "premiere",
    "what is",
    "which is",
    "where",
    "que es",
    "como funciona",
    "cual es",
    "quien es",
]

_MODEL_VERSION_RE = re.compile(
    r"\b(?:gpt|claude|gemini|llama|mistral|qwen|deepseek|openai)\s*[- ]?(?:v)?\d+(?:\.\d+){0,3}\b",
    re.IGNORECASE,
)

_VERSION_RE = re.compile(r"\b(?:v(?:ersion)?\s*)?\d+(?:\.\d+){1,3}\b", re.IGNORECASE)

_TECH_QUERY_HINTS = {
    "version",
    "versions",
    "update",
    "updates",
    "release",
    "releases",
    "changelog",
    "new features",
    "changes",
    "model",
    "models",
    "api",
    "sdk",
    "framework",
    "library",
    "libraries",
    "docs",
    "documentation",
    "que es",
    "como funciona",
    "cual es",
    "quien es",
}

_TECH_TOPIC_HINTS = {
    "openai",
    "groq",
    "gpt",
    "claude",
    "gemini",
    "llama",
    "mistral",
    "qwen",
    "deepseek",
    "python",
    "node",
    "next",
    "react",
    "django",
    "flask",
    "quart",
    "fastapi",
    "langchain",
    "tool",
    "tools",
    "sdk",
    "api",
}


def _normalizar_social_texto(text: str) -> str:
    import unicodedata

    t = reparar_unicode(str(text or "")).strip().lower()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _contiene_frase(t: str, frases: list[str] | tuple[str, ...]) -> bool:
    for frase in frases:
        frase_norm = _normalizar_social_texto(frase)
        if not frase_norm:
            continue
        if " " in frase_norm and frase_norm in t:
            return True
        if " " not in frase_norm and re.search(rf"\b{re.escape(frase_norm)}\b", t):
            return True
    return False


def _has_information_intent(t: str) -> bool:
    markers = {
        "weather",
        "temperature",
        "forecast",
        "clima",
        "temperatura",
        "pronostico",
        "what time",
        "current time",
        "que hora",
        "hora es",
        "what day",
        "what date",
        "today's date",
        "todays date",
        "que fecha",
        "que dia",
        "news",
        "noticias",
    }
    return any(marker in t for marker in markers)


def _es_consulta_tecnica_actualizable(t: str) -> bool:
    if not t:
        return False
    if _MODEL_VERSION_RE.search(t):
        return True

    has_version = bool(_VERSION_RE.search(t))
    has_tech_hint = any(k in t for k in _TECH_TOPIC_HINTS)
    has_query_hint = any(k in t for k in _TECH_QUERY_HINTS)

    if has_tech_hint and (has_version or has_query_hint):
        return True
    if has_version and has_query_hint:
        return True
    return False


def _debe_buscar_en_web(text: str) -> bool:
    lang = get_current_language()
    bt = BACKEND_TRANSLATIONS.get(lang, BACKEND_TRANSLATIONS["en"])

    t = reparar_unicode(str(text or "")).strip().lower()
    if not t:
        return False

    social_short = set(bt["social_hi"]) | {"ok", "okay", "bye", "goodbye"}
    if t in social_short:
        return False

    import unicodedata
    t_norm = unicodedata.normalize("NFD", t)
    t_norm = "".join(c for c in t_norm if unicodedata.category(c) != "Mn")
    t_norm = re.sub(r"[^\w\s]", " ", t_norm)
    t_norm = re.sub(r"\s+", " ", t_norm).strip()

    social_local = (
        bt["social_hi"]
        + bt["social_how"]
        + bt["social_who"]
        + bt.get("social_assistant_who", [])
        + bt["social_thanks"]
    )
    if _contiene_frase(t_norm, social_local):
        return False

    if any(k in t_norm for k in bt["keywords_web"]):
        return True

    # Cost keywords also from i18n in a real scenario, but let's keep it simple
    if any(k in t_norm for k in ["cost", "price", "worth", "sale", "precio", "cuesta"]):
        return True

    return _es_consulta_tecnica_actualizable(t)


def _respuesta_rapida_social(user_input: str, profile_id: str = DEFAULT_PROFILE_ID) -> str | None:
    """Respuestas instantaneas para conversacion casual sin ir al LLM."""
    lang = get_current_language()
    bt = BACKEND_TRANSLATIONS.get(lang, BACKEND_TRANSLATIONS["en"])

    t = reparar_unicode(user_input or "").strip().lower()
    t_plain = _normalizar_social_texto(t)
    if _has_information_intent(t_plain):
        return None

    _snap = get_auth_snapshot()
    pid_norm = str(profile_id or "").strip().lower()
    snap_pid = str(_snap.get("profile_id") or "").strip().lower()
    if pid_norm == DEFAULT_PROFILE_ID:
        nombre_activo = _snap.get("nombre") if snap_pid == pid_norm else bt["profile_administrator"]
    elif snap_pid == pid_norm and _snap.get("nombre"):
        nombre_activo = _snap.get("nombre")
    else:
        nombre_activo = bt["profile_guest"]

    if _contiene_frase(t_plain, bt["social_hi"]):
        if str(profile_id or "").strip().lower() == DEFAULT_PROFILE_ID:
            return bt["social_online_admin"]
        return bt["social_online_guest"].format(name=nombre_activo)

    if _contiene_frase(t_plain, bt["social_how"]):
        if str(profile_id or "").strip().lower() == DEFAULT_PROFILE_ID:
            return bt["social_status_admin"]
        return bt["social_status_guest"].format(name=nombre_activo)

    if _contiene_frase(t_plain, bt.get("social_assistant_who", [])):
        return bt["social_assistant_identity"]

    if _contiene_frase(t_plain, bt["social_who"]):
        if str(profile_id or "").strip().lower() == DEFAULT_PROFILE_ID:
            return bt["social_identity_admin"]
        return bt["social_identity_guest"].format(name=nombre_activo)

    if _contiene_frase(t_plain, bt["social_thanks"]):
        if str(profile_id or "").strip().lower() == DEFAULT_PROFILE_ID:
            return bt["social_thanks_admin"]
        return bt["social_thanks_guest"].format(name=nombre_activo)

    if _contiene_frase(t_plain, bt["social_stop"]):
        return None  # El router maneja estos casos

    return None


def _respuesta_seguimiento_contextual(user_input: str, history: list) -> str | None:
    """Evita respuestas fuera de contexto en seguimientos cortos."""
    t = reparar_unicode(user_input or "").strip().lower()
    t = re.sub(r"[^\wáéíóúñü\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t_plain = t

    lang = get_current_language()
    bt = BACKEND_TRANSLATIONS.get(lang, BACKEND_TRANSLATIONS["en"])

    if any(p in t_plain for p in bt["social_why"]):
        pass # continue
    else:
        return None

    last_ai = None
    for m in reversed(history or []):
        if isinstance(m, AIMessage):
            last_ai = str(getattr(m, "content", "") or "")
            break
    if not last_ai:
        return None

    ltxt = reparar_unicode(last_ai).lower()
    # Check for browser/spotify keywords in translated strings
    if "browser" in ltxt or "navegador" in ltxt:
        return bt["browser_fail"]
    if "spotify" in ltxt:
        return bt["spotify_fail"]
    return None
