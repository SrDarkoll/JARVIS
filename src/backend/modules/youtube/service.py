"""YouTube orchestration, candidate ranking, and playback policy."""

import difflib
import json
import re
import urllib.parse
import urllib.request

from modules.youtube.messages import text as _yt_text
from modules.youtube.messages import video_label as _yt_label
from modules.youtube.models import YouTubeCandidate
from tools.browser import (
    _abrir_en_navegador_sistema,
    _browser_prefers_system,
    _ensure_pw_worker,
    _pw_goto,
)


def get_youtube_search_candidates(query: str) -> list[YouTubeCandidate]:
    """Busca en YouTube y extrae candidatos estructurados con título, canal, vistas y duración."""
    clean_query = str(query or "").strip()
    if not clean_query:
        return []

    search_url = (
        "https://www.youtube.com/results?search_query="
        + urllib.parse.quote(clean_query)
    )
    req = urllib.request.Request(
        search_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )

    try:
        html = urllib.request.urlopen(req, timeout=5).read().decode("utf-8")
    except Exception:
        return []

    match = re.search(r"var ytInitialData = (\{.*?\});</script>", html)
    if not match:
        match = re.search(r"window\[\"ytInitialData\"\] = (\{.*?\});</script>", html)

    candidates: list[YouTubeCandidate] = []
    if match:
        try:
            data = json.loads(match.group(1))
            contents = data["contents"]["twoColumnSearchResultsRenderer"][
                "primaryContents"
            ]["sectionListRenderer"]["contents"]
            for section in contents:
                item_section = section.get("itemSectionRenderer", {})
                for item in item_section.get("contents", []):
                    vr = item.get("videoRenderer")
                    if not vr:
                        continue
                    vid_id = vr.get("videoId")
                    title = "".join(
                        r.get("text", "")
                        for r in vr.get("title", {}).get("runs", [])
                    )
                    channel = "".join(
                        r.get("text", "")
                        for r in vr.get("ownerText", {}).get("runs", [])
                    )
                    duration = vr.get("lengthText", {}).get("simpleText", "")
                    views = vr.get("viewCountText", {}).get("simpleText", "")

                    # Filter out short meme clips/shorts under 30s unless explicitly requested
                    if duration and (duration.count(":") == 0 or (duration.startswith("0:") and int(duration.split(":")[1]) < 30)):
                        if "short" not in clean_query.lower():
                            continue

                    if vid_id and title:
                        from utils.jarvis_i18n import reparar_unicode
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
        except Exception:
            pass

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

    return best_candidate or candidates[0]


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

    if not best:
        # Fallback to search results URL if no candidate could be parsed
        video_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_query)}"
        label = f"'{clean_query}'"
    else:
        video_url = best.url
        label = _yt_label(best.title, best.channel)

    if _browser_prefers_system():
        if _abrir_en_navegador_sistema(video_url, require_policy=False):
            return _yt_text(
                f"Playing {label} on YouTube.",
                f"Reproduciendo {label} en YouTube.",
            )
        return _yt_text(
            "Could not open YouTube in system browser.",
            "No se pudo abrir YouTube en el navegador.",
        )
    try:
        worker = _ensure_pw_worker()
        worker.execute(_pw_goto, video_url)
        return _yt_text(
            f"Playing {label} on YouTube via Playwright.",
            f"Reproduciendo {label} en YouTube mediante Playwright.",
        )
    except Exception as e:
        if _abrir_en_navegador_sistema(video_url):
            return _yt_text(
                f"Playing {label} on YouTube.",
                f"Reproduciendo {label} en YouTube.",
            )
        return f"Error: {e}"
