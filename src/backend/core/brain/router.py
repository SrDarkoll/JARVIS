import ast
import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal, InvalidOperation, localcontext

from core import jarvis_state
from core.app_config import get_default_location
from core.brain.brain_utils import (
    _compactar_resumen_busqueda as _compactar_resumen_busqueda,
)
from core.brain.brain_utils import (
    _normalizar_ascii,
    parse_reminder,
    parse_volume_command,
)
from core.brain.social_engine import _debe_buscar_en_web
from core.command_pipeline.models import (
    ActionPlan,
    ActionStep,
    CommandRequest,
    PlanSource,
)
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
    r"sing|canta|quien|qui.n|who|"
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

_BROAD_WEATHER_REGIONS = frozenset(
    {
        "africa",
        "america",
        "america del norte",
        "america del sur",
        "amazon",
        "amazon rainforest",
        "amazonas",
        "amazonia",
        "antarctica",
        "antartida",
        "asia",
        "europe",
        "europa",
        "mundo",
        "north america",
        "oceania",
        "sahara",
        "south america",
        "the amazon",
        "world",
    }
)


def _lang_is_english() -> bool:
    return get_current_language().startswith("en")


def _language_is_english(language: str | None = None) -> bool:
    if language is None:
        return _lang_is_english()
    return str(language).strip().lower().startswith("en")


def _localized_time(now, language: str | None = None) -> str:
    if _language_is_english(language):
        return f"It is {now.strftime('%I:%M %p').lstrip('0')}."
    return f"Son las {now.strftime('%I:%M %p').lstrip('0')}."


def _localized_date(now, language: str | None = None) -> str:
    if _language_is_english(language):
        day = _DAYS_EN[now.weekday()]
        month = _MONTHS_EN[now.month - 1]
        return f"Today is {day}, {month} {now.day}, {now.year}."
    day = _DAYS_ES[now.weekday()]
    month = _MONTHS_ES[now.month - 1]
    return f"Hoy es {day} {now.day} de {month} de {now.year}."


def _extract_weather_city(text: str, default_city: str | None = None) -> str:
    if default_city is None:
        default_city = get_default_location()
    normalized = _normalizar_ascii(text).strip(" \t\r\n?!.¿¡")
    patterns = [
        r"(?:weather|temperature|forecast)\s+(?:in|for|at)\s+([a-z0-9,\s.-]{3,60})$",
        r"(?:in|for|at)\s+([a-z0-9,\s.-]{3,60})$",
        r"(?:clima|temperatura|pronostico|tiempo)\s+(?:en|de|del|para)\s+([a-z0-9,\s.-]{3,60})$",
        r"(?:en|de|del|para)\s+([a-z0-9,\s.-]{3,60})$",
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
            r"^(?:in|at|for|en|de|del|para)\s+", "", candidate
        ).strip(" ,.-")
        region = re.sub(
            r"^(?:el|la|los|las|the)\s+",
            "",
            _normalizar_ascii(candidate),
        )
        if region in _BROAD_WEATHER_REGIONS:
            return ""
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
    q = re.sub(r"\b(\d{1,2})\s*a\.?\s*m\.?\b", r"\1 AM", q, flags=re.IGNORECASE)
    q = re.sub(r"\b(\d{1,2})\s*p\.?\s*m\.?\b", r"\1 PM", q, flags=re.IGNORECASE)
    if q in {"music", "musica", "song", "a song", "algo", "something", "like"}:
        return ""
    return q


