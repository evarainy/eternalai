# P0-DOMAIN-008b - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- app/ports/adapter.py (the AdapterPort contract this task implements — read before writing any adapter code)
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted section P0-DOMAIN-008b; do not paste or rewrite the full spec.

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
task_id: P0-DOMAIN-008b
branch: "phase0/P0-DOMAIN-008b"
title: Mock OA Adapter
type: implementation
depends_on:
  - P0-DOMAIN-008a
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
    Mock OA Adapter is a Phase 0 mock implementation that must satisfy AdapterPort
    exactly (including all 6 error injection modes) and must not call real business
    systems. Codex owns TDD because the error-mode matrix and AdapterResult contract
    must be fully exercised before downstream gateway integration begins.

objective: >
  Implement a Mock OA (泛微) Adapter that satisfies AdapterPort.execute, returns
  well-formed AdapterResult on success and on each mock_error_mode trigger, and
  supports Phase 0 Golden Task verification. This task creates only mock/test
  implementation; it must not call a real OA system or store real credentials.

structured_output_baseline_applicability: "not_applicable - this task does not implement LLM structured output."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-008b_<timestamp>_passed.yaml"

constraints_to_carry_forward:
  - "AdapterPort contract (app/ports/adapter.py) must be satisfied exactly — return AdapterResult, never bare dict"
  - "mock_error_mode mapping must match MOCK_ERROR_MODE_TO_ERROR_CODE in adapter.py exactly"
  - "No real OA calls, no real credentials, no HTTP client imports (requests/httpx/aiohttp)"
  - "No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values in fixtures, logs, reports, or Task Record evidence"
  - "No new Python dependencies; no __init__.py (namespace packages)"
  - "Phase 1 user-value boundary: mock only, no full Phase 1 workflow"

deliverable:
  - app/execution_fabric/mock_adapters/oa/
  - tests/execution_fabric/mock_adapters/oa/

constraints:
  - Implement MockOAAdapter satisfying AdapterPort.execute (async method).
  - Return AdapterResult (never bare dict).
  - Support all 6 mock_error_mode values from MockErrorMode TypeAlias in adapter.py.
  - Each error mode must return the exact ErrorCode from MOCK_ERROR_MODE_TO_ERROR_CODE.
  - Happy-path pending-workflow query data must include at minimum: workflow_id (str), title (str), status (Literal["pending","approved","rejected"]), applicant (str), created_at (str).
  - Happy-path workflow-status data must include at minimum: workflow_id (str), current_step (Literal["draft","pending","approved","rejected"]), approver (str).
  - No real OA HTTP calls; no requests/httpx/subprocess/aiohttp/playwright imports.
  - No real credentials or plaintext token-like values in any fixture or test data.
  - Do not add new Python dependencies.
  - No __init__.py (namespace packages throughout).
  - Can be called by Gateway; must not be called directly by Runtime.
  - Do not modify app/ports/adapter.py or any other port file.

acceptance_criteria:
  - criterion: "MockOAAdapter satisfies AdapterPort Protocol (duck-type check: has async execute method with correct signature)"
    result: "pending"
    evidence: ""
  - criterion: "Happy path returns AdapterResult(status='success') with required workflow fields"
    result: "pending"
    evidence: ""
  - criterion: "mock_error_mode=timeout returns AdapterResult(status='error', error_code='adapter_timeout')"
    result: "pending"
    evidence: ""
  - criterion: "mock_error_mode=permission_denied returns AdapterResult(status='error', error_code='upstream_permission_denied')"
    result: "pending"
    evidence: ""
  - criterion: "mock_error_mode=malformed_json returns AdapterResult(status='error', error_code='adapter_payload_invalid') — Gateway still receives standard AdapterResult, no uncaught exception raised"
    result: "pending"
    evidence: ""
  - criterion: "mock_error_mode=empty_response returns AdapterResult(status='error', error_code='adapter_empty_response')"
    result: "pending"
    evidence: ""
  - criterion: "mock_error_mode=http_500 returns AdapterResult(status='error', error_code='adapter_http_500')"
    result: "pending"
    evidence: ""
  - criterion: "mock_error_mode=missing_required_field returns AdapterResult(status='error', error_code='adapter_missing_required_field')"
    result: "pending"
    evidence: ""
  - criterion: "No real HTTP client import in adapter implementation files"
    result: "pending"
    evidence: ""

