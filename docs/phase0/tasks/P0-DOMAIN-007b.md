# P0-DOMAIN-007b - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- app/ports/runtime.py (RuntimePort — the interface this task implements)
- app/ports/capability_gateway.py (CapabilityGatewayPort, ExecutionResult, ExecutionStatus, ErrorCode, RequestOrgContext)
- app/ports/task_store.py (TaskStorePort, TaskRecord, TaskStatus, SessionStorePort, SessionRecord)
- app/ports/trace.py (TracePort, TraceEvent, TraceEventType)
- app/ports/structured_output.py (StructuredOutputPort, StructuredOutputResult, StructuredOutputError)
- app/ports/response_envelope.py (ResponseEnvelope, ResponseEnvelopeStatus — re-exports from app/contracts/sdui/models.py)
- app/infra/sdui/response_envelope_builder.py (ResponseEnvelopeBuilder — the tool for building ResponseEnvelope instances)
- app/infra/gateway/capability_gateway.py (read: understand what CapabilityGateway expects as injected ports)
- app/infra/llm/mock_structured_output/mock_structured_output_provider.py (read: understand how MockStructuredOutputProvider.register() / parse_to_schema() works)
- docs/phase0/tasks/P0-DOMAIN-007a.md (the interface contract this task implements)
- docs/phase0/tasks/P0-DOMAIN-003b2.md (gateway skeleton this runtime calls into)
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted sections P0-DOMAIN-007b and Golden Task trace sequences (GT-001, GT-002 event_sequence fields); do not paste or rewrite the full spec.

## Global hard rules

- Execute only this task_id.
- Start this task only after all depends_on tasks have been reviewed, approved, and merged to the Phase 0 base branch.
- Output a Plan first and wait for human confirmation before modifying files.
- Do not modify frozen blueprint files.
- Do not implement Phase 1 features.
- Do not add unapproved dependencies.
- Do not weaken tests to pass.
- Stop after Unified Task Record and wait for human confirmation.
- No commit, no push, no merge.
- Any execution/pass claim must include exact command, exit code, and evidence output in the Task Record.
- Independent staged review is required before any commit, push, or merge.

## Task YAML

