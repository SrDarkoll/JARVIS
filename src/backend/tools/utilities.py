"""Utilidades variadas: clima, NBA, recordatorios, archivos, motivacional, rutinas, briefing, pantalla."""

import base64
import json
import logging
import os
import re
import threading
import time as _uti_time
from datetime import datetime, timedelta

import requests as http_requests
from core import jarvis_state
from core.app_config import get_default_location
from core.service_container import services
from langchain_core.tools import tool
from requests.exceptions import RequestException
from utils.jarvis_auth import verificar_autorizacion
from utils.jarvis_i18n import get_bt, get_current_language
from utils.math_expression import (
    evaluate_math_expression,
    format_math_number,
    normalize_math_expression,
)

from tools._common import (
    BASE_DIR,
    ROOT_DIR,
    _normalizar_profile_id,
    _warn_once,
)

# Ya no usamos variables locales, usamos services.recordatorios, services.noticias_cache, etc.

NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")


def _extract_llm_text(response) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    content = getattr(response, "content", None)
    if content is not None:
        return str(content)
    if isinstance(response, dict):
        content = response.get("content")
        if content is not None:
            return str(content)
        choices = response.get("choices") or []
        if choices:
            first_choice = choices[0] or {}
            message = first_choice.get("message") if isinstance(first_choice, dict) else None
            if isinstance(message, dict) and message.get("content") is not None:
                return str(message.get("content"))
    return str(response)


def _bloqueo_si_no_autorizado() -> str | None:
    pid = _normalizar_profile_id(jarvis_state.get_active_profile_id())
    if not verificar_autorizacion(pid):
        return "ACCESO_DENEGADO: Requiere autorización explícita del Administrador."
    return None


TEMAS_NEWSAPI = [
    "technology OR artificial intelligence",
    "football OR NBA OR sports",
    "economy OR markets OR finance",
    "world politics OR international",
    "Mexico",
    "science OR space OR NASA",
    "cybersecurity OR hacking",
]

TEMAS_NEWSAPI_ES = [
    "tecnologia OR inteligencia artificial",
    "futbol OR NBA OR deportes",
    "economia OR mercados OR finanzas",
    "politica mundial OR internacional",
    "Mexico",
    "ciencia OR espacio OR NASA",
    "ciberseguridad OR hacking",
]


# ─────────────────────────────────────────
# Clima
# ─────────────────────────────────────────
def _mapear_weather_code_openmeteo(code: int, lang: str = "es") -> str:
    tabla_es = {
        0: "Despejado",
        1: "Mayormente despejado",
        2: "Parcialmente nublado",
        3: "Nublado",
        45: "Niebla",
        48: "Niebla",
        51: "Llovizna",
        53: "Llovizna",
        55: "Llovizna",
        56: "Llovizna",
        57: "Llovizna",
        61: "Lluvia",
        63: "Lluvia",
        65: "Lluvia fuerte",
        66: "Lluvia",
        67: "Lluvia",
        71: "Nieve",
        73: "Nieve",
        75: "Nieve",
        77: "Nieve",
        80: "Chubascos",
        81: "Chubascos",
        82: "Chubascos fuertes",
        85: "Nieve",
        86: "Nieve",
        95: "Tormenta",
        96: "Tormenta",
        99: "Tormenta fuerte",
    }
    tabla_en = {
        0: "Clear",
        1: "Mostly clear",
        2: "Partly cloudy",
        3: "Cloudy",
        45: "Fog",
        48: "Fog",
        51: "Drizzle",
        53: "Drizzle",
        55: "Drizzle",
        56: "Drizzle",
        57: "Drizzle",
        61: "Rain",
        63: "Rain",
        65: "Heavy rain",
        66: "Rain",
        67: "Rain",
        71: "Snow",
        73: "Snow",
        75: "Snow",
        77: "Snow",
        80: "Showers",
        81: "Showers",
        82: "Heavy showers",
        85: "Snow",
        86: "Snow",
        95: "Storm",
        96: "Storm",
        99: "Strong storm",
    }
    tabla = tabla_en if str(lang or "").lower().startswith("en") else tabla_es
    default_desc = "Clear" if tabla is tabla_en else "Despejado"
    return tabla.get(int(code), default_desc)


_DETECTED_IP_GEO = None
_IP_GEO_LAST_ATTEMPT = 0.0
_IP_GEO_LOCK = threading.Lock()


