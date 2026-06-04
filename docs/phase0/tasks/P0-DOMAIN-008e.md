# P0-DOMAIN-008e - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- app/ports/adapter.py (AdapterPort contract — error injection must use MockErrorMode TypeAlias)
- app/execution_fabric/mock_adapters/ (the 008b/c/d implementations this task wraps — read before writing any control code)
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted section P0-DOMAIN-008e; do not paste or rewrite the full spec.

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
- CRITICAL: The mock control endpoint must be completely absent in non-testing environments (404 or unregistered). This is a security constraint.

## Task YAML

```yaml
task_id: P0-DOMAIN-008e
branch: "phase0/P0-DOMAIN-008e"
title: Mock Adapter Error Injection Control Endpoint
type: implementation
depends_on:
  - P0-DOMAIN-008b
  - P0-DOMAIN-008c
  - P0-DOMAIN-008d
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
    Error injection control endpoint is a security-sensitive Phase 0 test tool.
    It must be completely guarded from production environments. Codex owns TDD
    because the environment guard, injection lifecycle, and duration-exhaustion
    paths must be fully tested before Golden Task negative-path work begins.

objective: >
  Provide a FastAPI mock control endpoint (POST /mock/{capability_id}/inject) that
  allows Golden Task tests to inject deterministic, repeatable error states into
  mock adapters (OA/U8/Hikvision). The endpoint must be completely absent in
  non-testing environments. All errors must be explicitly injected — no random errors.

structured_output_baseline_applicability: "not_applicable - this task does not implement LLM structured output."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-008e_<timestamp>_passed.yaml"

constraints_to_carry_forward:
  - "Endpoint must only be registered when ENV=testing or PHASE0_MOCK_MODE=true"
  - "Non-testing environment: endpoint returns 404 or is completely absent from routing — never just hidden in frontend"
  - "error_mode enum must exactly match MockErrorMode TypeAlias in adapter.py"
  - "All errors must be explicitly injected — no random errors"
  - "duration=next_1_call: injection cleared after one adapter call; must not permanently pollute state"
  - "No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values in fixtures, logs, reports, or Task Record evidence"
  - "No new Python dependencies; no __init__.py (namespace packages)"

deliverable:
  - app/api/v1/mock_control.py
  - app/execution_fabric/mock_adapters/error_injection.py
  - tests/execution_fabric/mock_adapters/test_error_injection_guard.py
  - tests/execution_fabric/mock_adapters/test_error_injection_lifecycle.py

constraints:
  - Implement POST /mock/{capability_id}/inject FastAPI endpoint.
  - Register the route ONLY when ENV=testing or PHASE0_MOCK_MODE=true; otherwise return 404 or do not register.
  - Request body must support: error_mode (MockErrorMode enum), duration (Literal["next_1_call","next_3_calls","permanent"]), error_detail (optional str).
  - error_mode values must exactly match MockErrorMode TypeAlias from adapter.py.
  - Injection state is stored in a module-level or request-scoped registry; cleared per duration contract.
  - duration=next_1_call: cleared after one adapter execute call (second call returns normal result).
  - duration=next_3_calls: cleared after three adapter execute calls.
  - duration=permanent: persists until explicitly cleared (test teardown).
  - Injection state can be cleared after tests (provide a reset/clear mechanism for test teardown).
  - No random errors; all error states must be explicitly injected via this endpoint.
  - Do not expose any production system endpoint or internal system state via this control plane.
  - Do not add new Python dependencies.
  - No __init__.py (namespace packages throughout).
  - Do not modify app/ports/ files.

acceptance_criteria:
  - criterion: "POST /mock/{capability_id}/inject is available when ENV=testing or PHASE0_MOCK_MODE=true"
    result: "pending"
    evidence: ""
  - criterion: "Endpoint returns 404 or is unregistered when ENV is not testing and PHASE0_MOCK_MODE is not true"
    result: "pending"
    evidence: ""
  - criterion: "Request body validates error_mode against MockErrorMode enum; invalid value returns 422"
    result: "pending"
    evidence: ""
  - criterion: "duration=next_1_call: first adapter call returns injected error; second call returns normal result"
    result: "pending"
    evidence: ""
  - criterion: "duration=next_3_calls: first three calls return injected error; fourth returns normal"
    result: "pending"
    evidence: ""
  - criterion: "duration=permanent: error persists until cleared"
    result: "pending"
    evidence: ""
  - criterion: "Injection state can be cleared for test teardown"
    result: "pending"
    evidence: ""
  - criterion: "All 6 error_mode values work correctly end-to-end with at least one mock adapter"
    result: "pending"
    evidence: ""

failure_examples:
  - name: endpoint_disabled_outside_testing
    trigger: "ENV is not 'testing' and PHASE0_MOCK_MODE is not 'true'"
    expected_result: "Endpoint returns 404 or is completely absent from routing"
    forbidden_shortcut: "禁止仅靠前端隐藏端点或只用 if 语句跳过处理而路由仍注册"
  - name: invalid_error_mode
    trigger: "error_mode value not in MockErrorMode TypeAlias"
    expected_result: "422 Unprocessable Entity or validation_error response"
    forbidden_shortcut: "禁止把未知模式当 timeout 或静默忽略"
  - name: duration_exhausted
    trigger: "duration=next_1_call and adapter called twice"
    expected_result: "First call returns injected error; second call returns normal AdapterResult"
    forbidden_shortcut: "禁止永久污染全局状态"
  - name: state_leaked_across_tests
    trigger: "Test A injects permanent error; Test B runs without resetting state"
    expected_result: "Test teardown clears state; Test B receives normal result"
    forbidden_shortcut: "禁止测试间共享可变全局注入状态而无清理机制"

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify all depends_on Task Records exist"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-DOMAIN-008b_*_passed.yaml | Select-Object -First 1; Get-ChildItem docs/phase0/task_logs/P0-DOMAIN-008c_*_passed.yaml | Select-Object -First 1; Get-ChildItem docs/phase0/task_logs/P0-DOMAIN-008d_*_passed.yaml | Select-Object -First 1"
    evidence: ""
  - step: "Create error injection tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/execution_fabric/mock_adapters/test_error_injection_guard.py tests/execution_fabric/mock_adapters/test_error_injection_lifecycle.py"
    evidence: "Expected non-zero exit before implementation exists."
  - step: "Implement error_injection.py and mock_control.py"
    result: "pending"
    command: "Test-Path app/api/v1/mock_control.py; Test-Path app/execution_fabric/mock_adapters/error_injection.py"
    evidence: ""
  - step: "Run environment guard test (TDD green)"
    result: "pending"
    command: "uv run pytest tests/execution_fabric/mock_adapters/test_error_injection_guard.py -v"
    evidence: ""
  - step: "Run injection lifecycle test"
    result: "pending"
    command: "uv run pytest tests/execution_fabric/mock_adapters/test_error_injection_lifecycle.py -v"
    evidence: ""
  - step: "Run lint and type checks"
    result: "pending"
    command: "uv run ruff check app/api/v1/mock_control.py app/execution_fabric/mock_adapters/error_injection.py tests/execution_fabric/mock_adapters/; uv run mypy app/api/v1/mock_control.py app/execution_fabric/mock_adapters/error_injection.py"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/execution_fabric/mock_adapters/ -v"
  - "uv run ruff check app/api/v1/mock_control.py app/execution_fabric/mock_adapters/error_injection.py tests/execution_fabric/mock_adapters/"
  - "uv run mypy app/api/v1/mock_control.py app/execution_fabric/mock_adapters/error_injection.py"

touched_paths:
  - app/api/v1/mock_control.py
  - app/execution_fabric/mock_adapters/error_injection.py
  - tests/execution_fabric/mock_adapters/

forbidden_paths:
  - app/runtime/
  - app/execution_fabric/real_adapters/
  - app/ports/
  - pyproject.toml
  - uv.lock

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-008e"
  - "Working tree is dirty at task start"
  - "P0-DOMAIN-008b, 008c, or 008d passed Task Record is missing"
  - "Any forbidden path is modified"
  - "Endpoint is registered in non-testing environments (not guarded)"
  - "error_mode enum does not exactly match MockErrorMode TypeAlias"
  - "Random errors introduced instead of explicit injection"
  - "Injection state leaks across tests with no clear mechanism"
  - "New Python dependency added"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
```

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm these carried-forward constraints:

1. Endpoint is registered ONLY when ENV=testing or PHASE0_MOCK_MODE=true; production environments get 404 or no route.
2. error_mode enum exactly matches MockErrorMode TypeAlias from adapter.py (6 values).
3. All errors are explicitly injected — no random errors anywhere.
4. duration=next_1_call is cleared after exactly one adapter execute call.
5. Injection state has a clear/reset mechanism for test teardown to prevent inter-test leakage.
6. No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values in fixtures, logs, reports, or Task Record evidence.
7. No new Python dependencies. No __init__.py files (namespace packages).

## Structured-output baseline applicability

not_applicable - this task does not implement LLM structured output. It must not change the Phase 1 baseline and must not reopen instructor or PydanticAI default decisions.
