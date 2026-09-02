"""Lightweight deterministic lexical feature hashing and similarity engine."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

_STOPWORDS = frozenset({
    "de", "la", "el", "en", "con", "y", "un", "una", "unos", "unas", "para", "por", "que", "es", "son",
    "del", "al", "los", "las", "o", "u", "se", "su", "sus", "lo", "como", "mas", "pero",
    "the", "a", "an", "in", "of", "to", "for", "and", "with", "on", "at", "by", "from", "is", "are",
})


def _tokenize(text: str) -> list[str]:
    """Tokenize and extract normalized content words and subword n-grams."""
    clean = re.sub(r"[^\w\s]", " ", str(text or "").lower())
    words = [w for w in clean.split() if len(w) >= 3 and w not in _STOPWORDS]
    tokens = list(words)
    for w in words:
        if len(w) >= 4:
            tokens.extend([w[i : i + 3] for i in range(len(w) - 2)])
    return tokens


class LocalSemanticVectorizer:
    """Compatibility name for a deterministic lexical hashing vectorizer.

    This is not a learned embedding model and does not infer synonym meaning.
    """

    def __init__(self, dimension: int = 256) -> None:
        self.dimension = dimension

    def encode(self, text: str) -> list[float]:
        """Hash normalized words and trigrams into a normalized float vector."""
        vec = [0.0] * self.dimension
        tokens = _tokenize(text)
        if not tokens:
            return vec

        for token in tokens:
            # Deterministic bucket hashing
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dimension
            # Positive frequency weighting
            weight = 1.0 + math.log(1.0 + len(token))
            vec[idx] += weight

        # L2 Normalize vector
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 1e-9:
            vec = [v / norm for v in vec]

        return vec

    @staticmethod
    def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
        """Compute cosine similarity between two normalized vectors."""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
        return max(0.0, min(1.0, dot))


# Global singleton instance
local_vectorizer = LocalSemanticVectorizer(dimension=128)
