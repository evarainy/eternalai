# P0-DOMAIN-003b0 - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- app/ports/capability_gateway.py (primary CapabilityGatewayPort contract; read before writing gateway code)
- app/ports/adapter.py (AdapterPort and AdapterResult contract; MockOAAdapter satisfies this)
- docs/phase0/tasks/P0-DOMAIN-003a.md (gateway interface contract)
- docs/phase0/tasks/P0-DOMAIN-008b.md (MockOAAdapter implementation task that this task calls)
- app/execution_fabric/mock_adapters/oa/ (actual MockOAAdapter code to import and call; do not modify it)
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted Capability Gateway, AdapterResult, status-mapping, and P0-DOMAIN-003b0 context; do not paste or rewrite the full spec.

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
task_id: P0-DOMAIN-003b0
branch: "phase0/P0-DOMAIN-003b0"
title: Capability Gateway Pass-through Integration Skeleton
type: implementation
depends_on:
  - P0-DOMAIN-003a
  - P0-DOMAIN-005a
  - P0-DOMAIN-008a
  - P0-DOMAIN-008b
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
    Gateway is architecture-sensitive; must prove pass-through works before
    adding Policy, Identity, or Trace in P0-DOMAIN-003b1. Codex owns TDD because
    the gateway-to-adapter integration must preserve the CapabilityGatewayPort
    and AdapterResult contracts before downstream short-circuit behavior is added.

objective: >
  Implement the first minimal Capability Gateway pass-through skeleton:
  receive a capability execution request, call MockOAAdapter directly, and return
  a gateway response wrapper preserving the AdapterResult outcome. This task is
  Gateway -> MockOAAdapter -> return result only. It must not add Policy guard
  checks, IdentityMapping, Trace integration, CapabilityRegistry lookup, real OA
  calls, or Phase 1 workflow behavior.

structured_output_baseline_applicability: "not_applicable"
structured_output_baseline_not_applicable:
  reason: "This task implements Gateway pass-through only; it does not implement LLM structured output."
  scope: "app/infra/gateway/ and tests/infra/gateway/"
  blocked_by_task_id: "none"
  activation_task_id: "P0-DOMAIN-010b"
  expiry_condition: "Structured-output baseline becomes applicable only in LLM provider or structured-output implementation tasks."
  evidence: "CapabilityGatewayPort has no LLM provider or structured-output method."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-003b0_<timestamp>_passed.yaml"

constraints_to_carry_forward:
  - "CapabilityGatewayPort contract (app/ports/capability_gateway.py) must be satisfied exactly: execute_capability(task_id, session_id, ai_user_id, capability_id, arguments, request_context) -> ExecutionResult"
  - "Current gateway response model is ExecutionResult. If an executor expects GatewayRequest/GatewayResponse names, stop and reconcile with app/ports/capability_gateway.py; do not edit app/ports/ in this task."
  - "Request context type is RequestOrgContext from app/ports/capability_gateway.py."
  - "AdapterPort contract (app/ports/adapter.py) must be used exactly: execute(capability_id, arguments, execution_context) -> AdapterResult."
  - "AdapterResult status, data, error_code, and raw_payload_ref must be preserved in the gateway response wrapper; never return a bare dict or bare AdapterResult from execute_capability."
  - "Pass-through only: explicitly no Policy guard check, no IdentityMapping or credential binding check, no TracePort write, no CapabilityRegistry lookup, and no Runtime orchestration in P0-DOMAIN-003b0."
  - "MockOAAdapter comes from app/execution_fabric/mock_adapters/oa/mock_oa_adapter.py and must be imported/called, not modified."
  - "No real OA calls, no real credentials, no HTTP client imports (requests/httpx/aiohttp), and no subprocess/playwright/selenium automation."
  - "No new Python dependencies; no __init__.py files (namespace packages)."

deliverable:
  - app/infra/gateway/
  - tests/infra/gateway/