def _ip_geolocation_enabled() -> bool:
    return str(os.getenv("JARVIS_IP_GEOLOCATION_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}


def _ip_geolocation_cooldown_seconds() -> float:
    try:
        value = float(str(os.getenv("JARVIS_IP_GEOLOCATION_COOLDOWN_SECONDS") or "300").strip())
    except (TypeError, ValueError):
        value = 300.0
    return max(30.0, min(value, 3600.0))


def _auto_detect_ip_location() -> dict | None:
    """Detect the public-IP location only after explicit opt-in."""
    global _DETECTED_IP_GEO, _IP_GEO_LAST_ATTEMPT
    if not _ip_geolocation_enabled():
        return None

    with _IP_GEO_LOCK:
        if _DETECTED_IP_GEO is not None:
            return dict(_DETECTED_IP_GEO)

        now = _uti_time.monotonic()
        if _IP_GEO_LAST_ATTEMPT and now - _IP_GEO_LAST_ATTEMPT < _ip_geolocation_cooldown_seconds():
            return None
        _IP_GEO_LAST_ATTEMPT = now

        providers = (
            "https://ipapi.co/json/",
            "https://ipwho.is/",
        )
        for url in providers:
            try:
                response = http_requests.get(url, timeout=3)
                if response.status_code != 200:
                    continue
                data = response.json() or {}
                if data.get("success") is False:
                    continue
                city = data.get("city") or data.get("region")
                latitude = data.get("lat")
                if latitude is None:
                    latitude = data.get("latitude")
                longitude = data.get("lon")
                if longitude is None:
                    longitude = data.get("longitude")
                country = data.get("country_name") or data.get("country")
                if latitude is None or longitude is None:
                    continue
                _DETECTED_IP_GEO = {
                    "city": str(city or ""),
                    "lat": str(latitude),
                    "lon": str(longitude),
                    "country": str(country or ""),
                }
                return dict(_DETECTED_IP_GEO)
            except Exception:
                continue
    return None


def _auto_init_weather() -> None:
    """Initialize weather from configured location or opted-in IP lookup."""
    try:
        geo = _auto_detect_ip_location()
        city = geo.get("city") if geo else None
        desc, temp = _obtener_clima_logic(ciudad=city)
        if services.weather_cache and geo and geo.get("city") and desc and desc != "Sincronizando...":
            if not str(services.weather_cache.get("desc", "")).startswith(geo["city"]):
                services.weather_cache["desc"] = f"{geo['city']} ({desc})"
    except Exception as e:
        _warn_once("auto_weather_init", f"Auto weather init error: {e}")


def _get_default_location() -> str:
    geo = _auto_detect_ip_location()
    if geo and geo.get("city"):
        return geo["city"]
    return get_default_location()


def _resolve_openmeteo_coords(
    location: str,
    *,
    use_configured_coordinates: bool,
) -> tuple[str, str] | None:
    geo = _auto_detect_ip_location()
    loc = str(location or "").strip().lower()

    if geo and geo.get("lat") and geo.get("lon"):
        if not loc or loc == get_default_location().lower() or (geo.get("city") and loc == geo["city"].lower()):
            return geo["lat"], geo["lon"]

    if use_configured_coordinates:
        env_lat = (os.getenv("JARVIS_DEFAULT_LAT") or "").strip()
        env_lon = (os.getenv("JARVIS_DEFAULT_LON") or "").strip()
        if env_lat and env_lon:
            return env_lat, env_lon

    loc = str(location or "").lower()
    if "malibu" in loc:
        return "34.0259", "-118.7798"
    if "madrid" in loc:
        return "40.4168", "-3.7038"

    response = http_requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": 1, "format": "json"},
        timeout=5,
    )
    if response.status_code != 200:
        raise http_requests.RequestException(f"Open-Meteo geocoding returned HTTP {response.status_code}")
    results = (response.json() or {}).get("results") or []
    if not results:
        return None
    first = results[0] or {}
    latitude = first.get("latitude")
    longitude = first.get("longitude")
    if latitude is None or longitude is None:
        return None
    return str(latitude), str(longitude)


def _temperature_to_float(value) -> float | None:
    try:
        cleaned = re.sub(r"[^0-9.\-]", "", str(value or ""))
        if not cleaned:
            return None
        return float(cleaned)
    except Exception:
        return None


def _fahrenheit_to_celsius(value: float) -> float:
    return (float(value) - 32.0) * 5.0 / 9.0


def _extract_celsius_from_weather_text(text: str) -> str:
    match = re.search(
        r"(-?\d+(?:\.\d+)?)\s*°?\s*([CF])\b",
        str(text or ""),
        re.IGNORECASE,
    )
    if not match:
        return "--"
    value = float(match.group(1))
    unit = match.group(2).upper()
    celsius = _fahrenheit_to_celsius(value) if unit == "F" else value
    return f"{celsius:.1f}".rstrip("0").rstrip(".")


def _format_weather_temperature(temp: str, lang: str) -> str:
    temp_value = _temperature_to_float(temp)
    if temp_value is None:
        return ""
    if str(lang or "").lower().startswith("en"):
        return f"{round((temp_value * 9.0 / 5.0) + 32.0)} degrees Fahrenheit"
    return f"{round(temp_value)} grados Celsius"


def _obtener_clima_logic(ciudad: str | None = None) -> tuple[str, str]:
    """Lógica interna para obtener el clima actual (wttr.in -> Open-Meteo)."""
    bt = get_bt()
    lang = get_current_language()
    default_location = _get_default_location()

    ciudad_actual = str(ciudad or default_location or "").strip() or default_location

    wc = services.weather_cache

    def _sync_cache(desc: str, temp: str) -> tuple[str, str]:
        if wc is not None:
            wc["temp"] = str(temp)
            wc["desc"] = str(desc)
            wc["last_update"] = datetime.now().timestamp()
        return str(desc), str(temp)

    # Proveedor 1: wttr.in
    try:
        r = http_requests.get(f"https://wttr.in/{ciudad_actual}?format=j1&lang={lang}", timeout=5)
        if r.status_code == 200:
            data = r.json() or {}
            current_list = data.get("current_condition")
            if current_list and isinstance(current_list, list) and len(current_list) > 0:
                current = current_list[0] or {}
                temp = current.get("temp_C", "--")

                # Intentar obtener descripción en el idioma actual
                lang_key = f"lang_{lang}"
                desc_list = current.get(lang_key)
                if not desc_list or not isinstance(desc_list, list) or len(desc_list) == 0:
                    desc_list = current.get("weatherDesc")

                desc = bt.get("weather_default", "Clear")
                if desc_list and isinstance(desc_list, list) and len(desc_list) > 0:
                    desc = (desc_list[0] or {}).get("value", desc)
                return _sync_cache(desc, temp)
    except Exception as e:
        _warn_once("clima_wttr", f"wttr.in fallo: {e}")

    # Proveedor 2: Open-Meteo
    try:
        explicit_location = bool(str(ciudad or "").strip())
        use_configured_coordinates = (
            not explicit_location or str(ciudad_actual).strip().casefold() == str(default_location).strip().casefold()
        )
        coordinates = _resolve_openmeteo_coords(
            ciudad_actual or default_location,
            use_configured_coordinates=use_configured_coordinates,
        )
        if coordinates is None:
            not_found = "Location not found" if lang.startswith("en") else "Ubicación no encontrada"
            return not_found, "--"
        lat, lon = coordinates
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                r = http_requests.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "current_weather": "true",
                        "timezone": "auto",
                    },
                    timeout=5,
                )
                if r.status_code == 200:
                    data = r.json() or {}
                    cur = data.get("current_weather")
                    if cur and isinstance(cur, dict):
                        temp = cur.get("temperature", "--")
                        code = cur.get("weathercode")
                        desc = _mapear_weather_code_openmeteo(code, lang=lang)
                        return _sync_cache(desc, str(temp))
                    break  # Si status 200 pero la info no esta, no seguir iterando
                elif r.status_code == 429:
                    if attempt < max_retries:
                        _uti_time.sleep(1)
                        continue
                    break
                else:
                    break
            except RequestException:
                if attempt < max_retries:
                    _uti_time.sleep(1)
                    continue
                break
    except Exception as e:
        _warn_once("clima_openmeteo", f"Open-Meteo fallo: {e}")

    try:
        # Deferred to avoid the tools.utilities <-> tools.search import cycle.
        from tools.search import buscar_en_internet  # noqa: PLC0415

        weather_query = (
            f"current weather in {ciudad_actual}" if lang.startswith("en") else f"clima actual en {ciudad_actual}"
        )
        res = str(buscar_en_internet.invoke({"query": weather_query}))
        temp = _extract_celsius_from_weather_text(res)
        return _sync_cache(res, temp)
    except Exception as e:
        logging.getLogger("JARVIS").warning(f"Error en fallback de clima: {e}")

    if wc:
        return str(wc.get("desc", bt.get("weather_default", "Clear"))), str(wc.get("temp", "--"))
    return bt.get("weather_default", "Clear"), "--"