def _extract_music_request(text: str) -> str:
    raw = reparar_unicode(str(text or "")).strip().lower()
    if not raw:
        return ""

    raw = re.sub(
        r"^(reproduce|pon|ponme|play|toca)\.([a-z0-9])",
        r"\1 \2",
        raw,
        flags=re.IGNORECASE,
    )

    play_patterns = [
        r"^(?:pon|ponme|reproduce|play|toca|open|show)\s+(.+)$",
        r"^(?:reproducir|reproduzcas)\s+(.+)$",
        r"^(?:no,?\s+)?(?:es|es la de|es el tema|es la cancion|es un video de|es el video de|me refiero a|hablo de)\s+(.+)$",
        r"^(?:solo\s+quiero\s+que|solo\s+quiero|solo)\s+(?:reproduzcas|pongas|escuchar|escuchemos)?\s*(.+)$",
        r"^(?:put\s+on|put)\s+(.+)$",
        r"\b(?:puedes\s+(?:reproducir|poner|tocar)|"
        r"puedes\s+que\s+(?:reproduzcas|pongas)|"
        r"can\s+(?:you\s+)?(?:play|reproduce|put(?:\s+on)?|open|show|start|launch))\s+(.+)$",
        r"\b(?:i\s+(?:want|need)\s+(?:you\s+to\s+)?(?:play|reproduce|put(?:\s+on)?|open|show)|"
        r"quiero\s+(?:que\s+)?(?:pongas|reproduzcas|escuchar)|"
        r"necesito\s+(?:que\s+)?(?:pongas|reproduzcas))\s+(.+)$",
        r"\b(?:(?:can|could)\s+you|you\s+can)?\s*"
        r"(?:sing|play|reproduce)\s+(?:to\s+)?me\s+(.+)$",
        r"\b(?:canta|cantame|c.ntame)\s+(.+)$",
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


def _format_compound_results(
    results: list[tuple[str, str]],
    language: str | None = None,
) -> str:
    if _language_is_english(language):
        lines = [f"Done. I completed {len(results)} actions:"]
        step_label = "Step"
    else:
        lines = [f"Listo. Ejecute {len(results)} acciones:"]
        step_label = "Paso"
    for index, (_segment, result) in enumerate(results, start=1):
        text = re.sub(r"\s+", " ", str(result or "")).strip()
        lines.append(f"{step_label} {index}: {text}")
    return "\n".join(lines)


def _format_partial_compound_results(
    formatted: str,
    unhandled: list[str],
    language: str | None = None,
) -> str:
    missing = "; ".join(unhandled)
    if _language_is_english(language):
        return f"{formatted}\nI could not understand this part: {missing}. Please rephrase it."
    return f"{formatted}\nNo pude entender esta parte: {missing}. Reformula esa parte."


def _tool_plan(
    request: CommandRequest,
    tool_name: str,
    arguments: Mapping[str, object],
    *,
    step_id: str = "step-1",
    requires_follow_up: bool = False,
) -> ActionPlan:
    return ActionPlan(
        request_id=request.request_id,
        source=PlanSource.DETERMINISTIC,
        steps=(
            ActionStep(
                step_id=step_id,
                tool_name=tool_name,
                arguments=arguments,
            ),
        ),
        requires_follow_up=requires_follow_up,
    )


def _direct_plan(
    request: CommandRequest,
    response: str,
    *,
    should_listen: bool = False,
) -> ActionPlan:
    return ActionPlan(
        request_id=request.request_id,
        source=PlanSource.DETERMINISTIC,
        direct_response=str(response or "").strip(),
        requires_follow_up=should_listen,
    )


def _compound_clarification(
    request: CommandRequest,
    unhandled: list[str],
) -> ActionPlan:
    missing = "; ".join(unhandled)
    if _language_is_english(request.language):
        response = (
            f"I could not understand this part: {missing}. "
            "Please rephrase it before I run any action."
        )
    else:
        response = (
            f"No pude entender esta parte: {missing}. "
            "Reformulela antes de que ejecute alguna accion."
        )
    return _direct_plan(request, response, should_listen=True)


def _plan_compound(request: CommandRequest) -> ActionPlan | None:
    segments = _split_compound_intents(request.text)
    if len(segments) < 2:
        return None

    collected_steps: list[ActionStep] = []
    direct_results: list[tuple[str, str]] = []
    unhandled: list[str] = []
    for segment in segments[:_MAX_COMPOUND_STEPS]:
        segment_request = replace(request, text=segment)
        segment_plan = plan_hybrid(segment_request, allow_compound=False)
        if segment_plan is None:
            unhandled.append(segment)
            continue
        if segment_plan.requires_follow_up:
            return _direct_plan(
                request,
                segment_plan.direct_response,
                should_listen=True,
            )
        if segment_plan.steps:
            collected_steps.extend(segment_plan.steps)
        elif segment_plan.direct_response:
            direct_results.append((segment, segment_plan.direct_response))
        else:
            unhandled.append(segment)

    if unhandled:
        return _compound_clarification(request, unhandled)
    if collected_steps and direct_results:
        return _compound_clarification(
            request,
            [segment for segment, _response in direct_results],
        )
    if collected_steps:
        stable_steps = tuple(
            replace(step, step_id=f"step-{index}")
            for index, step in enumerate(collected_steps, start=1)
        )
        return ActionPlan(
            request_id=request.request_id,
            source=PlanSource.DETERMINISTIC,
            steps=stable_steps,
        )
    if direct_results:
        return _direct_plan(
            request,
            _format_compound_results(direct_results, request.language),
        )
    return None


_ARITHMETIC_RE = re.compile(
    r"(?<![\w.])"
    r"(?P<expression>(?:\b(?:sqrt|cbrt|sin|cos|tan|log|ln|abs|floor|ceil|round|pi|e|tau)\b|√|∛|[-+*/%^()0-9.,\s])+)",
    re.IGNORECASE,
)
_SQUARE_ROOT_RE = re.compile(
    r"\b(?:square root of|raiz cuadrada de)\s+"
    r"(?P<number>[-+]?\d+(?:[.,]\d+)?)\b"
)


def _evaluate_arithmetic_node(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Expression):
        return _evaluate_arithmetic_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Decimal(str(node.value))
    if isinstance(node, ast.Name):
        var = node.id.lower()
        if var in ("pi", "π"):
            return Decimal(str(math.pi))
        if var == "e":
            return Decimal(str(math.e))
        if var == "tau":
            return Decimal(str(math.tau))
        raise ValueError(f"unsupported variable {node.id}")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _evaluate_arithmetic_node(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _evaluate_arithmetic_node(node.left)
        right = _evaluate_arithmetic_node(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ZeroDivisionError("division by zero")
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            if right == 0:
                raise ZeroDivisionError("division by zero")
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            if abs(right) > 100:
                raise ValueError("unsafe exponent")
            if right == right.to_integral_value():
                return left ** int(right)
            return Decimal(str(float(left) ** float(right)))
        raise ValueError("unsupported arithmetic operator")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = node.func.id.lower()
        args = [_evaluate_arithmetic_node(arg) for arg in node.args]
        if len(args) == 1:
            val = float(args[0])
            if fn in ("sqrt", "raiz"):
                if val < 0:
                    raise ValueError("negative sqrt")
                return Decimal(str(math.sqrt(val)))
            if fn == "cbrt":
                return Decimal(str(math.cbrt(val)))
            if fn == "abs":
                return abs(args[0])
            if fn == "sin":
                return Decimal(str(math.sin(val)))
            if fn == "cos":
                return Decimal(str(math.cos(val)))
            if fn == "tan":
                return Decimal(str(math.tan(val)))
            if fn == "log":
                if val <= 0:
                    raise ValueError("invalid log arg")
                return Decimal(str(math.log10(val)))
            if fn == "ln":
                if val <= 0:
                    raise ValueError("invalid ln arg")
                return Decimal(str(math.log(val)))
            if fn == "exp":
                return Decimal(str(math.exp(val)))
            if fn == "floor":
                return Decimal(str(math.floor(val)))
            if fn == "ceil":
                return Decimal(str(math.ceil(val)))
            if fn == "round":
                return Decimal(str(round(val)))
        elif len(args) == 2 and fn == "round":
            return Decimal(str(round(float(args[0]), int(args[1]))))
        elif len(args) == 2 and fn == "log":
            return Decimal(str(math.log(float(args[0]), float(args[1]))))
        raise ValueError(f"unsupported function {fn}")
    raise ValueError("unsupported arithmetic node")


def _format_arithmetic_number(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("non-finite arithmetic result")
    if value == value.to_integral_value():
        return f"{int(value):,}"
    rendered = format(value.normalize(), "f").rstrip("0").rstrip(".")
    integer, dot, fraction = rendered.partition(".")
    integer = f"{int(integer):,}"
    return integer + (dot + fraction if fraction else "")


def _try_square_root_reply(
    text: str,
    language: str | None = None,
) -> str | None:
    normalized = _normalizar_ascii(reparar_unicode(str(text or "")))
    match = _SQUARE_ROOT_RE.search(normalized)
    if not match:
        return None

    display_number = match.group("number")
    number = display_number
    if "," in number and "." not in number:
        integer, fraction = number.rsplit(",", 1)
        number = (
            integer + fraction
            if len(fraction) == 3
            else integer + "." + fraction
        )
    else:
        number = number.replace(",", "")

    try:
        value = Decimal(number)
        if value < 0:
            return None
        with localcontext() as context:
            context.prec = 28
            result = value.sqrt()
    except (ArithmeticError, InvalidOperation, ValueError):
        return None

    if result == result.to_integral_value():
        rendered = _format_arithmetic_number(result)
        approximate = False
    else:
        rendered = format(result, ".10f").rstrip("0").rstrip(".")
        approximate = True

    if _language_is_english(language):
        qualifier = "approximately " if approximate else ""
        return (
            f"The square root of {display_number} is "
            f"{qualifier}{rendered}."
        )
    qualifier = "aproximadamente " if approximate else ""
    return (
        f"La raiz cuadrada de {display_number} es "
        f"{qualifier}{rendered}."
    )


def _try_arithmetic_reply(
    text: str,
    language: str | None = None,
) -> str | None:
    square_root_reply = _try_square_root_reply(text, language)
    if square_root_reply is not None:
        return square_root_reply

    raw_text = reparar_unicode(str(text or "")).strip()
    if not any(ch.isdigit() for ch in raw_text):
        return None

    # Normalizar símbolos y palabras matemáticas comunes antes del parseo
    normalized = (
        raw_text.replace("multiplicado por", "*")
        .replace("multiplied by", "*")
        .replace("dividido entre", "/")
        .replace("dividido por", "/")
        .replace("divided by", "/")
        .replace("por la", "*")
        .replace("por el", "*")
        .replace(" por ", " * ")
        .replace(" times ", " * ")
        .replace(" entre ", " / ")
        .replace(" over ", " / ")
        .replace(" mas ", " + ")
        .replace(" más ", " + ")
        .replace(" plus ", " + ")
        .replace(" menos ", " - ")
        .replace(" minus ", " - ")
        .replace("\u00d7", "*")
        .replace("\u00f7", "/")
        .replace("^", "**")
        .replace("π", "pi")
    )
    # Reemplazar √X o √ X por sqrt(X)
    normalized = re.sub(
        r"√\s*(\d+(?:\.\d+)?|\([^)]+\))",
        r"sqrt(\1)",
        normalized,
    )
    normalized = normalized.replace("√", "sqrt").replace("∛", "cbrt")

    match = _ARITHMETIC_RE.search(normalized)
    if not match:
        return None
    display_expression = re.sub(r"\s+", " ", match.group("expression")).strip().rstrip(".")
    if not any(op in display_expression for op in ["+", "-", "*", "/", "%", "**", "^", "sqrt", "cbrt", "sin", "cos", "tan", "log", "ln", "abs"]):
        return None

    expression = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", display_expression)
    if len(expression) > 200:
        return None
    try:
        parsed = ast.parse(expression, mode="eval")
        with localcontext() as context:
            context.prec = 28
            value = _evaluate_arithmetic_node(parsed)
        return f"{display_expression} = {_format_arithmetic_number(value)}."
    except (ArithmeticError, InvalidOperation, SyntaxError, ValueError, ZeroDivisionError, OverflowError):
        return None


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


def _request_default_location(request: CommandRequest) -> str:
    return str(request.metadata.get("default_location") or "").strip()


def _sports_arguments(t_ascii: str) -> dict[str, str]:
    sport, league = "basketball", "nba"
    if any(key in t_ascii for key in ["nfl", "football", "americano"]):
        sport, league = "football", "nfl"
    elif any(key in t_ascii for key in ["mlb", "baseball", "beisbol"]):
        sport, league = "baseball", "mlb"
    elif any(
        key in t_ascii
        for key in ["champions", "premier", "liga", "futbol", "soccer"]
    ):
        sport = "soccer"
        if "champions" in t_ascii:
            league = "uefa.champions"
        elif "liga" in t_ascii:
            league = "esp.1"
        else:
            league = "eng.1"
    return {"deporte": sport, "liga": league, "consulta": "hoy"}


def _spotify_followup_plan(request: CommandRequest) -> ActionPlan | None:
    raw_choices = request.metadata.get("spotify_pending_choices")
    if not isinstance(raw_choices, (list, tuple)) or not raw_choices:
        return None

    from modules.spotify.desktop.models import SpotifyCandidate
    from modules.spotify.followup import (
        PendingSpotifySelections,
        SpotifySelectionStatus,
    )

    candidates: list[SpotifyCandidate] = []
    for index, item in enumerate(raw_choices[:3], start=1):
        if isinstance(item, SpotifyCandidate):
            candidates.append(item)
            continue
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        candidates.append(
            SpotifyCandidate(
                str(item.get("id") or f"snapshot-{index}"),
                title,
                str(item.get("artist") or "").strip(),
            )
        )
    from modules.spotify.followup import pending_spotify_selections
    if candidates:
        pending_spotify_selections.remember(request.profile_id, candidates)

    if not pending_spotify_selections.has_pending(request.profile_id):
        return None

    resolution = pending_spotify_selections.resolve(request.profile_id, request.text)
    if resolution is None or resolution.status is SpotifySelectionStatus.UNRELATED:
        pending_spotify_selections.clear(request.profile_id)
        return None
    if resolution.status is SpotifySelectionStatus.CANCELLED:
        pending_spotify_selections.clear(request.profile_id)
        response = (
            "Spotify selection cancelled."
            if _language_is_english(request.language)
            else "Seleccion de Spotify cancelada."
        )
        return _direct_plan(request, response)
    if resolution.status is SpotifySelectionStatus.CLARIFY:
        choices = "; ".join(
            f"{index}: {item.title} de {item.artist}"
            for index, item in enumerate(resolution.choices, start=1)
        )
        response = (
            "I could not identify the selection. "
            f"Say first, second, or the title: {choices}. Which one?"
            if _language_is_english(request.language)
            else "No pude identificar la seleccion. "
            f"Di primera, segunda o el titulo: {choices}. Cual?"
        )
        return _direct_plan(request, response, should_listen=True)

    candidate = resolution.candidate
    if candidate is None:
        return None
    query = " ".join(
        part
        for part in (
            candidate.title,
            f"de {candidate.artist}" if candidate.artist else "",
        )
        if part
    )
    return _tool_plan(request, "reproducir_en_spotify", {"cancion": query})


def _weather_clarification(request: CommandRequest) -> ActionPlan:
    response = (
        "Which city should I check?"
        if _language_is_english(request.language)
        else "De que ciudad desea consultar el clima?"
    )
    return _direct_plan(request, response, should_listen=True)


def _youtube_query(song: str) -> str:
    query = re.sub(
        r"\ben\s+youtube\b",
        "",
        song,
        flags=re.IGNORECASE,
    ).strip()
    query = re.sub(r"\byoutube\b", "", query, flags=re.IGNORECASE).strip()
    query = re.sub(r"^[,.\s]+|[,.\s]+$", "", query)
    return re.sub(r"\s*,\s*de\s+", " ", query)


def plan_hybrid(
    request: CommandRequest,
    *,
    allow_compound: bool = True,
) -> ActionPlan | None:
    """Build a deterministic plan without invoking tools or mutating state."""

    user_input = request.text
    text = reparar_unicode(user_input).strip().lower()
    text_ascii = _normalizar_ascii(text)
    if not text:
        return None

    if allow_compound:
        compound_plan = _plan_compound(request)
        if compound_plan is not None:
            return compound_plan

    followup_plan = _spotify_followup_plan(request)
    if followup_plan is not None:
        return followup_plan

    arithmetic_reply = _try_arithmetic_reply(
        user_input,
        request.language,
    )
    if arithmetic_reply is not None:
        return _direct_plan(request, arithmetic_reply)

    if text_ascii in {
        "quien eres",
        "quien eres tu",
        "como te llamas",
        "who are you",
        "what is your name",
        "whats your name",
    }:
        response = (
            "I am J.A.R.V.I.S., your local home assistant."
            if _language_is_english(request.language)
            else "Soy J.A.R.V.I.S., tu asistente local del hogar."
        )
        return _direct_plan(request, response)

    if text_ascii in {
        "quien soy",
        "quien soy yo",
        "yo quien soy",
        "who am i",
        "who am i really",
        "do you recognize me",
    }:
        is_admin = request.profile_id == jarvis_state.DEFAULT_PROFILE_ID
        if _language_is_english(request.language):
            response = (
                "Administrator, you are the active and authorized user."
                if is_admin
                else "You are the active Guest user."
            )
        else:
            response = (
                "Senor, usted es el Administrador, mi usuario activo y autorizado."
                if is_admin
                else "Usted es el usuario Invitado activo."
            )
        return _direct_plan(request, response)

    status_markers = [
        "como estas",
        "como te va",
        "que tal",
        "como anda",
        "how are you",
        "how is it going",
        "how are you doing",
    ]
    if any(key in text_ascii for key in status_markers) and not _has_actionable_marker(
        text_ascii
    ):
        is_admin = request.profile_id == jarvis_state.DEFAULT_PROFILE_ID
        if _language_is_english(request.language):
            response = (
                "One hundred percent operational, Administrator. How may I assist you?"
                if is_admin
                else "One hundred percent operational. How may I assist you, Guest?"
            )
        else:
            response = (
                "Cien por ciento operativo, Administrador. En que puedo ayudarle?"
                if is_admin
                else "Cien por ciento operativo. En que puedo ayudarle, Invitado?"
            )
        return _direct_plan(request, response)

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
    if any(key in text_ascii for key in time_markers | date_markers):
        now = request.received_at.astimezone()
        if any(key in text_ascii for key in time_markers):
            return _direct_plan(request, _localized_time(now, request.language))
        return _direct_plan(request, _localized_date(now, request.language))

    reminder_text, reminder_minutes = parse_reminder(user_input)
    if reminder_text and reminder_minutes:
        return _tool_plan(
            request,
            "poner_recordatorio",
            {"texto": reminder_text, "minutos": reminder_minutes},
        )

    volume_mode, volume_value = parse_volume_command(user_input)
    if volume_mode and volume_value is not None:
        level: object = volume_value
        if volume_mode == "relative":
            level = f"{volume_value:+g}"
        return _tool_plan(request, "ajustar_volumen", {"nivel": level})

    weather_markers = [
        "clima",
        "temperatura",
        "tiempo",
        "pronostico",
        "weather",
        "temperature",
        "forecast",
    ]
    if any(key in text_ascii for key in weather_markers):
        incomplete = re.search(
            r"\b(?:weather|temperature|forecast)\s+(?:in|for|at)\s*$|"
            r"\b(?:clima|temperatura|pronostico|tiempo)\s+(?:en|de|para)\s*$",
            text_ascii.strip(" \t\r\n?!."),
        )
        city = _extract_weather_city(
            text,
            _request_default_location(request),
        )
        if incomplete or not city:
            return _weather_clarification(request)
        return _tool_plan(request, "obtener_clima", {"ciudad": city})

    sports_markers = [
        "nba",
        "basket",
        "basketball",
        "nfl",
        "football",
        "soccer",
        "futbol",
        "f1",
        "formula 1",
        "mlb",
        "baseball",
        "beisbol",
        "tennis",
        "tenis",
        "champions",
        "premier",
        "liga",
    ]
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
    if any(key in text_ascii for key in sports_markers) and any(
        key in text_ascii for key in sports_intent_markers
    ):
        return _tool_plan(
            request,
            "obtener_deportes_espn",
            _sports_arguments(text_ascii),
        )

    is_reminder = any(
        key in text_ascii
        for key in ["recuerdame", "recordatorio", "remind me", "reminder"]
    )
    cancel_markers = [
        "cancela apagado",
        "cancelar apagado",
        "cancela reinicio",
        "cancelar reinicio",
        "cancela hibernacion",
        "cancelar hibernacion",
    ]
    if any(key in text_ascii for key in cancel_markers):
        return _tool_plan(request, "controlar_pc", {"accion": "cancelar"})
    pc_actions = (
        (["apaga", "apagar"], "apagar"),
        (["reinicia", "reiniciar"], "reiniciar"),
        (["hiberna", "hibernar"], "hibernar"),
        (["bloquea", "bloquear"], "bloquear"),
    )
    if not is_reminder:
        for markers, action in pc_actions:
            if any(marker in text for marker in markers):
                return _tool_plan(
                    request,
                    "controlar_pc",
                    {"accion": action},
                )

    mix_seed = _extract_spotify_mix_request(text)
    if mix_seed:
        return _tool_plan(
            request,
            "reproducir_mix_spotify",
            {"semilla": mix_seed},
        )

    song = _extract_music_request(text)
    if song:
        is_youtube = any(
            k in text
            for k in [
                "youtube",
                "video",
                "canal",
                "creador",
                "yt",
                "vdeo",
                "bideo",
            ]
        ) or request.metadata.get("last_media_source") == "youtube"
        if is_youtube:
            return _tool_plan(
                request,
                "reproducir_en_youtube",
                {"query": _youtube_query(song)},
            )
        return _tool_plan(
            request,
            "reproducir_en_spotify",
            {"cancion": song},
        )

    search_match = re.match(
        r"^(?:busca|buscar|busques|encuentra|search|investiga|investigues|"
        r"averigua|averigues|consulta|googlea)[\s,.:;-]+(.+)$",
        text,
    )
    if search_match and search_match.group(1).strip():
        if any(key in text_ascii for key in weather_markers):
            city = _extract_weather_city(
                search_match.group(1),
                _request_default_location(request),
            )
            if not city:
                return _weather_clarification(request)
            return _tool_plan(request, "obtener_clima", {"ciudad": city})
        if any(key in text_ascii for key in sports_markers):
            return _tool_plan(
                request,
                "obtener_deportes_espn",
                _sports_arguments(text_ascii),
            )
        return _tool_plan(
            request,
            "buscar_en_internet",
            {"query": user_input},
        )

    release_markers = [
        "cuando sale",
        "cuando se estrena",
        "sale la",
        "estren",
        "lanzamient",
    ]
    if any(key in text for key in release_markers):
        return _tool_plan(
            request,
            "buscar_en_internet",
            {"query": text},
        )

    price_markers = ["cuesta", "precio", "valor", "vale", "cotiza", "cuanto"]
    if any(key in text for key in price_markers):
        return _tool_plan(
            request,
            "buscar_en_internet",
            {"query": text},
        )

    if _debe_buscar_en_web(text):
        if any(key in text_ascii for key in weather_markers):
            city = _extract_weather_city(
                text,
                _request_default_location(request),
            )
            if not city:
                return _weather_clarification(request)
            return _tool_plan(request, "obtener_clima", {"ciudad": city})
        if any(key in text_ascii for key in sports_markers) or any(
            key in text_ascii for key in sports_intent_markers
        ):
            return _tool_plan(
                request,
                "obtener_deportes_espn",
                _sports_arguments(text_ascii),
            )
        return _tool_plan(
            request,
            "buscar_en_internet",
            {"query": text},
        )

    if text in {
        "recarga plugins",
        "recargar plugins",
        "reload plugins",
        "actualiza plugins",
    }:
        return _tool_plan(request, "recargar_plugins", {})

    routine_markers = [
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
        "buen dia",
    ]
    if any(key in text for key in routine_markers):
        routine = "trabajo"
        if any(
            key in text
            for key in ["gaming", "game", "gamer", "juego", "jugar"]
        ):
            routine = "gaming"
        elif any(key in text for key in ["buenos dias", "buen dia"]):
            routine = "buenos dias"
        return _tool_plan(
            request,
            "ejecutar_rutina",
            {"nombre": routine},
        )

    tech_entities = {
        "openai",
        "gpt",
        "claude",
        "gemini",
        "llama",
        "mistral",
        "qwen",
        "deepseek",
        "groq",
        "anthropic",
        "google ai",
        "langchain",
        "grok",
        "spotify",
        "chatgpt",
    }
    tech_definition = any(
        f"es {entity}" in text for entity in tech_entities
    )
    tech_behavior = any(
        key in text for key in ["funcion", "funciona", "funcionar"]
    ) and any(entity in text for entity in tech_entities)
    if tech_definition or tech_behavior:
        return _tool_plan(
            request,
            "buscar_en_internet",
            {"query": user_input},
        )

    stop_aliases = [
        "para la musica",
        "para musica",
        "stop the music",
        "stop music",
    ]
    if any(marker in text_ascii for marker in stop_aliases) or re.fullmatch(
        r"(?:jarvis\s+)?(?:stop|para)(?:\s+(?:please|por favor))?",
        text_ascii,
    ):
        response = (
            "No action taken."
            if _language_is_english(request.language)
            else "No ejecute ninguna accion."
        )
        return _direct_plan(request, response)

    pause_markers = [
        "pausa la musica",
        "pausa musica",
        "deten la musica",
        "deten musica",
        "pause the music",
        "pause music",
    ]
    if any(marker in text_ascii for marker in pause_markers):
        return _tool_plan(
            request,
            "controlar_reproduccion",
            {"accion": "pausar"},
        )

    media_actions = [
        (["pausa", "pausar", "deten"], "pausar"),
        (["reanuda", "continuar", "resume"], "reanudar"),
        (["siguiente", "next"], "siguiente"),
        (["anterior", "prev", "atras"], "anterior"),
        (["shuffle on", "aleatorio on", "activa shuffle"], "shuffle on"),
        (["shuffle off", "aleatorio off", "desactiva shuffle"], "off"),
    ]
    for markers, action in media_actions:
        if any(marker in text_ascii for marker in markers):
            return _tool_plan(
                request,
                "controlar_reproduccion",
                {"accion": action},
            )

    navigation = re.match(
        r"^(?:abre|abrir|abras|abraz|ve a|navega a|inicia|ejecuta|lanza)"
        r"[\s,.:;-]+(.+)$",
        text,
    )
    if navigation:
        destination_raw, _had_prefix = _extraer_objetivo_apertura(text)
        destination_raw = re.sub(
            r"\s+en\s+(?:el\s+)?navegador$",
            "",
            destination_raw,
        ).strip()
        destination_raw = re.sub(
            r"\s+",
            " ",
            destination_raw,
        ).strip(" \t\r\n.,;:!?")
        destination_normalized = destination_raw.lower()
        is_url = bool(
            re.match(r"^(?:https?://|www\.)", destination_normalized)
        )
        looks_like_domain = bool(
            re.match(
                r"^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$",
                destination_normalized,
            )
        )
        if (
            destination_normalized
            and destination_normalized in _ROUTER_APP_CANDIDATOS
            and not is_url
        ):
            return _tool_plan(
                request,
                "abrir_aplicacion",
                {"nombre_app": destination_raw},
            )
        if (
            "youtube" in destination_raw
            and destination_normalized not in {"youtube", "www.youtube.com"}
        ):
            query = destination_raw.replace("youtube", "").strip()
            return _tool_plan(request, "abrir_youtube", {"query": query})

        destination = _ROUTER_WEB_DIRECTO.get(
            destination_normalized,
            destination_raw,
        )
        if destination:
            return _tool_plan(
                request,
                "abrir_navegador",
                {"destino": destination},
            )
        if is_url or looks_like_domain:
            return _tool_plan(
                request,
                "abrir_navegador",
                {"destino": destination_raw},
            )

    return None


def _legacy_request(user_input: str) -> CommandRequest | None:
    text = reparar_unicode(str(user_input or "")).strip()
    if not text:
        return None
    return CommandRequest.create(
        text=text,
        profile_id=jarvis_state.get_active_profile_id(),
        channel="legacy-router",
        language=get_current_language(),
        metadata={"default_location": get_default_location()},
    )


def _legacy_plan_result(plan: ActionPlan | None) -> str | None:
    if plan is None:
        return None
    obs_inc("router_hits", 1)
    if plan.steps:
        raise RuntimeError("legacy_router_execution_removed")
    return plan.direct_response


def _router_hibrido(
    user_input: str,
    *,
    allow_compound: bool = True,
) -> str | None:
    """Temporary compatibility facade for direct-response-only callers."""

    request = _legacy_request(user_input)
    if request is None:
        return None
    return _legacy_plan_result(
        plan_hybrid(request, allow_compound=allow_compound)
    )


def _router_compuesto(user_input: str) -> str | None:
    """Temporary compatibility facade for compound direct responses."""

    request = _legacy_request(user_input)
    if request is None:
        return None
    return _legacy_plan_result(_plan_compound(request))
