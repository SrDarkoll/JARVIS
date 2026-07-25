import re
import unicodedata
import webbrowser
from typing import Any

from core import jarvis_config, jarvis_state
from utils.jarvis_text import normalizar_tratamiento_admin, reparar_unicode

DEFAULT_PROFILE_ID = jarvis_state.DEFAULT_PROFILE_ID


def _normalizar_ascii(text: str) -> str:
    if not text:
        return ""
    t = reparar_unicode(str(text or "")).lower()
    t = "".join(ch for ch in unicodedata.normalize("NFKD", t) if not unicodedata.combining(ch))
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _limpiar_thinking(text: str) -> str:
    """Removes <think>...</think> tags and their content from the final text."""
    if not text:
        return ""
    try:
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    except Exception:
        return text


def _limpiar_metadatos_voz(texto: str) -> str:
    """Removes <think>, <WIDGET> and HTML/tags so that TTS does not read them."""
    if not texto:
        return ""
    try:
        t = _limpiar_thinking(texto)
        t = re.sub(r"<WIDGET>.*?</WIDGET>", "", t, flags=re.DOTALL).strip()
        t = re.sub(r"<[^>]+>", "", t).strip()
        return t
    except Exception:
        return texto


def _formatear_reply_por_perfil(reply: str, profile_id: str) -> str:
    txt = reparar_unicode(str(reply or "")).strip() or "Understood."
    pid = str(profile_id or "").strip().lower()
    if pid == DEFAULT_PROFILE_ID:
        return normalizar_tratamiento_admin(txt)
    txt = re.sub(r"(?i)^administrator[,:]?\s*", "", txt)
    txt = re.sub(r"(?i)[,]\s*administrator[,.]?\s*$", ".", txt)
    txt = re.sub(r"(?i),\s*administrator[,]\s*", ", ", txt)
    txt = re.sub(r"\s{2,}", " ", txt).strip(" ,.;:-")
    return txt or "Understood."


def _limpiar_contexto_memoria(texto: str) -> str:
    raw = reparar_unicode(str(texto or "")).replace("\x00", " ").strip()
    if not raw:
        return ""
    drop_tokens = [
        "no new data",
        "does not provide information",
        "prioritize situation",
        '"type": "human"',
        '"type": "ai"',
    ]
    kept = []
    seen = set()
    for ln in re.split(r"[\n|]+", raw):
        line = re.sub(r"\s+", " ", ln).strip(" \t-")
        if not line or len(line) > 220:
            continue
        norm = _normalizar_ascii(line)
        if any(tok in norm for tok in drop_tokens):
            continue
        if norm in seen:
            continue
        seen.add(norm)
        kept.append(line)
        if len(kept) >= 8:
            break
    return "\n".join(f"- {x}" for x in kept)


def parse_reminder(user_input: str) -> tuple[str | None, int | None]:
    text = reparar_unicode(str(user_input or "")).strip()
    patterns = [
        r"\bremind me\s+(.+?)\s+in\s+(\d+)\s*(minutes?|mins?|hours?|hrs?|min|h)\b",
        r"\bremind me\s+(.+?)\s+in\s+(\d+)(minutes?|mins?|hours?|hrs?|min|h)\b",
        r"\b(?:recuerdame|recu.rdam[e]?|recordame|acu.rdam[e]?)\s+(?:de\s+)?(.+?)\s+en\s+(\d+)\s*(minutos?|mins?|min|horas?|hrs?|h)\b",
        r"\b(?:pon(?:me)?\s+un\s+recordatorio(?:\s+para)?|recordatorio(?:\s+para)?)\s+(.+?)\s+en\s+(\d+)\s*(minutos?|mins?|min|horas?|hrs?|h)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        reminder_text = re.sub(
            r"\b(?:por favor|please|gracias|thanks)\b.*$",
            "",
            match.group(1),
            flags=re.IGNORECASE,
        ).strip(" \t\r\n,.;:!?")
        amount = int(match.group(2))
        unit = match.group(3).lower()
        minutes = amount * 60 if "hour" in unit or "hora" in unit or unit in {"h", "hr", "hrs"} else amount
        return reminder_text, minutes
    return None, None


def parsear_recordatorio(user_input: str) -> tuple[str | None, int | None]:
    return parse_reminder(user_input)