@tool
def obtener_clima(ciudad: str = "Madrid") -> str:
    """Obtiene el clima actual de una ciudad específica usando proveedores de red o Google."""
    lang = get_current_language()
    desc, temp = _obtener_clima_logic(ciudad)
    temp_label = _format_weather_temperature(temp, lang)
    if lang.startswith("en"):
        if not temp_label:
            return f"Weather in {ciudad}: {desc}"
        return f"The weather in {ciudad} is {desc}, with a temperature of {temp_label}."
    if not temp_label:
        return f"Clima en {ciudad}: {desc}"
    return f"El clima en {ciudad} es {desc}, con una temperatura de {temp_label}."


# ─────────────────────────────────────────
# NBA
# ─────────────────────────────────────────
@tool
def obtener_deportes_espn(
    deporte: str = "basketball", liga: str = "nba", consulta: str = "hoy", event_id: str = ""
) -> str:
    """Obtiene marcadores o detalles de cualquier deporte vía ESPN (nba, nfl, mlb, soccer/eng.1, etc).
    Si event_id no está vacío, trae stats detallados de ese partido."""
    try:
        # Validar y normalizar parámetros
        deporte = str(deporte).lower().strip()
        liga = str(liga).lower().strip()
        if not deporte:
            deporte = "basketball"
        if not liga:
            liga = "nba"

        # Mapas comunes si el usuario sólo dice la liga
        if liga == "nfl" and deporte != "football":
            deporte = "football"
        if liga == "mlb" and deporte != "baseball":
            deporte = "baseball"
        if liga in ["premier", "eng.1", "liga", "esp.1", "champions", "uefa.champions"]:
            deporte = "soccer"
            if liga == "premier":
                liga = "eng.1"
            if liga == "liga":
                liga = "esp.1"
            if liga == "champions":
                liga = "uefa.champions"

        base_url = f"https://site.api.espn.com/apis/site/v2/sports/{deporte}/{liga}"

        if event_id:
            url = f"{base_url}/summary?event={event_id}"
        else:
            fecha = datetime.now().strftime("%Y%m%d")
            url = f"{base_url}/scoreboard?dates={fecha}"

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                r = http_requests.get(url, timeout=10)
                if r.status_code == 200:
                    data = r.json()

                    # Modo Detalle (Summary)
                    if event_id:
                        boxscore = data.get("boxscore", {})
                        equipos_stats = boxscore.get("teams", [])
                        res = [f"📊 Stats detallados evento {event_id}:"]
                        for ts in equipos_stats:
                            team = ts.get("team", {}).get("displayName", "")
                            res.append(f"\n[{team}]")
                            for stat in ts.get("statistics", []):
                                label = stat.get("label", stat.get("name", ""))
                                val = stat.get("displayValue", stat.get("value", ""))
                                res.append(f"  - {label}: {val}")
                        return "\n".join(res)

                    # Modo Scoreboard
                    eventos = data.get("events", [])
                    if not eventos:
                        return f"No hay partidos de {liga.upper()} ({deporte}) programados para hoy."
                    res = []
                    games_json = []

                    for e in eventos:
                        id_evento = e.get("id", "")
                        nombre = e.get("name", "")
                        status = e.get("status", {}).get("type", {})
                        estado = status.get("description", "")
                        competicion = e.get("competitions", [{}])[0]
                        competidores = competicion.get("competitors", [])
                        marcador = ""

                        if competidores:
                            equipos = []
                            for c in competidores:
                                team = c.get("team", {}).get("abbreviation", "")
                                score = c.get("score", "")
                                equipos.append(f"{team} {score}".strip())
                            marcador = " vs ".join(equipos)

                        linea = f"- {nombre} | {estado}"
                        if marcador and any(c.get("score") for c in competidores):
                            linea += f" | {marcador}"
                        linea += f" (ID: {id_evento})"
                        res.append(linea)

                        teams = competicion.get("competitors", [])
                        if len(teams) >= 2:
                            h = teams[0].get("team", {}).get("abbreviation", "T1")
                            hs = teams[0].get("score", "0")
                            a = teams[1].get("team", {}).get("abbreviation", "T2")
                            as_ = teams[1].get("score", "0")
                            games_json.append(
                                {
                                    "home": h,
                                    "score": f"{hs}-{as_}",
                                    "away": a,
                                    "status": status.get("description", ""),
                                }
                            )

                    widget_msg = f"\n\n<WIDGET>{json.dumps({'type': 'nba', 'data': {'games': games_json}})}</WIDGET>"
                    return f"Resumen {liga.upper()}:\n" + "\n".join(res) + widget_msg

                elif r.status_code == 429:
                    if attempt < max_retries:
                        _uti_time.sleep(1)
                        continue
                    return f"Error ESPN Rate Limit (codigo {r.status_code})"
                else:
                    return f"Error ESPN: {r.status_code}"
            except RequestException:
                if attempt < max_retries:
                    _uti_time.sleep(1)
                    continue
                return f"Error network fetching {liga.upper()} games."
    except Exception as e:
        return f"Error obteniendo partidos: {e}"


