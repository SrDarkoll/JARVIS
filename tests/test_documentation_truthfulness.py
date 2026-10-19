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


def test_gemini_live_configuration_documents_privacy_cost_and_stable_tools():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    for key in (
        "JARVIS_LIVE_CONTEXT_COMPRESSION_ENABLED",
        "JARVIS_LIVE_COMPRESSION_TRIGGER_TOKENS",
        "JARVIS_LIVE_COMPRESSION_TARGET_TOKENS",
        "JARVIS_LIVE_INPUT_TRANSCRIPTION_ENABLED",
        "JARVIS_LIVE_OUTPUT_TRANSCRIPTION_ENABLED",
        "JARVIS_LIVE_MAX_SESSIONS",
        "JARVIS_LIVE_MAX_SESSIONS_PER_PROFILE",
    ):
        assert key in env_example

    readme_lower = readme.lower()
    assert "stable voice-tool allowlist" in readme_lower
    assert "transcription token surcharge" in readme_lower
    assert "native session resumption remains disabled" in readme_lower
    assert "dynamically exposes the registered base tool schemas" not in readme_lower


def test_stable_core_documents_hybrid_reasoning_and_experimental_flags():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert 'JARVIS_REASONING_MODE="hybrid"' in env_example
    for flag in (
        "JARVIS_COMPUTER_USE_ENABLED",
        "JARVIS_PROACTIVE_TOOLS_ENABLED",
        "JARVIS_COGNITIVE_MEMORY_ENABLED",
        "JARVIS_ACTION_PLANS_ENABLED",
    ):
        assert flag in env_example
    assert "experimental tool surfaces are excluded" in readme.lower()
