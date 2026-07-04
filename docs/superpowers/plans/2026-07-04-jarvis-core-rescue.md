# JARVIS Core Rescue Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with systematic debugging and TDD.

**Goal:** Produce a stable core mode that starts with Groq chat, local voice input, Piper TTS, basic memory, Spotify, and the segmented conversation UI while keeping heavy subsystems optional.

**Architecture:** Environment-backed feature flags live in `core.jarvis_config`. Existing modules remain available in full mode, but imports and background initialization check the flags before loading SpeechBrain, Hugging Face RAG, Groq vision, plugins, or startup briefing work.

**Tech Stack:** Python 3.11/3.12, Quart, pytest, Groq OpenAI-compatible API, vanilla JavaScript.

---

### Task 1: Close the Groq migration regression

**Files:**
- Modify: `src/backend/core/brain/social_engine.py`
- Test: `tests/test_smoke.py`

- [x] Add Spanish technical-query indicators (`que es`, `como funciona`, `cual es`) to `_TECH_QUERY_HINTS`.
- [x] Run `pytest tests/test_smoke.py::test_dynamic_queries_force_web_tools -q` and confirm it passes.

### Task 2: Add centralized core-mode flags

**Files:**
- Modify: `src/backend/core/jarvis_config.py`
- Test: `tests/test_core_mode.py`

- [x] Add a strict boolean environment parser.
- [x] Add `JARVIS_CORE_MODE`, `JARVIS_VOICE_ID_ENABLED`, `JARVIS_RAG_ENABLED`, `JARVIS_VISION_ENABLED`, `JARVIS_PLUGINS_ENABLED`, `JARVIS_BRIEFING_ENABLED`, and `JARVIS_TELEGRAM_ENABLED`.
- [x] Make heavy features default to disabled in core mode and enabled in full mode.
- [x] Test defaults and explicit overrides using a pure feature-flag builder.

### Task 3: Gate heavy initialization

**Files:**
- Modify: `src/backend/voice/identifier.py`
- Modify: `src/backend/engines/memory_rag.py`
- Modify: `src/backend/core/brain/llm_engine.py`
- Modify: `src/backend/core/brain/prompts.py`
- Test: `tests/test_core_mode.py`

- [x] Skip SpeechBrain import and voice-identifier initialization when disabled.
- [x] Skip RAG background initialization and retrieval when disabled.
- [x] Skip Groq vision model creation, dynamic plugins, and startup briefing when disabled.
- [x] Keep Groq text chat, local STT, Piper TTS, memory, and base tools active.

### Task 4: Document and verify

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `AGENTS.md`

- [x] Document `JARVIS_CORE_MODE=true` as the recommended recovery/default troubleshooting mode.
- [x] Run the full pytest suite, Python compilation, JavaScript syntax checks, Pyflakes, and `git diff --check`.
- [x] Start the backend in core mode and verify `/api/status`.