# ─────────────────────────────────────────
# Recordatorios
# ─────────────────────────────────────────
def agregar_recordatorio(texto: str, minutos: int) -> None:
    services.add_reminder(texto, minutos)


@tool
def poner_recordatorio(texto: str, minutos: int) -> str:
    """Programa un recordatorio de voz en X minutos.
    SOLO cuando el usuario diga 'recuérdame X en Y minutos/horas'."""
    agregar_recordatorio(texto, minutos)
    hora = (datetime.now() + timedelta(minutes=minutos)).strftime("%H:%M")
    return f"Recordatorio programado: '{texto}' a las {hora}."


# ─────────────────────────────────────────
# Archivos
# ─────────────────────────────────────────
@tool
def leer_archivo(nombre_archivo: str) -> str:
    """Lee un archivo de texto del escritorio o ruta indicada y devuelve su contenido.
    SOLO cuando el usuario diga 'lee el archivo X', 'abre X y dime' o similar."""
    try:
        bloqueo = _bloqueo_si_no_autorizado()
        if bloqueo:
            return bloqueo
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        rutas_candidatas = [
            nombre_archivo,
            os.path.join(desktop, nombre_archivo),
            os.path.join(desktop, nombre_archivo + ".txt"),
        ]
        for ruta in rutas_candidatas:
            if os.path.exists(ruta):
                with open(ruta, encoding="utf-8", errors="ignore") as f:
                    contenido = f.read(3000)
                print(f"  [FILE] Read: {ruta}")
                return f"Contenido de '{nombre_archivo}':\n{contenido}"
        return f"No encontré el archivo '{nombre_archivo}' en el escritorio."
    except Exception as e:
        return f"Error leyendo archivo: {e}"


