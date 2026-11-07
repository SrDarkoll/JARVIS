# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

`AGENTS.md` (imported above) is the authoritative source for setup, run/verification commands, security rules, env-var defaults, and release workflow. Keep it in sync rather than duplicating its content here. This file adds the cross-file architecture that is not obvious from any single module.

## Quick commands

```powershell
pytest -q                                              # full suite (repo-local runtime via tests/conftest.py)
pytest tests\test_command_orchestrator.py -q           # one file
pytest tests\test_smoke.py::test_dynamic_queries_force_web_tools -q   # one test
pytest -m "not slow" -q                                # skip slow-marked tests
python -m ruff check src/backend                       # CI lint gate
python -m compileall -q start_app.py src\backend
python start_app.py                                    # desktop launcher (re-execs into ./venv)
python src/backend/jarvis_backend.py                   # backend only, http://localhost:5002
```

## Import roots

Backend modules import each other as top-level packages (`from core.command_pipeline...`, `from services...`, `from tools...`), not `src.backend...`. `src/backend` is put on `sys.path` by `jarvis_backend.py` and by `tests/conftest.py`. Follow the same style in new code and tests.

## Architecture

**Entry point:** `src/backend/jarvis_backend.py` builds the Quart app, registers the blueprints from `api/` (chat, voice, tts, status, security, language, sync, device sync), and installs the `before_request` and WebSocket gates. Those gates handle API token, loopback plus local `Host` header trust, Origin/Referer/`Sec-Fetch-Site` checks, remote-proxy detection, browser-profile cookie, security headers, and rate limits. The trust predicates themselves are pure functions in `core/security/request_gate.py` (unit tests in `tests/test_request_gate.py`). The final allow/deny policy is `protected_http_request_allowed` in `core/device_sync/auth.py`. End-to-end gate regressions live in `tests/test_smoke.py` and `tests/test_remote_access_hardening.py`.

**Single command pipeline (`core/command_pipeline/`):** Text chat, streaming chat, and voice all funnel into one `CommandOrchestrator` (`orchestrator.py`). `core/brain/processor.py` constructs it lazily (`_get_command_orchestrator`). Flow:
1. `CommandRequest` (`models.py`) is built from the user input and profile id.
2. `DeterministicPlanner` (`deterministic.py`) tries a local rule-based plan. If that fails, an LLM planner (`groq_planner.py`) runs, gated by `ReasoningMode` (`reasoning.py`: OFFLINE / HYBRID / ...). Planners return only immutable `ActionPlan`s and never call tools.
3. The orchestrator validates the plan (request id match, no mixed direct-response plus steps, unique step ids, dependency ordering).
4. `ToolExecutionService` (`execution.py`) is the only side-effect boundary. It enforces tool policy, confirmation, and request/step idempotency, and returns `ExecutionReceipt`s.
5. `ResponseComposer` (`responses.py`, `synthesis.py`, `truthfulness.py`) writes the user-facing reply from the receipts. Replies must not claim effects that no receipt confirms.
6. History is persisted through `services/memory_manager`.

**Tool layer:** `tools/*.py` are LangChain-style tool adapters. Several are thin facades over the real implementations in `modules/` (`spotify/`, `youtube/`, `computer_use/`, `memory/`, `proactive/`). Registration and catalog live in `core/command_pipeline/tool_registry.py` and `core/brain/tool_manager.py`. Policy (critical/confirmation, system tools off by default) lives in `core/security/tool_policy.py`.

**Shared runtime state:** `core/service_container.py` is a singleton `services` holding injected callables (observability, security guard, LLM handles, `tool_execution`, reminders). It is wired in `jarvis_backend.py` and by `init_brain()` (via `core_tools.inject_dependencies` → `tools/__init__._inject_dependencies_impl`). Its attribute set is frozen, so assigning an undeclared name raises. `missing_required()` is checked at startup. `core/jarvis_state.py` holds the active profile and caches. Ruff ignores some rules for this injected-state pattern (see `pyproject.toml`).

**LLM providers:** `core/llm_providers.py` plus `core/brain/llm_engine.py`. Gemini is primary and Groq is the fallback (order reversed by `JARVIS_LLM_PROVIDER=groq`). Missing keys must yield `llm_unconfigured` errors.

**Voice (`voice/`):** There are two paths. (a) Gemini Live full-duplex: `live_session.py`, `live_gemini.py`, `live_transport.py`, `live_hybrid.py`. Live tool calls go through a voice-tool allowlist into `live_tool_execution.py`, which uses the same `ToolExecutionService`. (b) Standard: `capture.py`, `transcription.py` (STT), the orchestrator, then Piper TTS (`engines/tts_engine.py`). Audio conversion goes through FFmpeg only (`utils/audio_conversion.py`).

**Runtime paths:** `core/runtime_paths.py` resolves per-OS data dirs (logs, DBs, FAISS index). Tests redirect these to repo-local paths. `core/capabilities.py` and `JARVIS_CORE_MODE` decide which optional subsystems load (RAG, vision, plugins, Telegram, voice ID).

**Frontend:** vanilla ES modules in `src/frontend/static/js/` (`main.js` plus `modules/`), templates in `src/frontend/templates/`, served by Quart and hosted in a WebView2 shell. There is no build step, so `node --check` is the only JS validation. The mobile PWA pairs through `device-sync.js` / `mobile-pairing.js` and backend `core/device_sync/` + `api/device_sync_routes.py`.

## Conventions worth knowing

- Many identifiers and user-facing strings are Spanish (`invocar_tool`, `reparar_unicode`, `buscar_en_internet`). Keep tool names stable because planners and tests match on them. New internal identifiers go in English (see "Naming Notes" in AGENTS.md). i18n lives in `utils/jarvis_i18n.py`.
- Pyright CI runs with only `requirements.txt` installed. Files importing optional packages (`pywinauto`, etc.) stay in the `pyproject.toml` baseline even if they pass locally with the full venv.
- `core/jarvis_brain.py` is a lazy `__getattr__` facade. Add new re-exports to both `__getattr__` and the `TYPE_CHECKING` block plus `__all__`, otherwise callers lose static types.
- Live desktop integration scripts (no mocks) are in `scripts/test_live_*.py`. They are not part of `pytest`.
