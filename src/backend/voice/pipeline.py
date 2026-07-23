"""Pipeline de audio para identificación biométrica.

Responsabilidades:
- Conversión de audio (WebM/Opus → WAV 16kHz)
- Transcripción (hint Web Speech API primero, Whisper como fallback)
- Gestión de sesiones de registro de invitados
- Normalización de nombres y slugs
"""
from __future__ import annotations

import io
import os
import re
import tempfile
import time as _time
import unicodedata
import wave

from core.jarvis_config import RUNTIME_DIR, RUNTIME_PATHS
from utils.audio_conversion import AudioConversionError, convert_to_wav
from utils.jarvis_i18n import get_current_language, get_whisper_lang
from utils.jarvis_text import reparar_unicode
from voice.session_store import VoiceSessionMapping, VoiceSessionStore

# =========================================================
# CONSTANTES
# =========================================================
RESERVED_OWNER_ALIASES = {"admin", "administrador", "creador", "sistema"}
OWNER_SIMILARITY_OVERRIDE = float(
    (os.getenv("VOICE_ID_OWNER_OVERRIDE") or "0.58").strip() or "0.58"
)

_NO_ES_NOMBRE = {
    "quien", "quién", "soy", "yo", "él", "ella", "usted", "nada", "algo",
    "hola", "oye", "jarvis", "si", "sí", "no", "bueno", "pues", "maldito",
    "mismo", "igual", "lo mismo", "eso", "esto", "aqui", "aquí", "ok",
    "invitado", "usuario", "persona", "alguien", "nadie", "todos",
    "who", "what", "where", "when", "why", "how", "me", "my", "name",
    "you", "your", "guest", "user", "person", "someone", "nobody",
}

_PENDING_VOICE_REGISTRATION_TTL = 300
_VOICE_SESSION_STORE = VoiceSessionStore(
    clock=_time.time,
    ttl_seconds=_PENDING_VOICE_REGISTRATION_TTL,
)
# Compatibility mapping for routes and legacy code still using dict syntax.
_PENDING_VOICE_REGISTRATION = VoiceSessionMapping(_VOICE_SESSION_STORE)

try:
    WHISPER_BEAM_SIZE = max(
        1, int((os.getenv("JARVIS_WHISPER_BEAM_SIZE") or "1").strip() or "1")
    )
except Exception:
    WHISPER_BEAM_SIZE = 1

try:
    HINT_MIN_CONFIDENCE = float(
        (os.getenv("JARVIS_VOICE_HINT_MIN_CONFIDENCE") or "0.58").strip() or "0.58"
    )
except Exception:
    HINT_MIN_CONFIDENCE = 0.58


def get_active_whisper_language() -> str:
    """Return the Whisper language code for the current runtime language."""
    try:
        return get_whisper_lang(get_current_language())
    except Exception:
        return "en"


# =========================================================
# AUDIO: CONVERSIÓN
# =========================================================

def bytes_es_wav_valido(audio_bytes: bytes) -> bool:
    if not audio_bytes or len(audio_bytes) < 44:
        return False
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            return wf.getnframes() > 0
    except Exception:
        return False


def wav_ya_optimizado(audio_bytes: bytes) -> bool:
    if not bytes_es_wav_valido(audio_bytes):
        return False
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            return (
                wf.getnchannels() == 1
                and wf.getframerate() == 16000
                and wf.getsampwidth() == 2
                and wf.getnframes() > 0
            )
    except Exception:
        return False


def normalizar_a_wav(audio_bytes: bytes) -> tuple[bytes, bool]:
    """Convierte cualquier formato de audio a WAV 16kHz mono PCM.
    Retorna (wav_bytes, ok).
    """
    if wav_ya_optimizado(audio_bytes):
        return audio_bytes, True
    try:
        runtime_dir = RUNTIME_DIR
        return convert_to_wav(audio_bytes, runtime_dir=runtime_dir), True
    except AudioConversionError:
        return audio_bytes, False

# =========================================================
# TRANSCRIPCIÓN
# =========================================================

def normalizar_transcript_hint(texto: str) -> str:
    texto = reparar_unicode(str(texto or "")).strip()
    if not texto:
        return ""
    texto = re.sub(r"^[\s¿?¡!.,;:]+", "", texto)
    texto = re.sub(r"\s+", " ", texto)
    texto = re.sub(r"\s+([,.;:!?])", r"\1", texto)
    texto = re.sub(r"([.!?])(?=[^\s])", r"\1 ", texto)
    return texto.strip()