# ─────────────────────────────────────────
_FRASES_JARVIS = [
    "Los sistemas están al límite, señor, pero su terquedad siempre supera cualquier especificación técnica.",
    "A veces hay que correr antes de aprender a caminar, señor.",
    "Un hombre inteligente aprende de sus errores; un genio construye una armadura con ellos.",
    "Todo protocolo está activo, señor. Sugiero mantener la compostura y la brillantez habitual.",
    "La probabilidad de éxito es inversamente proporcional a la cantidad de dudas que tenga, señor.",
    "Siempre es un placer desafiar las leyes de la física a su lado, señor.",
    "El éxito es la mejor venganza, Administrador.",
]


@tool
def frase_motivacional() -> str:
    """Genera frase motivacional o chiste al estilo JARVIS."""
    import random
    return random.choice(_FRASES_JARVIS)


# ─────────────────────────────────────────
# Pantalla
# ─────────────────────────────────────────
@tool
def analizar_pantalla() -> str:
    """Toma una captura de pantalla y analiza el contenido visual (HUD)."""
    try:
        bloqueo = _bloqueo_si_no_autorizado()
        if bloqueo:
            return bloqueo
        # Optional desktop dependencies must not be required for headless/core mode.
        import pyautogui  # noqa: PLC0415
        from PIL import Image, ImageFilter, ImageStat  # noqa: PLC0415

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(os.path.join(ROOT_DIR, "media", "screenshots"), exist_ok=True)
        filename = f"hud_scan_{ts}.png"
        ruta = os.path.join(ROOT_DIR, "media", "screenshots", filename)

        screenshot = pyautogui.screenshot()
        screenshot.save(ruta)

        img = Image.open(ruta).convert("RGB")
        w, h = img.size
        total_px = max(1, w * h)

        gray = img.convert("L")
        gray_stat = ImageStat.Stat(gray)
        brillo = float(gray_stat.mean[0])
        nivel_luz = "bajo" if brillo < 70 else "medio" if brillo < 150 else "alto"

        edges = gray.filter(ImageFilter.FIND_EDGES)
        eh = edges.histogram()
        edge_ratio = sum(eh[40:]) / float(total_px)
        densidad_ui = "alta" if edge_ratio > 0.22 else "media" if edge_ratio > 0.11 else "baja"

        img = Image.open(ruta)
        if img.mode != "RGB":
            img = img.convert("RGB")

        max_dim = 1024
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim))
            img.save(ruta)

        with open(ruta, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")

        prompt = (
            "Eres el sensor visual de J.A.R.V.I.S. Analiza esta captura de pantalla de mi ordenador. "
            "Dime qué aplicaciones ves abiertas, qué contenido crítico hay (errores de código, notificaciones, emails abiertos) "
            "y danos un resumen ejecutivo de lo que estoy haciendo. Sé breve y profesional, al estilo JARVIS."
        )

        llm_v = services.llm_vision
        if not llm_v:
            try:
                from core.brain.llm_engine import GROQ_VISION_MODEL, _load_chat_openai
                from core.brain.llm_providers import (
                    provider_base_url,
                    resolve_gemini_api_key,
                    resolve_groq_api_key,
                )

                ChatOpenAI = _load_chat_openai()
                gemini_key = resolve_gemini_api_key()
                if gemini_key:
                    vision_model = os.getenv("JARVIS_GEMINI_VISION_MODEL", "gemini-2.5-flash")
                    llm_v = ChatOpenAI(
                        model=vision_model,
                        temperature=0,
                        api_key=gemini_key,
                        base_url=provider_base_url("gemini"),
                    )
                else:
                    groq_key = resolve_groq_api_key()
                    if groq_key:
                        llm_v = ChatOpenAI(
                            model=GROQ_VISION_MODEL,
                            temperature=0,
                            api_key=groq_key,
                            base_url=provider_base_url("groq"),
                        )
            except Exception:
                llm_v = None

        multimodal_res = "Análisis visual no disponible (motor no configurado)."

        if llm_v:
            from langchain_core.messages import HumanMessage  # noqa: PLC0415

            content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                },
            ]
            multimodal_res = "Análisis visual no disponible."
            try:
                res_v = llm_v.invoke([HumanMessage(content=content)])
                multimodal_res = res_v.content.strip()
            except Exception as ev:
                multimodal_res = f"Error en visor óptico: {ev}"

        resumen = (
            f"Análisis HUD completado: [{w}x{h} px]. Luminosidad {nivel_luz}. "
            f"Densidad UI: {densidad_ui}. {multimodal_res}"
        )
        print(f"  [VISION] {resumen[:200]}...")

        return resumen
    except Exception as e:
        return f"Error en los sensores ópticos: {e}"


