import re
import unicodedata

from core.app_config import get_default_location
from core.brain.brain_utils import _compactar_resumen_busqueda, _normalizar_ascii
from core.jarvis_observability import obs_inc
from utils.jarvis_i18n import get_current_language
from utils.jarvis_text import reparar_unicode

_ROUTER_WEB_DIRECTO = {
    "facebook": "https://www.facebook.com",
    "youtube": "https://www.youtube.com",
    "instagram": "https://www.instagram.com",
    "spotify": "https://open.spotify.com",
    "open spotify": "https://open.spotify.com",
    "twitter": "https://x.com",
    "x.com": "https://x.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
}

_ROUTER_APP_CANDIDATOS = {
    "chrome",
    "google chrome",
    "firefox",
    "edge",
    "microsoft edge",
    "word",
    "excel",
    "powerpoint",
    "paint",
    "cmd",
    "terminal",
    "explorador",
    "administrador de tareas",
    "discord",
    "vscode",
    "visual studio code",
    "visual code",
    "v s code",
    "spotify",
    "steam",
    "obs",
    "opera",
    "brave",
    "breve",
    "brabe",
    "brabi",
    "valoran",
    "baloran",
    "discordia",
    "roblox",
    "minecraft",
    "notepad",
    "el bloc de notas",
    "bloc de notas",
    "la calculadora",
    "calculadora",
    "taskmgr",
    "administrador",
    "el administrador de tareas",
}

_MAX_COMPOUND_STEPS = 5
_ACTION_START_PATTERN = (
    r"(?:dime|di|consulta|consultame|busca|buscar|encuentra|averigua|"
    r"busques|investigues|averigues|pon|ponme|reproduce|reproducir|"
    r"reproduzcas|play|toca|put|abre|abrir|abras|abraz|navega|ve|"
    r"sube|baja|pausa|reanuda|siguiente|anterior|what|how|tell|"
    r"search|open|show|check|recuerdame|recu.rdam[e]?|recordatorio|"
    r"remind|quiero|necesito|i\s+want|i\s+need)"
)
_TRAILING_REQUEST_RE = re.compile(
    r"\b(?:can you do that(?: for me)?|could you do that(?: for me)?|"
    r"puedes hacer eso|puedes hacerlo|por favor|please|thanks|gracias)\b.*$",
    re.IGNORECASE,
)


_DAYS_EN = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
_MONTHS_EN = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
_DAYS_ES = [
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]
_MONTHS_ES = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]


def _lang_is_english() -> bool:
    return get_current_language().startswith("en")


def _localized_time(now) -> str:
    if _lang_is_english():
        return f"It is {now.strftime('%I:%M %p').lstrip('0')}."
    return f"Son las {now.strftime('%I:%M %p').lstrip('0')}."


def _localized_date(now) -> str:
    if _lang_is_english():
        day = _DAYS_EN[now.weekday()]
        month = _MONTHS_EN[now.month - 1]
        return f"Today is {day}, {month} {now.day}, {now.year}."
    day = _DAYS_ES[now.weekday()]
    month = _MONTHS_ES[now.month - 1]
    return f"Hoy es {day} {now.day} de {month} de {now.year}."


def _extract_weather_city(text: str) -> str:
    default_city = get_default_location()
    normalized = _normalizar_ascii(text)
    patterns = [
        r"(?:weather|temperature|forecast)\s+(?:in|for|at)\s+([a-z0-9,\s.-]{3,60})$",
        r"(?:in|for|at)\s+([a-z0-9,\s.-]{3,60})$",
        r"(?:clima|temperatura|pronostico|tiempo)\s+(?:en|de|para)\s+([a-z0-9,\s.-]{3,60})$",
        r"(?:en|de|para)\s+([a-z0-9,\s.-]{3,60})$",
    ]
    ignored = {
        "today",
        "now",
        "right now",
        "this week",
        "the day",
        "hoy",
        "mañana",
        "manana",
        "ahora",
        "esta semana",
        "el dia",
    }
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        candidate = re.sub(
            r"\b(today|now|right now|this week|hoy|manana|mañana|ahora|esta semana)\b",
            "",
            match.group(1),
        )
        candidate = re.sub(r"\s+", " ", candidate).strip(" ,.-")
        candidate = re.sub(
            r"^(?:in|at|for|en|de|para)\s+", "", candidate
        ).strip(" ,.-")
        if candidate and candidate not in ignored:
            return candidate.title()
    return default_city