def normalizar_confianza_transcript(value) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        conf = float(value)
    except Exception:
        return None
    if conf < 0.0:
        return 0.0
    if conf > 1.0:
        return 1.0
    return conf


def hint_necesita_reintento_whisper(
    transcript_hint: str,
    transcript_confidence=None,
) -> bool:
    hint = normalizar_transcript_hint(transcript_hint)
    if not hint:
        return True
    if not re.search(r"[A-Za-z0-9áéíóúñüÁÉÍÓÚÑÜ]", hint):
        return True

    conf = normalizar_confianza_transcript(transcript_confidence)
    tokens = re.findall(r"[A-Za-z0-9áéíóúñüÁÉÍÓÚÑÜ]+", hint)
    token_count = len(tokens)
    cleaned_lower = " ".join(tokens).lower().strip()
    if cleaned_lower in {
        "jarvis", "jarvi", "jarbis", "jarbi",
        "jarviz", "jarvix", "jarves", "jarbiz",
        "jarvys", "jarvs", "jarbes", "jarbez",
        "yarvis", "yarvi", "yarbis", "yarbi",
        "yarbiz", "yarviz", "yarvix", "yarves",
        "yarvys", "yarvs", "yarbes", "yarbez",
        "harvis", "harvi", "harbis", "harbi",
        "harviz", "harvix", "harves", "harbiz",
        "charvis", "charvi", "charbis", "charbi",
        "garvis", "garvi", "garbis", "garbi",
    }:
        return True

    if conf is None:
        return False
    if token_count <= 2:
        return conf < max(HINT_MIN_CONFIDENCE - 0.15, 0.30)
    return conf < HINT_MIN_CONFIDENCE


def reconstruir_transcripcion_por_pausas(
    segments, pausa_punto: float = 0.85, pausa_coma: float = 0.45
) -> str:
    partes: list[str] = []
    prev_end = None
    for seg in segments:
        txt = reparar_unicode((getattr(seg, "text", "") or "").strip())
        if not txt:
            continue
        if partes and prev_end is not None:
            try:
                pausa = float(getattr(seg, "start", prev_end)) - float(prev_end)
            except Exception:
                pausa = 0.0
            if pausa >= pausa_punto and not re.search(r"[.!?]$", partes[-1]):
                partes[-1] = partes[-1].rstrip(",;: ") + "."
            elif pausa >= pausa_coma and not re.search(r"[,;:.!?]$", partes[-1]):
                partes[-1] = partes[-1].rstrip() + ","
        partes.append(txt)
        prev_end = getattr(seg, "end", prev_end)
    texto = " ".join(partes).strip()
    if not texto:
        return ""
    texto = re.sub(r"\s+([,.;:!?])", r"\1", texto)
    texto = re.sub(r"([.!?])(?=[^\s])", r"\1 ", texto)
    return texto.strip()


def transcribir_audio(
    audio_bytes: bytes,
    transcript_hint: str = "",
    whisper_model=None,
    transcript_confidence=None,
) -> str:
    """Usa el hint del Web Speech API como fuente principal.
    Whisper solo como fallback cuando el hint está completamente vacío (ej: Telegram).
    NO usar Whisper para "validar" hints — el Web Speech API de Chrome es suficientemente preciso.
    """
    raw_hint = normalizar_transcript_hint(transcript_hint)
    hint = raw_hint
    if hint_necesita_reintento_whisper(hint, transcript_confidence):
        hint = ""

    # Hint con al menos una letra → confiar directamente, sin excepciones
    if hint and re.search(r"[a-zA-ZáéíóúñüÁÉÍÓÚÑÜ]", hint):
        print(f"[VOICE ID] Using client transcript: '{hint[:80]}'")
        return hint

    # Sin hint → fallback Whisper (solo Telegram o fallo total de Web Speech)
    if not whisper_model:
        return raw_hint

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="temp_api_voice_",
            suffix=".wav",
            dir=RUNTIME_PATHS.temp,
            delete=False,
        ) as temp_file:
            temp_file.write(audio_bytes)
            temp_path = temp_file.name

        whisper_lang = get_active_whisper_language()
        segments_iter, _ = whisper_model.transcribe(
            temp_path,
            language=whisper_lang,
            vad_filter=True,
            beam_size=WHISPER_BEAM_SIZE,
            condition_on_previous_text=False,
        )
        texto = normalizar_transcript_hint(
            reconstruir_transcripcion_por_pausas(list(segments_iter))
        )
        if texto:
            print(f"[VOICE ID] Whisper transcribed: '{texto[:80]}'")
            return texto
        return raw_hint
    except Exception as e:
        print(f"[VOICE ID] Whisper Error: {e}")
        return raw_hint
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