constraints:
  - Implement a concrete CapabilityGateway skeleton under app/infra/gateway/.
  - The concrete gateway must satisfy CapabilityGatewayPort via async execute_capability.
  - Construct or inject MockOAAdapter and call its async execute method directly.
  - Build adapter execution_context from gateway inputs without adding a new port contract. For MockOAAdapter error-mode tests, propagate arguments.mock_error_mode into execution_context.mock_error_mode.
  - Return ExecutionResult as the gateway response wrapper. Do not return bare AdapterResult or bare dict.
  - Preserve AdapterResult data and error_code. Success must map to ExecutionResult.status="completed"; adapter_timeout must map to status="timeout"; upstream_permission_denied must map to status="denied"; other adapter error modes must map to status="failed".
  - Set ExecutionResult.trace_id from request_context.request_id; this is the only approved trace_id source in Phase 0; do not generate a random UUID, use another field, or leave trace_id empty.
  - Do not add Policy guard checks; P0-DOMAIN-003b1 adds Policy short-circuit behavior.
  - Do not add IdentityMapping, binding prechecks, credential handling, or SecretProvider integration; P0-DOMAIN-003b1 handles that path.
  - Do not add TracePort integration, trace pre-record, trace post-record, OpenTelemetry, or Langfuse writes; those are out of scope for P0-DOMAIN-003b0.
  - Do not add CapabilityRegistry lookup or Runtime orchestration.
  - Do not call a real OA system and do not import requests, httpx, aiohttp, subprocess, playwright, or selenium in gateway code.
  - Do not modify app/ports/capability_gateway.py, app/ports/adapter.py, or any other port file.
  - Do not modify app/execution_fabric/mock_adapters/.
  - Do not add new Python dependencies.
  - No __init__.py files.

acceptance_criteria:
  - criterion: "Concrete gateway satisfies CapabilityGatewayPort Protocol and exposes async execute_capability with the current port signature"
    result: "pending"
    evidence: ""
  - criterion: "execute_capability calls MockOAAdapter.execute with capability_id, arguments, and execution_context"
    result: "pending"
    evidence: ""
  - criterion: "Happy path works end-to-end through Gateway -> MockOAAdapter and returns ExecutionResult(status='completed') preserving AdapterResult data"
    result: "pending"
    evidence: ""
  - criterion: "MockOAAdapter error mode timeout propagates through gateway as ExecutionResult(status='timeout', error_code='adapter_timeout')"
    result: "pending"
    evidence: ""
  - criterion: "MockOAAdapter error mode permission_denied propagates through gateway as ExecutionResult(status='denied', error_code='upstream_permission_denied')"
    result: "pending"
    evidence: ""
  - criterion: "MockOAAdapter malformed_json, empty_response, http_500, and missing_required_field modes propagate through gateway as failed ExecutionResult with the exact AdapterResult error_code"
    result: "pending"
    evidence: ""
  - criterion: "Tests are integration-style: construct gateway with MockOAAdapter, call execute_capability, assert ExecutionResult gateway response wrapper"
    result: "pending"
    evidence: ""
  - criterion: "No Policy, IdentityMapping, TracePort, CapabilityRegistry, Runtime, real OA call, or dependency change is introduced"
    result: "pending"
    evidence: ""

