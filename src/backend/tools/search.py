"""Herramientas de búsqueda en internet: Brave, NewsAPI, YouTube, multi-source."""

import html
import re

import requests as http_requests
from langchain_core.tools import tool
from utils.jarvis_i18n import get_current_language

from tools._common import _limpiar_respuesta, _similitud_texto

_tavily_missing_reported = False


def _clean_search_field(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _web_unavailable_message(status_code: int | None = None) -> str:
    suffix = f" (HTTP {status_code})" if status_code is not None else ""
    if get_current_language().startswith("en"):
        return f"Web search is temporarily unavailable{suffix}."
    return f"La búsqueda web no está disponible temporalmente{suffix}."


# ─────────────────────────────────────────
# Brave Search API
# ─────────────────────────────────────────
def _buscar_en_tavily(query: str) -> str:
    """Búsqueda usando Tavily AI (prioridad alta)."""
    global _tavily_missing_reported

    from datetime import datetime

    from core.jarvis_config import TAVILY_API_KEY

    if not TAVILY_API_KEY:
        return None  # Indica que debe probar siguiente fuente

    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=TAVILY_API_KEY)
        response = client.search(
            query=query,
            search_depth="basic",
            max_results=5,
            include_answer=True,
            include_raw_content=False,
        )
        datetime.now().strftime("%d/%m/%Y")
        answer = response.get("answer", "")
        results = response.get("results", [])
        if not results:
            return None

        if answer:
            return answer

        res = []
        for it in results[:5]:
            title = it.get("title", "")
            desc = it.get("content", "")[:150]
            if title and desc:
                res.append(f"- {title}: {desc}")
        return "\n".join(res)
    except ModuleNotFoundError:
        if not _tavily_missing_reported:
            print("  [TAVILY] Optional client unavailable; using Brave Search.")
            _tavily_missing_reported = True
        return None
    except Exception as e:
        print(f"  [TAVILY] Request failed: {type(e).__name__}")
        return None


