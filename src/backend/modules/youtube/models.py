"""YouTube candidate data structures."""

from dataclasses import dataclass


@dataclass(frozen=True)
class YouTubeCandidate:
    id: str
    title: str
    channel: str
    duration: str
    views: str
    url: str
    score: float = 0.0