failure_examples:
  - name: bare_adapter_result_returned
    trigger: "Gateway execute_capability returns AdapterResult directly"
    expected_result: "Tests fail because CapabilityGatewayPort requires ExecutionResult as the gateway response wrapper"
    forbidden_shortcut: "Do not change app/ports/capability_gateway.py to make bare AdapterResult acceptable"
  - name: adapter_error_swallowed
    trigger: "MockOAAdapter returns AdapterResult(status='error', error_code='adapter_payload_invalid')"
    expected_result: "Gateway returns ExecutionResult(status='failed', error_code='adapter_payload_invalid') and preserves adapter result details"
    forbidden_shortcut: "Do not convert adapter errors to generic success, None, or internal_error unless the adapter result lacks an error_code"
  - name: real_oa_call_attempted
    trigger: "Gateway imports requests/httpx/aiohttp/subprocess/playwright/selenium or constructs a real OA client"
    expected_result: "Review and verification fail; P0-DOMAIN-003b0 must call MockOAAdapter only"
    forbidden_shortcut: "Do not add a fake real-client branch hidden behind a test flag"
  - name: policy_identity_trace_added_early
    trigger: "Gateway imports or calls PolicyGuard, IdentityMapping, SecretProvider, TracePort, OpenTelemetry, or Langfuse"
    expected_result: "Review fails because P0-DOMAIN-003b0 is pass-through only; P0-DOMAIN-003b1 adds these integrations"
    forbidden_shortcut: "Do not add no-op policy/identity/trace placeholders to satisfy future tasks"

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify dependency Task Records exist, including P0-DOMAIN-008b passed log"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-DOMAIN-003a_*_passed.yaml, docs/phase0/task_logs/P0-DOMAIN-005a_*_passed.yaml, docs/phase0/task_logs/P0-DOMAIN-008a_*_passed.yaml, docs/phase0/task_logs/P0-DOMAIN-008b_*_passed.yaml | Select-Object Name"
    evidence: ""
  - step: "Verify MockOAAdapter can be imported from app/execution_fabric/mock_adapters/oa/"
    result: "pending"
    command: "uv run python -c \"from app.execution_fabric.mock_adapters.oa.mock_oa_adapter import MockOAAdapter; print(MockOAAdapter.__name__)\""
    evidence: ""
  - step: "Create gateway integration tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/infra/gateway/"
    evidence: "Expected non-zero exit before gateway implementation exists."
  - step: "Implement pass-through Capability Gateway skeleton"
    result: "pending"
    command: "Test-Path app/infra/gateway/"
    evidence: ""
  - step: "Run gateway integration tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/infra/gateway/ -v"
    evidence: ""
  - step: "Run lint and type checks"
    result: "pending"
    command: "uv run ruff check app/infra/gateway/ tests/infra/gateway/; uv run mypy app/infra/gateway/"
    evidence: ""
  - step: "Verify no Policy, Identity, Trace, Runtime, or real OA integration was added"
    result: "pending"
    command: >
      $files = Get-ChildItem app/infra/gateway/ -Filter '*.py' -Recurse;
      $matched = $files | Select-String -Pattern 'PolicyGuard','IdentityMapping','SecretProvider','TracePort','OpenTelemetry','Langfuse','app.runtime','requests','httpx','aiohttp','subprocess','playwright','selenium' -Quiet;
      if ($matched) { throw 'Forbidden integration detected in gateway pass-through task' } else { 'PASSED' }
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "$forbidden = @('app/ports/','app/runtime/','app/execution_fabric/mock_adapters/','pyproject.toml','uv.lock'); $changed = git diff --cached --name-only; $hits = foreach ($path in $changed) { foreach ($prefix in $forbidden) { if ($path -like \"$prefix*\") { $path } } }; if ($hits) { $hits; throw 'Forbidden path staged' } else { 'PASSED' }"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/infra/gateway/ -v"
  - "uv run ruff check app/infra/gateway/ tests/infra/gateway/"
  - "uv run mypy app/infra/gateway/"

touched_paths:
  - app/infra/gateway/
  - tests/infra/gateway/

forbidden_paths:
  - app/ports/
  - app/runtime/
  - app/execution_fabric/mock_adapters/
  - pyproject.toml
  - uv.lock

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-003b0"
  - "Working tree is dirty at task start"
  - "Any depends_on passed Task Record is missing, especially P0-DOMAIN-008b"
  - "Any forbidden path is modified"
  - "GatewayRequest or GatewayResponse names are assumed despite not existing in current app/ports/capability_gateway.py"
  - "Policy guard check, IdentityMapping, SecretProvider, TracePort, CapabilityRegistry, Runtime orchestration, OpenTelemetry, or Langfuse code is added"
  - "Real OA call or real HTTP/browser/subprocess client is introduced"
  - "MockOAAdapter is modified instead of imported and called"
  - "Bare AdapterResult or bare dict is returned from execute_capability"
  - "New Python dependency added"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
```

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm these carried-forward constraints:

1. CapabilityGatewayPort is satisfied exactly using the current app/ports/capability_gateway.py contract: execute_capability returns ExecutionResult.
2. Gateway integration is pass-through only in P0-DOMAIN-003b0: Gateway -> MockOAAdapter -> ExecutionResult wrapper.
3. AdapterPort and AdapterResult from app/ports/adapter.py are used exactly; adapter status, data, error_code, and raw_payload_ref are preserved.
4. MockOAAdapter is imported from app/execution_fabric/mock_adapters/oa/mock_oa_adapter.py and called directly; app/execution_fabric/mock_adapters/ is not modified.
5. No Policy guard check, IdentityMapping, SecretProvider, credential binding, TracePort write, CapabilityRegistry lookup, Runtime orchestration, OpenTelemetry, or Langfuse integration is added. These are deferred to P0-DOMAIN-003b1 or later tasks.
6. No real OA system calls; no requests/httpx/aiohttp/subprocess/playwright/selenium imports.
7. No app/ports/ changes, no pyproject.toml or uv.lock changes, no new dependencies, and no __init__.py files.
8. Integration tests construct Gateway with MockOAAdapter, call execute_capability, and assert the ExecutionResult gateway response wrapper for happy path and error modes.

## Structured-output baseline applicability

not_applicable - this task does not implement LLM structured output. It must not change the Phase 1 baseline and must not reopen instructor or PydanticAI default decisions.