def _buscar_en_brave(query: str) -> str:
    """Búsqueda usando Brave Search API (fallback)."""
    from datetime import datetime

    from core.jarvis_config import BRAVE_API_KEY

    if not BRAVE_API_KEY:
        return (
            "I could not query the network porque BRAVE_API_KEY no esta configurada "
            "en este entorno."
        )

    ahora = datetime.now()
    ahora.strftime("%d/%m/%Y")
    query_mod = query
    query_lower = query.lower()
    palabras_tiempo = [
        "hoy", "ayer", "mañana", "manana", "clima", "partido",
        "nba", "noticias", "precio", "valor", "marcador", "estreno",
    ]

    palabras = query.strip().split()
    es_nombre_propio = (
        len(palabras) <= 3
        and any(p[0].isupper() for p in palabras if p)
        and not any(p in query_lower for p in palabras_tiempo)
    )
    if es_nombre_propio:
        query_mod = f'"{query}"'
        print("  [SEARCH OPTIMIZER] Nombre propio detectado — usando comillas exactas.")
    if (
        any(p in query_lower for p in palabras_tiempo)
        and str(ahora.year) not in query
    ):
        query_mod = f"{query} {ahora.year}"
        print(
            f"  [SEARCH OPTIMIZER] Inyectando año '{ahora.year}' para mayor precisión."
        )

    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": BRAVE_API_KEY,
    }
    params = {"q": query_mod, "count": 10, "lang": "es"}
    if "hoy" in query.lower() or "clima" in query.lower():
        params["freshness"] = "pw"

    try:
        import time

        from requests.exceptions import RequestException

        print(f"  [TOOL] Internet Search (Brave): {query_mod}")

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                r = http_requests.get(url, headers=headers, params=params, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    results = data.get("web", {}).get("results", [])
                    if not results:
                        return f"Sin resultados para '{query_mod}' en la red."
                    res = []
                    for it in results[:5]:
                        title = _clean_search_field(it.get("title"))
                        description = _clean_search_field(it.get("description"))
                        if not title:
                            continue
                        if description:
                            res.append(f"- {title}: {description}")
                        else:
                            res.append(f"- {title}")
                    resultado = "\n".join(res)
                    return resultado
                elif r.status_code == 429: # Rate limit
                    if attempt < max_retries:
                        time.sleep(2)
                        continue
                    return _web_unavailable_message(r.status_code)
                else:
                    return _web_unavailable_message(r.status_code)
            except RequestException as exc:
                print(f"  [SEARCH] Brave request failed: {type(exc).__name__}")
                if attempt < max_retries:
                    time.sleep(1)
                    continue
                return _web_unavailable_message()
        return _web_unavailable_message()
    except Exception as e:
        print(f"  [SEARCH] Brave unexpected error: {type(e).__name__}")
        return _web_unavailable_message()


# Alias para compatibilidad
_buscar_en_internet_api = _buscar_en_brave


def _buscar_en_internet_impl(query: str) -> str:
    """Implementación unificada: Tavily (primario) → Brave (fallback)."""
    # 1. Tavily (más inteligente, respuesta con AI)
    tavily_result = _buscar_en_tavily(query)
    if tavily_result:
        return tavily_result

    # 2. Brave fallback
    return _buscar_en_brave(query)


# ─────────────────────────────────────────
# NewsAPI
# ─────────────────────────────────────────
def _buscar_en_newsapi(query: str) -> list:
    """Búsqueda en NewsAPI para noticias."""
    from core.jarvis_config import NEWSAPI_KEY

    if not NEWSAPI_KEY:
        return []
    try:
        news_lang = "es" if get_current_language().startswith("es") else "en"
        import time

        from requests.exceptions import RequestException
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                r = http_requests.get(
                    "https://newsapi.org/v2/everything",
                    params={
                        "q": query,
                        "language": news_lang,
                        "sortBy": "publishedAt",
                        "pageSize": 5,
                        "apiKey": NEWSAPI_KEY,
                    },
                    timeout=8,
                )
                if r.status_code == 200:
                    articles = r.json().get("articles", [])
                    return [
                        {
                            "title": a.get("title", ""),
                            "desc": a.get("description", ""),
                            "url": a.get("url", ""),
                            "fecha": a.get("publishedAt", ""),
                        }
                        for a in articles
                        if a.get("title") and "[Removed]" not in a.get("title", "")
                    ]
                elif r.status_code == 429:
                    if attempt < max_retries:
                        time.sleep(1.5)
                        continue
                    break
                else:
                    break
            except RequestException:
                if attempt < max_retries:
                    time.sleep(1)
                    continue
                break
    except Exception as e:
        print(f"  [NEWSAPI] Error: {e}")
    return []


# ─────────────────────────────────────────
# YouTube Data API
# ─────────────────────────────────────────
def _buscar_en_youtube(query: str) -> list:
    """Búsqueda en YouTube Data API."""
    from core.jarvis_config import YOUTUBE_API_KEY

    if not YOUTUBE_API_KEY:
        return []
    try:
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": 3,
            "key": YOUTUBE_API_KEY,
        }
        r = http_requests.get(
            "https://www.googleapis.com/youtube/v3/search", params=params, timeout=8
        )
        if r.status_code == 200:
            items = r.json().get("items", [])
            return [
                {
                    "title": i.get("snippet", {}).get("title", ""),
                    "videoId": i.get("id", {}).get("videoId", ""),
                    "canal": i.get("snippet", {}).get("channelTitle", ""),
                }
                for i in items
                if i.get("id", {}).get("videoId")
            ]
    except Exception as e:
        print(f"  [YOUTUBE] Error: {e}")
    return []


