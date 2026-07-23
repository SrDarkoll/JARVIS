# Reasoning-First Command Pipeline Design

## Objective

Make JARVIS reason about every user request by default without allowing the
model to perform side effects. Preserve deterministic validation, explicit
confirmation, exactly-once execution, offline degradation, and concise TTS
responses.

## Evidence

The current deterministic-first pipeline can commit to a route before Groq
sees the request. The attached runtime log demonstrates three failure classes:

- a square-root question was sent to web search instead of local calculation;
- a conversational question about JARVIS reasoning was sent to web search;
- weather requests for continent-sized regions produced a specific forecast
  instead of asking for a city or local area.

After tool execution, the current response composer concatenates tool output.
Consequently, search snippets, URLs, and markup can be sent directly to TTS.

## Modes

`JARVIS_REASONING_MODE` accepts:

- `always`: default. Build a deterministic candidate, invoke Groq with that
  candidate as advisory context, validate the returned plan, and execute it.
- `hybrid`: retain the current low-latency behavior. A deterministic plan wins;
  Groq handles only unresolved requests.
- `offline`: never invoke Groq. Execute a deterministic plan or return a
  controlled offline clarification.

When `always` cannot reach Groq, a valid deterministic candidate is used as a
degraded fallback. If no candidate exists, the existing controlled
`llm_unconfigured` or `chat_unavailable` behavior remains.

## Planning

The deterministic router remains side-effect free and produces an immutable
candidate. In `always` mode, the candidate is serialized into a trusted system
message containing its source, confidence, direct response, tools, and
arguments. Groq may:

- return a direct conversational answer;
- ask a clarification question;
- confirm the candidate tool plan;
- replace it with a more suitable valid tool plan.

If a high-confidence deterministic candidate contains an action and Groq
returns a non-question statement without a tool call, JARVIS keeps the
candidate. This prevents a model from claiming an action was completed without
actually executing it.

The planner invokes Groq once and cannot call tools. Unknown tools, duplicate
operations, mixed text/tool responses, oversized plans, and request-ID
mismatches remain invalid.

## Execution

`ToolExecutionService` remains the only side-effect boundary. Planning mode
does not change authorization, confirmation, operation signatures, or
request/step idempotency.

Blocked, failed, unavailable, and duplicate receipts are composed
deterministically. A model must never rewrite a security denial or explicit
confirmation request.

## Response Synthesis

After at least one successful tool receipt, a plain Groq model without bound
tools receives:

- the original request and language;
- tool names and bounded result text;
- the deterministic fallback response;
- instructions to remain factual, concise, and suitable for TTS.

The synthesizer cannot execute tools. It must not invent success, links, facts,
or actions. Its output is bounded and cleaned. If synthesis is unavailable,
empty, too long, or raises an exception, JARVIS returns the deterministic
fallback response.

## Deterministic Corrections

The router gains explicit square-root parsing for Spanish and English.
Ambiguous weather regions such as continents and broad natural regions produce
a clarification plan instead of querying one arbitrary forecast.

These corrections improve `hybrid` and `offline` modes and provide better
candidates to `always`.

## Observability

Planning events record the selected mode, whether Groq validated a candidate,
and whether deterministic fallback was used. Logs store exception class names,
not provider error text or secrets.

## Testing

Tests must prove:

- `always` invokes Groq even when a deterministic candidate exists;
- `hybrid` preserves deterministic fast paths;
- `offline` never invokes Groq;
- a Groq outage falls back only when a candidate exists;
- actions still execute exactly once;
- successful search results are synthesized once by a tool-free model;
- blocked/failed receipts bypass synthesis;
- synthesis failure returns the deterministic response;
- square-root, reasoning-conversation, and ambiguous-weather regressions are
  covered;
- chat, streaming, and voice continue sharing the same orchestrator.

## Compatibility

Core mode keeps working without Groq through deterministic fallback.
`JARVIS_REASONING_MODE=hybrid` restores the current latency profile. No new
dependency is required.
