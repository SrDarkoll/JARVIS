from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_avoids_unverified_agi_performance_and_capability_claims():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()

    for unsupported in (
        "sub-100ms ttfa",
        "zero-latency visual reasoning",
        "<0.05ms",
        "1-click quick-fixes",
        "interconnected semantic graph",
    ):
        assert unsupported not in readme

    assert "experimental" in readme
    assert "deterministic lexical" in readme
    assert "explicit confirmation" in readme


def test_memory_and_live_descriptions_match_implemented_algorithms():
    embeddings = (
        ROOT / "src/backend/modules/memory/embeddings.py"
    ).read_text(encoding="utf-8").lower()
    jit_cache = (
        ROOT / "src/backend/modules/memory/jit_cache.py"
    ).read_text(encoding="utf-8").lower()
    memory_tools = (
        ROOT / "src/backend/modules/memory/tools.py"
    ).read_text(encoding="utf-8").lower()
    live_client = (
        ROOT / "src/frontend/static/js/modules/live-voice.js"
    ).read_text(encoding="utf-8").lower()
    live_gemini = (
        ROOT / "src/backend/voice/live_gemini.py"
    ).read_text(encoding="utf-8").lower()

    assert "deterministic lexical" in embeddings
    assert "semantic dense vectorizer" not in embeddings
    assert "sub-millisecond associative search" not in jit_cache
    assert "< 0.1ms" not in memory_tools
    assert "zero-latency" not in live_client
    assert "record_barge_in(85.0)" not in live_gemini
    assert '"total_ms": ttfa_ms' not in live_gemini


def test_api_token_documentation_covers_protected_http_and_websocket_routes():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "protected http/websocket routes" in readme.lower()
    assert "protected http and websocket routes" in env_example.lower()
