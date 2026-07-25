from __future__ import annotations

import json

from modules.youtube import service
from modules.youtube.models import YouTubeCandidate


def _candidate(
    title: str,
    channel: str,
    *,
    video_id: str = "video-1",
) -> YouTubeCandidate:
    return YouTubeCandidate(
        id=video_id,
        title=title,
        channel=channel,
        duration="3:33",
        views="1M views",
        url=f"https://www.youtube.com/watch?v={video_id}",
    )


def test_initial_data_parser_handles_nested_json_and_braces_in_strings():
    payload = {
        "contents": {
            "title": "value with }; and { braces",
            "nested": {"items": [1, 2, 3]},
        }
    }
    html = "<script>var ytInitialData = " + json.dumps(payload) + ";</script>"

    assert service._extract_yt_initial_data(html) == payload


def test_search_candidates_parse_modern_initial_data(monkeypatch):
    payload = {
        "contents": {
            "twoColumnSearchResultsRenderer": {
                "primaryContents": {
                    "sectionListRenderer": {
                        "contents": [
                            {
                                "itemSectionRenderer": {
                                    "contents": [
                                        {
                                            "videoRenderer": {
                                                "videoId": "dQw4w9WgXcQ",
                                                "title": {"runs": [{"text": ("Never Gonna Give You Up")}]},
                                                "ownerText": {"runs": [{"text": "Rick Astley"}]},
                                                "lengthText": {"simpleText": "3:33"},
                                                "viewCountText": {"simpleText": "1B views"},
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                }
            }
        }
    }
    html = ('<html><script>window["ytInitialData"] = ' + json.dumps(payload) + ";</script></html>").encode()

    class Response:
        def read(self, _limit=-1):
            return html

    monkeypatch.setattr(
        service.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    candidates = service.get_youtube_search_candidates("Never Gonna Give You Up Rick Astley")

    assert len(candidates) == 1
    assert candidates[0].id == "dQw4w9WgXcQ"
    assert candidates[0].channel == "Rick Astley"


def test_rank_best_match_rejects_unrelated_candidate():
    result = service.rank_best_match(
        "Never Gonna Give You Up Rick Astley",
        [_candidate("Minecraft building tutorial", "Blocks Channel")],
    )

    assert result is None


def test_rank_best_match_returns_close_candidate():
    expected = _candidate(
        "Never Gonna Give You Up (Official Music Video)",
        "Rick Astley",
    )

    result = service.rank_best_match(
        "Never Gonna Give You Up Rick Astley",
        [
            _candidate("Minecraft building tutorial", "Blocks Channel"),
            expected,
        ],
    )

    assert result == expected


def test_play_reports_search_results_instead_of_false_playback(monkeypatch):
    opened = []
    monkeypatch.setattr(
        service,
        "get_youtube_search_candidates",
        lambda _query: [],
    )
    monkeypatch.setattr(service, "_browser_prefers_system", lambda: True)
    monkeypatch.setattr(
        service,
        "_abrir_en_navegador_sistema",
        lambda url, **_kwargs: opened.append(url) or True,
    )

    result = service.play("an unknown video")

    normalized = result.lower()
    assert "search results" in normalized or "resultados de busqueda" in normalized
    assert "playing" not in normalized
    assert "reproduciendo" not in normalized
    assert "search_query=" in opened[0]


def test_play_reports_opened_match_without_claiming_verified_playback(
    monkeypatch,
):
    expected = _candidate("Never Gonna Give You Up", "Rick Astley")
    monkeypatch.setattr(
        service,
        "get_youtube_search_candidates",
        lambda _query: [expected],
    )
    monkeypatch.setattr(service, "_browser_prefers_system", lambda: True)
    monkeypatch.setattr(
        service,
        "_abrir_en_navegador_sistema",
        lambda *_args, **_kwargs: True,
    )

    result = service.play("Never Gonna Give You Up Rick Astley")

    normalized = result.lower()
    assert "opened" in normalized or "abri" in normalized
    assert "playing" not in normalized
    assert "reproduciendo" not in normalized
