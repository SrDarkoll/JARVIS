"""Herramientas de navegador: Playwright worker, navegador sistema, YouTube."""

import os
import re
import subprocess
import threading
import time as _time
import webbrowser
from urllib.parse import quote_plus, urlparse, urlunparse

from core.service_container import services
from langchain_core.tools import tool

from tools._common import (
    _open_url_or_app,
)

# ─────────────────────────────────────────
# Playwright imports
# ─────────────────────────────────────────
sync_playwright = None
PLAYWRIGHT_IMPORT_ERROR = None
PlaywrightTimeoutError = Exception

try:
    from playwright.sync_api import (
        TimeoutError as _PlaywrightTimeoutError,
    )
    from playwright.sync_api import (
        sync_playwright as _sync_playwright,
    )
    sync_playwright = _sync_playwright
    PlaywrightTimeoutError = _PlaywrightTimeoutError
    PLAYWRIGHT_IMPORT_ERROR = ""
except Exception as e:
    PLAYWRIGHT_IMPORT_ERROR = str(e)


# ─────────────────────────────────────────
# Browser Mode
# ─────────────────────────────────────────
BROWSER_MODE = (os.getenv("JARVIS_BROWSER_MODE", "system") or "system").strip().lower()
playwright_lock = threading.Lock()
_pw_worker = None


def _browser_prefers_system() -> bool:
    """
    Modo por defecto: navegador predeterminado del usuario.
    Para forzar Playwright: JARVIS_BROWSER_MODE=playwright
    """
    return BROWSER_MODE not in {"playwright", "pw", "real"}


def _playwright_hint() -> str:
    detalle = f" Detalle: {PLAYWRIGHT_IMPORT_ERROR}." if PLAYWRIGHT_IMPORT_ERROR else ""
    return (
        "Playwright no esta available."
        f"{detalle} Instala dependencias con 'pip install playwright' y luego ejecuta "
        "'python -m playwright install chromium'."
    )


# ─────────────────────────────────────────
# BrowserWorker (Playwright thread)
# ─────────────────────────────────────────
class BrowserWorker(threading.Thread):
    def __init__(self):
        super().__init__(name="BrowserWorker", daemon=True)
        from queue import Queue

        self.tasks = Queue()
        self.stop_signal = False

    def run(self):
        if sync_playwright is None:
            return

        IDLE_TIMEOUT = 600.0  # 10 minutos de inactividad
        last_activity = _time.time()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            ctx = browser.new_context(viewport={"width": 1536, "height": 864})
            page = ctx.new_page()

            try:
                while not self.stop_signal:
                    try:
                        from queue import Empty
                        # Timeout pequeño para revisar señales de stop y el idle timeout
                        task = self.tasks.get(timeout=5.0)
                        if task is None:
                            break

                        last_activity = _time.time()
                        fn, args, result_container, event = task
                        try:
                            res = fn(page, *args)
                            result_container["result"] = res
                        except Exception as e:
                            result_container["error"] = str(e)
                            services.log_event(
                                "browser_worker_task_error",
                                fn=str(fn.__name__),
                                error=str(e)[:300],
                            )
                        finally:
                            event.set()
                            self.tasks.task_done()
                    except Empty:
                        if _time.time() - last_activity > IDLE_TIMEOUT:
                            services.log_event("browser_worker_idle_timeout")
                            print("[BROWSER] Cerrando navegador por inactividad.")
                            break
                        continue
            finally:
                try:
                    page.close()
                    ctx.close()
                    browser.close()
                except Exception as e:
                    print(f"[WARN BROWSER] Error en limpieza final: {e}")

                # Zombie Reaper: Marcamos globalmente que el worker ha muerto
                global _pw_worker
                with playwright_lock:
                    if _pw_worker == self:
                        _pw_worker = None

    def execute(self, fn, *args, timeout=35):
        event = threading.Event()
        container = {"result": None, "error": None}
        self.tasks.put((fn, args, container, event))
        if not event.wait(timeout=timeout):
            return "Error: Tiempo de espera agotado en BrowserWorker."
        if container["error"]:
            raise RuntimeError(container["error"])
        return container["result"]


def _ensure_pw_worker():
    global _pw_worker
    with playwright_lock:
        if _pw_worker is None or not _pw_worker.is_alive():
            _pw_worker = BrowserWorker()
            _pw_worker.start()
    return _pw_worker


