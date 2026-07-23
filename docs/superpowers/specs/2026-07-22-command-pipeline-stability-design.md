# JARVIS Core Command Pipeline Stability Design

## Status

Approved for implementation planning on 2026-07-22.

## Purpose

JARVIS already has useful capabilities. The immediate problem is reliability:
the same user command can be classified and executed through several overlapping
paths, while request-specific context is partly copied into shared mutable
state.

This design freezes new feature work and makes the following path predictable:

1. Listen.
2. Understand.
3. Select a tool.
4. Execute it exactly once.
5. Respond clearly.

The work is incremental. Existing tools and verified behavior are preserved
behind compatibility adapters while their callers move to a single command
pipeline.

## Scope

### In scope

- One command pipeline shared by text chat, streaming chat, and voice.
- Pure planning from deterministic rules and Groq.
- One centralized tool execution service.
- Exactly-once execution within one accepted command.
- Explicit request context instead of ambient profile state.
- Incremental reduction of shared mutable memory state.
- Uniform capability states.
- A fixed end-to-end matrix for the 20 most important commands.
- A consistent Windows setup, launch, shutdown, and diagnostic experience.
- Clean-clone validation on supported Python versions.
- Core mode as the default distribution.

### Out of scope

- New end-user features.
- A replacement UI.
- Distributed queues, external brokers, or multi-machine execution.
- General autonomous agents.
- Making RAG, voice biometrics, plugins, briefing, or Telegram core
  dependencies.
- Rewriting every legacy tool before the new pipeline can ship.
- Exactly-once guarantees across separate process restarts or separate user
  commands.

## Current Problems

### Multiple execution paths

`core/brain/processor.py` can currently reach tool execution through:

- preflight handlers;
- the hybrid router;
- Spotify follow-up and music shortcuts;
- the Groq tool loop;
- parallel Groq tool calls;
- strict web retry;
- compound-command recursion.

`core/brain/router.py` both classifies input and executes tools. This makes it
impossible to inspect the complete plan before side effects begin.

### Shared request state

Profile history and extracted facts are copied into process-wide
`chat_history` and `DATOS_CURIOSOS` values. The active profile uses a
`ContextVar`, but a legacy global alias is updated as well. Concurrent requests
can therefore observe state that belongs to another request or profile.

### Unclear capability health

The status API mixes feature flags, configuration, dependency availability, and
runtime health. For example, running on Windows does not prove that Spotify
Desktop is installed or controllable.

### Incomplete installation evidence

The repository has good static installation contracts, but it does not yet
prove the full setup and launch path from a clean Windows clone. The launcher
can also wait for its entire timeout when the child backend has already exited,
and it accepts any HTTP 200 response from `/api/status` as JARVIS.

## Design Principles

1. **Plan before side effects.** No resolver may execute a tool.
2. **One execution boundary.** Every tool call passes through the same service.
3. **Request ownership.** Profile, channel, language, and command identity are
   explicit inputs.
4. **Verified claims.** JARVIS reports success only when the execution receipt
   supports it.
5. **Deterministic first.** Known commands use local rules. Groq handles
   ambiguity and general conversation.
6. **Core stays small.** Optional integrations must fail closed and remain
   outside startup-critical imports.
7. **Migration compatibility.** Existing public functions remain as thin
   facades until all callers move.
8. **Tests define behavior.** Characterization and end-to-end tests precede
   behavioral changes.

## Architecture

The new pipeline is a synchronous application service with an event-producing
adapter for streaming:

```text
audio or text
    |
    v
CommandRequest
    |
    v
TranscriptionCoordinator (voice only)
    |
    v
CommandOrchestrator
    |
    +--> DeterministicResolver
    |        |
    |        +--> resolved ActionPlan
    |
    +--> GroqPlanner (only when unresolved or conversational)
             |
             +--> ActionPlan or ConversationPlan
    |
    v
PlanValidator
    |
    v
ToolExecutionService
    |
    v
ExecutionReceipt
    |
    v
ResponseComposer
    |
    v
CommandResponse
```