def parse_volume_command(
    user_input: str,
) -> tuple[str | None, float | None]:
    text = _normalizar_ascii(user_input).strip().lower()
    volume_markers = (
        "volume",
        "volumen",
        "raise",
        "lower",
        "increase",
        "decrease",
        "sube",
        "subir",
        "baja",
        "bajar",
        "mute",
        "silence",
        "silencia",
    )
    if not any(marker in text for marker in volume_markers):
        return None, None

    normalized = re.sub(r"\s+", " ", text).strip()
    if any(marker in normalized for marker in ("mute", "silence", "silencia", "silenciar")):
        return "absolute", 0

    number_match = re.search(
        r"(?<!\d)-?\d{1,3}(?:[.,]\d+)?\s*%?(?!\d)",
        normalized,
    )
    value: int | None = None
    if number_match:
        try:
            value = int(float(number_match.group(0).replace("%", "").replace(",", ".").strip()))
        except ValueError:
            value = None

    if value is not None:
        explicit_target = bool(
            re.search(
                r"\b(?:to|at|in|a|al|en)\s*"
                r"-?\d{1,3}(?:[.,]\d+)?\s*%?"
                r"(?=\s|$|[.,;:!?])",
                normalized,
            )
            or any(
                marker in normalized
                for marker in (
                    "set",
                    "put",
                    "adjust",
                    "fix",
                    "pon",
                    "ajusta",
                    "fija",
                    "establece",
                )
            )
        )
        if explicit_target:
            return "absolute", value

    if any(
        marker in normalized
        for marker in (
            "lower",
            "decrease",
            "reduce",
            "less",
            "baja",
            "bajar",
        )
    ):
        return "relative", -(value if value is not None else 10)
    if any(
        marker in normalized
        for marker in (
            "raise",
            "increase",
            "more",
            "sube",
            "subir",
            "aumenta",
        )
    ):
        return "relative", value if value is not None else 10
    if value is not None and any(marker in normalized for marker in ("volume", "volumen")):
        return "absolute", value
    return None, None


def parsear_comando_volumen(
    user_input: str,
) -> tuple[str | None, float | None]:
    """Compatibility alias for older plugins and tests."""
    return parse_volume_command(user_input)


def _abrir_en_navegador_sistema(url: str) -> bool:
    try:
        webbrowser.open(url)
        return True
    except Exception:
        return False


def _normalizar_destino_web(url: str) -> str:
    if not url:
        return "https://google.com"
    if url.startswith(("http", "www")):
        return url
    return f"https://www.google.com/search?q={url}"


def _extraer_fragmento_json_desde_texto(txt: str) -> str | None:
    """Extracts the first valid JSON block from a text."""
    m = re.search(r"(\{.*?\})", txt, flags=re.DOTALL)
    if m:
        m.group(1)
        # Expand until valid JSON
        depth = 0
        start = m.start(1)
        for i, ch in enumerate(txt[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return txt[start : i + 1]
    return None


def _compactar_resumen_busqueda(res: Any) -> str:
    import json

    from core import core_tools

    txt = reparar_unicode(str(res or "")).strip()
    if not txt:
        return "I did not find reliable data at this time."

    json_fragment = _extraer_fragmento_json_desde_texto(txt)
    if json_fragment:
        try:
            payload = json.loads(json_fragment)
            items = ((payload or {}).get("web") or {}).get("results") if isinstance(payload, dict) else None
            if isinstance(items, list) and items:
                parts = []
                for it in items[:3]:
                    title = str((it or {}).get("title") or "").strip()
                    desc = str((it or {}).get("description") or "").strip()
                    if title and desc:
                        parts.append(f"{title}: {desc}")
                    elif title:
                        parts.append(title)
                if parts:
                    return core_tools._limpiar_respuesta("Summary found: " + " | ".join(parts))
        except Exception as e:
            print(f"[WARN _compactar_resumen_busqueda] Error: {e}")

    one_line = re.sub(r"\s+", " ", txt)
    return core_tools._limpiar_respuesta(one_line[:700])


def _respuesta_necesita_web_forzarla(user_input: str, reply: str, messages: list) -> bool:
    """In STRICT_WEB_SEARCH mode: True if the topic is dynamic but there was no web search tool call."""
    if not getattr(jarvis_config, "STRICT_WEB_SEARCH", False):
        return False
    from core.brain.social_engine import _debe_buscar_en_web

    if not _debe_buscar_en_web(user_input):
        return False
    web_tool_names = {"buscar_en_internet", "search_on_internet"}
    has_web_tool = any(
        str(getattr(message, "name", "") or "") in web_tool_names
        or any(
            str((tool_call or {}).get("name") or "") in web_tool_names
            for tool_call in (getattr(message, "tool_calls", None) or [])
        )
        for message in messages
    )
    return not has_web_tool