# ─────────────────────────────────────────
# URL Normalization
# ─────────────────────────────────────────
def _normalizar_destino_web(destino: str) -> str:
    destino = (destino or "").strip()
    destino = destino.strip(" \t\r\n\"'`\u201c\u201d")
    destino = re.sub(r"[)\]}.,:;!?]+$", "", destino).strip()
    destino = re.sub(r"\s+", " ", destino).strip()
    if not destino:
        return "https://www.google.com"

    alias = {
        "google": "https://www.google.com",
        "youtube": "https://www.youtube.com",
        "facebook": "https://www.facebook.com",
        "instagram": "https://www.instagram.com",
        "spotify": "https://open.spotify.com",
        "x": "https://x.com",
        "twitter": "https://x.com",
        "gmail": "https://mail.google.com",
    }
    alias_url = alias.get(destino.lower())
    if alias_url:
        return alias_url

    if re.match(r"^https?://", destino, flags=re.IGNORECASE):
        parsed = urlparse(destino)
        host = (parsed.netloc or "").strip().rstrip(".")
        if (
            host
            and "." not in host
            and re.match(r"^[a-z0-9-]+$", host, flags=re.IGNORECASE)
        ):
            host = f"{host}.com"
        if host and re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", host, flags=re.IGNORECASE):
            return urlunparse(parsed._replace(netloc=host))
        return f"https://www.google.com/search?q={quote_plus(destino)}"

    if " " not in destino:
        token = destino.rstrip(".")
        token = re.sub(r"^https?://", "", token, flags=re.IGNORECASE)
        host, path = token, ""
        if "/" in token:
            host, rest = token.split("/", 1)
            path = "/" + rest
        host = host.strip().rstrip(".")
        if (
            host
            and "." not in host
            and re.match(r"^[a-z0-9-]+$", host, flags=re.IGNORECASE)
        ):
            host = f"{host}.com"
        if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", host, flags=re.IGNORECASE):
            return f"https://{host}{path}"

    return f"https://www.google.com/search?q={quote_plus(destino)}"


# ─────────────────────────────────────────
# System Browser Fallback
# ─────────────────────────────────────────
def _abrir_en_navegador_sistema(url: str, require_policy: bool = True) -> bool:
    if require_policy:
        allowed = True
        try:
            if services.security_allow_fallback:
                allowed = bool(services.security_allow_fallback())
        except Exception:
            allowed = True
        if not allowed:
            if services.obs_inc:
                services.obs_inc("metric_security_warning_total", 1)
            if services.security_audit:
                services.security_audit(
                    "system_browser_fallback_blocked",
                    level="warning",
                    tool="browser_fallback",
                    reason="Fallback al navegador del sistema bloqueado por politica.",
                    metadata={"url": (url or "")[:200]},
                )
            return False
    err_open = ""
    err_startfile = ""
    err_cmd = ""
    try:
        opened = webbrowser.open(url, new=2)
        if opened:
            return True
    except Exception as e:
        err_open = str(e)
    if _open_url_or_app(url):
        return True
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", url],
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as e:
        err_cmd = str(e)
        services.log_event(
            "system_browser_fallback_error",
            url=(url or "")[:200],
            webbrowser=err_open[:100],
            startfile=err_startfile[:100],
            cmd=err_cmd[:100],
        )
        print(
            "[WARN WEB] Fallo fallback de navegador sistema: "
            f"webbrowser={err_open or 'n/a'} | startfile={err_startfile or 'n/a'} | cmd={err_cmd or 'n/a'}"
        )
        return False


