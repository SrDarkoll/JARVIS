# Unified Readable Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a rotating, redacted, human-readable `log.txt` containing JARVIS conversations, actions, frontend events, runtime diagnostics, and backend console output.

**Architecture:** A dedicated `core.unified_log` module owns one file-only rotating logger and an optional stdout/stderr tee. Existing conversation, tool, frontend, runtime, and observability boundaries send categorized records to it; the tee captures legacy diagnostics that do not yet use structured logging.

**Tech Stack:** Python 3.11+, standard-library `logging`, `RotatingFileHandler`, Quart, pytest, vanilla JavaScript.

---

### Task 1: Writer, redaction, and rotation

**Files:**
- Create: `src/backend/core/unified_log.py`
- Modify: `src/backend/core/jarvis_config.py`
- Create: `tests/test_unified_log.py`

- [x] Add failing tests for timestamped categories, escaped multiline content,
  environment-secret redaction, explicit credential redaction, and rotation.
- [x] Add bounded integer configuration for a 5 MiB file and three backups.
- [x] Implement a lazy file-only logger, structured context formatting, and a
  reset hook used only by tests.
- [x] Run `pytest tests/test_unified_log.py -q` and expect all tests to pass.

### Task 2: Runtime and console integration

**Files:**
- Modify: `src/backend/core/runtime_logger.py`
- Modify: `src/backend/jarvis_backend.py`
- Test: `tests/test_unified_log.py`

- [x] Add tests proving runtime levels are written once and stdout/stderr are
  mirrored while still reaching their original streams.
- [x] Add an idempotent line-buffering tee with runtime-log prefix suppression
  to prevent duplicate records.
- [x] Install capture before feature imports when not running tests.
- [x] Run the focused logging tests.

### Task 3: Conversation, tool, frontend, and event integration

**Files:**
- Modify: `src/backend/core/brain/processor.py`
- Modify: `src/backend/core/brain/tool_manager.py`
- Modify: `src/backend/core/jarvis_observability.py`
- Modify: `src/backend/api/api_routes.py`
- Modify: `tests/test_frontend_terminal_log.py`
- Create: `tests/test_unified_log_integration.py`

- [x] Test exact `USUARIO(profile)` and `JARVIS(profile)` conversation entries.
- [x] Test tool START/END records for successful and blocked outcomes.
- [x] Test frontend events use the `FRONTEND` category without duplicate file
  writes.
- [x] Mirror JSONL observability events as readable `EVENT` lines.
- [x] Run focused conversation, tool, frontend, and observability tests.

### Task 4: Distribution documentation and verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `tests/test_installation_contract.py`

- [x] Document path, privacy implications, rotation, and environment controls.
- [x] Assert the public configuration contract and ignored log path.
- [x] Run `pytest -q`, compilation, Ruff F checks, frontend Node syntax checks,
  `pip check`, and `git diff --check`.
- [x] Start the backend, submit representative frontend/conversation/tool
  events, and verify timestamped categorized lines in `log.txt`.
- [x] Commit only intentional source, tests, and documentation.