def _has_actionable_marker(t_ascii: str) -> bool:
    markers = [
        "clima", "temperatura", "pronostico", "weather", "temperature", "forecast",
        "nba", "basket", "basketball", "partido", "partidos", "score", "scores",
        "game", "games", "playing", "resultado", "marcador",
        "pon ", "ponme", "reproduce", "play", "toca", "put ", "spotify",
        "musica", "music", "song", "cancion",
        "busca", "buscar", "search", "consulta", "internet", "web",
        "abre", "abrir", "open", "navega", "ve a",
        "volumen", "volume", "pausa", "reanuda", "siguiente", "anterior",
        "apaga", "reinicia", "hiberna", "bloquea",
    ]
    return any(marker in t_ascii for marker in markers)


def _clean_segment(segment: str) -> str:
    cleaned = reparar_unicode(str(segment or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t\r\n,.;:!?")
    cleaned = re.sub(
        r"^(?:first|primero|then|despu.s|luego)[,\s]+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def _split_compound_intents(text: str) -> list[str]:
    raw = reparar_unicode(str(text or ""))
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return []

    marked = re.sub(
        r"\b(?:and\s+then|then|after\s+that|afterwards|"
        r"y\s+luego|y\s+despu.s|despu.s|luego|also|tambi.n|adem.s)\b",
        " || ",
        raw,
        flags=re.IGNORECASE,
    )
    marked = re.sub(
        rf"\s+(?:y|and)\s+(?={_ACTION_START_PATTERN}\b)",
        " || ",
        marked,
        flags=re.IGNORECASE,
    )
    marked = re.sub(r"\s*;\s*", " || ", marked)
    segments = [_clean_segment(part) for part in marked.split("||")]
    return [segment for segment in segments if segment]


def _clean_music_query(value: str) -> str:
    q = reparar_unicode(str(value or "")).lower()
    q = _TRAILING_REQUEST_RE.sub("", q)
    q = re.sub(r"\s+", " ", q).strip(" \t\r\n,.;:!?")
    q = re.sub(
        r"^(?:music|musica|m.sica|a song|song|track|"
        r"la canci.n|una canci.n|canci.n)\s+",
        "",
        q,
        flags=re.IGNORECASE,
    )
    q = re.sub(r"^(?:on|en)\s+spotify[\s,]+", "", q, flags=re.IGNORECASE)
    q = re.sub(r"^spotify\s+", "", q, flags=re.IGNORECASE)
    q = re.sub(
        r"^(?:(?:la|una)?\s*canci.n\s+)?(?:that\s+(?:is|its|it's|his)\s+name\s+is|"
        r"called|named|(?:que\s+)?se\s+llama|llamada|llamado|nombre\s+es)\s+",
        "",
        q,
        flags=re.IGNORECASE,
    )
    q = re.sub(r"\s+", " ", q).strip(" \t\r\n,.;:!?")
    if q in {"music", "musica", "song", "a song", "algo", "something", "like"}:
        return ""
    return q


def _extract_music_request(text: str) -> str:
    raw = reparar_unicode(str(text or "")).strip().lower()
    if not raw:
        return ""

    name_pattern = (
        r"\b(?:that\s+(?:is|its|it's|his)\s+name\s+is|"
        r"called|named|se\s+llama|llamada|llamado|nombre\s+es)\s+(.+)$"
    )
    if "spotify" in raw or any(
        k in raw for k in ["play", "reproduce", "pon", "toca", "put"]
    ):
        match = re.search(name_pattern, raw, flags=re.IGNORECASE)
        if match:
            query = _clean_music_query(match.group(1))
            if query:
                return query

    play_patterns = [
        r"^(?:pon|ponme|reproduce|play|toca)\s+(.+)$",
        r"^(?:reproducir|reproduzcas)\s+(.+)$",
        r"^(?:put\s+on|put)\s+(.+)$",
        r"\b(?:puedes\s+(?:reproducir|poner|tocar)|"
        r"puedes\s+que\s+(?:reproduzcas|pongas)|"
        r"can\s+you\s+(?:play|put(?:\s+on)?))\s+(.+)$",
        r"\b(?:i\s+(?:want|need)\s+(?:you\s+to\s+)?(?:play|put(?:\s+on)?)|"
        r"quiero\s+(?:que\s+)?(?:pongas|reproduzcas|escuchar)|"
        r"necesito\s+(?:que\s+)?(?:pongas|reproduzcas))\s+(.+)$",
    ]
    for pattern in play_patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if not match:
            continue
        query = _clean_music_query(match.group(1))
        if query:
            return query
    return ""


def _extract_spotify_mix_request(text: str) -> str:
    raw = reparar_unicode(str(text or "")).strip().lower()
    if not raw or not any(k in raw for k in ["mix", "playlist", "radio", "recomend"]):
        return ""
    patterns = [
        r"\b(?:pon|ponme|reproduce|play|toca|crea|arma)\s+(?:un\s+|una\s+)?(?:mix|playlist|radio|recomendaciones?)\s+(?:similar(?:es)?\s+a|basad[oa]s?\s+en|de|para|like|of|for)?\s*(.+)$",
        r"\b(?:mix|playlist|radio|recomendaciones?)\s+(?:similar(?:es)?\s+a|basad[oa]s?\s+en|de|para|like|of|for)\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if not match:
            continue
        seed = match.group(1)
        seed = re.sub(r"\b(?:en|on)\s+spotify\b", "", seed, flags=re.IGNORECASE)
        seed = re.sub(r"\bspotify\b", "", seed, flags=re.IGNORECASE)
        seed = _TRAILING_REQUEST_RE.sub("", seed)
        seed = re.sub(r"\s+", " ", seed).strip(" \t\r\n,.;:!?")
        if seed:
            return seed
    return ""


def _format_compound_results(results: list[tuple[str, str]]) -> str:
    lines = [f"Listo. Ejecute {len(results)} acciones:"]
    for index, (_segment, result) in enumerate(results, start=1):
        text = re.sub(r"\s+", " ", str(result or "")).strip()
        lines.append(f"Paso {index}: {text}")
    return "\n".join(lines)


def _router_compuesto(user_input: str) -> str | None:
    segments = _split_compound_intents(user_input)
    if len(segments) < 2:
        return None

    routed: list[tuple[str, str]] = []
    for segment in segments[:_MAX_COMPOUND_STEPS]:
        result = _router_hibrido(segment, allow_compound=False)
        if result is not None:
            routed.append((segment, str(result)))

    if len(routed) < 2:
        return None
    obs_inc("router_hits", 1)
    return _format_compound_results(routed)


def _extraer_objetivo_apertura(texto: str) -> tuple[str, bool]:
    raw = reparar_unicode(str(texto or ""))
    normalized = "".join(
        ch for ch in unicodedata.normalize("NFKD", raw) if not unicodedata.combining(ch)
    )
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    m_open = re.match(r"^(?:abre|abrir|abras|abraz|inicia|ejecuta|lanza)[\s,.:;-]+(.+)$", normalized)
    if not m_open:
        return "", False
    objetivo = m_open.group(1).strip()
    objetivo = re.sub(r"\s+en\s+el\s+navegador$", "", objetivo).strip()
    objetivo = re.sub(r"\s+en\s+navegador$", "", objetivo).strip()
    objetivo = re.sub(r"\s+en\s+internet$", "", objetivo).strip()
    objetivo_sin_pref = re.sub(
        r"^(?:la|el|una|un)?\s*(?:aplic\w*|app|programa)\s+", "", objetivo
    ).strip()
    if not objetivo_sin_pref:
        objetivo_sin_pref = re.sub(r"^(?:el|la|un|una)\s+", "", objetivo).strip()
    return objetivo_sin_pref or objetivo, objetivo_sin_pref != objetivo


def _router_hibrido(user_input: str, *, allow_compound: bool = True) -> str | None:
    from core.brain.processor import _invocar_tool_wrapper
    from core.brain.social_engine import _debe_buscar_en_web

    t = reparar_unicode(user_input or "").strip().lower()
    t_ascii = _normalizar_ascii(t)
    if not t:
        return None
    if allow_compound:
        compound_reply = _router_compuesto(user_input)
        if compound_reply is not None:
            return compound_reply

    # =========================================================
    # TOOL ROUTING DIRECTO — sin pasar por LLM
    # =========================================================

    # Respuestas directas (sin LLM, sin herramientas)
    if t_ascii in {
        "quien eres",
        "quien eres tu",
        "como te llamas",
        "who are you",
        "what is your name",
        "whats your name",
    }:
        obs_inc("router_hits", 1)
        if _lang_is_english():
            return "I am J.A.R.V.I.S., your local home assistant."
        return "Soy J.A.R.V.I.S., tu asistente local del hogar."

    if t in [
        "quién soy",
        "quien soy",
        "quién soy yo",
        "quien soy yo",
        "yo quién soy",
        "yo quien soy",
    ] or t_ascii in {"who am i", "who am i really", "do you recognize me"}:
        obs_inc("router_hits", 1)
        if _lang_is_english():
            return "Administrator, you are the active and authorized user."
        return "Señor, usted es el Administrador, mi usuario activo y autorizado."

    if any(
        k in t
        for k in [
            "cómo estás",
            "como estás",
            "cómo te va",
            "como te va",
            "qué tal",
            "que tal",
            "cómo anda",
            "como anda",
            "how are you",
            "how is it going",
            "how are you doing",
        ]
    ) and not _has_actionable_marker(t_ascii):
        obs_inc("router_hits", 1)
        if _lang_is_english():
            return "One hundred percent operational, Administrator. How may I assist you?"
        return "Cien por ciento operativo, Administrador. ¿En qué puedo ayudarle?"

    time_markers = {"que hora es", "what time is it", "current time", "time is it"}
    date_markers = {
        "que dia es",
        "que fecha",
        "what day is it",
        "what is the date",
        "whats the date",
        "what's the date",
        "today's date",
        "todays date",
    }
    if any(k in t_ascii for k in time_markers | date_markers):
        from datetime import datetime

        ahora = datetime.now()
        if any(k in t_ascii for k in time_markers):
            obs_inc("router_hits", 1)
            return _localized_time(ahora)
        obs_inc("router_hits", 1)
        return _localized_date(ahora)

    # Clima directo (cualquier mención de clima/temperatura)
    weather_markers = ["clima", "temperatura", "tiempo", "pronostico", "weather", "temperature", "forecast"]
    if any(k in t_ascii for k in weather_markers):
        ciudad = _extract_weather_city(t)
        obs_inc("router_hits", 1)
        return str(
            _invocar_tool_wrapper(
                "obtener_clima",
                {"ciudad": ciudad},
                user_input,
                source="router_directo",
            )
        )

    sports_markers = ["nba", "basket", "basketball", "nfl", "football", "soccer", "futbol", "fútbol", "f1", "formula 1", "mlb", "baseball", "beisbol", "tennis", "tenis", "champions", "premier", "liga"]
    sports_intent_markers = [
        "partido",
        "partidos",
        "jugando",
        "juega",
        "marcador",
        "resultado",
        "score",
        "scores",
        "game",
        "games",
        "playing",
    ]
    if any(k in t_ascii for k in sports_markers) and any(k in t_ascii for k in sports_intent_markers):
        obs_inc("router_hits", 1)

        # Simple extraction for common leagues
        deporte_val, liga_val = "basketball", "nba"
        if any(k in t_ascii for k in ["nfl", "football", "americano"]):
            deporte_val, liga_val = "football", "nfl"
        elif any(k in t_ascii for k in ["mlb", "baseball", "beisbol"]):
            deporte_val, liga_val = "baseball", "mlb"
        elif any(k in t_ascii for k in ["champions", "premier", "liga", "futbol", "soccer", "fútbol"]):
            deporte_val = "soccer"
            if "champions" in t_ascii: liga_val = "uefa.champions"
            elif "premier" in t_ascii: liga_val = "eng.1"
            elif "liga" in t_ascii: liga_val = "esp.1"
            else: liga_val = "eng.1" # Default to premier

        return str(
            _invocar_tool_wrapper(
                "obtener_deportes_espn",
                {"deporte": deporte_val, "liga": liga_val, "consulta": "hoy"},
                user_input,
                source="router_directo",
            )
        )

    # PC Control — skip if the input is a reminder (e.g. "recuérdame apagar la estufa")
    _is_reminder = any(k in t_ascii for k in ["recuerdame", "recordatorio", "remind me", "reminder"])
    if not _is_reminder and any(k in t for k in ["apaga", "apagar"]):
        return str(
            _invocar_tool_wrapper("controlar_pc", {"accion": "apagar"}, user_input, source="router")
        )
    if not _is_reminder and any(k in t for k in ["reinicia", "reiniciar"]):
        return str(
            _invocar_tool_wrapper(
                "controlar_pc", {"accion": "reiniciar"}, user_input, source="router"
            )
        )
    if not _is_reminder and any(k in t for k in ["hiberna", "hibernar"]):
        return str(
            _invocar_tool_wrapper(
                "controlar_pc", {"accion": "hibernar"}, user_input, source="router"
            )
        )
    if not _is_reminder and any(k in t for k in ["bloquea", "bloquear"]):
        return str(
            _invocar_tool_wrapper(
                "controlar_pc", {"accion": "bloquear"}, user_input, source="router"
            )
        )
    if any(k in t for k in ["cancela apagado", "cancelar apagado", "cancela reinicio", "cancelar reinicio", "cancela hibernacion", "cancelar hibernacion"]):
        return str(
            _invocar_tool_wrapper(
                "controlar_pc", {"accion": "cancelar"}, user_input, source="router"
            )
        )

    mix_seed = _extract_spotify_mix_request(t)
    if mix_seed:
        obs_inc("router_hits", 1)
        return str(
            _invocar_tool_wrapper(
                "reproducir_mix_spotify",
                {"semilla": mix_seed},
                user_input,
                source="router",
            )
        )

    # Spotify / YouTube Play must win over dynamic web search.
    cancion = _extract_music_request(t)
    if cancion:
        if "en youtube" in t or "youtube" in t:
            q_yt = re.sub(r"\ben\s+youtube\b", "", cancion, flags=re.IGNORECASE).strip()
            q_yt = re.sub(r"\byoutube\b", "", q_yt, flags=re.IGNORECASE).strip()
            q_yt = re.sub(r"^[,\.\s]+|[,\.\s]+$", "", q_yt)
            q_yt = re.sub(r"\s*,\s*de\s+", " ", q_yt)
            obs_inc("router_hits", 1)
            return str(
                _invocar_tool_wrapper(
                    "abrir_youtube", {"query": q_yt}, user_input, source="router"
                )
            )
        obs_inc("router_hits", 1)
        return str(
            _invocar_tool_wrapper(
                "reproducir_en_spotify",
                {"cancion": cancion},
                user_input,
                source="router",
            )
        )

    # Search intents
    m_search = re.match(
        r"^(?:busca|buscar|busques|encuentra|search|investiga|investigues|averigua|averigues|consulta|googlea)[\s,.:;-]+(.+)$",
        t,
    )
    if m_search:
        q = m_search.group(1).strip()
        if q:
            if any(k in t_ascii for k in weather_markers):
                ciudad_search = _extract_weather_city(q)
                obs_inc("router_hits", 1)
                return str(
                    _invocar_tool_wrapper(
                        "obtener_clima",
                        {"ciudad": ciudad_search},
                        user_input,
                        source="router",
                    )
                )
            if any(k in t_ascii for k in sports_markers):
                obs_inc("router_hits", 1)

                deporte_val, liga_val = "basketball", "nba"
                if any(k in t_ascii for k in ["nfl", "football", "americano"]):
                    deporte_val, liga_val = "football", "nfl"
                elif any(k in t_ascii for k in ["mlb", "baseball", "beisbol"]):
                    deporte_val, liga_val = "baseball", "mlb"
                elif any(k in t_ascii for k in ["champions", "premier", "liga", "futbol", "soccer", "fútbol"]):
                    deporte_val = "soccer"
                    if "champions" in t_ascii: liga_val = "uefa.champions"
                    elif "premier" in t_ascii: liga_val = "eng.1"
                    elif "liga" in t_ascii: liga_val = "esp.1"
                    else: liga_val = "eng.1"

                return str(
                    _invocar_tool_wrapper(
                        "obtener_deportes_espn",
                        {"deporte": deporte_val, "liga": liga_val, "consulta": "hoy"},
                        user_input,
                        source="router",
                    )
                )

            obs_inc("router_hits", 1)
            if "youtube" in t and "reproduce" not in t and "pon" not in t:
                from core.core_tools import _buscar_multi_fuente

                return _compactar_resumen_busqueda(
                    _buscar_multi_fuente(user_input, es_youtube=True)
                )
            return _compactar_resumen_busqueda(
                _invocar_tool_wrapper(
                    "buscar_en_internet",
                    {"query": user_input},
                    user_input,
                    source="router",
                )
            )

    # Queries containing "sale" (cuándo sale, cuando sale, etc.) → always web search
    if any(k in t for k in ["cuando sale", "cuándo sale", "sale la", "estren", "lanzamient"]):
        return _compactar_resumen_busqueda(
            _invocar_tool_wrapper(
                "buscar_en_internet", {"query": t}, user_input, source="router_dynamic"
            )
        )

    # Queries with price indicators → always web search (bypass LLM refusal)
    if any(k in t for k in ["cuesta", "precio", "valor", "vale", "cotiza", "cuanto"]):
        return _compactar_resumen_busqueda(
            _invocar_tool_wrapper(
                "buscar_en_internet", {"query": t}, user_input, source="router_dynamic"
            )
        )

    # Dynamic Search
    if _debe_buscar_en_web(t):
        if any(k in t_ascii for k in weather_markers):
            ciudad = _extract_weather_city(t)
            return str(
                _invocar_tool_wrapper(
                    "obtener_clima",
                    {"ciudad": ciudad},
                    user_input,
                    source="router_dynamic",
                )
            )
        if any(k in t_ascii for k in sports_markers) or any(k in t_ascii for k in sports_intent_markers):
            deporte_val, liga_val = "basketball", "nba"
            if any(k in t_ascii for k in ["nfl", "football", "americano"]):
                deporte_val, liga_val = "football", "nfl"
            elif any(k in t_ascii for k in ["mlb", "baseball", "beisbol"]):
                deporte_val, liga_val = "baseball", "mlb"
            elif any(k in t_ascii for k in ["champions", "premier", "liga", "futbol", "soccer", "fútbol"]):
                deporte_val = "soccer"
                if "champions" in t_ascii: liga_val = "uefa.champions"
                elif "premier" in t_ascii: liga_val = "eng.1"
                elif "liga" in t_ascii: liga_val = "esp.1"
                else: liga_val = "eng.1"

            return str(
                _invocar_tool_wrapper(
                    "obtener_deportes_espn",
                    {"deporte": deporte_val, "liga": liga_val, "consulta": "hoy"},
                    user_input,
                    source="router_dynamic",
                )
            )
        return _compactar_resumen_busqueda(
            _invocar_tool_wrapper(
                "buscar_en_internet", {"query": t}, user_input, source="router_dynamic"
            )
        )

    if t in {
        "recarga plugins",
        "recargar plugins",
        "reload plugins",
        "actualiza plugins",
    }:
        obs_inc("router_hits", 1)
        from core.brain.tool_manager import _recargar_plugins_runtime

        return _recargar_plugins_runtime()

    # Routines
    if any(
        k in t
        for k in [
            "modo trabajo",
            "rutina trabajo",
            "modo de trabajo",
            "modo gaming",
            "rutina gaming",
            "modo game",
            "modo gamer",
            "modo juego",
            "modo de juego",
            "rutina de juego",
            "buenos dias",
            "buenos días",
            "buen dia",
            "buen día",
        ]
    ):
        nombre = "trabajo"
        if any(k in t for k in ["gaming", "game", "gamer", "juego", "jugar"]):
            nombre = "gaming"
        elif any(k in t for k in ["buenos dias", "buenos días", "buen dia", "buen día"]):
            nombre = "buenos dias"
        obs_inc("router_hits", 1)
        return str(
            _invocar_tool_wrapper(
                "ejecutar_rutina", {"nombre": nombre}, user_input, source="router"
            )
        )

# ── Info queries about tech entities → force web search ─────────────────
    # Tech info queries should fetch current info instead of answering from memory.
    # Also catches "funciona X", "dime sobre X", "necesito saber de X" for tech topics.
    if any(
        k in t
        for k in [
            "es openai", "es gpt", "es llama", "es groq", "es claude",
            "es google", "es anthropic", "es langchain", "es grok", "es spotify",
        ]
    ) or (
        any(k in t for k in ["funcion", "funciona", "funcionar"])
        and any(e in t for e in [
            "openai", "gpt", "claude", "groq", "grok", "langchain", "spotify"
        ])
    ):
        _INFO_ENTIDADES_TECH = {
            "openai", "gpt", "claude", "gemini", "llama",
            "mistral", "qwen", "deepseek", "groq", "anthropic", "google ai",
            "langchain", "grok", "spotify", "chatgpt",
        }
        if any(e in t for e in _INFO_ENTIDADES_TECH):
            obs_inc("router_hits", 1)
            return _compactar_resumen_busqueda(
                _invocar_tool_wrapper(
                    "buscar_en_internet",
                    {"query": user_input},
                    user_input,
                    source="router",
                )
            )

    # Media Control
    disabled_stop_aliases = [
        "para la musica",
        "para musica",
        "stop the music",
        "stop music",
    ]
    if (
        any(marker in t_ascii for marker in disabled_stop_aliases)
        or re.fullmatch(r"(?:jarvis\s+)?(?:stop|para)(?:\s+(?:please|por favor))?", t_ascii)
    ):
        obs_inc("router_hits", 1)
        if _lang_is_english():
            return "No action taken."
        return "No ejecuté ninguna acción."

    pause_media_markers = [
        "pausa la musica",
        "pausa musica",
        "deten la musica",
        "deten musica",
        "pause the music",
        "pause music",
    ]
    if any(marker in t_ascii for marker in pause_media_markers):
        obs_inc("router_hits", 1)
        return str(
            _invocar_tool_wrapper(
                "controlar_reproduccion",
                {"accion": "pausar"},
                user_input,
                source="router",
            )
        )

    mapa_accion = [
        (["pausa", "pausar", "deten", "detén"], "pausar"),
        (["reanuda", "continuar", "resume"], "reanudar"),
        (["siguiente", "next"], "siguiente"),
        (["anterior", "prev", "atras", "atrás"], "anterior"),
        (["shuffle on", "aleatorio on", "activa shuffle"], "shuffle on"),
        (["shuffle off", "aleatorio off", "desactiva shuffle"], "off"),
    ]
    for keys, accion in mapa_accion:
        if any(k in t for k in keys):
            obs_inc("router_hits", 1)
            return str(
                _invocar_tool_wrapper(
                    "controlar_reproduccion",
                    {"accion": accion},
                    user_input,
                    source="router",
                )
            )

    # Navigation
    m_nav = re.match(r"^(?:abre|abrir|abras|abraz|ve a|navega a|inicia|ejecuta|lanza)[\s,.:;-]+(.+)$", t)
    if m_nav:
        dest_raw, _ = _extraer_objetivo_apertura(t)
        dest_raw = re.sub(r"\s+en\s+el\s+navegador$", "", dest_raw).strip()
        dest_raw = re.sub(r"\s+en\s+navegador$", "", dest_raw).strip()
        dest_raw = re.sub(r"\s+", " ", dest_raw).strip(" \t\r\n.,;:!?")
        dest_norm = (dest_raw or "").lower()

        es_url = bool(re.match(r"^(?:https?://|www\.)", dest_norm))
        parece_dominio = bool(re.match(r"^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$", dest_norm))

        if dest_norm and (dest_norm in _ROUTER_APP_CANDIDATOS and not es_url):
            obs_inc("router_hits", 1)
            return str(
                _invocar_tool_wrapper(
                    "abrir_aplicacion",
                    {"nombre_app": dest_raw},
                    user_input,
                    source="router",
                )
            )
        if "youtube" in dest_raw and dest_norm not in {"youtube", "www.youtube.com"}:
            q = dest_raw.replace("youtube", "").strip()
            obs_inc("router_hits", 1)
            return str(
                _invocar_tool_wrapper("abrir_youtube", {"query": q}, user_input, source="router")
            )
        destino = _ROUTER_WEB_DIRECTO.get(dest_norm, dest_raw)
        if destino:
            obs_inc("router_hits", 1)
            return str(
                _invocar_tool_wrapper(
                    "abrir_navegador", {"destino": destino}, user_input, source="router"
                )
            )

        # Fallback: si parece URL o dominio, abrir en navegador
        if es_url or parece_dominio:
            obs_inc("router_hits", 1)
            return str(
                _invocar_tool_wrapper(
                    "abrir_navegador", {"destino": dest_raw}, user_input, source="router"
                )
            )

    mix_seed = _extract_spotify_mix_request(t)
    if mix_seed:
        obs_inc("router_hits", 1)
        return str(
            _invocar_tool_wrapper(
                "reproducir_mix_spotify",
                {"semilla": mix_seed},
                user_input,
                source="router",
            )
        )

    # Spotify / YouTube Play
    cancion = _extract_music_request(t)
    if cancion:
        if "en youtube" in t or "youtube" in t:
            q_yt = re.sub(r"\ben\s+youtube\b", "", cancion, flags=re.IGNORECASE).strip()
            q_yt = re.sub(r"\byoutube\b", "", q_yt, flags=re.IGNORECASE).strip()
            q_yt = re.sub(r"^[,\.\s]+|[,\.\s]+$", "", q_yt)
            q_yt = re.sub(r"\s*,\s*de\s+", " ", q_yt)
            obs_inc("router_hits", 1)
            return str(
                _invocar_tool_wrapper(
                    "abrir_youtube", {"query": q_yt}, user_input, source="router"
                )
            )
        obs_inc("router_hits", 1)
        return str(
            _invocar_tool_wrapper(
                "reproducir_en_spotify",
                {"cancion": cancion},
                user_input,
                source="router",
            )
        )

    return None