# ─────────────────────────────────────────
# Playwright page helpers
# ─────────────────────────────────────────
def _pw_goto(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    return f"Navegador abierto en {page.url}."


def _pw_click(page, selector):
    try:
        page.locator(selector).first.click(timeout=8000)
        return f"Click ejecutado en selector '{selector}'."
    except Exception:
        page.get_by_text(selector, exact=False).first.click(timeout=8000)
        return f"Click ejecutado por texto '{selector}'."


def _pw_write(page, selector, texto, enter):
    target = None
    try:
        target = page.locator(selector).first
        target.fill(texto, timeout=8000)
    except Exception:
        target = page.get_by_placeholder(selector, exact=False).first
        target.fill(texto, timeout=8000)
    if enter:
        target.press("Enter")
    return f"Texto escrito en '{selector}'."


def _pw_read(page, max_chars):
    return _resumen_pagina(page, max_chars=max_chars)


def _resumen_pagina(page, max_chars: int = 1200) -> str:
    titulo = page.title().strip() if page.title() else "Sin titulo"
    url = page.url or "sin URL"
    try:
        texto = page.inner_text("body")
    except Exception:
        texto = ""
    texto = re.sub(r"\s+", " ", texto).strip()
    if len(texto) > max_chars:
        texto = texto[:max_chars].rstrip() + "..."
    if not texto:
        texto = "Sin contenido de texto legible."
    return f"Titulo: {titulo}\nURL: {url}\nContenido: {texto}"


# ─────────────────────────────────────────
# Tools
# ─────────────────────────────────────────
@tool
def abrir_navegador(destino: str = "https://www.google.com") -> str:
    """Abre el navegador predeterminado del sistema al destino indicado (Playwright opcional por config)."""
    url = _normalizar_destino_web(destino)
    if _browser_prefers_system():
        if _abrir_en_navegador_sistema(url, require_policy=False):
            return f"Navegador predeterminado abierto en {url}."
        return f"I could not open {url} en el navegador predeterminado."
    try:
        worker = _ensure_pw_worker()
        return worker.execute(_pw_goto, url)
    except Exception as e:
        if _abrir_en_navegador_sistema(url):
            return f"Playwright fallo ({e}). Abri {url} en el navegador del sistema."
        return f"Error al abrir navegador Playwright: {e}"


@tool
def navegar_en_navegador(destino: str) -> str:
    """Navega a una URL o búsqueda en el navegador (predeterminado por defecto)."""
    url = _normalizar_destino_web(destino)
    if _browser_prefers_system():
        if _abrir_en_navegador_sistema(url, require_policy=False):
            return f"Navegando en navegador predeterminado a {url}."
        return f"I could not navigate a {url} en el navegador predeterminado."
    try:
        worker = _ensure_pw_worker()
        return worker.execute(_pw_goto, url)
    except Exception as e:
        if _abrir_en_navegador_sistema(url):
            return f"Playwright fallo ({e}). Abri {url} en el navegador del sistema."
        return f"Error al navegar: {e}"


@tool
def click_en_navegador(selector: str) -> str:
    """Hace click en la página actual (CSS/XPath o texto visible)."""
    try:
        worker = _ensure_pw_worker()
        return worker.execute(_pw_click, selector)
    except Exception as e:
        return f"Error al hacer click: {e}"


@tool
def escribir_en_navegador(selector: str, texto: str, enter: bool = False) -> str:
    """Escribe texto en un campo del navegador (selector CSS/XPath o placeholder)."""
    try:
        worker = _ensure_pw_worker()
        return worker.execute(_pw_write, selector, texto, enter)
    except Exception as e:
        return f"Error al escribir en navegador: {e}"


@tool
def leer_pagina_navegador(max_chars: int = 1200) -> str:
    """Lee título, URL y texto visible de la página actual en Playwright."""
    try:
        worker = _ensure_pw_worker()
        max_chars_val = max(300, min(int(max_chars), 4000))
        return worker.execute(_pw_read, max_chars_val)
    except Exception as e:
        return f"Error al leer página: {e}"


@tool
def cerrar_navegador_playwright() -> str:
    """Cierra la sesión de navegador controlada por Playwright."""
    global _pw_worker
    with playwright_lock:
        if _pw_worker:
            _pw_worker.stop_signal = True
            _pw_worker.tasks.put(None)
            _pw_worker = None
    return "Sesion Playwright cerrada."


def _obtener_top_youtube_url(query: str) -> str:
    """Extrae la URL del primer video relevante en YouTube para reproducirlo directamente."""
    try:
        import urllib.request
        import urllib.parse
        q_clean = str(query or "").strip()
        if not q_clean:
            return "https://www.youtube.com"
        search_url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(q_clean)
        req = urllib.request.Request(
            search_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        html = urllib.request.urlopen(req, timeout=4).read().decode("utf-8")
        vids = re.findall(r"watch\?v=([a-zA-Z0-9_-]{11})", html)
        if vids:
            return f"https://www.youtube.com/watch?v={vids[0]}"
        return search_url
    except Exception:
        return f"https://www.youtube.com/results?search_query={quote_plus(query)}"


@tool
def abrir_youtube(query: str) -> str:
    """Busca y reproduce un video en YouTube. Abre y reproduce el video directo cuando se especifique un tema o creador."""
    query_limpio = (query or "").strip()
    if query_limpio:
        url = _obtener_top_youtube_url(query_limpio)
    else:
        url = "https://www.youtube.com"

    if _browser_prefers_system():
        if _abrir_en_navegador_sistema(url, require_policy=False):
            if query_limpio:
                return f"Reproduciendo video de '{query_limpio}' en YouTube."
            return "YouTube abierto en navegador predeterminado."
        return "No se pudo abrir YouTube en el navegador predeterminado."
    try:
        worker = _ensure_pw_worker()
        worker.execute(_pw_goto, url)
        if query_limpio:
            return f"Reproduciendo '{query_limpio}' en YouTube con Playwright."
        return "YouTube abierto con Playwright."
    except Exception as e:
        if _abrir_en_navegador_sistema(url):
            if query_limpio:
                return f"Reproduciendo '{query_limpio}' en YouTube."
            return "YouTube abierto en el navegador."
        return f"Error al abrir YouTube: {e}"


@tool
def buscar_en_wikipedia(consulta: str) -> str:
    """Busca un tema y abre directamente el artículo de Wikipedia en el navegador del usuario."""
    query_limpio = str(consulta or "").strip()
    if not query_limpio:
        url = "https://es.wikipedia.org"
    else:
        url = f"https://es.wikipedia.org/wiki/Especial:Buscar?search={quote_plus(query_limpio)}"

    if _browser_prefers_system():
        if _abrir_en_navegador_sistema(url, require_policy=False):
            return f"Abriendo artículo sobre '{query_limpio}' en Wikipedia."
        return "No se pudo abrir Wikipedia."
    try:
        worker = _ensure_pw_worker()
        worker.execute(_pw_goto, url)
        return f"Abriendo '{query_limpio}' en Wikipedia con Playwright."
    except Exception as e:
        if _abrir_en_navegador_sistema(url):
            return f"Abriendo '{query_limpio}' en Wikipedia."
        return f"Error al abrir Wikipedia: {e}"
