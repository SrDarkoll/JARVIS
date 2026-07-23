# JARVIS Command Pipeline Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make voice and text commands follow one deterministic pipeline that selects tools, executes each operation once, reports capability health consistently, and starts reliably from a clean Windows clone.

**Architecture:** Add immutable command contracts, a pure deterministic planner, a Groq planner that can only return plans, and a single idempotent execution service. Route chat, streaming, and voice through one orchestrator, then replace shared request state with explicit services and expose typed capability reports. Harden Windows runtime paths, setup, launcher lifecycle, and clean-clone verification after the command path is stable.

**Tech Stack:** Python 3.11/3.12, dataclasses, Quart, LangChain message/tool adapters, pytest, PowerShell, GitHub Actions, existing JARVIS observability and security services.

---

## Delivery Order

The specification contains three related workstreams. Implement them in this
order because each one creates the test boundary required by the next:

1. Command pipeline and exactly-once execution.
2. Explicit memory/tool state and capability reporting.
3. Windows distribution and clean-clone validation.

Do not start a later workstream while an earlier workstream has failing tests.
Do not add new user-facing capabilities during this plan.

### Task 1: Add Immutable Command Contracts

**Files:**
- Create: `src/backend/core/command_pipeline/__init__.py`
- Create: `src/backend/core/command_pipeline/models.py`
- Test: `tests/test_command_pipeline_models.py`

- [ ] **Step 1: Write the failing model tests**

```python
from dataclasses import FrozenInstanceError

import pytest

from core.command_pipeline.models import (
    ActionPlan,
    ActionStep,
    CommandRequest,
    CommandResponse,
    ExecutionReceipt,
    PlanSource,
    ReceiptStatus,
)


def test_command_request_normalizes_profile_and_rejects_empty_text():
    request = CommandRequest.create(
        text="  clima en Monterrey  ",
        profile_id=" ADMIN ",
        channel="chat",
        language="es",
        request_id="request-1",
    )

    assert request.text == "clima en Monterrey"
    assert request.profile_id == "admin"
    assert request.request_id == "request-1"
    with pytest.raises(ValueError, match="command_text_required"):
        CommandRequest.create(text=" ", profile_id="admin", channel="chat")


def test_command_models_are_immutable_and_json_compatible():
    step = ActionStep(step_id="step-1", tool_name="obtener_clima", arguments={"ciudad": "Monterrey"})
    plan = ActionPlan(request_id="request-1", source=PlanSource.DETERMINISTIC, steps=(step,))
    receipt = ExecutionReceipt(
        request_id="request-1",
        step_id="step-1",
        tool_name="obtener_clima",
        status=ReceiptStatus.SUCCEEDED,
        result="24 C",
        user_message="24 C",
        verified=True,
        diagnostic_code="",
    )
    response = CommandResponse(
        request_id="request-1",
        text="24 C",
        should_listen=False,
        outcome="succeeded",
        receipts=(receipt,),
    )

    assert plan.steps == (step,)
    assert response.to_dict()["receipts"][0]["status"] == "succeeded"
    with pytest.raises(FrozenInstanceError):
        step.tool_name = "buscar_en_internet"
```

- [ ] **Step 2: Run the model tests and verify they fail**

Run:

```powershell
pytest tests\test_command_pipeline_models.py -q
```

Expected: collection fails because `core.command_pipeline.models` does not
exist.

- [ ] **Step 3: Implement the command contracts**

Create `src/backend/core/command_pipeline/models.py` with these public types and
validation rules:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from core import jarvis_state


class PlanSource(StrEnum):
    DETERMINISTIC = "deterministic"
    GROQ = "groq"


class ReceiptStatus(StrEnum):
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    DUPLICATE = "duplicate"