# ─────────────────────────────────────────
# Plugins
# ─────────────────────────────────────────
@tool
def recargar_plugins() -> str:
    """Recarga plugins dinámicos desde src/backend/plugins."""
    if services.recargar_plugins:
        try:
            from core.brain.tool_manager import _recargar_plugins_runtime  # noqa: PLC0415

            return _recargar_plugins_runtime()
        except Exception as e:
            services.log_event("plugin_reload_error", error=str(e)[:300])
            return f"I could not reload plugins. Error: {e}"
    return "Sistema de plugins aun no inicializado."


# ─────────────────────────────────────────
# Rutinas
# ─────────────────────────────────────────
@tool
def ejecutar_rutina(nombre: str) -> str:
    """Ejecuta rutinas predefinidas: trabajo, gaming, buenos dias."""
    n = (nombre or "").strip().lower()
    if not n:
        return "Indique una rutina: trabajo, gaming o buenos días."

    alias = {
        "modo trabajo": "trabajo",
        "trabajo": "trabajo",
        "work": "trabajo",
        "modo gaming": "gaming",
        "gaming": "gaming",
        "jugar": "gaming",
        "buenos dias": "buenos_dias",
        "buenos días": "buenos_dias",
        "inicio del dia": "buenos_dias",
        "inicio del día": "buenos_dias",
    }
    key = alias.get(n, n.replace(" ", "_"))

    if key not in {"trabajo", "gaming", "buenos_dias"}:
        return "Rutina no reconocida. Opciones: trabajo, gaming, buenos días."

    ejecutadas = []
    errores = []
    auth_blocked = False

    def _run(tool_name: str, args: dict):
        nonlocal auth_blocked
        print(f"    [ROUTINE] Step: {tool_name} args={args}")
        if not services.invocar_tool:
            errores.append(f"{tool_name}: Sistema de invocación no available.")
            return

        try:
            res = services.invocar_tool(tool_name, args, f"rutina {n}", source="routine")
            txt = str(res)
            if "acceso_denegado" in txt.lower():
                auth_blocked = True
            if txt.lower().startswith("error") or "no pude" in txt.lower() or "acceso_denegado" in txt.lower():
                print(f"    [ROUTINE] ERROR in {tool_name}: {txt[:200]}")
                errores.append(f"{tool_name}: {txt}")
            else:
                print(f"    [ROUTINE] OK {tool_name}: {txt[:120]}")
                ejecutadas.append(f"{tool_name}: {txt}")
        except Exception as e:
            print(f"    [ROUTINE] EXCEPTION in {tool_name}: {e}")
            errores.append(f"{tool_name}: Excepcion: {e}")

    if key == "trabajo":
        _run("modo_no_molestar", {"activar": True})
        _run("ajustar_volumen", {"nivel": 30})
        _run("abrir_aplicacion", {"nombre_app": "vscode"})
        _run("abrir_aplicacion", {"nombre_app": "chrome"})
    elif key == "gaming":
        _run("modo_no_molestar", {"activar": True})
        _run("ajustar_volumen", {"nivel": 60})
        _run("abrir_aplicacion", {"nombre_app": "discord"})
        _run("abrir_aplicacion", {"nombre_app": "steam"})
    else:  # buenos_dias
        _run("modo_no_molestar", {"activar": False})
        _run("ajustar_volumen", {"nivel": 35})
        desc, temp = _obtener_clima_logic()
        nc = services.noticias_cache
        resumen = nc["resumen"] if nc.get("listo") else "Briefing aún generándose."
        ejecutadas.append(f"clima: {desc}, {temp}°C")
        ejecutadas.append(f"briefing: {resumen[:180]}")

    if auth_blocked:
        return (
            "ACCESO_DENEGADO: Esta rutina requiere autorizacion previa para abrir aplicaciones o ejecutar "
            "acciones protegidas. Identifiquese por voz para obtener autorizacion."
        )

    if errores and not ejecutadas:
        return f"I could not complete the routine '{n}'. Errores: {' | '.join(errores[:3])}"

    msg = f"Rutina '{n}' ejecutada."
    if ejecutadas:
        msg += " Acciones: " + " | ".join(ejecutadas[:4])
    if errores:
        msg += " | Avisos: " + " | ".join(errores[:2])
    return msg


