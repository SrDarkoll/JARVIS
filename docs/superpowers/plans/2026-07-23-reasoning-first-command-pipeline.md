# Reasoning-First Command Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Groq validate every request by default, keep exactly-once tool execution, and synthesize successful tool results into concise responses.

**Architecture:** Add an explicit reasoning-mode policy to `CommandOrchestrator`. The deterministic router supplies an advisory candidate, Groq produces the final validated plan in `always` mode, and a plain tool-free model synthesizes successful receipts after execution. Deterministic fallback remains available when Groq is unavailable.

**Tech Stack:** Python 3.11/3.12, Quart, LangChain messages, Groq OpenAI-compatible chat, pytest.

---

### Task 1: Reasoning Mode Contract

**Files:**
- Create: `src/backend/core/command_pipeline/reasoning.py`
- Modify: `src/backend/core/jarvis_config.py`
- Modify: `.env.example`
- Test: `tests/test_reasoning_policy.py`

- [ ] Add failing parameterized tests for `always`, `hybrid`, `offline`, empty
  values, and invalid values.
- [ ] Run `pytest tests/test_reasoning_policy.py -q` and confirm failure.
- [ ] Implement `ReasoningMode` and `resolve_reasoning_mode`, defaulting to
  `always`.
- [ ] Export `REASONING_MODE` from `jarvis_config.py` and document
  `JARVIS_REASONING_MODE`.
- [ ] Run the focused tests and commit the contract.

### Task 2: Mode-Aware Planning

**Files:**
- Modify: `src/backend/core/command_pipeline/orchestrator.py`
- Modify: `src/backend/core/command_pipeline/groq_planner.py`
- Modify: `src/backend/core/brain/processor.py`
- Test: `tests/test_command_orchestrator.py`
- Test: `tests/test_groq_planner.py`

- [ ] Add failing tests proving that `always` invokes Groq with a deterministic
  candidate, `hybrid` skips Groq for resolved commands, and `offline` never
  invokes Groq.
- [ ] Add failing tests for deterministic fallback on
  `LLMUnavailableError`/`LLMServiceError` and for rejection when no fallback
  candidate exists.
- [ ] Add a trusted candidate system message to `GroqPlanner`.
- [ ] Implement mode arbitration in `CommandOrchestrator` without changing the
  execution boundary.
- [ ] Pass the configured mode from the runtime processor.
- [ ] Run focused planner/orchestrator tests and commit.

### Task 3: Tool-Free Response Synthesis

**Files:**
- Create: `src/backend/core/command_pipeline/synthesis.py`
- Modify: `src/backend/core/command_pipeline/responses.py`
- Modify: `src/backend/core/brain/processor.py`
- Test: `tests/test_response_synthesis.py`
- Test: `tests/test_command_orchestrator.py`

- [ ] Add failing tests for successful receipt synthesis, bounded input,
  blocked/failed bypass, and synthesis failure fallback.
- [ ] Implement a synthesizer that receives a plain model and never binds
  tools.
- [ ] Integrate an optional synthesizer into `ResponseComposer`.
- [ ] Add the runtime model adapter with sanitized observability and
  deterministic fallback.
- [ ] Run focused response tests and commit.

### Task 4: Runtime Log Regressions

**Files:**
- Modify: `src/backend/core/brain/router.py`
- Modify: `tests/test_router.py`
- Modify: `tests/test_command_pipeline_e2e.py`
- Modify: `tests/test_i18n_regressions.py`

- [ ] Add failing tests for Spanish/English square-root questions.
- [ ] Add failing tests requiring clarification for Africa, South America, the
  Amazon, and equivalent broad English regions.
- [ ] Add an `always`-mode test where Groq answers a reasoning-capability
  question directly rather than accepting the web-search candidate.
- [ ] Implement bounded Decimal square-root parsing and broad-region
  clarification.
- [ ] Verify repaired Unicode reaches planning tests without mojibake.
- [ ] Run focused regressions and commit.

### Task 5: Documentation And Release Verification

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`

- [ ] Document reasoning modes, latency trade-offs, fallback, and synthesis.
- [ ] Run:

```powershell
pytest -q
python -m compileall -q start_app.py src\backend
python -m ruff check src\backend tests --select F
python -m pip check
node --check src\frontend\static\js\main.js
node --check src\frontend\static\js\modules\api.js
git diff --check
```

- [ ] Confirm `.env` and runtime artifacts remain ignored.
- [ ] Update the verified test baseline and commit the release-ready result.
