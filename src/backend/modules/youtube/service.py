"""YouTube orchestration, candidate ranking, and playback policy."""

import difflib
import json
import re
import urllib.parse
import urllib.request

from core import jarvis_state
from core.media_state import set_last_media_source
from modules.youtube.messages import text as _yt_text
from modules.youtube.messages import video_label as _yt_label
from modules.youtube.models import YouTubeCandidate
from tools.browser import (
    _abrir_en_navegador_sistema,
    _browser_prefers_system,
    _ensure_pw_worker,
    _pw_goto,
)
from utils.jarvis_text import reparar_unicode

_MAX_SEARCH_RESPONSE_BYTES = 4 * 1024 * 1024
_MINIMUM_MATCH_SCORE = 0.30
_INITIAL_DATA_MARKERS = (
    re.compile(r"(?:var\s+)?ytInitialData\s*=\s*"),
    re.compile(r"window\[\s*[\"']ytInitialData[\"']\s*\]\s*=\s*"),
)


def _es_short_clip(duration: str) -> bool:
    """Verifica de forma segura si el video es un Short o clip menor a 30 segundos."""
    if not duration:
        return False
    parts = duration.split(":")
    if len(parts) == 2:
        try:
            mins = int(parts[0].strip())
            secs = int(parts[1].strip())
            if mins == 0 and secs < 30:
                return True
        except ValueError:
            pass
    return False


def _extract_yt_initial_data(html: str) -> dict | None:
    """Decode the first balanced ytInitialData JSON object."""
    decoder = json.JSONDecoder()
    for marker in _INITIAL_DATA_MARKERS:
        for match in marker.finditer(str(html or "")):
            remainder = html[match.end() :].lstrip()
            if not remainder.startswith("{"):
                continue
            try:
                payload, _end = decoder.raw_decode(remainder)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                return payload
    return None


def get_youtube_search_candidates(query: str) -> list[YouTubeCandidate]:
    """Busca en YouTube y extrae candidatos estructurados con título, canal, vistas y duración."""
    clean_query = str(query or "").strip()
    if not clean_query:
        return []

    # Phonetic STT corrections for common YouTube terms & channel titles
    clean_query = re.sub(
        r"\b(?:informen|inforcut)\s+(?:de\s+)?hcf\b",
        "Informe HCF",
        clean_query,
        flags=re.IGNORECASE,
    )

    search_url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(clean_query)
    req = urllib.request.Request(
        search_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
        },
    )

    try:
        raw_html = urllib.request.urlopen(req, timeout=5).read(_MAX_SEARCH_RESPONSE_BYTES + 1)
        if len(raw_html) > _MAX_SEARCH_RESPONSE_BYTES:
            return []
        html = raw_html.decode("utf-8", errors="replace")
    except Exception:
        return []

    data = _extract_yt_initial_data(html)
    candidates: list[YouTubeCandidate] = []
    if data:
        try:
            contents = (
                data.get("contents", {})
                .get("twoColumnSearchResultsRenderer", {})
                .get("primaryContents", {})
                .get("sectionListRenderer", {})
                .get("contents", [])
            )
            for section in contents:
                item_section = section.get("itemSectionRenderer", {})
                for item in item_section.get("contents", []):
                    vr = item.get("videoRenderer")
                    if not vr:
                        continue
                    vid_id = vr.get("videoId")
                    title = "".join(r.get("text", "") for r in vr.get("title", {}).get("runs", []))
                    channel = "".join(r.get("text", "") for r in vr.get("ownerText", {}).get("runs", []))
                    duration = vr.get("lengthText", {}).get("simpleText", "")
                    views = vr.get("viewCountText", {}).get("simpleText", "")

                    # Filter out short meme clips/shorts under 30s unless explicitly requested
                    if _es_short_clip(duration) and "short" not in clean_query.lower():
                        continue

                    if vid_id and title:
                        safe_title = reparar_unicode(title)
                        safe_channel = reparar_unicode(channel)
                        candidates.append(
                            YouTubeCandidate(
                                id=vid_id,
                                title=safe_title,
                                channel=safe_channel,
                                duration=duration,
                                views=views,
                                url=f"https://www.youtube.com/watch?v={vid_id}",
                            )
                        )
        except (AttributeError, TypeError, ValueError):
            return []

    return candidates