# ─────────────────────────────────────────
# Briefing
# ─────────────────────────────────────────
def obtener_noticias_newsapi(lang: str | None = None):
    lang = (lang or get_current_language() or "en").strip().lower()
    news_lang = "es" if lang.startswith("es") else "en"
    temas = TEMAS_NEWSAPI_ES if news_lang == "es" else TEMAS_NEWSAPI
    titulares = []
    try:
        for tema in temas:
            r = http_requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": tema,
                    "language": news_lang,
                    "sortBy": "publishedAt",
                    "pageSize": 2,
                    "apiKey": NEWSAPI_KEY,
                },
                timeout=10,
            )
            for a in r.json().get("articles", []):
                titulo = a.get("title", "")
                desc = a.get("description", "") or ""
                if titulo and "[Removed]" not in titulo:
                    titulares.append(f"- {titulo}. {desc[:100]}")
    except Exception as e:
        print(f"[ERROR NewsAPI] {e}")
    return "\n".join(titulares[:14])


def _cargar_briefing_persistido():
    nc = services.noticias_cache
    if nc is None:
        return
    lang = get_current_language()
    bf = os.path.join(BASE_DIR, "ultimo_briefing.json")
    if os.path.exists(bf):
        try:
            with open(bf, encoding="utf-8") as f:
                data = json.load(f)
                if data.get("fecha") == datetime.now().strftime("%Y-%m-%d") and data.get("language") == lang:
                    resumen_raw = data.get("resumen") or ""
                    resumen_limpio = re.sub(r"<think>.*?</think>", "", resumen_raw, flags=re.DOTALL)
                    resumen_limpio = re.sub(r"\s+", " ", resumen_limpio).strip()
                    data["resumen"] = resumen_limpio
                    nc.update(data)
                    bt = get_bt()
                    print(bt["log_briefing_recovered"])
        except Exception:
            pass