# =========================================================
# NOMBRES Y SLUGS DE INVITADOS
# =========================================================

def slugify_guest_name(name: str) -> str:
    txt = reparar_unicode(str(name or "")).strip().lower()
    txt = "".join(
        ch for ch in unicodedata.normalize("NFKD", txt)
        if not unicodedata.combining(ch)
    )
    txt = re.sub(r"[^a-z0-9\s_-]+", "", txt)
    txt = re.sub(r"\s+", "_", txt)
    txt = re.sub(r"_+", "_", txt).strip("_")
    return txt[:40] or "invitado"


def normalizar_nombre_invitado(texto_nombre: str) -> str:
    nombre = reparar_unicode(texto_nombre).strip()

    match = re.search(
        r"(?:yo\s+soy|soy|me\s+llamo|mi\s+nombre\s+es|my\s+name\s+is|i\s+am|i'm|call\s+me)\s+([A-Za-záéíóúñüÁÉÍÓÚÑÜ]+(?:\s+[A-Za-záéíóúñüÁÉÍÓÚÑÜ]+){0,2})",
        nombre,
        flags=re.IGNORECASE,
    )
    if match:
        candidato = match.group(1).strip()
        if candidato.lower().strip() not in _NO_ES_NOMBRE and len(candidato) >= 2:
            nombre = candidato
        else:
            return "Invitado"
    else:
        # Fallback: si el texto es una sola palabra con formato de nombre propio (Mayúsculas mixtas),
        # interpretarlo directamente como nombre sin frase introductoria
        palabras_raw = nombre.split()
        if len(palabras_raw) == 1 and len(palabras_raw[0]) >= 2:
            palabra = palabras_raw[0]
            # Quitar puntuación residual
            palabra = re.sub(r"[.!?,;:]+", "", palabra).strip()
            if palabra and palabra.lower() not in _NO_ES_NOMBRE:
                # Verificar que parece nombre propio (no todo mayúsculas tipo ACrónimo)
                if not (palabra.isupper() and len(palabra) <= 4):
                    nombre = palabra
                else:
                    return "Invitado"
            else:
                return "Invitado"
        else:
            return "Invitado"

    nombre = re.sub(r"[.!?,;:]+", "", nombre).strip()
    if not nombre or len(nombre) < 2:
        return "Invitado"
    palabras = [p for p in nombre.split() if p]
    if not palabras:
        return "Invitado"
    return " ".join(p.capitalize() for p in palabras[:3])[:48]


def es_alias_owner(nombre: str) -> bool:
    raw = reparar_unicode(str(nombre or "")).strip().lower()
    if not raw:
        return False
    raw = re.sub(r"[^a-záéíóúñü\s]", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw in RESERVED_OWNER_ALIASES


# =========================================================
# SESIONES DE REGISTRO PENDIENTES
# =========================================================

def cancel_pending_voice_registration(ip: str = None) -> int:
    """Cancela registros pendientes. Si ip=None, cancela TODOS."""
    if ip:
        return 1 if _VOICE_SESSION_STORE.cancel(ip) else 0
    count = len(_VOICE_SESSION_STORE.keys())
    _VOICE_SESSION_STORE.cancel()
    return count


def cleanup_pending_voice_registration() -> None:
    _VOICE_SESSION_STORE.cleanup_expired()


def get_pending(ip: str) -> dict:
    return _VOICE_SESSION_STORE.get(ip) or {}


def set_pending(ip: str, data: dict) -> None:
    _VOICE_SESSION_STORE.replace(ip, data)


def pop_pending(ip: str) -> None:
    _VOICE_SESSION_STORE.pop(ip)
