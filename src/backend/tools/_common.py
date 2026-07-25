"""
Helpers compartidos por todos los sub-módulos de tools.
Consolidado para usar ServiceContainer exclusivamente.
"""

import os
import re
import sys
import unicodedata

from core import jarvis_config, jarvis_state
from core.service_container import services

IS_WINDOWS = sys.platform == "win32"


def _open_url_or_app(target: str) -> bool:
    """Abre una URL o URI de aplicación de forma multiplataforma.

    Args:
        target: URL (http://..., https://...) o URI (spotify:, discord:, etc.)

    Returns:
        True si se abrió exitosamente, False si no se pudo.
    """
    try:
        if IS_WINDOWS:
            import webbrowser

            if target.startswith(("http://", "https://")):
                webbrowser.open(target)
            else:
                webbrowser.open(target)
            return True
        else:
            import subprocess

            if sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
            return True
    except Exception:
        return False


def _es_platform_windows() -> bool:
    return IS_WINDOWS


_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(_TOOLS_DIR)
ROOT_DIR = os.path.dirname(os.path.dirname(BASE_DIR))  # jarvis/

MEMORIA_PROFILES_FILE = jarvis_config.MEMORIA_PROFILES_FILE
MEMORIA_FILE = jarvis_config.MEMORIA_FILE

DEFAULT_PROFILE_ID = jarvis_state.DEFAULT_PROFILE_ID
SHARED_PROFILE_ID = "shared"
memoria_lock = jarvis_state.memoria_lock
_perfiles_memoria = jarvis_state._perfiles_memoria
_msg_counter_by_profile = jarvis_state._msg_counter_by_profile


def _log_event(event_type: str, **payload) -> None:
    services.log_event(event_type, **payload)


def _warn_once(key: str, err: Exception | str) -> None:
    msg = str(err or "").strip() or "error desconocido"
    print(f"[WARN {key}] {msg}")
    _log_event("core_tools_warning", key=key, error=msg[:300])


def _normalizar_profile_id(profile_id: str | None) -> str:
    pid = (profile_id or DEFAULT_PROFILE_ID).strip().lower()
    pid = re.sub(r"[^a-z0-9_.-]+", "_", pid)
    pid = pid.strip("._-") or DEFAULT_PROFILE_ID
    return pid[:64]


def _profile_scope(profile_id: str | None) -> str:
    pid = _normalizar_profile_id(profile_id)
    if pid == DEFAULT_PROFILE_ID:
        return "owner"
    if pid == SHARED_PROFILE_ID:
        return "shared"
    return "guest"


def _texto_limpio_memoria(texto: str) -> str:
    out = str(texto or "").replace("\x00", " ")
    try:
        if services.reparar_unicode:
            out = services.reparar_unicode(out)
    except Exception:
        pass
    return re.sub(r"\s+", " ", out).strip()


def _normalizar_ascii(texto: str) -> str:
    t = _texto_limpio_memoria(texto).lower()
    t = "".join(ch for ch in unicodedata.normalize("NFKD", t) if not unicodedata.combining(ch))
    return t


def _limpiar_respuesta(texto: str) -> str:
    """Limpia símbolos especiales del texto de respuesta para una lectura más limpia."""
    if not texto:
        return ""
    txt = texto.strip()
    txt = re.sub(r"\*\*", "", txt)
    txt = re.sub(r"(?m)^-\s*", "", txt)
    txt = re.sub(r"\(https?://[^)]+\)", "", txt)
    txt = re.sub(r"\([^\)]*REF[^\)]*\)", "", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def _similitud_texto(a: str, b: str) -> float:
    """Similitud básica entre dos textos (Jaccard de palabras)."""
    if not a or not b:
        return 0.0
    pa = set(a.lower().split())
    pb = set(b.lower().split())
    if not pa or not pb:
        return 0.0
    interseccion = len(pa & pb)
    union = len(pa | pb)
    return interseccion / union if union > 0 else 0.0


def _normalizar_destino_web(destino: str) -> str:
    d = str(destino or "").strip().lower()
    if not d:
        return "https://www.google.com"
    d = re.sub(r"^(abre|habre|abreme|ir a|entrar a|busca|pon)\s+", "", d)
    if "youtube" in d and "." not in d:
        d = "youtube.com"
    if "facebook" in d and "." not in d:
        d = "facebook.com"
    if "google" in d and "." not in d:
        d = "google.com"
    if "github" in d and "." not in d:
        d = "github.com"
    if "." not in d:
        d = f"{d}.com"
    if not d.startswith(("http", "www")):
        d = f"https://{d}"
    return d