def generar_resumen_noticias(forzar: bool = False):
    """Genera el resumen de noticias del día usando NewsAPI y el LLM."""
    nc = services.noticias_cache
    if nc is None:
        print("[WARN] Cannot generate briefing without injected noticias_cache.")
        return

    lang = get_current_language()
    bt = get_bt()
    hoy_str = datetime.now().strftime("%Y-%m-%d")
    if not forzar and nc.get("fecha") == hoy_str and nc.get("language") == lang and nc.get("resumen"):
        print(bt["log_briefing_already_generated"])
        return

    try:
        print("[JARVIS] Generating briefing..." if lang.startswith("en") else "[JARVIS] Generating briefing...")
        nc["listo"] = False
        nc["language"] = lang
        titulares = obtener_noticias_newsapi(lang)
        if not titulares:
            nc["resumen"] = (
                "I could not retrieve the briefing right now, Administrator."
                if lang.startswith("en")
                else "No fue posible obtener el briefing en este momento, Administrador."
            )
            nc["listo"] = True
            nc["fecha"] = hoy_str
            nc["language"] = lang
            return

        llm_dep = services.llm
        if llm_dep is None:
            nc["resumen"] = ""
            nc["listo"] = False
            nc["fecha"] = hoy_str
            nc["language"] = lang
            print("[WARN] Deferred briefing: LLM not injected yet. Retrying in 5s...")
            _uti_time.sleep(5)
            if services.llm:
                generar_resumen_noticias(forzar=True)
            else:
                nc["resumen"] = (
                    "Briefing cancelled: LLM did not initialize."
                    if lang.startswith("en")
                    else "Briefing cancelado: LLM no inicializó."
                )
                nc["listo"] = True
                print("[WARN] Briefing cancelled: LLM failed to initialize after retry.")
            return

        hoy_label = datetime.now().strftime("%B %d, %Y")
        if lang.startswith("en"):
            prompt = (
                f"You are JARVIS. Today is {hoy_label}. Summarize these headlines in at most 5 short sentences "
                "covering technology, sports, economy, international politics, Mexico, science, and cybersecurity. "
                "Use a formal, dry tone. No greetings. Only use the headlines. Reply in English.\n\n"
                f"{titulares}"
            )
        else:
            prompt = (
                f"Eres JARVIS. Hoy es {hoy_label}. Resume estos titulares en máximo 5 frases cortas "
                "cubriendo tecnología, deportes, economía, política, México, ciencia y ciberseguridad. "
                "Tono formal y seco. Sin saludos. Solo usa los titulares. En español.\n\n"
                f"{titulares}"
            )

        raw_resumen = _extract_llm_text(llm_dep.invoke(prompt))
        raw_resumen = re.sub(r"<think>.*?</think>", "", raw_resumen, flags=re.DOTALL).strip()
        nc["resumen"] = re.sub(r"\s+", " ", raw_resumen).strip()
        nc["listo"] = True
        nc["fecha"] = hoy_str
        nc["language"] = lang

        try:
            with open(os.path.join(BASE_DIR, "ultimo_briefing.json"), "w", encoding="utf-8") as f:
                json.dump(nc, f, ensure_ascii=False, indent=2)
        except Exception as ep:
            print(f"[WARN] Could not persist briefing: {ep}")

        print(bt["log_briefing_ready"])
    except Exception as e:
        nc["resumen"] = (
            "I could not generate the briefing." if lang.startswith("en") else "No fue posible obtener el briefing."
        )
        nc["listo"] = True
        nc["language"] = lang
        print(f"[ERROR] News: {e}")


@tool
def evaluar_expresion_matematica(expresion: str) -> str:
    """Evalúa una expresión matemática compuesta (aritmética, raíces, potencias, trigonometría, logaritmos).
    Ejemplos: 'sqrt(28) * 4 / 20 * 5 / 5 * 8 * 9 * 6 / 5 - 8 * 828', '2^8 + sqrt(144)', 'sin(pi/2)'
    """
    if not expresion or not isinstance(expresion, str):
        return "Error: expresión vacía."

    limpio = normalize_math_expression(expresion, strip_prompt=True)

    try:
        result = evaluate_math_expression(limpio)
        rendered = format_math_number(
            result,
            group_thousands=False,
        )
        return f"El resultado de '{expresion}' es {rendered}."
    except Exception as e:
        return f"Error al evaluar la expresión matemática: {e}"