failure_examples:
  - name: timeout
    trigger: "execution_context contains mock_error_mode='timeout'"
    expected_result: "AdapterResult(status='error', error_code='adapter_timeout')"
    forbidden_shortcut: "禁止 sleep 固定长时间导致测试不可控"
  - name: permission_denied
    trigger: "execution_context contains mock_error_mode='permission_denied'"
    expected_result: "AdapterResult(status='error', error_code='upstream_permission_denied')"
    forbidden_shortcut: "禁止返回 status='success'"
  - name: malformed_json
    trigger: "execution_context contains mock_error_mode='malformed_json'"
    expected_result: "AdapterResult(status='error', error_code='adapter_payload_invalid'); Gateway receives standard AdapterResult"
    forbidden_shortcut: "禁止向 Gateway 抛出未捕获裸异常"
  - name: missing_required_field
    trigger: "execution_context contains mock_error_mode='missing_required_field'"
    expected_result: "AdapterResult(status='error', error_code='adapter_missing_required_field')"
    forbidden_shortcut: "禁止自动补全缺失字段让测试通过"

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify P0-DOMAIN-008a passed Task Record exists"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-DOMAIN-008a_*_passed.yaml | Select-Object -First 1"
    evidence: ""
  - step: "Create mock adapter tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/execution_fabric/mock_adapters/oa/"
    evidence: "Expected non-zero exit before implementation exists."
  - step: "Implement MockOAAdapter"
    result: "pending"
    command: "Test-Path app/execution_fabric/mock_adapters/oa/"
    evidence: ""
  - step: "Run all adapter tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/execution_fabric/mock_adapters/oa/ -v"
    evidence: ""
  - step: "Run lint and type checks"
    result: "pending"
    command: "uv run ruff check app/execution_fabric/mock_adapters/oa/ tests/execution_fabric/mock_adapters/oa/; uv run mypy app/execution_fabric/mock_adapters/oa/"
    evidence: ""
  - step: "Verify no real HTTP client imports"
    result: "pending"
    command: "$files = Get-ChildItem app/execution_fabric/mock_adapters/oa/ -Filter '*.py' -Recurse; $matched = $files | Select-String -Pattern 'import requests','import httpx','import aiohttp','import subprocess' -Quiet; if ($matched) { throw 'Real HTTP client import detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/execution_fabric/mock_adapters/oa/ -v"
  - "uv run ruff check app/execution_fabric/mock_adapters/oa/ tests/execution_fabric/mock_adapters/oa/"
  - "uv run mypy app/execution_fabric/mock_adapters/oa/"

touched_paths:
  - app/execution_fabric/mock_adapters/oa/
  - tests/execution_fabric/mock_adapters/oa/

forbidden_paths:
  - app/runtime/
  - app/execution_fabric/real_adapters/
  - app/ports/
  - pyproject.toml
  - uv.lock

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-008b"
  - "Working tree is dirty at task start"
  - "P0-DOMAIN-008a passed Task Record is missing"
  - "Any forbidden path is modified"
  - "Real HTTP client (requests/httpx/aiohttp) imported in adapter code"
  - "Bare dict returned instead of AdapterResult"
  - "New Python dependency added"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
```

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm these carried-forward constraints:

1. AdapterPort contract (app/ports/adapter.py) is satisfied exactly — MockOAAdapter.execute is async and returns AdapterResult, never a bare dict.
2. All 6 mock_error_mode values map to the exact ErrorCode from MOCK_ERROR_MODE_TO_ERROR_CODE (adapter.py).
3. No real OA system calls; no HTTP client imports (requests/httpx/aiohttp).
4. No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values in fixtures, logs, reports, or Task Record evidence.
5. No new Python dependencies. No __init__.py files (namespace packages).
6. Phase 1 user-value boundary: mock only, no full Phase 1 workflow.

## Structured-output baseline applicability

not_applicable - this task does not implement LLM structured output. It must not change the Phase 1 baseline and must not reopen instructor or PydanticAI default decisions.