```yaml
task_id: P0-DOMAIN-007b
branch: "phase0/P0-DOMAIN-007b"
title: Runtime Main Chain Minimal Skeleton
type: implementation
depends_on:
  - P0-DOMAIN-007a
  - P0-DOMAIN-001b
  - P0-DOMAIN-003b2
  - P0-DOMAIN-005b
  - P0-DOMAIN-009b
  - P0-DOMAIN-010b
priority: P0
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "execution"
  execution_owner: "codex"
  review_owner: "separate_session"
  review_mode: "codex_review"
  method: "TDD"
  reason_for_owner_choice: >
    Runtime main chain is the highest-value integration seam in Phase 0; it wires
    together all prior skeletons (TaskStore, Gateway, Trace, SDUI, StructuredOutput).
    Codex owns TDD because the status-mapping correctness (completed/blocked/failed/
    no_capability_found), trace event sequencing, and no-execution_fabric boundary
    are regression-sensitive and must be proven by tests before Golden Task validation.
    Independent review must happen in a separate Codex review session.

objective: >
  Implement the minimal Runtime main chain: user message → TaskRecord creation →
  CapabilityRef resolution via StructuredOutputPort → CapabilityGatewayPort execution
  → ResponseEnvelope. Concretely:
    - app/runtime/runtime.py: RuntimeImpl class satisfying RuntimePort.handle_user_message
    - app/runtime/models.py: CapabilityRef Pydantic model (capability_id, arguments)
    - app/api/v1/runtime.py: minimal FastAPI router (POST /api/v1/runtime/handle)
    - tests/runtime/: three test modules covering task creation, gateway result mapping,
      and response envelope composition
  RuntimeImpl receives all dependencies via constructor injection. It must not import
  any class from app/execution_fabric/.

structured_output_baseline_applicability: "applicable"
structured_output_baseline_applicable:
  reason: >
    Runtime uses StructuredOutputPort.parse_to_schema(message, CapabilityRef) to
    resolve the user message to a capability. In Phase 0 this calls
    MockStructuredOutputProvider; the port contract is identical to the Phase 1
    path. The Plan B baseline (raw OpenAI-compatible SDK, response_format=json_object,
    Pydantic model_validate, Literal enum validation) must remain unchanged; this
    task must not reopen instructor or PydanticAI decisions.
  phase0_mock_path: >
    MockStructuredOutputProvider.register(raw_response_key, CapabilityRef, parsed)
    is the test-time stand-in. The Runtime treats message as the raw_response key.
  plan_b_constraint: >
    Do not implement real LLM calls. Do not import openai, instructor, pydanticai,
    httpx, requests, or any HTTP client in app/runtime/. The StructuredOutputPort
    adapter is injected; the Runtime has no LLM awareness.

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-007b_<timestamp>_passed.yaml"

constraints_to_carry_forward:
  - "RuntimePort contract (app/ports/runtime.py) must be satisfied exactly; do not modify app/ports/."
  - "handle_user_message must accept: channel (Literal[web,cli,api,mock]), ai_user_id, session_id, message, client_capabilities — all unchanged from 007a."
  - "handle_user_message must return ResponseEnvelope — not a bare dict, not a subclass, not None."
  - "RuntimeImpl must accept all dependencies via constructor (task_store, session_store, gateway, trace_port, structured_output, response_builder). No global singletons in production code."
  - "Runtime must NOT import anything from app/execution_fabric/. It calls CapabilityGatewayPort only."
  - "CapabilityRef is a Pydantic model defined in app/runtime/models.py with fields: capability_id: str, arguments: dict[str, Any], and model_config = ConfigDict(extra='forbid')."
  - "StructuredOutputPort.parse_to_schema(message, CapabilityRef) is called with the raw user message as raw_response. If result.parsed is None or result.error is not None, treat as no_capability_found."
  - "no_capability_found path: update TaskRecord status='no_capability_found'; ResponseEnvelope.status='no_capability_found'. Must NOT return status='failed' instead."
  - "policy_denied path: Gateway returns ExecutionResult(status='denied'); ResponseEnvelope.status='blocked'. Must NOT continue to execute the next capability."
  - "binding_required path: Gateway returns ExecutionResult(status='binding_required', error_code='identity_unbound'); ResponseEnvelope.status='blocked' and ui.component_type='binding_required_card' or 'operator_handback_card'."
  - "gateway_timeout path: Gateway returns ExecutionResult(status='timeout'); ResponseEnvelope.status='failed' and trace_id is preserved in the envelope."
  - "Trace event sequence for happy path: task_created, intent_parsed, capability_selected, gateway_pre_recorded, gateway_post_recorded, response_envelope_created, task_completed."
  - "Trace event sequence for no_capability_found: task_created, intent_parsed, no_capability_found, response_envelope_created."
  - "All TaskRecord field updates must use TaskStorePort methods (create_task, update_status); raw DB writes are forbidden."
  - "No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values may appear in Trace attributes, ResponseEnvelope, fixtures, logs, reports, or Task Record evidence."
  - "Phase 1 user-value boundary is preserved: no real LLM calls, no real system calls, no complex planner, no Adapter imports."
  - "No new Python dependencies; no pyproject.toml or uv.lock changes; no __init__.py files in new namespace-package directories."

deliverable:
  - app/runtime/runtime.py
  - app/runtime/models.py
  - app/api/v1/runtime.py
  - tests/runtime/test_task_creation.py
  - tests/runtime/test_runtime_gateway_result_mapping.py
  - tests/runtime/test_runtime_response_envelope.py

constraints:
  - Implement only under app/runtime/, app/api/v1/runtime.py, and tests/runtime/.
  - Do not modify app/ports/ (any port file), app/main.py, app/api/v1/health.py, or any Batch 3/4/5 implementation file.
  - app/runtime/runtime.py: define RuntimeImpl; do NOT make it a Protocol or ABC.
  - app/runtime/models.py: define CapabilityRef with model_config = ConfigDict(extra="forbid"); capability_id: str; arguments: dict[str, Any] with default_factory=dict.
  - app/api/v1/runtime.py: define a FastAPI router with a single POST endpoint (e.g. /handle). The router must accept a RuntimeImpl (or RuntimePort) via constructor/factory so it can be tested with a test-local FastAPI app without modifying app/main.py. Do NOT add the router to app/main.py in this task (main.py is not in touched_paths).
  - Tests use MockStructuredOutputProvider, a mock TaskStorePort, mock TracePort, mock Gateway, and ResponseEnvelopeBuilder. All async methods tested with asyncio.run() in synchronous test functions (pytest-asyncio is not available).
  - Do not call real LLM endpoints; do not import openai, instructor, pydanticai, httpx, requests, aiohttp, or any HTTP client in app/runtime/.
  - CapabilityRef must have a test: construct with valid values, assert capability_id accessible, assert extra field rejected with ValidationError, assert arguments defaults to empty dict.

acceptance_criteria:
  - criterion: "happy path: handle_user_message creates TaskRecord with status='running', calls parse_to_schema, calls execute_capability, updates TaskRecord status='completed', returns ResponseEnvelope(status='completed')"
    result: "pending"
    evidence: ""
  - criterion: "no_capability_found path: when parse_to_schema returns error or unrecognized capability, TaskRecord.status='no_capability_found' and ResponseEnvelope.status='no_capability_found' (not 'failed')"
    result: "pending"
    evidence: ""
  - criterion: "policy_denied path: when Gateway returns ExecutionResult(status='denied'), ResponseEnvelope.status='blocked', task status='failed', Gateway is NOT called again"
    result: "pending"
    evidence: ""
  - criterion: "binding_required path: when Gateway returns ExecutionResult(status='binding_required', error_code='identity_unbound'), ResponseEnvelope.status='blocked' and ui.component_type is 'binding_required_card' or 'operator_handback_card'"
    result: "pending"
    evidence: ""
  - criterion: "gateway_timeout path: when Gateway returns ExecutionResult(status='timeout'), ResponseEnvelope.status='failed' and ResponseEnvelope.trace_id is set"
    result: "pending"
    evidence: ""
  - criterion: "full trace event sequence for happy path matches: task_created, intent_parsed, capability_selected, gateway_pre_recorded, gateway_post_recorded, response_envelope_created, task_completed"
    result: "pending"
    evidence: ""
  - criterion: "full trace event sequence for no_capability_found matches: task_created, intent_parsed, no_capability_found, response_envelope_created"
    result: "pending"
    evidence: ""
  - criterion: "CapabilityRef model: extra field raises ValidationError; capability_id accessible; arguments defaults to empty dict"
    result: "pending"
    evidence: ""
  - criterion: "app/api/v1/runtime.py: POST endpoint returns 200 with ResponseEnvelope-shaped JSON for a happy-path request (tested via TestClient with injected mock RuntimeImpl)"
    result: "pending"
    evidence: ""
  - criterion: "RuntimeImpl does not import any symbol from app/execution_fabric/"
    result: "pending"
    evidence: ""
  - criterion: "All 6 depends_on Task Records exist as passed"
    result: "pending"
    evidence: ""

failure_examples:
  - name: no_capability_found_as_failed
    trigger: "parse_to_schema returns error or capability_id not recognized"
    expected_result: "ResponseEnvelope.status='no_capability_found', TaskRecord.status='no_capability_found'"
    forbidden_shortcut: "Forbidden to return status='failed' or status='blocked' for a no-capability case"
  - name: policy_denied_continue
    trigger: "Gateway returns ExecutionResult(status='denied')"
    expected_result: "ResponseEnvelope.status='blocked', execution stops immediately"
    forbidden_shortcut: "Forbidden to call execute_capability again or fall through to a second capability"
  - name: binding_required_wrong_ui
    trigger: "Gateway returns ExecutionResult(status='binding_required', error_code='identity_unbound')"
    expected_result: "ResponseEnvelope.status='blocked', ui.component_type in {'binding_required_card','operator_handback_card'}"
    forbidden_shortcut: "Forbidden to return ui.component_type='none' or status='failed'"
  - name: gateway_timeout_swallowed
    trigger: "Gateway returns ExecutionResult(status='timeout')"
    expected_result: "ResponseEnvelope.status='failed', trace_id preserved"
    forbidden_shortcut: "Forbidden to return status='completed' or omit trace_id from envelope"
  - name: runtime_imports_execution_fabric
    trigger: "app/runtime/*.py contains import from app/execution_fabric"
    expected_result: "Boundary check fails; architecture test fails"
    forbidden_shortcut: "Forbidden regardless of test-only context"

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git diff --name-only; git diff --cached --name-only"
    evidence: ""
  - step: "Verify all 6 depends_on passed Task Records exist"
    result: "pending"
    command: "$missing = @(); foreach ($tid in @('P0-DOMAIN-007a','P0-DOMAIN-001b','P0-DOMAIN-003b2','P0-DOMAIN-005b','P0-DOMAIN-009b','P0-DOMAIN-010b')) { if (-not (Get-ChildItem docs/phase0/task_logs/${tid}_*_passed.yaml -ErrorAction SilentlyContinue | Select-Object -First 1)) { $missing += $tid } }; if ($missing.Count -gt 0) { throw \"Missing depends_on Task Record(s): $($missing -join ', ')\" } else { 'PASSED' }"
    evidence: ""
  - step: "Create runtime tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/runtime/ -v 2>&1 | Select-Object -First 30"
    evidence: "Expected non-zero exit before app/runtime/ is implemented."
  - step: "Implement app/runtime/models.py (CapabilityRef)"
    result: "pending"
    command: "Test-Path app/runtime/models.py"
    evidence: ""
  - step: "Implement app/runtime/runtime.py (RuntimeImpl)"
    result: "pending"
    command: "Test-Path app/runtime/runtime.py"
    evidence: ""
  - step: "Implement app/api/v1/runtime.py (FastAPI router)"
    result: "pending"
    command: "Test-Path app/api/v1/runtime.py"
    evidence: ""
  - step: "Run all runtime tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/runtime/ -v"
    evidence: ""
  - step: "Run port contract regression tests to confirm no regressions"
    result: "pending"
    command: "uv run pytest tests/ports/test_runtime_port.py -v"
    evidence: ""
  - step: "Run full test suite to check no cross-boundary regressions"
    result: "pending"
    command: "uv run pytest -v 2>&1 | tail -20"
    evidence: ""
  - step: "Run lint and type checks on new files"
    result: "pending"
    command: "uv run ruff check app/runtime/ app/api/v1/runtime.py tests/runtime/; uv run mypy app/runtime/ app/api/v1/runtime.py"
    evidence: ""
  - step: "Verify RuntimeImpl does not import from app/execution_fabric/"
    result: "pending"
    command: "$hits = Get-ChildItem app/runtime/ -Filter '*.py' -Recurse | Select-String -Pattern 'from app\\.execution_fabric|import app\\.execution_fabric' -Quiet; if ($hits) { throw 'execution_fabric import detected in app/runtime/' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify no __init__.py files were created in new directories"
    result: "pending"
    command: "$paths = @('app/runtime','tests/runtime'); $hits = foreach ($path in $paths) { if (Test-Path $path) { Get-ChildItem $path -Filter '__init__.py' -Recurse -ErrorAction SilentlyContinue } }; if ($hits) { $hits | ForEach-Object { $_.FullName }; throw '__init__.py detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify app/main.py was NOT modified"
    result: "pending"
    command: "$changed = git diff --cached --name-only; if ($changed -contains 'app/main.py') { throw 'app/main.py is in staged diff — forbidden' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "$forbidden = @('app/ports/','app/execution_fabric/','app/main.py','pyproject.toml','uv.lock'); $changed = git diff --cached --name-only; $hits = foreach ($path in $changed) { foreach ($prefix in $forbidden) { if ($path -like \"$prefix*\" -or $path -eq $prefix.TrimEnd('/')) { $path } } }; if ($hits) { $hits; throw 'Forbidden path staged' } else { 'PASSED' }"
    evidence: ""
  - step: "Staged secret scan"
    result: "pending"
    command: "$secretPattern = '(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|authorization|bearer|cookie|session[_-]?id|dsn|connection[_-]?string)\\s*[:=]\\s*[\"'']?[^\"''\\s]{6,}'; $hits = git diff --cached -U0 | Select-String -Pattern $secretPattern; if ($hits) { 'SECRET SCAN FAIL:'; $hits | ForEach-Object { $_.Line } } else { 'SECRET SCAN: no hits' }"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/runtime/ -v"
  - "uv run pytest tests/ports/test_runtime_port.py -v"
  - "uv run ruff check app/runtime/ app/api/v1/runtime.py tests/runtime/"
  - "uv run mypy app/runtime/ app/api/v1/runtime.py"

touched_paths:
  - app/runtime/
  - app/api/v1/runtime.py
  - tests/runtime/

forbidden_paths:
  - app/execution_fabric/
  - app/ports/
  - app/main.py
  - pyproject.toml
  - uv.lock

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-007b"
  - "Working tree is dirty at task start"
  - "Any of the 6 depends_on passed Task Records is missing"
  - "app/runtime/ imports from app/execution_fabric/"
  - "ResponseEnvelope is returned as a bare dict instead of a Pydantic model instance"
  - "no_capability_found case returns ResponseEnvelope.status='failed'"
  - "app/main.py is modified"
  - "Any forbidden path is modified"
  - "New Python dependency added"
  - "__init__.py file created in new namespace-package directories"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
```

