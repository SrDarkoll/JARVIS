# Spotify Module Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Spotify into a cohesive first-party module while preserving all existing playback, recommendation, control, and follow-up behavior.

**Architecture:** `modules/spotify` becomes the source of truth. API, Desktop, messages, follow-up state, orchestration, and tool adapters receive explicit package boundaries; `tools/spotify.py` remains only as a compatibility facade.

**Tech Stack:** Python 3.11+, Quart integration, LangChain tools, Spotipy, pywinauto, pytest.

---

### Task 1: Establish the package boundary

**Files:**
- Create: `src/backend/modules/__init__.py`
- Create: `src/backend/modules/spotify/__init__.py`
- Move: `src/backend/tools/spotify_desktop/*.py` to `src/backend/modules/spotify/desktop/`
- Move: `src/backend/tools/spotify_desktop/followup.py` to `src/backend/modules/spotify/followup.py`

- [x] Move files without changing behavior.
- [x] Update internal imports to `modules.spotify.desktop`.
- [x] Run the Desktop, matching, and follow-up tests.

### Task 2: Separate shared configuration and messages

**Files:**
- Create: `src/backend/modules/spotify/config.py`
- Create: `src/backend/modules/spotify/messages.py`

- [x] Move environment-backed constants and redirect validation into `config.py`.
- [x] Move language and track-label formatting into `messages.py`.
- [x] Add focused tests for the public helpers through existing regressions.

### Task 3: Separate Web API responsibilities

**Files:**
- Create: `src/backend/modules/spotify/api/__init__.py`
- Create: `src/backend/modules/spotify/api/client.py`
- Create: `src/backend/modules/spotify/api/recommendations.py`
- Create: `src/backend/modules/spotify/api/playback.py`

- [x] Move OAuth, cached-token, search, market, and device helpers to `client.py`.
- [x] Move AutoMix and similar-track selection to `recommendations.py`.
- [x] Move deterministic playback and API controls to `playback.py`.
- [x] Replace cross-file globals with module-qualified state or structured results.
- [x] Run recommendation and API fallback tests.

### Task 4: Add service and tool adapters

**Files:**
- Create: `src/backend/modules/spotify/service.py`
- Create: `src/backend/modules/spotify/tools.py`
- Replace: `src/backend/tools/spotify.py`
- Modify: `src/backend/tools/__init__.py`
- Modify: `src/backend/core/core_tools.py`
- Modify: `src/backend/core/brain/processor.py`

- [x] Put provider selection and fallback state in `service.py`.
- [x] Keep only decorated LangChain entry points in `tools.py`.
- [x] Make the old path re-export public tools for compatibility.
- [x] Update first-party callers to the new package.

### Task 5: Update tests and remove obsolete implementation paths

**Files:**
- Modify: `tests/test_spotify_*.py`
- Modify: `tests/test_i18n_regressions.py`
- Remove: obsolete `src/backend/tools/spotify_desktop/` implementation files

- [x] Point tests at the module that owns each responsibility.
- [x] Preserve behavior assertions and monkeypatch the actual dependency owner.
- [x] Verify no first-party import references obsolete implementation paths.

### Task 6: Release verification

- [x] Run all Spotify tests and expect all to pass.
- [x] Run `pytest -q` and expect no regressions.
- [x] Run `python -m compileall -q start_app.py src/backend`.
- [x] Run `python -m ruff check src/backend tests --select F`.
- [x] Run frontend `node --check` release commands.
- [x] Run `git diff --check`.
- [x] Perform a real Windows Spotify Desktop playback smoke test.
- [x] Commit only intentional source, tests, and documentation.