# ─────────────────────────────────────────
# Fusión Multi-Source
# ─────────────────────────────────────────
def _fusionar_resultados(fuentes: list, tipo: str = "general") -> str:
    """Fusiona resultados de múltiples fuentes eliminando redundancias."""
    if not fuentes:
        return ""
    items = []
    for fuente in fuentes:
        items.extend(fuente.get("items", []))
    if not items:
        return ""
    unicos = []
    reemplazos = []
    for item in items:
        es_duplicado = False
        for u in unicos:
            if _similitud_texto(item.get("title", ""), u.get("title", "")) > 0.6:
                if item.get("fecha", "") > u.get("fecha", ""):
                    reemplazos.append((u, item))
                es_duplicado = True
                break
        if not es_duplicado:
            unicos.append(item)
    for old, new in reemplazos:
        idx = unicos.index(old)
        unicos[idx] = new
    unicos = unicos[:6]
    lineas = []
    for it in unicos:
        titulo = it.get("title", "").strip()
        desc = it.get("desc", "").strip() or it.get("canal", "").strip()
        if titulo:
            if desc:
                lineas.append(f"{titulo}: {desc[:120]}")
            else:
                lineas.append(titulo)
    return " | ".join(lineas)


def _buscar_multi_fuente(query: str, es_youtube: bool = False) -> str:
    """Búsqueda en múltiples fuentes con fusión intelligente."""
    from datetime import datetime

    datetime.now().strftime("%d/%m/%Y")
    query_lower = query.lower()
    resultados = []
    fuentes_a_consultar = []
    if es_youtube:
        fuentes_a_consultar.append(
            {"tipo": "youtube", "fn": lambda: _buscar_en_youtube(query), "items": []}
        )
    else:
        fuentes_a_consultar.append(
            {"tipo": "brave", "fn": lambda: _buscar_en_internet_api(query), "items": []}
        )
        if any(
            p in query_lower
            for p in ["noticia", "noticias", "actual", "último", "reciente"]
        ):
            fuentes_a_consultar.append(
                {
                    "tipo": "newsapi",
                    "fn": lambda: _buscar_en_newsapi(query),
                    "items": [],
                }
            )

    for fuente in fuentes_a_consultar:
        try:
            data = fuente["fn"]()
            if fuente["tipo"] == "brave":
                # Parsear el string formateado de vuelta a items estructurados
                items = []
                for line in data.split("\n"):
                    if line.startswith("- "):
                        # Formato: "- Title: Description (url)"
                        rest = line[2:]
                        colon_idx = rest.find(": ")
                        if colon_idx != -1:
                            title = rest[:colon_idx].strip()
                            desc_url = rest[colon_idx + 2:]
                            # Remove trailing "(url)" to extract the description.
                            # Use a URL-aware pattern so Wikipedia-style URLs
                            # like (https://…/Example_(disambiguation)) work too.
                            paren_m = re.search(r"\s*\(https?://\S+\)\s*$", desc_url)
                            if not paren_m:
                                paren_m = re.search(r"\s*\([^)]*\)\s*$", desc_url)
                            desc = desc_url[:paren_m.start()].strip() if paren_m else desc_url.strip()
                            items.append({"title": title, "desc": desc, "fecha": ""})
                resultados.append({"tipo": "brave", "items": items})
            else:
                resultados.append(
                    {
                        "tipo": fuente["tipo"],
                        "items": [
                            {
                                "title": d.get("title", ""),
                                "desc": d.get("desc", ""),
                                "fecha": d.get("fecha", ""),
                            }
                            for d in data
                        ],
                    }
                )
        except Exception as e:
            print(f"  [MULTI_SEARCH] Error en {fuente['tipo']}: {e}")

    fusionado = _fusionar_resultados(resultados, "general")
    if fusionado:
        return fusionado
    return _limpiar_respuesta(
        resultados[0].get("items", "")
        if resultados
        else f"Sin resultados para '{query}'."
    )


# ─────────────────────────────────────────
# Tool: buscar_en_internet
# ─────────────────────────────────────────
@tool
def buscar_en_internet(query: str) -> str:
    """Busca información actual de CUALQUIER TIPO. Prioridad: Tavily AI → Brave Search.
    Úsala para:
    - Clima en cualquier ciudad: 'clima en monterrey'
    - Deportes (NBA, Fútbol, resultados en tiempo real): 'quien juega hoy en la nba'
    - Noticias de última hora: 'noticias de hoy sobre mexico'
    - Información general y datos recientes que no conozcas."""
    try:
        print(f"  [TOOL] Internet Search: {query}")
        return _buscar_en_internet_impl(query)[:1200]
    except Exception:
        return "I could not query the internet at this time."