## Design notes

### CapabilityRef — local model in app/runtime/models.py

```python
class CapabilityRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
```

This is the structured output schema that RuntimeImpl parses the user message into. It is defined in `app/runtime/` (not in `app/ports/`) because it is an implementation-internal type, not a port contract. Phase 1 may replace it with a richer Intent model.

### RuntimeImpl constructor injection pattern

Follow the same pattern as `app/infra/gateway/capability_gateway.py` (CapabilityGateway): all dependencies passed via `__init__`. No global singletons in production code. Example shape:

```python
class RuntimeImpl:
    def __init__(
        self,
        task_store: TaskStorePort,
        session_store: SessionStorePort,
        gateway: CapabilityGatewayPort,
        trace_port: TracePort,
        structured_output: StructuredOutputPort,
        response_builder: ResponseEnvelopeBuilder,
    ) -> None: ...
```

### StructuredOutputPort usage

Call `await self.structured_output.parse_to_schema(message, CapabilityRef)`. If `result.parsed` is `None` or `result.error` is not `None`, treat as no-capability-found and skip the gateway entirely.

In tests, use `MockStructuredOutputProvider.register(message_text, CapabilityRef, capability_ref_instance)` to register deterministic mappings.

### handle_user_message internal flow (minimal happy path)

```
1. Generate task_id = str(uuid4()), trace_id = str(uuid4()), response_id = str(uuid4())
2. await task_store.create_task(TaskRecord(task_id=..., session_id=..., ai_user_id=..., status="created", trace_id=...))
3. await trace_port.start_task_trace(trace_id, task_id, session_id)
4. await trace_port.record_step(..., event_type="task_created", status="ok")
5. result = await structured_output.parse_to_schema(message, CapabilityRef)
6. await trace_port.record_step(..., event_type="intent_parsed", status="ok"/"failed")
7. if not result.parsed or result.error:
     → update_status(task_id, "no_capability_found")
     → trace record_step event_type="no_capability_found", status="blocked"
     → build + return ResponseEnvelope(status="no_capability_found")
8. capability_ref: CapabilityRef = result.parsed
9. await trace_port.record_step(..., event_type="capability_selected", status="ok", capability_id=...)
10. request_context = RequestOrgContext(request_id=str(uuid4()), channel=channel, ...)
11. await trace_port.record_step(..., event_type="gateway_pre_recorded", status="ok")
12. exec_result = await gateway.execute_capability(task_id, session_id, ai_user_id, capability_ref.capability_id, capability_ref.arguments, request_context)
13. await trace_port.record_step(..., event_type="gateway_post_recorded", status=...)
14. Map exec_result → ResponseEnvelope (see mapping table below)
15. await trace_port.record_step(..., event_type="response_envelope_created", status="ok")
16. await task_store.update_status(task_id, final_status)
17. await trace_port.finalize_task_trace(...)
18. return envelope
```