def rank_best_match(query: str, candidates: list[YouTubeCandidate]) -> YouTubeCandidate | None:
    """Reordena y selecciona el candidato de YouTube que más se parece a la búsqueda del usuario."""
    if not candidates:
        return None

    query_lower = query.lower()
    q_words = set(re.findall(r"\w+", query_lower))
    numbers_in_query = set(re.findall(r"\b\d+\b", query_lower))

    best_candidate = None
    best_score = -10.0

    for cand in candidates:
        cand_str = f"{cand.title} {cand.channel}".lower()
        seq_ratio = difflib.SequenceMatcher(None, query_lower, cand_str).ratio()

        c_words = set(re.findall(r"\w+", cand_str))
        overlap = len(q_words & c_words) / max(1, len(q_words)) if q_words else 0.0

        # Episode/Number match evaluation
        cand_numbers = set(re.findall(r"\b\d+\b", cand_str))
        number_bonus = 0.0
        if numbers_in_query:
            if numbers_in_query.issubset(cand_numbers):
                number_bonus = 0.4
            else:
                number_bonus = -0.3

        # Weighted score: word overlap (40%), sequence similarity (30%), number match (bonus/penalty)
        score = (seq_ratio * 0.3) + (overlap * 0.4) + number_bonus

        if score > best_score:
            best_score = score
            best_candidate = cand

    if best_score < _MINIMUM_MATCH_SCORE:
        return None
    return best_candidate


def _opened_youtube_response(
    query: str,
    candidate: YouTubeCandidate | None,
    *,
    via_playwright: bool = False,
) -> str:
    set_last_media_source(
        jarvis_state.get_active_profile_id(),
        "youtube",
    )
    if candidate is None:
        return _yt_text(
            f"I opened YouTube search results for '{query}' because I could not identify a reliable exact match.",
            f"Abri los resultados de busqueda de YouTube para '{query}' porque no pude identificar una coincidencia confiable.",
        )

    label = _yt_label(candidate.title, candidate.channel)
    if via_playwright:
        return _yt_text(
            f"Opened {label} on YouTube via Playwright.",
            f"Abri {label} en YouTube mediante Playwright.",
        )
    return _yt_text(
        f"Opened {label} on YouTube.",
        f"Abri {label} en YouTube.",
    )


def play(query: str) -> str:
    """Busca en YouTube, lee títulos, selecciona la mejor coincidencia y reproduce el video."""
    clean_query = str(query or "").strip()
    if not clean_query:
        return _yt_text(
            "Tell me what video or creator to play on YouTube.",
            "Dime qué video o creador deseas reproducir en YouTube.",
        )

    candidates = get_youtube_search_candidates(clean_query)
    best = rank_best_match(clean_query, candidates)

    opened_search_results = best is None
    if opened_search_results:
        video_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_query)}"
    else:
        video_url = best.url

    if _browser_prefers_system():
        if _abrir_en_navegador_sistema(video_url, require_policy=False):
            return _opened_youtube_response(clean_query, best)
        return _yt_text(
            "Could not open YouTube in system browser.",
            "No se pudo abrir YouTube en el navegador.",
        )
    try:
        worker = _ensure_pw_worker()
        worker.execute(_pw_goto, video_url)
        return _opened_youtube_response(
            clean_query,
            best,
            via_playwright=True,
        )
    except Exception:
        if _abrir_en_navegador_sistema(video_url):
            return _opened_youtube_response(clean_query, best)
        return _yt_text(
            "Could not open YouTube.",
            "No se pudo abrir YouTube.",
        )