Chat, streaming chat, and voice call the same `CommandOrchestrator`. Streaming
observes pipeline events; it does not implement a second decision path.

## Core Data Contracts

### CommandRequest

An immutable request envelope:

- `request_id`: UUID generated at the API or voice boundary.
- `text`: normalized non-empty user text.
- `profile_id`: normalized profile identity.
- `channel`: `chat`, `stream`, or `voice`.
- `language`: resolved language code.
- `received_at`: timezone-aware timestamp.
- `metadata`: bounded, non-secret transport metadata.

The request identifier follows the command through logs, plans, executions, and
the final response.

### ActionPlan

An immutable description of intended work:

- `request_id`;
- `source`: `deterministic` or `groq`;
- `steps`: ordered `ActionStep` objects;
- `response_mode`: direct, tool summary, clarification, or conversation;
- `requires_follow_up`;
- `confidence`.

Each `ActionStep` contains:

- `step_id`: stable within the request;
- `tool_name`;
- validated JSON-compatible arguments;
- dependency step identifiers;
- execution policy: sequential or parallel-safe;
- risk classification inherited from the existing tool policy.

An empty action plan is valid for conversation. A plan cannot mix an
unstructured assistant answer with undeclared side effects.

### ExecutionReceipt

The executor returns a structured receipt instead of an arbitrary string:

- `request_id`;
- `step_id`;
- `tool_name`;
- `status`: succeeded, blocked, unavailable, failed, or duplicate;
- `result`;
- `user_message`;
- `started_at` and `finished_at`;
- `verified`;
- sanitized diagnostic code.

Legacy string-returning tools are adapted at the execution boundary. Existing
error-pattern detection remains temporarily available only inside that adapter.

### CommandResponse

The final application result contains:

- final user-facing text;
- `should_listen`;
- overall outcome;
- capability state when relevant;
- execution receipts;
- safe streaming events.

## Interpretation And Arbitration

### DeterministicResolver

The existing high-value rules move out of the executing router and return plans
or direct responses. These include:

- time, date, arithmetic, and identity;
- weather and sports;
- reminders and volume;
- Spotify playback and media controls;
- application and browser opening;
- explicit web search;
- compound commands;
- pending Spotify selection.

The resolver is pure for a given request plus an explicit read-only context
snapshot.

### GroqPlanner

Groq is used only when deterministic resolution returns `UNRESOLVED`, or when
the request is classified as conversation. Tool-capable output must be parsed
into an `ActionPlan` and validated before execution.

Groq cannot invoke tools directly. Invalid tool names, malformed arguments,
excessive steps, or policy violations produce a controlled clarification or
failure response.

### Arbitration rule

There is one winner:

1. A complete deterministic plan wins.
2. A deterministic clarification wins and stops processing.
3. An unresolved request goes to Groq once.
4. Groq returns either conversation text or a validated plan.
5. No successful deterministic result is sent to Groq for a second opinion.

Strict web behavior becomes a planning rule. It may amend an unexecuted plan,
but it may not execute a second tool after finalization.

## Exactly-Once Execution

`ToolExecutionService` is the only component allowed to invoke a registered
tool.

The idempotency key is:

```text
request_id + step_id + canonical(tool_name, arguments)
```

For each accepted key, the service stores an in-memory execution record with
`pending`, `running`, or terminal state. Concurrent attempts for the same key
share the first result. A completed key is not invoked again.

The guarantee is scoped to one backend process and one accepted command. This
prevents router/LLM/streaming duplication without pretending to provide a
distributed transaction across restarts.

Before invocation the service:

1. validates the tool and argument schema;
2. evaluates contextual anti-hallucination rules;
3. evaluates profile authorization and explicit confirmation;
4. records the idempotency key as running;
5. invokes the tool once;
6. verifies and normalizes the result;
7. records a terminal receipt;
8. emits sanitized observability and unified-log events.

Automatic healing may retry only when a tool-specific adapter declares the
operation safe to retry. Side-effecting tools are not retried implicitly.