### ExecutionResult → ResponseEnvelope mapping table

| ExecutionResult.status | TaskRecord.status | ResponseEnvelope.status | UI component |
|---|---|---|---|
| completed | completed | completed | none |
| denied | failed | blocked | operator_handback_card (action=clarify_scope) |
| binding_required | failed | blocked | binding_required_card or operator_handback_card |
| timeout | failed | failed | none |
| failed | failed | failed | none |
| no_capability_found | no_capability_found | no_capability_found | operator_handback_card |
| waiting_user | waiting_user | waiting_user | confirm_card |

### API endpoint pattern

`app/api/v1/runtime.py` creates a router factory (or plain router with request body). Tests use a test-local FastAPI app. Do NOT register the router in `app/main.py` as part of this task (main.py is not in touched_paths).

Minimal example (adapt as needed):

```python
# app/api/v1/runtime.py
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from typing import Any
from app.ports.runtime import RuntimePort

def make_router(runtime: RuntimePort) -> APIRouter:
    router = APIRouter()

    class HandleRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        channel: str
        ai_user_id: str
        session_id: str
        message: str
        client_capabilities: dict[str, Any] = {}

    @router.post("/handle")
    async def handle(body: HandleRequest) -> dict[str, Any]:
        envelope = await runtime.handle_user_message(
            channel=body.channel,  # type: ignore[arg-type]
            ai_user_id=body.ai_user_id,
            session_id=body.session_id,
            message=body.message,
            client_capabilities=body.client_capabilities,
        )
        return envelope.model_dump()

    return router
```