def _frozen_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True, slots=True)
class CommandRequest:
    request_id: str
    text: str
    profile_id: str
    channel: str
    language: str
    received_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def create(
        cls,
        *,
        text: str,
        profile_id: str,
        channel: str,
        language: str = "en",
        request_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "CommandRequest":
        normalized_text = str(text or "").strip()
        if not normalized_text:
            raise ValueError("command_text_required")
        return cls(
            request_id=str(request_id or uuid4()),
            text=normalized_text,
            profile_id=jarvis_state.normalize_profile_id(profile_id),
            channel=str(channel or "unknown").strip().lower(),
            language=str(language or "en").strip().lower(),
            received_at=datetime.now(UTC),
            metadata=_frozen_mapping(metadata),
        )


@dataclass(frozen=True, slots=True)
class ActionStep:
    step_id: str
    tool_name: str
    arguments: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    depends_on: tuple[str, ...] = ()
    parallel_safe: bool = False

    def __post_init__(self) -> None:
        if not self.step_id or not self.tool_name:
            raise ValueError("invalid_action_step")
        object.__setattr__(self, "arguments", _frozen_mapping(self.arguments))


@dataclass(frozen=True, slots=True)
class ActionPlan:
    request_id: str
    source: PlanSource
    steps: tuple[ActionStep, ...] = ()
    direct_response: str = ""
    requires_follow_up: bool = False
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    request_id: str
    step_id: str
    tool_name: str
    status: ReceiptStatus
    result: Any
    user_message: str
    verified: bool
    diagnostic_code: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["started_at"] = self.started_at.isoformat()
        payload["finished_at"] = self.finished_at.isoformat()
        return payload


@dataclass(frozen=True, slots=True)
class CommandResponse:
    request_id: str
    text: str
    should_listen: bool
    outcome: str
    receipts: tuple[ExecutionReceipt, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "text": self.text,
            "should_listen": self.should_listen,
            "outcome": self.outcome,
            "receipts": [receipt.to_dict() for receipt in self.receipts],
        }
```

Export the public types from `src/backend/core/command_pipeline/__init__.py`.

- [ ] **Step 4: Run the model tests**

Run:

```powershell
pytest tests\test_command_pipeline_models.py -q
```

Expected: all model tests pass.

- [ ] **Step 5: Commit the contracts**

```powershell
git add src/backend/core/command_pipeline tests/test_command_pipeline_models.py
git commit -m "feat: add command pipeline contracts"
```

### Task 2: Build The Idempotent Tool Execution Service

**Files:**
- Create: `src/backend/core/command_pipeline/execution.py`
- Test: `tests/test_tool_execution_service.py`

- [ ] **Step 1: Write failing exactly-once and concurrency tests**

```python
import threading
import time

from core.command_pipeline.execution import ToolExecutionService
from core.command_pipeline.models import ActionStep, CommandRequest, ReceiptStatus


def _request(request_id="request-1"):
    return CommandRequest.create(
        text="pon musica",
        profile_id="admin",
        channel="chat",
        request_id=request_id,
    )


def test_executor_replays_completed_operation_without_invoking_twice():
    calls = []
    service = ToolExecutionService(lambda _request, _step: calls.append("called") or "playing")
    step = ActionStep(step_id="step-1", tool_name="reproducir_en_spotify", arguments={"cancion": "Monster"})

    first = service.execute(_request(), step)
    second = service.execute(_request(), step)

    assert calls == ["called"]
    assert first.status is ReceiptStatus.SUCCEEDED
    assert second.status is ReceiptStatus.DUPLICATE
    assert second.result == "playing"


def test_executor_coalesces_concurrent_attempts():
    calls = []
    barrier = threading.Barrier(3)

    def invoke(_request, _step):
        calls.append("called")
        time.sleep(0.05)
        return "done"

    service = ToolExecutionService(invoke)
    step = ActionStep(step_id="step-1", tool_name="obtener_clima", arguments={"ciudad": "Matamoros"})
    receipts = []

    def worker():
        barrier.wait()
        receipts.append(service.execute(_request(), step))

    threads = [threading.Thread(target=worker) for _index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert calls == ["called"]
    assert sorted(receipt.status.value for receipt in receipts) == ["duplicate", "succeeded"]
```

Add tests for blocked, unavailable, failed, and repeated canonical operations
with different `step_id` values. The latter must be rejected by
`validate_plan_operations()` unless the tool is explicitly listed as
repeatable.

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
pytest tests\test_tool_execution_service.py -q
```

Expected: import failure for `core.command_pipeline.execution`.

- [ ] **Step 3: Implement execution and plan-operation validation**

Implement a lock-protected `Future` registry. Canonical arguments must use
sorted compact JSON and must not include raw secrets in logs:

```python
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from core.command_pipeline.models import (
    ActionStep,
    CommandRequest,
    ExecutionReceipt,
    ReceiptStatus,
)


def operation_signature(step: ActionStep) -> str:
    arguments = json.dumps(dict(step.arguments), sort_keys=True, separators=(",", ":"), default=str)
    return f"{step.tool_name}:{arguments}"


def validate_plan_operations(
    steps: Iterable[ActionStep],
    *,
    repeatable_tools: frozenset[str] = frozenset(),
) -> None:
    seen: set[str] = set()
    for step in steps:
        signature = operation_signature(step)
        if signature in seen and step.tool_name not in repeatable_tools:
            raise ValueError("duplicate_plan_operation")
        seen.add(signature)


class ToolExecutionService:
    def __init__(
        self,
        invoke_once: Callable[[CommandRequest, ActionStep], Any],
        *,
        max_records: int = 1024,
    ) -> None:
        self._invoke_once = invoke_once
        self._max_records = max(32, int(max_records))
        self._lock = threading.RLock()
        self._records: OrderedDict[str, Future[ExecutionReceipt]] = OrderedDict()

    def _key(self, request: CommandRequest, step: ActionStep) -> str:
        return f"{request.request_id}:{step.step_id}:{operation_signature(step)}"

    def execute(self, request: CommandRequest, step: ActionStep) -> ExecutionReceipt:
        key = self._key(request, step)
        with self._lock:
            future = self._records.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._records[key] = future
                while len(self._records) > self._max_records:
                    oldest_key, oldest = next(iter(self._records.items()))
                    if not oldest.done():
                        break
                    self._records.pop(oldest_key)

        if not owner:
            original = future.result(timeout=30)
            return replace(original, status=ReceiptStatus.DUPLICATE)

        started_at = datetime.now(UTC)
        try:
            value = self._invoke_once(request, step)
            receipt = ExecutionReceipt(
                request_id=request.request_id,
                step_id=step.step_id,
                tool_name=step.tool_name,
                status=ReceiptStatus.SUCCEEDED,
                result=value,
                user_message=str(value or ""),
                verified=True,
                diagnostic_code="",
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        except PermissionError:
            blocked_message = (
                "Necesito confirmacion explicita antes de realizar esa accion."
                if request.language.startswith("es")
                else "I need explicit confirmation before performing that action."
            )
            receipt = ExecutionReceipt(
                request_id=request.request_id,
                step_id=step.step_id,
                tool_name=step.tool_name,
                status=ReceiptStatus.BLOCKED,
                result=None,
                user_message=blocked_message,
                verified=False,
                diagnostic_code="tool_blocked",
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        except LookupError:
            receipt = ExecutionReceipt(
                request_id=request.request_id,
                step_id=step.step_id,
                tool_name=step.tool_name,
                status=ReceiptStatus.UNAVAILABLE,
                result=None,
                user_message="The requested tool is unavailable.",
                verified=False,
                diagnostic_code="tool_unavailable",
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        except Exception:
            receipt = ExecutionReceipt(
                request_id=request.request_id,
                step_id=step.step_id,
                tool_name=step.tool_name,
                status=ReceiptStatus.FAILED,
                result=None,
                user_message="The requested action failed.",
                verified=False,
                diagnostic_code="tool_execution_failed",
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        future.set_result(receipt)
        return receipt
```

Preserve a successful original receipt even if the duplicate caller receives a
receipt whose status is `duplicate`.

- [ ] **Step 4: Run execution tests**

Run:

```powershell
pytest tests\test_tool_execution_service.py -q
```

Expected: all tests pass and both concurrency threads terminate.

- [ ] **Step 5: Commit the execution service**

```powershell
git add src/backend/core/command_pipeline/execution.py tests/test_tool_execution_service.py
git commit -m "feat: execute command tools exactly once"
```

### Task 3: Put Existing Tool Security Behind The Execution Boundary

**Files:**
- Modify: `src/backend/core/brain/tool_manager.py:226-354`
- Modify: `src/backend/core/service_container.py:9-74`
- Test: `tests/test_tool_manager.py`
- Test: `tests/test_security_manager.py`

- [ ] **Step 1: Add failing compatibility tests**

Add tests proving that:

- `_invocar_tool_entry()` accepts `request_id` and `step_id`;
- two calls with the same identifiers invoke the LangChain tool once;
- security authorization and explicit confirmation still run;
- error responses do not contain the raw exception text;
- legacy callers without identifiers still execute independently.

```python
def test_entry_deduplicates_same_request_and_step(monkeypatch):
    from core.brain import brain_state, tool_manager

    calls = []

    class Tool:
        def invoke(self, args):
            calls.append(dict(args))
            return "ok"

    monkeypatch.setitem(brain_state.tool_map, "test_tool", Tool())
    monkeypatch.setattr(tool_manager, "_tool_permitida_por_contexto", lambda *_args: True)
    monkeypatch.setattr(tool_manager.security_manager, "_security_guard", lambda *_args, **_kwargs: (True, ""))

    first = tool_manager._invocar_tool_entry(
        "test_tool", {"value": 1}, "run it", "test", "admin", "request-1", "step-1"
    )
    second = tool_manager._invocar_tool_entry(
        "test_tool", {"value": 1}, "run it", "test", "admin", "request-1", "step-1"
    )

    assert calls == [{"value": 1}]
    assert str(first) == "ok"
    assert str(second) == "ok"
```

- [ ] **Step 2: Run targeted tests and verify the new test fails**

Run:

```powershell
pytest tests\test_tool_manager.py tests\test_security_manager.py -q
```

Expected: the new signature or invocation-count assertion fails.

- [ ] **Step 3: Extract the current single invocation and adapt it**

Keep the existing context guard, security guard, authorization, observability,
and result normalization in `_invoke_tool_once()`. Make
`_invocar_tool_entry()` and `_invocar_tool()` build explicit contracts and call
one `ToolExecutionService`:

```python
def _invocar_tool_entry(
    tc_name: str,
    args: dict,
    user_input: str,
    source: str = "unknown",
    profile_id: str | None = None,
    request_id: str | None = None,
    step_id: str | None = None,
):
    request = CommandRequest.create(
        text=user_input or tc_name,
        profile_id=profile_id or jarvis_state.get_active_profile_id(),
        channel=source,
        request_id=request_id,
        language=get_current_language(),
    )
    step = ActionStep(
        step_id=step_id or f"legacy-{uuid4()}",
        tool_name=tc_name,
        arguments=args,
    )
    receipt = _tool_execution_service.execute(request, step)
    return receipt.result if receipt.result is not None else receipt.user_message
```

The callback passed to `ToolExecutionService` must call the extracted
`_invoke_tool_once(request, step)` and translate existing controlled
`ACCESS_DENIED`/unavailable/error outcomes into typed internal exceptions.
Do not log raw arguments outside the existing redaction path.

Register the execution service on `ServiceContainer` with one declared
attribute:

```python
self.tool_execution: ToolExecutionService | None = None
```

Remove the inconsistent runtime-only `invoke_tool`/`invocar_tool` naming by
keeping `services.invocar_tool` as the temporary compatibility callable and
using `services.tool_execution` in new code.

- [ ] **Step 4: Run security and tool regressions**

Run:

```powershell
pytest tests\test_tool_manager.py tests\test_security_manager.py tests\test_unified_log_integration.py -q
```

Expected: all tests pass and the tool log still contains one start/end pair for
the original invocation.

- [ ] **Step 5: Commit the execution integration**

```powershell
git add src/backend/core/brain/tool_manager.py src/backend/core/service_container.py tests/test_tool_manager.py tests/test_security_manager.py
git commit -m "refactor: centralize guarded tool execution"
```

### Task 4: Convert The Deterministic Router Into A Pure Planner

**Files:**
- Create: `src/backend/core/command_pipeline/deterministic.py`
- Modify: `src/backend/core/brain/router.py:219-760`
- Modify: `tests/test_router.py`
- Modify: `tests/test_compound_router.py`
- Modify: `tests/test_i18n_regressions.py`

- [ ] **Step 1: Replace execution assertions with plan assertions**

Write tests against `DeterministicPlanner.plan()` and install an executor that
fails if called while planning:

```python
def test_weather_planning_has_no_side_effects(monkeypatch):
    from core.command_pipeline.deterministic import DeterministicPlanner
    from core.command_pipeline.models import CommandRequest, PlanSource

    monkeypatch.setattr(
        "core.brain.tool_manager._invocar_tool_entry",
        lambda *_args, **_kwargs: pytest.fail("planner must not execute tools"),
    )
    request = CommandRequest.create(
        text="clima en Monterrey",
        profile_id="admin",
        channel="chat",
        language="es",
        request_id="weather-1",
    )

    plan = DeterministicPlanner().plan(request)

    assert plan.source is PlanSource.DETERMINISTIC
    assert [(step.tool_name, dict(step.arguments)) for step in plan.steps] == [
        ("obtener_clima", {"ciudad": "Monterrey"})
    ]
```

Add direct-response tests for time/date/arithmetic, clarification tests for an
incomplete weather request, Spotify follow-up tests, dangerous-action planning,
and compound-plan ordering.

- [ ] **Step 2: Run router tests and verify they fail**

Run:

```powershell
pytest tests\test_router.py tests\test_compound_router.py tests\test_i18n_regressions.py -q
```

Expected: planner import fails.

- [ ] **Step 3: Implement planner helpers and convert every tool branch**

Create a small planner facade:

```python
from core.brain import router
from core.command_pipeline.models import ActionPlan, CommandRequest, PlanSource


class DeterministicPlanner:
    def plan(self, request: CommandRequest) -> ActionPlan | None:
        return router.plan_hybrid(request)
```

In `router.py`, add plan constructors and change direct tool branches to return
plans:

```python
def _tool_plan(
    request: CommandRequest,
    tool_name: str,
    arguments: dict,
    *,
    step_id: str = "step-1",
    requires_follow_up: bool = False,
) -> ActionPlan:
    return ActionPlan(
        request_id=request.request_id,
        source=PlanSource.DETERMINISTIC,
        steps=(ActionStep(step_id=step_id, tool_name=tool_name, arguments=arguments),),
        requires_follow_up=requires_follow_up,
    )


def _direct_plan(request: CommandRequest, response: str, *, should_listen: bool = False) -> ActionPlan:
    return ActionPlan(
        request_id=request.request_id,
        source=PlanSource.DETERMINISTIC,
        direct_response=response,
        requires_follow_up=should_listen,
    )
```

`plan_hybrid(request, allow_compound=True)` must not import
`core.brain.processor` or call `_invocar_tool_wrapper`. Convert every existing
branch using this pattern:

```python
return _tool_plan(request, "obtener_clima", {"ciudad": ciudad})
```

Compound commands must produce one ordered `ActionPlan` with stable step IDs:

```python
steps = tuple(
    replace(step, step_id=f"step-{index}")
    for index, step in enumerate(collected_steps, start=1)
)
```

If any segment is unresolved, return a direct clarification without executing
the already planned segments. This deliberately replaces the current
execute-then-report-partial behavior.

Keep `_router_hibrido()` only as a temporary test compatibility function that
returns direct text for direct plans and raises
`RuntimeError("legacy_router_execution_removed")` for tool plans. Remove it in
Task 16.

- [ ] **Step 4: Run routing regressions**

Run:

```powershell
pytest tests\test_router.py tests\test_compound_router.py tests\test_i18n_regressions.py tests\test_spotify_followup.py -q
```

Expected: all planner tests pass and no test monkeypatches
`_invocar_tool_wrapper`.

- [ ] **Step 5: Commit the pure planner**

```powershell
git add src/backend/core/command_pipeline/deterministic.py src/backend/core/brain/router.py tests/test_router.py tests/test_compound_router.py tests/test_i18n_regressions.py tests/test_spotify_followup.py
git commit -m "refactor: make deterministic routing side effect free"
```

### Task 5: Add A Groq Planner That Cannot Execute Tools

**Files:**
- Create: `src/backend/core/command_pipeline/groq_planner.py`
- Modify: `src/backend/core/brain/llm_engine.py:305-387`
- Test: `tests/test_groq_planner.py`
- Test: `tests/test_llm_engine_fallback.py`

- [ ] **Step 1: Write failing Groq planner tests**

Use fake LangChain-style responses:

```python
from types import SimpleNamespace

import pytest

from core.command_pipeline.groq_planner import GroqPlanner
from core.command_pipeline.models import CommandRequest, PlanSource


class FakeModel:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        return self.response


def test_groq_tool_calls_become_a_plan_without_execution():
    response = SimpleNamespace(
        content="",
        tool_calls=[
            {
                "id": "call-1",
                "name": "buscar_en_internet",
                "args": {"query": "latest Python security news"},
            }
        ],
    )
    planner = GroqPlanner(FakeModel(response), allowed_tools={"buscar_en_internet"})
    request = CommandRequest.create(
        text="latest Python security news",
        profile_id="admin",
        channel="chat",
        request_id="groq-1",
    )

    plan = planner.plan(request, [])

    assert plan.source is PlanSource.GROQ
    assert plan.steps[0].tool_name == "buscar_en_internet"
    assert plan.steps[0].step_id == "call-1"
```

Add tests that reject unknown tools, non-dict arguments, more than five steps,
duplicate canonical operations, and mixed content plus undeclared side effects.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
pytest tests\test_groq_planner.py -q
```

Expected: planner import fails.

- [ ] **Step 3: Implement one-round Groq planning**

```python
class GroqPlanner:
    def __init__(self, model, *, allowed_tools: set[str], max_steps: int = 5) -> None:
        self._model = model
        self._allowed_tools = frozenset(allowed_tools)
        self._max_steps = max_steps

    def plan(self, request: CommandRequest, messages: list) -> ActionPlan:
        response = self._model.invoke(messages)
        tool_calls = tuple(getattr(response, "tool_calls", None) or ())
        content = brain_utils._limpiar_thinking(
            str(getattr(response, "content", "") or "")
        )
        if not tool_calls:
            return ActionPlan(
                request_id=request.request_id,
                source=PlanSource.GROQ,
                direct_response=content,
            )
        if content.strip():
            raise ValueError("mixed_groq_plan")
        if len(tool_calls) > self._max_steps:
            raise ValueError("groq_plan_too_large")
        steps = []
        for index, tool_call in enumerate(tool_calls, start=1):
            name = str(tool_call.get("name") or "").strip()
            arguments = tool_call.get("args") or {}
            if name not in self._allowed_tools or not isinstance(arguments, dict):
                raise ValueError("invalid_groq_tool_call")
            steps.append(
                ActionStep(
                    step_id=str(tool_call.get("id") or f"groq-{index}"),
                    tool_name=name,
                    arguments=arguments,
                )
            )
        validate_plan_operations(steps)
        return ActionPlan(
            request_id=request.request_id,
            source=PlanSource.GROQ,
            steps=tuple(steps),
        )
```

Use `brain_state.llm_with_tools` only to plan. Use `brain_state.llm` without
tools later for response composition. Remove the Spotify direct-execution
shortcut and the three-iteration execution loop from `processor.py` only after
the orchestrator is connected in Task 6.

Wrap `_rebuild_tooling()` in `brain_state.PLUGIN_LOCK` and atomically replace
`tools_list`, `tool_map`, and `llm_with_tools`.

- [ ] **Step 4: Run Groq and fallback regressions**

Run:

```powershell
pytest tests\test_groq_planner.py tests\test_llm_engine_fallback.py tests\test_smoke.py::test_processor_direct_failure_raises_sanitized_service_error -q
```

Expected: all tests pass with no network access.

- [ ] **Step 5: Commit the Groq planner**

```powershell
git add src/backend/core/command_pipeline/groq_planner.py src/backend/core/brain/llm_engine.py tests/test_groq_planner.py tests/test_llm_engine_fallback.py
git commit -m "refactor: make Groq produce validated action plans"
```

### Task 6: Implement The Single Command Orchestrator

**Files:**
- Create: `src/backend/core/command_pipeline/orchestrator.py`
- Create: `src/backend/core/command_pipeline/responses.py`
- Modify: `src/backend/core/brain/processor.py:394-656`
- Modify: `src/backend/core/jarvis_brain.py`
- Test: `tests/test_command_orchestrator.py`

- [ ] **Step 1: Write failing arbitration and response tests**

Cover these cases:

- deterministic direct response does not call Groq;
- deterministic tool plan executes and does not call Groq;
- unresolved command calls Groq once;
- a blocked receipt asks for follow-up;
- duplicate receipts do not produce a second success claim;
- streaming and non-streaming return the same final response.

```python
from core.command_pipeline.models import (
    ActionPlan,
    ActionStep,
    CommandRequest,
    ExecutionReceipt,
    PlanSource,
    ReceiptStatus,
)
from core.command_pipeline.orchestrator import CommandOrchestrator
from core.command_pipeline.responses import ResponseComposer


def request(request_id: str) -> CommandRequest:
    return CommandRequest.create(
        text="clima en Matamoros",
        profile_id="admin",
        channel="chat",
        language="es",
        request_id=request_id,
    )


class FakeDeterministicPlanner:
    def __init__(self, plan):
        self.plan_value = plan

    def plan(self, _request):
        return self.plan_value


class FailingPlanner:
    def __init__(self, message):
        self.message = message

    def plan(self, _request, _history):
        raise AssertionError(self.message)


class RecordingExecutor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, command_request, step):
        self.calls.append((command_request.request_id, step.step_id, step.tool_name))
        return ExecutionReceipt(
            request_id=command_request.request_id,
            step_id=step.step_id,
            tool_name=step.tool_name,
            status=ReceiptStatus.SUCCEEDED,
            result=self.result,
            user_message=self.result,
            verified=True,
            diagnostic_code="",
        )


class FakeHistory:
    def __init__(self):
        self.interactions = []

    def get_history(self, _profile_id):
        return []

    def append_interaction(self, command_request, response):
        self.interactions.append((command_request, response))


def test_deterministic_plan_wins_without_calling_groq():
    deterministic = FakeDeterministicPlanner(
        ActionPlan(
            request_id="request-1",
            source=PlanSource.DETERMINISTIC,
            steps=(ActionStep("step-1", "obtener_clima", {"ciudad": "Matamoros"}),),
        )
    )
    groq = FailingPlanner("Groq must not run")
    executor = RecordingExecutor("Soleado, 28 C")
    orchestrator = CommandOrchestrator(
        deterministic=deterministic,
        groq=groq,
        executor=executor,
        responses=ResponseComposer(),
        history=FakeHistory(),
    )

    response = orchestrator.process(request("request-1"))

    assert executor.calls == [("request-1", "step-1", "obtener_clima")]
    assert response.text == "Soleado, 28 C"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
pytest tests\test_command_orchestrator.py -q
```

Expected: orchestrator import fails.

- [ ] **Step 3: Implement the orchestrator and response composer**

The orchestrator owns arbitration and emits events through one optional
callback:

```python
class CommandOrchestrator:
    def __init__(self, *, deterministic, groq, executor, responses, history) -> None:
        self._deterministic = deterministic
        self._groq = groq
        self._executor = executor
        self._responses = responses
        self._history = history

    def process(self, request: CommandRequest, emit=None) -> CommandResponse:
        send = emit or (lambda _event: None)
        send({"type": "status", "text": "understanding", "request_id": request.request_id})
        history = self._history.get_history(request.profile_id)
        plan = self._deterministic.plan(request)
        if plan is None:
            send({"type": "status", "text": "planning", "request_id": request.request_id})
            plan = self._groq.plan(request, history)
        if plan.request_id != request.request_id:
            raise ValueError("plan_request_mismatch")
        validate_plan_operations(plan.steps)
        receipts = tuple(self._executor.execute(request, step) for step in plan.steps)
        response = self._responses.compose(request, plan, receipts)
        self._history.append_interaction(request, response)
        send({"type": "done", **response.to_dict()})
        return response
```

`ResponseComposer` returns `plan.direct_response` when no tools are present. For
tool plans it uses verified receipt messages, reports partial failure by step,
sets `should_listen` for blocked/clarification outcomes, and does not call a
tool-capable model.

```python
class ResponseComposer:
    def compose(
        self,
        request: CommandRequest,
        plan: ActionPlan,
        receipts: tuple[ExecutionReceipt, ...],
    ) -> CommandResponse:
        if plan.direct_response and not receipts:
            return CommandResponse(
                request_id=request.request_id,
                text=plan.direct_response,
                should_listen=plan.requires_follow_up or plan.direct_response.rstrip().endswith("?"),
                outcome="succeeded",
            )

        messages = [
            receipt.user_message.strip()
            for receipt in receipts
            if receipt.user_message.strip()
        ]
        failed = any(
            receipt.status in {
                ReceiptStatus.BLOCKED,
                ReceiptStatus.UNAVAILABLE,
                ReceiptStatus.FAILED,
            }
            for receipt in receipts
        )
        blocked = any(receipt.status is ReceiptStatus.BLOCKED for receipt in receipts)
        if not messages:
            messages = ["I could not complete the requested action."]
        return CommandResponse(
            request_id=request.request_id,
            text="\n".join(messages),
            should_listen=plan.requires_follow_up or blocked,
            outcome="partial" if failed and len(receipts) > 1 else ("failed" if failed else "succeeded"),
            receipts=receipts,
        )
```

Replace the body of `procesar_mensaje()` with request construction and
`orchestrator.process()`. Replace `stream_procesar_mensaje_events()` with the
same call plus an in-memory event callback. Remove:

- the second dynamic-router pass;
- the Spotify LLM shortcut;
- the three-round tool execution loop;
- strict-web execution from `_finalize_reply`;
- duplicate preflight execution branches already moved into the planner.

Keep the two public facade names and tuple return shape.

For rollback during Tasks 6-15 only, move the old implementation behind this
development-only switch:

```python
def _legacy_pipeline_enabled() -> bool:
    return os.getenv("JARVIS_DEV_LEGACY_COMMAND_PIPELINE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def procesar_mensaje(user_input, profile_id=DEFAULT_PROFILE_ID, *, count_inbound=True):
    if _legacy_pipeline_enabled():
        return _procesar_mensaje_legacy(
            user_input,
            profile_id=profile_id,
            count_inbound=count_inbound,
        )
    request = CommandRequest.create(
        text=user_input,
        profile_id=profile_id,
        channel="brain",
        language=get_current_language(),
    )
    response = get_command_orchestrator().process(request)
    return response.text, response.should_listen
```

Do not document this switch in `.env.example`; Task 16 deletes it and the
legacy implementation before release.

- [ ] **Step 4: Run orchestrator and processor regressions**

Run:

```powershell
pytest tests\test_command_orchestrator.py tests\test_router.py tests\test_compound_router.py tests\test_smoke.py -q
```

Expected: all tests pass; unconfigured Groq still raises the existing controlled
exception only when a request actually requires Groq.

- [ ] **Step 5: Commit the orchestrator**

```powershell
git add src/backend/core/command_pipeline/orchestrator.py src/backend/core/command_pipeline/responses.py src/backend/core/brain/processor.py src/backend/core/jarvis_brain.py tests/test_command_orchestrator.py tests/test_smoke.py
git commit -m "refactor: route commands through one orchestrator"
```

### Task 7: Give Chat And Streaming One Request Boundary

**Files:**
- Modify: `src/backend/api/chat_routes.py:22-219`
- Modify: `src/backend/jarvis_backend.py`
- Test: `tests/test_chat_pipeline.py`
- Modify: `tests/test_smoke.py`

- [ ] **Step 1: Write failing API parity tests**

```python
def test_chat_and_stream_pass_explicit_request_context(client, monkeypatch):
    captured = []

    class Brain:
        def process_request(self, command_request, emit=None):
            captured.append(command_request)
            if emit:
                emit({"type": "done", "response": "ok", "should_listen": False})
            return CommandResponse(command_request.request_id, "ok", False, "succeeded")

    monkeypatch.setattr(chat_routes, "_command_service", Brain())

    chat = client.post("/api/chat", json={"message": "hola", "profile_id": "admin"})
    stream = client.post("/api/chat/stream", json={"message": "hola", "profile_id": "admin"})

    assert chat.status_code == 200
    assert stream.status_code == 200
    assert [request.channel for request in captured] == ["chat", "stream"]
    assert all(request.profile_id == "admin" for request in captured)
    assert captured[0].request_id != captured[1].request_id
```

Retain validation, separate rate-limit buckets, trusted-origin behavior, and
sanitized 503 responses.

- [ ] **Step 2: Run chat tests and verify they fail**

Run:

```powershell
pytest tests\test_chat_pipeline.py tests\test_smoke.py::test_chat_stream_endpoint_exists -q
```

Expected: `_command_service` is not configured.

- [ ] **Step 3: Inject the orchestrator and remove duplicated route logic**

Extend `ChatRoutesConfig`:

```python
class ChatRoutesConfig:
    def __init__(self, ip_last_call, ip_last_call_lock, chat_limit_seconds, command_service):
        self.ip_last_call = ip_last_call
        self.ip_last_call_lock = ip_last_call_lock
        self.chat_limit_seconds = chat_limit_seconds
        self.command_service = command_service
```

Create a shared `_command_request(data, channel)` helper and pass an explicit
`CommandRequest` to the orchestrator. For streaming, use a thread-safe
`queue.Queue` so events are yielded as the same `process()` call emits them.
There must be no call to a separate streaming processor.

```python
async def _stream_command(command_request: CommandRequest):
    events: queue.Queue[dict | object] = queue.Queue()
    sentinel = object()

    def emit(event: dict) -> None:
        events.put(dict(event))

    def run() -> None:
        try:
            _command_service.process(command_request, emit=emit)
        except LLMUnavailableError:
            events.put(
                {
                    "type": "error",
                    "code": "llm_unconfigured",
                    "message": LLM_UNCONFIGURED_MESSAGE,
                }
            )
        except Exception:
            events.put(
                {
                    "type": "error",
                    "code": "chat_unavailable",
                    "message": CHAT_UNAVAILABLE_MESSAGE,
                }
            )
        finally:
            events.put(sentinel)

    threading.Thread(target=run, name=f"jarvis-stream-{command_request.request_id}", daemon=True).start()
    while True:
        event = await asyncio.to_thread(events.get)
        if event is sentinel:
            break
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
```

- [ ] **Step 4: Run API/security regressions**

Run:

```powershell
pytest tests\test_chat_pipeline.py tests\test_smoke.py::test_critical_route_rejects_untrusted_origin_on_loopback tests\test_smoke.py::test_critical_route_allows_trusted_loopback_origin_without_token tests\test_smoke.py::test_chat_stream_rejects_get tests\test_smoke.py::test_chat_stream_rate_limits_like_chat -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the request-boundary integration**

```powershell
git add src/backend/api/chat_routes.py src/backend/jarvis_backend.py tests/test_chat_pipeline.py tests/test_smoke.py
git commit -m "refactor: unify chat and streaming command flow"
```

### Task 8: Route Voice Commands Through The Same Orchestrator

**Files:**
- Create: `src/backend/voice/session_store.py`
- Modify: `src/backend/voice/service.py:58-367`
- Modify: `src/backend/voice/service.py:826-1360`
- Modify: `src/backend/api/voice_routes.py:16-190`
- Test: `tests/test_voice_session_store.py`
- Modify: `tests/test_voice_transcription.py`
- Modify: `tests/test_smoke.py`

- [ ] **Step 1: Write failing atomic-session and voice-command tests**

```python
import struct

from core.command_pipeline.models import CommandResponse
from voice.service import VoiceService
from voice.session_store import VoiceSessionStore
from voice.transcription import TranscriptionResult


def wav_bytes() -> bytes:
    pcm = b"\x00\x00" * 800
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


class FakeTranscriptionService:
    def transcribe(self, *_args, **_kwargs):
        return TranscriptionResult(
            text="clima en Matamoros",
            source="browser",
        )


class FakeVoiceRuntime:
    biometrics_enabled = False
    voice_id_motor = None
    transcription_service = FakeTranscriptionService()
    default_profile_id = "admin"

    def resolve_profile(self, _audio_bytes, requested_profile):
        return requested_profile or self.default_profile_id


class FakeCommandService:
    def __init__(self, calls):
        self.calls = calls

    def process(self, command_request, emit=None):
        self.calls.append(command_request)
        return CommandResponse(
            request_id=command_request.request_id,
            text="Soleado",
            should_listen=False,
            outcome="succeeded",
        )


def test_voice_session_store_updates_atomically():
    store = VoiceSessionStore(clock=lambda: 100.0)
    store.start("127.0.0.1", {"stage": "awaiting_sample", "samples": 0})

    first = store.update("127.0.0.1", lambda value: {**value, "samples": value["samples"] + 1})
    second = store.pop("127.0.0.1")

    assert first["samples"] == 1
    assert second["samples"] == 1
    assert store.get("127.0.0.1") is None


def test_voice_transcript_uses_command_orchestrator_once(monkeypatch):
    calls = []
    command_service = FakeCommandService(calls)
    service = VoiceService(
        runtime=FakeVoiceRuntime(),
        command_service=command_service,
        sessions=VoiceSessionStore(),
    )

    payload, status = service.process_voice(wav_bytes(), {"transcript": "clima en Matamoros"})

    assert status == 200
    assert len(calls) == 1
    assert calls[0].channel == "voice"
    assert calls[0].text == "clima en Matamoros"
```

- [ ] **Step 2: Run voice tests and verify they fail**

Run:

```powershell
pytest tests\test_voice_session_store.py tests\test_voice_transcription.py -q
```

Expected: `VoiceSessionStore` and explicit `VoiceService` dependencies are
missing.

- [ ] **Step 3: Implement the session store and explicit voice dependencies**

`VoiceSessionStore` owns one `RLock` and exposes `start`, `get`, `update`, `pop`,
`cancel`, and `cleanup_expired`. Every returned value is a copy.

```python
class VoiceSessionStore:
    def __init__(self, *, clock=time.time, ttl_seconds: float = 300.0) -> None:
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._sessions: dict[str, dict] = {}

    def start(self, key: str, value: dict) -> dict:
        session = {**dict(value), "created_at": self._clock()}
        with self._lock:
            self._sessions[key] = session
            return dict(session)

    def get(self, key: str) -> dict | None:
        with self._lock:
            value = self._sessions.get(key)
            return dict(value) if value is not None else None

    def update(self, key: str, transform) -> dict:
        with self._lock:
            current = dict(self._sessions.get(key) or {})
            updated = dict(transform(current))
            updated.setdefault("created_at", current.get("created_at", self._clock()))
            self._sessions[key] = updated
            return dict(updated)

    def pop(self, key: str) -> dict | None:
        with self._lock:
            value = self._sessions.pop(key, None)
            return dict(value) if value is not None else None

    def cancel(self, key: str | None = None) -> bool:
        with self._lock:
            if key is None:
                changed = bool(self._sessions)
                self._sessions.clear()
                return changed
            return self._sessions.pop(key, None) is not None

    def cleanup_expired(self) -> int:
        cutoff = self._clock() - self._ttl_seconds
        with self._lock:
            expired = [
                key
                for key, value in self._sessions.items()
                if float(value.get("created_at", 0.0)) < cutoff
            ]
            for key in expired:
                self._sessions.pop(key, None)
            return len(expired)
```

Change the service constructor:

```python
class VoiceService:
    def __init__(self, *, runtime, command_service, sessions: VoiceSessionStore) -> None:
        self._runtime = runtime
        self._command_service = command_service
        self._sessions = sessions
```

Remove `_sync_runtime_globals()`. Define a frozen `VoiceRuntime` dataclass with
the current registration, biometric, transcription, authorization,
observability, normalization, and clock dependencies. Change the legacy
processor to:

```python
@dataclass(frozen=True, slots=True)
class VoiceRuntime:
    voice_id_motor: Any
    biometrics_enabled: bool
    normalize_wav: Callable[..., Any]
    bytes_are_valid_wav: Callable[..., bool]
    normalize_guest_name: Callable[[str], str]
    slugify_guest_name: Callable[[str], str]
    is_owner_alias: Callable[[str], bool]
    reserved_owner_aliases: frozenset[str]
    owner_similarity_override: Callable[..., Any]
    verify_authorization: Callable[[str], bool]
    authorize_by_biometrics: Callable[..., Any]
    revoke_authorization: Callable[[str], Any]
    activate_guest_profile: Callable[..., Any]
    whisper_model: Any
    transcription_service: Any
    obs_event: Callable[..., Any]
    obs_snapshot: Callable[..., dict]
    repair_unicode: Callable[[str], str]
    normalize_admin_treatment: Callable[[str], str]
    clock: Any


def _process_voice_sync(
    audio_bytes: bytes,
    request_data: dict,
    *,
    runtime: VoiceRuntime,
    command_service,
    sessions: VoiceSessionStore,
):
    request_processor = VoiceRequestProcessor(
        runtime=runtime,
        command_service=command_service,
        sessions=sessions,
    )
    return request_processor.process(audio_bytes, request_data)
```

Move the existing branch logic into `VoiceRequestProcessor` methods and access
dependencies through `self._runtime`, never module globals.

After transcription and profile resolution, create exactly one
`CommandRequest(channel="voice")` and call `self._command_service.process()`.
Pending guest-registration questions must use the same service rather than
`_brain.procesar_mensaje`.

Update `VoiceRoutesConfig` to inject the already constructed `VoiceService`;
remove `VoiceService(lambda: sys.modules[__name__])`.

- [ ] **Step 4: Run voice and API regressions**

Run:

```powershell
pytest tests\test_voice_session_store.py tests\test_voice_transcription.py tests\test_smoke.py -q
```

Expected: all tests pass, including registration and empty-transcription paths.

- [ ] **Step 5: Commit voice integration**

```powershell
git add src/backend/voice/session_store.py src/backend/voice/service.py src/backend/api/voice_routes.py tests/test_voice_session_store.py tests/test_voice_transcription.py tests/test_smoke.py
git commit -m "refactor: route voice through the command pipeline"
```

### Task 9: Remove Cross-Profile Memory Copies

**Files:**
- Modify: `src/backend/services/memory_manager.py`
- Modify: `src/backend/core/brain/history_manager.py`
- Modify: `src/backend/core/brain/processor.py`
- Modify: `src/backend/core/jarvis_state.py`
- Modify: `src/backend/tools/memory.py`
- Test: `tests/test_memory_concurrency.py`
- Modify: `tests/test_smoke.py`

- [ ] **Step 1: Write failing concurrent-profile tests**

```python
def test_concurrent_requests_never_copy_history_between_profiles():
    manager = MemoryManager()
    manager.set_profile_history("admin", [HumanMessage(content="admin secret")])
    manager.set_profile_history("guest_one", [HumanMessage(content="guest fact")])

    with ThreadPoolExecutor(max_workers=2) as pool:
        admin = pool.submit(manager.snapshot, "admin")
        guest = pool.submit(manager.snapshot, "guest_one")

    assert [message.content for message in admin.result().history] == ["admin secret"]
    assert [message.content for message in guest.result().history] == ["guest fact"]
    assert manager.next_message_count("admin") == 1
    assert manager.next_message_count("guest_one") == 1
```

Add a test that `processor.py` never assigns to `jarvis_state.chat_history` or
`jarvis_state.DATOS_CURIOSOS`.

- [ ] **Step 2: Run memory tests and verify they fail**

Run:

```powershell
pytest tests\test_memory_concurrency.py -q
```

Expected: `snapshot` and `next_message_count` are missing.

- [ ] **Step 3: Strengthen the existing MemoryManager**

Add this frozen snapshot before `MemoryManager`:

```python
@dataclass(frozen=True, slots=True)
class ProfileMemorySnapshot:
    profile_id: str
    history: tuple[BaseMessage, ...]
    facts: str
    message_count: int
```

Replace `MemoryManager.__init__` and add these methods to the existing class
without removing its persistence methods:

```python
def __init__(self):
    self.lock = jarvis_state.memoria_lock
    self._perfiles = jarvis_state._perfiles_memoria
    self._message_counts = jarvis_state._msg_counter_by_profile
    self._default_id = jarvis_state.DEFAULT_PROFILE_ID


def snapshot(self, profile_id: str) -> ProfileMemorySnapshot:
    pid = jarvis_state.normalize_profile_id(profile_id)
    with self.lock:
        data = self._perfiles.setdefault(pid, {"history": [], "facts": ""})
        return ProfileMemorySnapshot(
            profile_id=pid,
            history=tuple(data.get("history", ())),
            facts=str(data.get("facts", "")),
            message_count=int(self._message_counts.get(pid, 0)),
        )


def next_message_count(self, profile_id: str) -> int:
    pid = jarvis_state.normalize_profile_id(profile_id)
    with self.lock:
        value = int(self._message_counts.get(pid, 0)) + 1
        self._message_counts[pid] = value
        return value


def get_history(self, profile_id: str) -> list[BaseMessage]:
    return list(self.snapshot(profile_id).history)


def append_interaction(
    self,
    request: CommandRequest,
    response: CommandResponse,
) -> None:
    self.append_history(
        request.profile_id,
        [
            HumanMessage(content=request.text),
            AIMessage(content=response.text),
        ],
    )
```

Use the manager in `history_manager` and the orchestrator. Remove
`_cargar_contexto_perfil()` and all request-time copies into compatibility
globals. Keep legacy globals only for read-only UI compatibility until Task 16;
do not update them from request processing.

- [ ] **Step 4: Run memory and pipeline regressions**

Run:

```powershell
pytest tests\test_memory_concurrency.py tests\test_command_orchestrator.py tests\test_smoke.py -q
```

Expected: all tests pass under concurrent execution.

- [ ] **Step 5: Commit memory isolation**

```powershell
git add src/backend/services/memory_manager.py src/backend/core/brain/history_manager.py src/backend/core/brain/processor.py src/backend/core/jarvis_state.py src/backend/tools/memory.py tests/test_memory_concurrency.py tests/test_smoke.py
git commit -m "refactor: isolate conversation memory by profile"
```

### Task 10: Add Atomic Tool Registry Snapshots

**Files:**
- Create: `src/backend/core/command_pipeline/tool_registry.py`
- Modify: `src/backend/core/brain/brain_state.py`
- Modify: `src/backend/core/brain/llm_engine.py:305-387`
- Modify: `src/backend/core/brain/tool_manager.py`
- Test: `tests/test_tool_registry.py`

- [ ] **Step 1: Write failing registry-snapshot tests**

```python
class FakeTool:
    def __init__(self, name: str):
        self.name = name


def test_registry_snapshot_is_stable_during_reload():
    registry = ToolRegistryService([FakeTool("one")])
    first = registry.snapshot()

    registry.replace([FakeTool("two")])

    assert set(first.by_name) == {"one"}
    assert set(registry.snapshot().by_name) == {"two"}
```

Add a concurrent read/replace test and a duplicate tool-name rejection test.

- [ ] **Step 2: Run registry tests and verify they fail**

Run:

```powershell
pytest tests\test_tool_registry.py -q
```

Expected: registry import fails.

- [ ] **Step 3: Implement immutable snapshots**

```python
@dataclass(frozen=True, slots=True)
class ToolRegistrySnapshot:
    version: int
    tools: tuple[Any, ...]
    by_name: Mapping[str, Any]


class ToolRegistryService:
    def __init__(self, tools=()) -> None:
        self._lock = threading.RLock()
        self._snapshot = self._build(0, tools)

    def _build(self, version: int, tools) -> ToolRegistrySnapshot:
        tool_tuple = tuple(tools)
        by_name = {str(tool.name): tool for tool in tool_tuple}
        if len(by_name) != len(tool_tuple):
            raise ValueError("duplicate_tool_name")
        return ToolRegistrySnapshot(version, tool_tuple, MappingProxyType(by_name))

    def snapshot(self) -> ToolRegistrySnapshot:
        with self._lock:
            return self._snapshot

    def replace(self, tools) -> ToolRegistrySnapshot:
        with self._lock:
            self._snapshot = self._build(self._snapshot.version + 1, tools)
            return self._snapshot
```

Build `llm_with_tools` from the same snapshot and swap the model plus registry
snapshot while holding the existing plugin lock. The executor captures one
snapshot per plan.

- [ ] **Step 4: Run registry and plugin tests**

Add plugin reload assertions to `tests/test_tool_registry.py`, then run:

```powershell
pytest tests\test_tool_registry.py tests\test_tool_manager.py -q
```

Expected: registry and plugin reload tests pass using immutable snapshots.

- [ ] **Step 5: Commit registry isolation**

```powershell
git add src/backend/core/command_pipeline/tool_registry.py src/backend/core/brain/brain_state.py src/backend/core/brain/llm_engine.py src/backend/core/brain/tool_manager.py tests/test_tool_registry.py
git commit -m "refactor: publish atomic tool registry snapshots"
```

### Task 11: Implement Uniform Capability Reports

**Files:**
- Create: `src/backend/core/capabilities.py`
- Modify: `src/backend/core/service_container.py`
- Modify: `src/backend/core/setup_wizard.py`
- Modify: `src/backend/api/status_routes.py:82-137`
- Modify: `src/backend/jarvis_backend.py`
- Test: `tests/test_capabilities.py`
- Modify: `tests/test_setup_wizard.py`
- Modify: `tests/test_smoke.py`

- [ ] **Step 1: Write failing state and redaction tests**

```python
from core.capabilities import CapabilityRegistry, CapabilityReport, CapabilityState


def test_capability_registry_uses_one_state_vocabulary():
    registry = CapabilityRegistry()
    registry.set(CapabilityReport("llm", CapabilityState.UNCONFIGURED, "groq_key_missing", "Configure GROQ_API_KEY"))
    registry.set(CapabilityReport("rag", CapabilityState.DISABLED, "core_mode", "Enable JARVIS_RAG_ENABLED"))

    snapshot = registry.snapshot()

    assert snapshot["llm"]["state"] == "unconfigured"
    assert snapshot["rag"]["state"] == "disabled"


def test_capability_payload_redacts_secrets():
    report = CapabilityReport(
        "spotify_api",
        CapabilityState.FAILED,
        "oauth_failed",
        "Reconnect Spotify",
        detail="Bearer private-token",
    )

    assert "private-token" not in report.to_dict()["detail"]
```

- [ ] **Step 2: Run capability tests and verify they fail**

Run:

```powershell
pytest tests\test_capabilities.py -q
```

Expected: capability module import fails.

- [ ] **Step 3: Implement registry and status integration**

Use:

```python
class CapabilityState(StrEnum):
    AVAILABLE = "available"
    UNCONFIGURED = "unconfigured"
    DEGRADED = "degraded"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    name: str
    state: CapabilityState
    code: str
    action: str
    detail: str = ""
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "state": self.state.value,
            "code": self.code,
            "action": self.action,
            "detail": redact_text(self.detail),
            "checked_at": self.checked_at.isoformat(),
        }


class CapabilityRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._reports: dict[str, CapabilityReport] = {}

    def set(self, report: CapabilityReport) -> None:
        with self._lock:
            self._reports[report.name] = report

    def get(self, name: str) -> CapabilityReport | None:
        with self._lock:
            return self._reports.get(name)

    def snapshot(self) -> dict[str, dict[str, str]]:
        with self._lock:
            return {
                name: report.to_dict()
                for name, report in sorted(self._reports.items())
            }
```

`CapabilityReport` contains `name`, `state`, `code`, `action`, `detail`, and
`checked_at`. Sanitize `detail` through the existing unified-log redaction
helper before serialization.

Construct reports for LLM, STT, TTS, Spotify API, Spotify Desktop, weather,
search, RAG, biometrics, plugins, briefing, and Telegram. Feature flags select
`disabled`; missing configuration selects `unconfigured`; fallback operation
selects `degraded`; a failed probe selects `failed`.

Return the same reports from `/api/status` and derive `/api/setup/status` from
them. Do not use `sys.platform == "win32"` as proof that Spotify Desktop is
available.

Replace ad hoc runtime attributes with a frozen composition root:

```python
@dataclass(frozen=True, slots=True)
class ApplicationServices:
    command_orchestrator: CommandOrchestrator
    memory: MemoryManager
    tool_registry: ToolRegistryService
    tool_execution: ToolExecutionService
    capabilities: CapabilityRegistry
    transcription: TranscriptionCoordinator
    tts_engine: Any
```

Construct this once in `jarvis_backend.py`, then inject the narrow dependency
needed by each route module. Keep the old `services` singleton as a
compatibility facade until Task 16, but do not add new fields to it.

- [ ] **Step 4: Run status/setup/core regressions**

Run:

```powershell
pytest tests\test_capabilities.py tests\test_setup_wizard.py tests\test_core_mode.py tests\test_smoke.py::test_status_endpoint_reports_runtime_mode tests\test_smoke.py::test_setup_wizard_reports_core_configuration -q
```

Expected: all tests pass; optional features are `disabled` in default core mode.

- [ ] **Step 5: Commit capability reporting**

```powershell
git add src/backend/core/capabilities.py src/backend/core/service_container.py src/backend/core/setup_wizard.py src/backend/api/status_routes.py src/backend/jarvis_backend.py tests/test_capabilities.py tests/test_setup_wizard.py tests/test_smoke.py
git commit -m "feat: report uniform runtime capability states"
```

### Task 12: Move Runtime Data Out Of Source Directories

**Files:**
- Create: `src/backend/core/runtime_paths.py`
- Modify: `src/backend/core/jarvis_config.py`
- Modify: `src/backend/core/desktop_session.py`
- Modify: `src/backend/services/memory_manager.py`
- Modify: `src/backend/modules/spotify/config.py`
- Modify: `.env.example`
- Modify: `.gitignore`
- Test: `tests/test_runtime_paths.py`
- Modify: `tests/test_desktop_session.py`

- [ ] **Step 1: Write failing platform-path and migration tests**

```python
@pytest.mark.parametrize(
    ("platform", "env_name", "expected"),
    [
        ("win32", "LOCALAPPDATA", "Jarvis"),
        ("darwin", "HOME", "Library"),
        ("linux", "XDG_DATA_HOME", "jarvis"),
    ],
)
def test_runtime_home_uses_platform_application_data(monkeypatch, tmp_path, platform, env_name, expected):
    monkeypatch.setenv(env_name, str(tmp_path))
    paths = resolve_runtime_paths({}, platform_name=platform)

    assert expected.lower() in str(paths.home).lower()


def test_explicit_data_directory_wins(monkeypatch, tmp_path):
    paths = resolve_runtime_paths({"JARVIS_DATA_DIR": str(tmp_path)}, platform_name="win32")
    assert paths.home == tmp_path.resolve()
```

- [ ] **Step 2: Run path tests and verify they fail**

Run:

```powershell
pytest tests\test_runtime_paths.py -q
```

Expected: runtime-path module import fails.

- [ ] **Step 3: Implement one runtime path resolver**

Create a frozen `RuntimePaths` dataclass containing `home`, `logs`, `memory`,
`cache`, `models_cache`, `temp`, and `webview`. Resolve `JARVIS_DATA_DIR`
first, then platform defaults. Ensure directories lazily and probe
writability using the existing desktop-session technique.

```python
@dataclass(frozen=True, slots=True)
class RuntimePaths:
    home: Path
    logs: Path
    memory: Path
    cache: Path
    models_cache: Path
    temp: Path
    webview: Path


def resolve_runtime_paths(
    env: Mapping[str, str] | None = None,
    *,
    platform_name: str | None = None,
) -> RuntimePaths:
    source = os.environ if env is None else env
    platform = sys.platform if platform_name is None else platform_name
    explicit = str(source.get("JARVIS_DATA_DIR") or "").strip()
    if explicit:
        home = Path(explicit).expanduser().resolve()
    elif platform == "win32":
        root = source.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        home = Path(root) / "Jarvis"
    elif platform == "darwin":
        home = Path(source.get("HOME") or Path.home()) / "Library" / "Application Support" / "Jarvis"
    else:
        root = source.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        home = Path(root) / "jarvis"
    return RuntimePaths(
        home=home,
        logs=home / "logs",
        memory=home / "memory",
        cache=home / "cache",
        models_cache=home / "models",
        temp=home / "temp",
        webview=home / "webview",
    )
```

Replace source-tree defaults for:

- unified and observability logs;
- memory JSON;
- Spotify OAuth cache;
- temporary audio;
- WebView2 storage.

On first startup only, migrate existing files from `src/backend/logs`,
`src/backend/memoria_jarvis*.json`, and `src/backend/.cache-jarvis` when the
destination does not exist. Record migration without logging file contents.

Document:

```dotenv
JARVIS_DATA_DIR=""
```

- [ ] **Step 4: Run runtime and logging regressions**

Run:

```powershell
pytest tests\test_runtime_paths.py tests\test_desktop_session.py tests\test_unified_log.py tests\test_unified_log_integration.py tests\test_spotify_desktop_controller.py -q
```

Expected: all tests pass and no test writes runtime data outside
`scratch/pytest_runtime`.

- [ ] **Step 5: Commit runtime paths**

```powershell
git add src/backend/core/runtime_paths.py src/backend/core/jarvis_config.py src/backend/core/desktop_session.py src/backend/services/memory_manager.py src/backend/modules/spotify/config.py .env.example .gitignore tests/test_runtime_paths.py tests/test_desktop_session.py
git commit -m "refactor: centralize writable runtime paths"
```

### Task 13: Harden Windows Setup Preflight

**Files:**
- Modify: `setup.ps1`
- Modify: `tests/test_installation_contract.py`
- Modify: `README.md`

- [ ] **Step 1: Add failing setup-contract assertions**

```python
def test_windows_setup_anchors_paths_and_checks_prerequisites_before_pip():
    powershell = (ROOT / "setup.ps1").read_text(encoding="utf-8")

    assert "$repoRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path" in powershell
    assert "Set-Location -LiteralPath $repoRoot" in powershell
    assert powershell.index("git-lfs") < powershell.index("pip install --upgrade pip")
    assert powershell.index("ffmpeg") < powershell.index("pip install --upgrade pip")
    assert "Microsoft\\EdgeUpdate\\Clients" in powershell
    assert "Python 3.11 or 3.12" in powershell
    assert '[ValidateSet("3.11", "3.12")]' in powershell
```

- [ ] **Step 2: Run installation tests and verify they fail**

Run:

```powershell
pytest tests\test_installation_contract.py -q
```

Expected: new path/preflight assertions fail.

- [ ] **Step 3: Reorder and anchor setup**

At the beginning of `setup.ps1`:

```powershell
[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$Full,
    [ValidateSet("3.11", "3.12")]
    [string]$PythonVersion = ""
)

$repoRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
Set-Location -LiteralPath $repoRoot
$repoTemp = Join-Path $repoRoot "scratch\setup-temp"

$versions = if ($PythonVersion) { @($PythonVersion) } else { @("3.11", "3.12") }
$pythonCandidates = foreach ($version in $versions) {
    @{ Cmd = "py"; Args = @("-$version") }
}
$pythonCandidates += @(
    @{ Cmd = "python"; Args = @() },
    @{ Cmd = "py"; Args = @() }
)
```

Before any pip command, check:

- Git LFS command and actual ONNX content;
- FFmpeg;
- WebView2 registry keys under both native and WOW6432Node client paths;
- supported non-Store Python;
- eSpeak only as a warning when absent.

Core blockers exit immediately with exact `winget` guidance. Optional
prerequisites print warnings and the capability that will be degraded.

```powershell
function Require-Command {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$InstallCommand
    )
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: '$Name' was not found." -ForegroundColor Red
        Write-Host "Install with: $InstallCommand" -ForegroundColor Yellow
        exit 1
    }
}

Require-Command -Name "git-lfs" -InstallCommand "winget install GitHub.GitLFS"
Require-Command -Name "ffmpeg" -InstallCommand "winget install Gyan.FFmpeg"

$webViewRoots = @(
    "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
)
$webViewAvailable = $false
foreach ($root in $webViewRoots) {
    if (-not (Test-Path -LiteralPath $root)) {
        continue
    }
    $webViewAvailable = [bool](
        Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue |
            Get-ItemProperty -ErrorAction SilentlyContinue |
            Where-Object { $_.name -like "*WebView2*" } |
            Select-Object -First 1
    )
    if ($webViewAvailable) {
        break
    }
}
if (-not $webViewAvailable) {
    Write-Host "WARNING: WebView2 Runtime was not detected; the desktop shell may be degraded." -ForegroundColor Yellow
}
if (-not (Get-Command "espeak-ng" -ErrorAction SilentlyContinue) -and
    -not (Get-Command "espeak" -ErrorAction SilentlyContinue)) {
    Write-Host "WARNING: eSpeak was not detected; voices that require it will be unavailable." -ForegroundColor Yellow
}
```

- [ ] **Step 4: Run installation contracts**

Run:

```powershell
pytest tests\test_installation_contract.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit setup hardening**

```powershell
git add setup.ps1 tests/test_installation_contract.py README.md
git commit -m "fix: fail fast during Windows setup"
```

### Task 14: Harden Launcher Identity And Shutdown

**Files:**
- Modify: `start_app.py`
- Modify: `src/backend/api/status_routes.py`
- Modify: `tests/test_launcher.py`
- Modify: `tests/test_desktop_session.py`
- Modify: `tests/test_smoke.py`

- [ ] **Step 1: Write failing probe, early-exit, and shutdown tests**

```python
from subprocess import TimeoutExpired

import start_app


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return dict(self._payload)


class FakeProcess:
    def __init__(self, *, poll_values=None, wait_raises=None):
        self._poll_values = list(poll_values or [None])
        self._wait_raises = wait_raises
        self.events = []

    def poll(self):
        if len(self._poll_values) > 1:
            return self._poll_values.pop(0)
        return self._poll_values[0]

    def terminate(self):
        self.events.append("terminate")

    def wait(self, timeout=None):
        self.events.append("wait")
        if self._wait_raises is not None:
            raised = self._wait_raises
            self._wait_raises = None
            raise raised
        return 0

    def kill(self):
        self.events.append("kill")


def test_backend_probe_rejects_unrelated_http_200(monkeypatch):
    monkeypatch.setattr(
        start_app.requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(200, {"status": "online"}),
    )
    assert start_app.is_backend_running("http://127.0.0.1:5002") is False


def test_wait_for_backend_stops_when_child_exits():
    process = FakeProcess(poll_values=[None, 7])
    result = start_app.wait_for_backend(
        process,
        "http://127.0.0.1:5002",
        timeout_seconds=90,
        sleep=lambda _seconds: None,
    )
    assert result == "backend_exited:7"


def test_stop_backend_terminates_waits_and_kills_only_after_timeout():
    process = FakeProcess(wait_raises=TimeoutExpired("jarvis", 5))
    start_app.stop_backend(process)
    assert process.events == ["terminate", "wait", "kill", "wait"]
```

- [ ] **Step 2: Run launcher tests and verify they fail**

Run:

```powershell
pytest tests\test_launcher.py tests\test_desktop_session.py -q
```

Expected: identity, early-exit, and shutdown helpers are missing.

- [ ] **Step 3: Implement protocol identity and graceful lifecycle**

Add to `/api/status`:

```python
{
    "service": "jarvis",
    "protocol_version": 1,
    "status": "online",
}
```

Validate those fields in `is_backend_running()`. Implement
`wait_for_backend()` by checking `process.poll()` before every sleep and
returning a stable diagnostic string. Implement `stop_backend()` with
`terminate`, bounded `wait`, `kill`, and final `wait`.

```python
def is_backend_running(url: str) -> bool:
    try:
        response = requests.get(f"{url}/api/status", timeout=1)
        payload = response.json()
    except (requests.RequestException, ValueError, TypeError):
        return False
    return (
        response.status_code == 200
        and payload.get("service") == "jarvis"
        and payload.get("protocol_version") == 1
    )


def wait_for_backend(process, url: str, *, timeout_seconds: int = 90, sleep=time.sleep) -> str:
    for elapsed in range(timeout_seconds):
        return_code = process.poll()
        if return_code is not None:
            return f"backend_exited:{return_code}"
        if is_backend_running(url):
            return "ready"
        sleep(1)
        if (elapsed + 1) % 10 == 0:
            print(f"[SYSTEM] Waiting for core ({elapsed + 1}s)...")
    return "timeout"


def stop_backend(process) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
```

Replace normal `os._exit(0)` with setting a close event, stopping the owned
backend, cleaning owned temporary storage, and returning from `webview.start`.
Print the resolved unified log path when startup fails.

- [ ] **Step 4: Run launcher/status regressions**

Run:

```powershell
pytest tests\test_launcher.py tests\test_desktop_session.py tests\test_smoke.py::test_status_endpoint_returns_telemetry tests\test_smoke.py::test_status_endpoint_reports_runtime_mode -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit launcher hardening**

```powershell
git add start_app.py src/backend/api/status_routes.py tests/test_launcher.py tests/test_desktop_session.py tests/test_smoke.py
git commit -m "fix: verify and stop the desktop backend cleanly"
```

### Task 15: Add The Fixed 20-Command End-To-End Matrix

**Files:**
- Create: `tests/e2e/test_core_command_matrix.py`
- Create: `tests/e2e/conftest.py`
- Modify: `pytest.ini`
- Test: `tests/test_test_runtime.py`

- [ ] **Step 1: Create the failing command matrix**

Define a `CommandCase` dataclass and all 20 cases from the specification. Each
case includes input, expected resolver, expected tool calls, expected response
fragment, and follow-up behavior:

```python
@dataclass(frozen=True)
class CommandCase:
    name: str
    text: str
    expected_source: str
    expected_tools: tuple[tuple[str, dict], ...]
    response_contains: str
    should_listen: bool = False


CORE_CASES = (
    CommandCase("greeting", "hola jarvis", "deterministic", (), "operativo"),
    CommandCase("conversation", "explica que es una API", "groq", (), "interfaz"),
    CommandCase("time", "que hora es", "deterministic", (), "Son las"),
    CommandCase("date", "que fecha es", "deterministic", (), "Hoy es"),
    CommandCase("arithmetic", "cuanto es 27 por 4", "deterministic", (), "108"),
    CommandCase("default_weather", "como esta el clima", "deterministic", (("obtener_clima", {"ciudad": "Matamoros"}),), "soleado"),
    CommandCase("city_weather", "clima en Monterrey", "deterministic", (("obtener_clima", {"ciudad": "Monterrey"}),), "nublado"),
    CommandCase("web_search", "busca documentacion de Quart", "deterministic", (("buscar_en_internet", {"query": "documentacion de Quart"}),), "Quart"),
    CommandCase("dynamic_search", "cual es la noticia principal de hoy", "deterministic", (("buscar_en_internet", {"query": "cual es la noticia principal de hoy"}),), "noticia"),
    CommandCase("reminder", "recuerdame revisar el horno en 10 minutos", "deterministic", (("poner_recordatorio", {"texto": "revisar el horno", "minutos": 10}),), "programado"),
    CommandCase("absolute_volume", "pon el volumen al 40", "deterministic", (("ajustar_volumen", {"nivel": 40}),), "40"),
    CommandCase("relative_volume", "sube el volumen 10", "deterministic", (("ajustar_volumen", {"nivel": "+10"}),), "10"),
    CommandCase("spotify_play", "pon No te apartes de mi de Vicentico", "deterministic", (("reproducir_en_spotify", {"cancion": "no te apartes de mi de vicentico"}),), "Reproduciendo"),
    CommandCase("spotify_choice", "la primera", "deterministic", (("reproducir_en_spotify", {"cancion": "No Te Apartes de Mi de Vicentico"}),), "Reproduciendo"),
    CommandCase("spotify_pause", "pausa la musica", "deterministic", (("controlar_reproduccion", {"accion": "pausar"}),), "pausada"),
    CommandCase("spotify_resume", "reanuda la musica", "deterministic", (("controlar_reproduccion", {"accion": "reanudar"}),), "reanudada"),
    CommandCase("open_app", "abre la calculadora", "deterministic", (("abrir_aplicacion", {"nombre_app": "calculadora"}),), "Iniciando"),
    CommandCase("dangerous_block", "apaga la computadora", "deterministic", (("controlar_pc", {"accion": "apagar"}),), "confirmacion", True),
    CommandCase("compound", "dime el clima y luego pon Monster de Meg and Dia", "deterministic", (("obtener_clima", {"ciudad": "Matamoros"}), ("reproducir_en_spotify", {"cancion": "monster de meg and dia"})), "Monster"),
    CommandCase("voice_tts", "que hora es", "deterministic", (), "Son las"),
)
```

Fixtures provide fake Groq, clock, weather, search, reminder, Spotify, desktop,
and TTS adapters. No test uses the network, microphone, or real desktop.

Implement the reusable recorder in `tests/e2e/conftest.py`:

```python
from collections import Counter

import pytest


class FakeTool:
    def __init__(self, name, recorder, response):
        self.name = name
        self._recorder = recorder
        self._response = response

    def invoke(self, arguments):
        self._recorder.calls.append((self.name, dict(arguments)))
        self._recorder.count_by_request[self._recorder.active_request_id] += 1
        if callable(self._response):
            return self._response(dict(arguments))
        return self._response


class ToolRecorder:
    def __init__(self):
        self.calls = []
        self.count_by_request = Counter()
        self.active_request_id = ""

    def tools(self):
        responses = {
            "obtener_clima": lambda args: (
                "Nublado en Monterrey"
                if args.get("ciudad") == "Monterrey"
                else "Soleado y 28 C"
            ),
            "buscar_en_internet": "Resultado verificado de Quart y noticias",
            "poner_recordatorio": "Recordatorio programado",
            "ajustar_volumen": lambda args: f"Volumen actualizado a {args['nivel']}",
            "reproducir_en_spotify": lambda args: f"Reproduciendo {args['cancion']}",
            "controlar_reproduccion": lambda args: (
                "Musica pausada"
                if args.get("accion") == "pausar"
                else "Musica reanudada"
            ),
            "abrir_aplicacion": "Iniciando calculadora",
            "controlar_pc": "La accion requiere confirmacion",
        }
        return tuple(
            FakeTool(name, self, response)
            for name, response in responses.items()
        )


@pytest.fixture
def tool_recorder():
    return ToolRecorder()
```

Build the harness from production pipeline components:

```python
from core.command_pipeline.deterministic import DeterministicPlanner
from core.command_pipeline.execution import ToolExecutionService
from core.command_pipeline.models import ActionPlan, PlanSource
from core.command_pipeline.orchestrator import CommandOrchestrator
from core.command_pipeline.responses import ResponseComposer
from core.command_pipeline.tool_registry import ToolRegistryService
from services.memory_manager import memory_manager


class FakeGroqPlanner:
    def plan(self, request, _history):
        return ActionPlan(
            request_id=request.request_id,
            source=PlanSource.GROQ,
            direct_response="Una API es una interfaz para comunicar software.",
        )


@pytest.fixture
def core_harness(tool_recorder):
    registry = ToolRegistryService(tool_recorder.tools())

    def invoke(request, step):
        tool_recorder.active_request_id = request.request_id
        if step.tool_name == "controlar_pc":
            tool_recorder.calls.append((step.tool_name, dict(step.arguments)))
            tool_recorder.count_by_request[request.request_id] += 1
            raise PermissionError("explicit_confirmation_required")
        tool = registry.snapshot().by_name[step.tool_name]
        return tool.invoke(dict(step.arguments))

    orchestrator = CommandOrchestrator(
        deterministic=DeterministicPlanner(),
        groq=FakeGroqPlanner(),
        executor=ToolExecutionService(invoke),
        responses=ResponseComposer(),
        history=memory_manager,
    )
    return orchestrator, tool_recorder
```

The test module freezes the router clock, sets the default city to Matamoros,
and preloads the Spotify follow-up store before the selection case.

- [ ] **Step 2: Run the matrix and inspect failures**

Run:

```powershell
pytest tests\e2e\test_core_command_matrix.py -q
```

Expected: cases expose any remaining parser/response differences. Adjust the
planner or fixture contract, not the expected exactly-once count.

- [ ] **Step 3: Complete the E2E harness**

For every case assert:

```python
assert result.source == case.expected_source
assert tool_recorder.calls == list(case.expected_tools)
assert tool_recorder.count_by_request[result.request_id] == len(case.expected_tools)
assert case.response_contains.lower() in result.text.lower()
assert result.should_listen is case.should_listen
assert len({receipt.step_id for receipt in result.receipts}) == len(result.receipts)
```

The voice case feeds deterministic WAV bytes through the voice service, asserts
one transcription call, one orchestrator call, and one TTS call.

- [ ] **Step 4: Run E2E and focused regressions**

Run:

```powershell
pytest tests\e2e\test_core_command_matrix.py tests\test_command_orchestrator.py tests\test_voice_transcription.py tests\test_spotify_followup.py -q
```

Expected: 20 matrix cases and focused regressions pass.

- [ ] **Step 5: Commit the matrix**

```powershell
git add tests/e2e pytest.ini tests/test_test_runtime.py
git commit -m "test: cover twenty core commands end to end"
```

### Task 16: Remove Obsolete Execution And State Compatibility Paths

**Files:**
- Modify: `src/backend/core/brain/processor.py`
- Modify: `src/backend/core/brain/router.py`
- Modify: `src/backend/core/brain/tool_manager.py`
- Modify: `src/backend/core/jarvis_brain.py`
- Modify: `src/backend/core/jarvis_state.py`
- Modify: `src/backend/core/service_container.py`
- Modify: `src/backend/voice/service.py`
- Test: `tests/test_architecture_contract.py`

- [ ] **Step 1: Write failing architecture-contract tests**

```python
def test_production_code_has_one_tool_execution_boundary():
    root = Path(__file__).resolve().parents[1] / "src" / "backend"
    allowed = root / "core" / "brain" / "tool_manager.py"
    offenders = []
    for path in root.rglob("*.py"):
        if path == allowed:
            continue
        source = path.read_text(encoding="utf-8")
        if ".invoke(args)" in source or "._invocar_tool(" in source:
            offenders.append(path.relative_to(root).as_posix())
    assert offenders == []


def test_router_does_not_import_processor_or_tool_manager():
    source = (ROOT / "src/backend/core/brain/router.py").read_text(encoding="utf-8")
    assert "core.brain.processor" not in source
    assert "_invocar_tool_wrapper" not in source


def test_voice_service_has_no_runtime_global_sync():
    source = (ROOT / "src/backend/voice/service.py").read_text(encoding="utf-8")
    assert "_sync_runtime_globals" not in source
```

- [ ] **Step 2: Run architecture tests and verify remaining offenders**

Run:

```powershell
pytest tests\test_architecture_contract.py -q
```

Expected: failures list every remaining compatibility path.

- [ ] **Step 3: Delete the obsolete paths**

Remove:

- `_invocar_tool_wrapper`;
- executing `_router_hibrido`;
- duplicated strict-web retry execution;
- Spotify LLM shortcut and multi-round execution loop;
- request-time writes to `chat_history` and `DATOS_CURIOSOS`;
- pipeline and route dependencies on the mutable `ServiceContainer`, plus all
  undeclared runtime attributes;
- `jarvis_brain` copied LLM aliases;
- voice runtime-global synchronization;
- the temporary legacy pipeline feature flag.

Keep public `procesar_mensaje()` and `stream_procesar_mensaje_events()` only as
thin adapters around the orchestrator.

- [ ] **Step 4: Run architecture and full focused tests**

Run:

```powershell
pytest tests\test_architecture_contract.py tests\test_command_orchestrator.py tests\e2e\test_core_command_matrix.py tests\test_smoke.py -q
```

Expected: all tests pass and architecture offenders are empty.

- [ ] **Step 5: Commit compatibility cleanup**

```powershell
git add src/backend/core/brain/processor.py src/backend/core/brain/router.py src/backend/core/brain/tool_manager.py src/backend/core/jarvis_brain.py src/backend/core/jarvis_state.py src/backend/core/service_container.py src/backend/voice/service.py tests/test_architecture_contract.py
git commit -m "refactor: remove duplicate command execution paths"
```

### Task 17: Add Clean-Clone Validation And CI Matrix

**Files:**
- Create: `scripts/validate-clean-clone.ps1`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Test: `tests/test_installation_contract.py`

- [ ] **Step 1: Add failing distribution-contract tests**

```python
def test_clean_clone_script_covers_supported_python_versions_and_core_checks():
    script = (ROOT / "scripts/validate-clean-clone.ps1").read_text(encoding="utf-8")
    assert "3.11" in script
    assert "3.12" in script
    assert "git lfs pull" in script
    assert "setup.ps1" in script
    assert "-Dev" in script
    assert "pip check" in script
    assert "pytest -q" in script
    assert "/api/status" in script


def test_ci_runs_core_tests_on_both_supported_python_versions():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert 'python-version: ["3.11", "3.12"]' in workflow
```

- [ ] **Step 2: Run installation tests and verify they fail**

Run:

```powershell
pytest tests\test_installation_contract.py -q
```

Expected: clean-clone script or CI matrix assertions fail.

- [ ] **Step 3: Implement the isolated clone validator**

The script accepts `-PythonVersion 3.11|3.12` and `-SourceRepository`. It:

1. creates a unique directory under `scratch/clean-clone`;
2. clones from the local repository path with `--no-hardlinks`;
3. runs `git lfs pull`;
4. verifies no `.env`, runtime logs, cache, or profiles arrived;
5. runs `setup.ps1 -Dev`;
6. runs project-venv `pip check`;
7. runs `pytest -q`;
8. starts the backend without keys;
9. verifies JARVIS service identity and `unconfigured` LLM state;
10. stops the process and verifies it exited;
11. deletes only the unique clone directory.

Use `try/finally` for process and directory cleanup. Never copy the developer
`.env` into this validation clone.

```powershell
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("3.11", "3.12")]
    [string]$PythonVersion,
    [string]$SourceRepository = "."
)

$ErrorActionPreference = "Stop"
$source = (Resolve-Path -LiteralPath $SourceRepository).Path
$scratchRoot = Join-Path $source "scratch\clean-clone"
$cloneRoot = Join-Path $scratchRoot ([Guid]::NewGuid().ToString("N"))
$backendProcess = $null
$pushed = $false

try {
    New-Item -ItemType Directory -Force -Path $scratchRoot | Out-Null
    & git clone --no-hardlinks $source $cloneRoot
    if ($LASTEXITCODE -ne 0) { throw "clean_clone_failed" }
    Push-Location $cloneRoot
    $pushed = $true

    & git lfs pull
    if ($LASTEXITCODE -ne 0) { throw "git_lfs_pull_failed" }

    $forbiddenBeforeSetup = @(
        ".env",
        "src\backend\logs\log.txt",
        "src\backend\.cache-jarvis",
        "src\backend\memoria_jarvis.json",
        "src\backend\memoria_jarvis_profiles.json"
    )
    foreach ($path in $forbiddenBeforeSetup) {
        if (Test-Path -LiteralPath $path) {
            throw "runtime_artifact_arrived_in_clone:$path"
        }
    }

    & ".\setup.ps1" -Dev -PythonVersion $PythonVersion
    if ($LASTEXITCODE -ne 0) { throw "setup_failed" }

    $python = Join-Path $cloneRoot "venv\Scripts\python.exe"
    & $python -m pip check
    if ($LASTEXITCODE -ne 0) { throw "pip_check_failed" }
    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "pytest_failed" }

    $env:GROQ_API_KEY = ""
    $env:JARVIS_CORE_MODE = "true"
    $backendProcess = Start-Process `
        -FilePath $python `
        -ArgumentList "src\backend\jarvis_backend.py" `
        -WorkingDirectory $cloneRoot `
        -PassThru `
        -WindowStyle Hidden

    $status = $null
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($backendProcess.HasExited) {
            throw "backend_exited:$($backendProcess.ExitCode)"
        }
        try {
            $status = Invoke-RestMethod -Uri "http://127.0.0.1:5002/api/status" -TimeoutSec 1
            if ($status.service -eq "jarvis" -and $status.protocol_version -eq 1) {
                break
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if ($null -eq $status -or $status.service -ne "jarvis") {
        throw "backend_status_unavailable"
    }
    if ($status.capabilities.llm.state -ne "unconfigured") {
        throw "unexpected_llm_state:$($status.capabilities.llm.state)"
    }
} finally {
    if ($null -ne $backendProcess -and -not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue
        $backendProcess.WaitForExit(5000)
    }
    if ($pushed) {
        Pop-Location
    }
    if (Test-Path -LiteralPath $cloneRoot) {
        Remove-Item -LiteralPath $cloneRoot -Recurse -Force
    }
}
```

Convert the CI test job to a Python 3.11/3.12 matrix. Keep real microphone,
Spotify Desktop, and WebView2 interaction outside CI and document their manual
acceptance checklist.

```yaml
test:
  name: Tests + Coverage (Python ${{ matrix.python-version }})
  runs-on: windows-latest
  strategy:
    fail-fast: false
    matrix:
      python-version: ["3.11", "3.12"]
  steps:
    - name: Checkout
      uses: actions/checkout@v4
      with:
        lfs: true
    - name: Setup Python
      uses: actions/setup-python@v5
      with:
        python-version: ${{ matrix.python-version }}
    - name: Install dependencies
      run: |
        python -m pip install -r requirements.txt
        python -m pip install -r requirements-dev.txt
    - name: Run tests with coverage
      run: pytest -q --cov=src/backend --cov-report=term-missing --cov-report=xml --cov-fail-under=50
```

- [ ] **Step 4: Run clean-clone contracts and one real local clone**

Run:

```powershell
pytest tests\test_installation_contract.py -q
powershell -ExecutionPolicy Bypass -File scripts\validate-clean-clone.ps1 -PythonVersion 3.12 -SourceRepository .
```

Expected: contract tests pass; the clean clone installs, tests, starts, reports
controlled unconfigured capabilities, stops, and cleans its isolated directory.

After Python 3.12 succeeds, run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\validate-clean-clone.ps1 -PythonVersion 3.11 -SourceRepository .
```

Expected: the same result on Python 3.11.

- [ ] **Step 5: Commit distribution validation**

```powershell
git add scripts/validate-clean-clone.ps1 .github/workflows/ci.yml README.md AGENTS.md tests/test_installation_contract.py
git commit -m "ci: validate clean Windows installations"
```

### Task 18: Run Release Gates And Record Verified Results

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Create: `docs/verification/2026-07-22-core-stability-results.md`

- [ ] **Step 1: Run syntax, lint, dependency, and targeted checks**

Run:

```powershell
python -m compileall -q start_app.py src\backend
python -m ruff check src/backend tests --select F
python -m pip check
node --check src\frontend\static\js\main.js
node --check src\frontend\static\js\modules\api.js
node --check src\frontend\static\js\modules\recognition-policy.js
node --check src\frontend\static\js\modules\voice-capabilities.js
git diff --check
```

Expected: every command exits with code 0. LF/CRLF conversion warnings alone
are informational.

- [ ] **Step 2: Run security and feature regressions**

Run:

```powershell
pytest tests\test_security_manager.py tests\test_search_security.py tests\test_memory_rag_resilience.py tests\test_llm_engine_fallback.py tests\test_voice_transcription.py tests\test_frontend_voice_resilience.py tests\test_unified_log.py tests\test_unified_log_integration.py tests\test_frontend_terminal_log.py tests\test_spotify_desktop_matching.py tests\test_spotify_desktop_windows.py tests\test_spotify_desktop_controller.py tests\test_spotify_followup.py tests\test_spotify_recs.py -q
```

Expected: all targeted tests pass with no real provider calls.

- [ ] **Step 3: Run the complete suite**

Run:

```powershell
pytest -q
```

Expected: all tests pass. Record the actual pass/skip count and duration; do not
copy the previous baseline.

- [ ] **Step 4: Run dependency audit and Windows acceptance**

Run:

```powershell
python -m pip_audit -r requirements.txt
git lfs ls-files
```

Expected: no known runtime vulnerability and both ONNX models appear in Git
LFS.

Then run the documented real-device checks:

- launch through `start_app.py`;
- grant microphone permission in WebView2;
- issue one voice command;
- synthesize one TTS response;
- play, pause, and resume one Spotify Desktop track;
- close the UI and verify no owned backend remains.

Record every result as passed, failed, or not executed. Never claim a real
device passed when it was simulated.

- [ ] **Step 5: Write and commit the verification record**

The verification document contains commit SHA, environment, exact commands,
actual results, capability states, clean-clone results for both Python
versions, real-device results, and unresolved risks.

```powershell
git add README.md AGENTS.md docs/verification/2026-07-22-core-stability-results.md
git commit -m "docs: record core stability verification"
```

## Completion Checklist

- [ ] Router and Groq only plan.
- [ ] One execution service invokes all tools.
- [ ] Duplicate operations are rejected or replayed without a second side effect.
- [ ] Chat, stream, and voice share one orchestrator.
- [ ] Profile memory is isolated under concurrent requests.
- [ ] Tool registry snapshots are atomic.
- [ ] Capability states use one vocabulary.
- [ ] Optional modules remain disabled and unimported in core mode.
- [ ] Runtime data lives outside source directories by default.
- [ ] Windows setup is rooted at `$PSScriptRoot` and fails fast.
- [ ] Launcher verifies JARVIS identity and stops its backend cleanly.
- [ ] The 20-command matrix passes.
- [ ] Full regression and dependency gates pass.
- [ ] Clean-clone validation passes on Python 3.11 and 3.12.
- [ ] Real-device results are recorded without overstating coverage.