## Explicit Services And State

### RequestContext

Request identity and profile information are passed explicitly. The current
`ContextVar` remains as a temporary adapter for legacy tools, but new pipeline
code must not read the legacy `_active_profile_id`.

### ConversationMemoryService

This service owns:

- history by profile;
- extracted facts by profile;
- message counters by profile;
- atomic snapshots and updates.

Pipeline code never copies profile data into process-wide compatibility lists.
Legacy `chat_history` and `DATOS_CURIOSOS` remain read-through facades only
during migration.

### ToolRegistryService

The registry exposes immutable snapshots of available tools. Plugin reload, when
enabled outside core mode, swaps a complete snapshot under one lock. An
execution uses the snapshot captured for its plan.

### ApplicationServices

The current mutable singleton evolves into a typed composition root assembled
at backend startup. Compatibility properties remain temporarily, but new
services are constructor dependencies of the orchestrator.

## Capability States

Every user-visible capability has exactly one state:

- `available`: configured, dependencies present, and operational probe passed;
- `unconfigured`: a required key, model, path, application, or user action is
  missing;
- `degraded`: usable through a documented fallback or with reduced behavior;
- `failed`: configured, but initialization or the latest operational probe
  failed;
- `disabled`: intentionally excluded by runtime feature configuration.

Each capability report includes a stable code, a safe user action, and a last
check timestamp. It never includes API keys, OAuth tokens, raw provider errors,
or local secret-bearing URLs.

The status API reports at least:

- LLM;
- speech-to-text;
- TTS;
- Spotify API;
- Spotify Desktop;
- weather/search providers;
- optional RAG, biometrics, plugins, briefing, and Telegram.

Feature flags and capability states remain separate concepts.

## Core And Optional Boundaries

Core mode remains the default and includes:

- Quart backend and browser/desktop UI;
- Groq chat when configured;
- deterministic local commands;
- adaptive voice transcription;
- Piper TTS;
- profile memory required for conversation;
- weather, web search, and base tools;
- Spotify with controlled degradation.

Core startup must not import or initialize:

- SpeechBrain biometrics;
- RAG embeddings or vector stores;
- plugins;
- Telegram;
- briefing jobs;
- optional monitoring jobs.

Enabling one optional feature must not force all optional dependencies to load.

## Error Handling And Responses

Errors are mapped once at the pipeline boundary:

- missing configuration -> `unconfigured`;
- missing optional dependency or fallback use -> `degraded`;
- security or confirmation denial -> blocked receipt and follow-up;
- provider timeout -> failed receipt with retry-safe message;
- malformed plan -> controlled clarification;
- internal exception -> generic failure plus sanitized diagnostic code.

`ResponseComposer` uses receipts and never infers success from the requested
action alone. Compound commands report each step separately when results differ.

Examples:

- Success: `Reproduciendo "No te apartes de mí" en Spotify.`
- Unconfigured: `Spotify API no está configurado; usaré el control de escritorio.`
- Degraded: `No pude verificar la reproducción, pero Spotify recibió el comando.`
- Blocked: `Necesito confirmación explícita antes de apagar el equipo.`
- Partial: `Consulté el clima, pero no pude completar la búsqueda web.`

## End-To-End Command Matrix

The fixed matrix contains 20 representative commands:

1. Greeting without tools.
2. General conversational question through Groq.
3. Current time.
4. Current date.
5. Arithmetic.
6. Weather using the configured default city.
7. Weather for an explicit city.
8. Explicit web search.
9. Dynamic/current-information query.
10. Create a reminder.
11. Set absolute volume.
12. Adjust relative volume.
13. Play an unambiguous Spotify track.
14. Resolve an ambiguous Spotify result with `la primera`.
15. Pause Spotify.
16. Resume Spotify.
17. Open an application.
18. Reject or request confirmation for a dangerous PC command.
19. Execute a two-step compound command.
20. Submit voice audio, transcribe it, execute the command, and synthesize the
    response.

Each case asserts:

- normalized input or transcript;
- selected resolver;
- selected tool and arguments;
- `invocation_count == 1` per action step;
- expected receipt status;
- clear final response;
- expected memory or follow-up state;
- no second execution from Groq, router, streaming, or retry logic.

External network, microphone, desktop, and provider behavior use deterministic
adapters in the automated suite. A smaller Windows acceptance suite exercises
real WebView2, microphone permission, Spotify Desktop, and TTS.

## Windows Distribution

### Setup

`setup.ps1` resolves the repository root from `$PSScriptRoot` and changes all
project paths to that root. Fast preflight checks run before dependency
installation:

- supported non-Store Python 3.11 or 3.12;
- Git and Git LFS;
- complete LFS model files;
- FFmpeg;
- WebView2 Runtime;
- eSpeak only when the selected Piper setup requires it.

Missing optional components produce actionable warnings. Missing core
components stop immediately with an installation command or documented link.

### Launcher

`start_app.py`:

- validates a JARVIS-specific status identity and protocol version;
- detects early backend process exit instead of waiting 90 seconds;
- reports the relevant log path;
- terminates and waits for the child process;
- avoids `os._exit()` during normal shutdown;
- cleans only storage it created;
- reports capability degradation before opening the UI when useful.

### Runtime data

Logs, memory, OAuth caches, downloaded STT models, and temporary audio are
runtime data, not source files. Paths are rooted in a configurable application
data directory with a backward-compatible migration from current locations.

## Clean-Clone Validation

Validation uses a new checkout with no copied `.env`, virtual environment,
caches, profiles, or logs.

The automated Windows matrix covers Python 3.11 and 3.12:

1. clone and pull Git LFS;
2. verify required model content;
3. run `setup.ps1 -Dev`;
4. run `pip check`;
5. run the core test suite;
6. start without keys and verify controlled `unconfigured` states;
7. validate `/api/status`, setup status, local commands, and TTS preflight;
8. stop and restart cleanly;
9. run provider-backed paths with fake adapters;
10. run a separately marked real-Windows acceptance checklist.

The repository is not declared clean-clone ready until this matrix passes from
an actual fresh directory.

## Migration Sequence

1. Add characterization tests and the 20-command matrix with fake adapters.
2. Add immutable request, plan, receipt, response, and capability models.
3. Add the idempotent `ToolExecutionService` behind the existing tool facade.
4. Convert deterministic router branches from execution to planning.
5. Convert Groq tool calls to validated plans.
6. Replace chat and streaming branching with one orchestrator.
7. route voice transcripts through the same orchestrator.
8. Introduce explicit memory and tool-registry services.
9. Remove obsolete duplicated execution paths and compatibility state.
10. Add capability registry and expose it in status/setup APIs.
11. Harden Windows setup, launcher, shutdown, and runtime paths.
12. Run the full regression, Windows acceptance, and clean-clone matrix.

Every migration step must leave the existing regression suite passing. Legacy
facades are removed only after repository search confirms that no production
caller depends on them.

## Acceptance Criteria

The stabilization is complete when:

- chat, streaming, and voice use one orchestrator;
- router and Groq cannot execute tools directly;
- every tool invocation produces one structured receipt;
- duplicate execution attempts for one step return the original receipt;
- the 20-command matrix proves one invocation per expected step;
- concurrent profile tests show no history or fact contamination;
- all core capabilities report one uniform state;
- optional features stay disabled and unimported in default core mode;
- Windows setup works from any current directory;
- launcher detects wrong services and early backend failure;
- normal shutdown leaves no owned backend process;
- the existing regression suite and new tests pass;
- clean-clone validation passes on Windows with Python 3.11 and 3.12;
- documentation accurately distinguishes automated tests from real-device
  acceptance results.

## Rollback Strategy

The existing `procesar_mensaje` and streaming entry points remain as facades
during migration. Each stage can route back to the legacy implementation behind
a temporary development-only feature flag until the corresponding
characterization and end-to-end tests pass. The flag is removed before declaring
the stabilization complete so production behavior has one path.