### Tests structure

Tests must use `asyncio.run()` for async methods (no pytest-asyncio). Use mock implementations (duck-typed classes or simple subclasses) for all ports. Do NOT call `uv run pytest tests/runtime/test_runtime_gateway_result_mapping.py` in isolation to confirm the split — all three test files should pass independently and as a suite.

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm these carried-forward constraints:

1. RuntimePort (app/ports/runtime.py) handle_user_message signature is satisfied exactly: channel/ai_user_id/session_id/message/client_capabilities → ResponseEnvelope.
2. RuntimeImpl does not import from app/execution_fabric/ — it calls CapabilityGatewayPort only.
3. CapabilityRef has model_config = ConfigDict(extra="forbid") and tests assert extra field raises ValidationError.
4. no_capability_found case sets TaskRecord.status="no_capability_found" and ResponseEnvelope.status="no_capability_found" (not "failed").
5. policy_denied case returns ResponseEnvelope.status="blocked" and does NOT continue to execute another capability.
6. binding_required case returns ResponseEnvelope.status="blocked" with ui.component_type="binding_required_card" or "operator_handback_card".
7. gateway_timeout case returns ResponseEnvelope.status="failed" with trace_id preserved.
8. Trace events follow the sequences documented in the spec (happy path and no_capability_found at minimum).
9. Structured-output Plan B baseline remains unchanged (no instructor/PydanticAI/openai imports in runtime code).
10. No new Python dependencies. No pyproject.toml or uv.lock changes. No __init__.py files. app/main.py not modified.
11. No plaintext credential/token/password/cookie/sessionid/access_token/refresh_token in fixtures, trace attributes, ResponseEnvelope, or Task Record evidence.

## Structured-output baseline applicability

applicable

- Phase 0 mock path: MockStructuredOutputProvider.register(message, CapabilityRef, result) is the test stand-in.
- Runtime treats user message as the raw_response key passed to parse_to_schema.
- Plan B (raw OpenAI SDK + response_format=json_object + Pydantic model_validate + Literal enum validation) remains unchanged.
- No LLM, instructor, PydanticAI, httpx, or requests imports in app/runtime/.
