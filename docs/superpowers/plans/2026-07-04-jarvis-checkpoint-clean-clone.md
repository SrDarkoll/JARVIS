# JARVIS Checkpoint And Clean Clone Plan

> **For agentic workers:** Execute this plan inline with dependency-contract tests and final verification.

**Goal:** Create a reviewable rescue checkpoint and prove that a clean core installation starts without heavy optional ML dependencies.

**Architecture:** `requirements.txt` contains only the stable core runtime. Heavy integrations live in `requirements-optional.txt` and setup scripts install them only with an explicit full-mode flag.

**Tech Stack:** Git, Python 3.11, pip, pytest, PowerShell, Bash.

---

### Task 1: Protect the checkpoint

- [x] Create branch `codex/jarvis-core-rescue`.
- [x] Confirm `.env` is ignored and untracked.
- [x] Scan tracked and new files for common secret formats.
- [x] Confirm Piper `.onnx` models remain under Git LFS.

### Task 2: Split core and optional dependencies

- [x] Add installation-contract tests.
- [x] Move SpeechBrain, FAISS, sentence transformers, Telegram, Playwright, and optional OS integrations out of core requirements.
- [x] Add explicit base dependencies currently supplied only transitively.
- [x] Add `-Full` and `--full` setup options.
- [x] Document core and full installation commands.

### Task 3: Verify from a clean local clone

- [ ] Commit the reviewed checkpoint.
- [ ] Clone the rescue branch into a temporary directory.
- [ ] Create a fresh Python 3.11 virtual environment.
- [ ] Install core requirements without optional packages.
- [ ] Run focused tests and start the backend in core mode.
