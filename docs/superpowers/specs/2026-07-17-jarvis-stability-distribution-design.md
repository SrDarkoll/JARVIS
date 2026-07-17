# JARVIS Stability and Distribution Design

**Date:** 2026-07-17
**Status:** Approved

## Objective

Make the existing JARVIS core mode predictable, installable, and supportable for
people other than the original developer. The work prioritizes runtime
stability, clean-clone installation, controlled degradation, and evidence-based
replacement of obsolete code without adding unrelated features.

## Current Baseline

The rescue branch already separates lean core dependencies from heavyweight
optional integrations and documents Windows, macOS, and Linux setup. A previous
clean-clone run passed 251 tests with one core-mode skip and successfully
exercised Groq chat and Piper TTS.

The current full local environment exposes issues that the lean environment did
not:

- APScheduler is instantiated in core mode whenever the optional package happens
  to be installed.
- A desktop-session test reuses a fixed directory and can inherit stale Windows
  permissions.
- Desktop session persistence has no controlled fallback when its preferred
  directory cannot be written.
- A local prompt edit reintroduced MiniMax references after the project selected
  Groq as its sole LLM provider.
- The full test process imports heavyweight optional packages and is much slower
  than the clean core environment.

## Selected Approach

Use a focused stabilization pass rather than a rewrite or an attempt to make
every optional integration production-ready at once. Existing public behavior
will be preserved unless it is demonstrably broken, unsafe, unsupported, or
obsolete.

The project will keep two explicit runtime profiles:

1. `core`: the supported default for new users.
2. `full`: an opt-in profile for hardware-specific and heavyweight integrations.

Installing an optional package must not silently enable its feature. Runtime
feature flags, not package presence, are the source of truth.

## Runtime Architecture

### Feature lifecycle

`RuntimeFeatures` remains the central configuration contract. Every optional
service must satisfy both conditions before initialization:

1. Its feature flag is enabled.
2. Its runtime dependency is importable and usable.

Monitoring will receive an explicit runtime feature decision. In core mode its
APScheduler object will not be created even if APScheduler is installed. Full
mode can enable the scheduler, and a missing scheduler dependency will produce a
controlled diagnostic instead of an import or startup failure.

The status API and setup diagnostics must report requested state separately from
availability when that distinction helps users understand a disabled feature.

### Desktop session persistence

Desktop session loading will follow this flow:

1. Resolve the configured or platform-default persistent directory.
2. Validate that the storage directory can be created and written.
3. Read valid prior JSON when available; malformed JSON is ignored safely.
4. Write session metadata atomically where possible.
5. If persistence fails, continue with a writable temporary session and mark
   `persist_permissions` as false.

Expected filesystem errors will produce concise warnings without stack traces or
secret-bearing paths in API responses. Unexpected programming errors remain
visible to developers instead of being swallowed broadly.

### Provider configuration

Groq remains the only LLM provider in active configuration and documentation.
MiniMax references will be removed from prompts, tests, setup files, and docs.
The application must start without `GROQ_API_KEY`, report that chat is
unconfigured, and keep non-LLM diagnostics available. With a valid key, the same
core build must support chat without source edits.

No API key will have a source-code default. Existing local `.env` values remain
untracked and will not be printed during validation.

## Obsolescence Policy

An item is considered obsolete only when at least one of these is true:

- The runtime emits a deprecation or removal warning attributable to project
  code.
- Official documentation marks the API, model, package, or configuration as
  deprecated, removed, or unsupported.
- The supported Python versions are incompatible with the dependency or API.
- Static and runtime tracing show that local code has no consumers.
- A duplicate implementation is fully covered by the retained implementation.

For each obsolete item, choose one action in this order:

1. Update to the supported API while preserving behavior.
2. Add a small compatibility adapter when callers still depend on the old
   interface.
3. Remove the item only after tests prove that no supported behavior is lost.

Package versions will not be upgraded merely because a newer release exists.
Security fixes, compatibility, active deprecations, and installation failures are
valid reasons. Research about current models and APIs will use official provider
documentation or primary project release notes.

The Python 3.11 and 3.12 support window remains in place during this pass. A
Python 3.13 migration is a separate project because the current audio stack
still depends on APIs removed or deprecated there.

## Test Isolation and Validation

Tests will not reuse personal runtime directories. Test caches, desktop sessions,
databases, and model state will live under a unique writable test root and be
cleaned without touching user data.

Validation will cover two environments:

### Core development environment

- Full pytest suite in core mode.
- Python compilation.
- Focused Ruff/Pyflakes checks for changed Python files.
- JavaScript syntax checks.
- `pip check` and a dependency vulnerability audit against shipped core
  requirements.
- Backend startup without optional packages.
- Controlled behavior without an API key.

### Clean clone

- Git LFS model verification.
- Setup using the documented development command.
- Full core regression suite.
- Backend status smoke test.
- Groq chat smoke test only when a key is locally available.
- Piper TTS response validation.
- Confirmation that `.env`, caches, logs, session data, and test artifacts remain
  untracked.

Optional full-mode integrations will receive import and graceful-degradation
tests. Hardware-dependent behavior such as microphone quality, Spotify device
availability, and biometric accuracy will be reported separately rather than
claimed as universally verified.

## Documentation and Distribution

README, `.env.example`, setup scripts, dependency manifests, and `AGENTS.md` will
be updated only when implementation or validation changes their contract.
Instructions must distinguish required core prerequisites from optional full-mode
ones and include actionable errors for missing Git LFS assets, FFmpeg, eSpeak,
WebView2, and provider credentials.

The repository will remain a technical beta unless the clean-clone acceptance
criteria pass. This stabilization does not create a signed installer or promise
consumer-grade support for every platform.

## Non-Goals

- No visual redesign.
- No new assistant feature family.
- No broad rename of all Spanish identifiers during the stability pass.
- No rewrite of the brain/router architecture.
- No automatic publication or remote push as part of local stabilization.
- No claim that optional hardware integrations work without the relevant
  hardware and credentials.

## Acceptance Criteria

The work is complete when all of the following are true:

- Core behavior is independent of whether optional packages are installed.
- The current scheduler regression has a failing test before the fix and passes
  afterward.
- Desktop session persistence cannot prevent startup solely because its preferred
  storage path is unavailable.
- Tests use isolated writable runtime paths and pass repeatedly.
- Active MiniMax references are absent and Groq configuration contains no
  embedded secret.
- Evidence-backed obsolete project APIs are updated, adapted, or removed without
  losing covered behavior.
- Core dependency checks and security audit pass.
- A clean clone installs, starts, serves status, and produces valid TTS output.
- Chat succeeds with a locally supplied Groq key and fails clearly without one.
- Git status after validation contains only intentional source and documentation
  changes.

