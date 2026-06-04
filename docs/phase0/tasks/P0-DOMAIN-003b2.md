# P0-DOMAIN-003b2 - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- app/ports/capability_gateway.py (ExecutionResult, ErrorCode, ExecutionStatus)
- app/ports/adapter.py (AdapterPort, AdapterResult)
- docs/phase0/tasks/P0-DOMAIN-003b1.md (what this task EXTENDS — read before editing any gateway code)
- app/infra/gateway/ (the 003b1 implementation to extend — read existing code before writing)
- docs/phase0/tasks/P0-DOMAIN-008b.md (MockOAAdapter — oa target_system)
- docs/phase0/tasks/P0-DOMAIN-008c.md (MockU8Adapter — u8 target_system)
- docs/phase0/tasks/P0-DOMAIN-008d.md (MockHikvisionAdapter — hikvision_ivms target_system)
- docs/phase0/tasks/P0-DOMAIN-008e.md (error injection modes and their expected error codes)
- app/execution_fabric/mock_adapters/ (read actual adapter code to understand dispatch targets and error modes)
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted Gateway adapter dispatch and P0-DOMAIN-003b2 context; do not paste or rewrite the full spec.

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
task_id: P0-DOMAIN-003b2
branch: "phase0/P0-DOMAIN-003b2"
title: Gateway Adapter Execution Skeleton
type: implementation
depends_on:
  - P0-DOMAIN-003b1
  - P0-DOMAIN-008b
  - P0-DOMAIN-008c
  - P0-DOMAIN-008d
  - P0-DOMAIN-008e
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
    Gateway adapter dispatch is the final integration step of the Batch 5 Gateway path.
    Routing to the correct Mock Adapter by target_system and mapping all error injection
    modes to the correct gateway-level ErrorCode is architecture-sensitive. TDD required
    to prove each dispatch route and error mapping independently before Runtime integration.

objective: >
  Extend app/infra/gateway/ (from P0-DOMAIN-003b1) to dispatch to the correct
  AdapterPort implementation based on CapabilitySpec.target_system:
    oa              → injected OA adapter (MockOAAdapter in tests)
    u8              → injected U8 adapter (MockU8Adapter in tests)
    hikvision_ivms  → injected Hikvision adapter (MockHikvisionAdapter in tests)
    None            → return error ExecutionResult (not raise exception)
  All adapter error injection modes from P0-DOMAIN-008e must map to the correct
  gateway-level ErrorCode in ExecutionResult. Adapters are injected via constructor;
  gateway production code must not import MockAdapter classes directly.
  All P0-DOMAIN-003b0 and P0-DOMAIN-003b1 tests must remain passing.

structured_output_baseline_applicability: "not_applicable"
structured_output_baseline_not_applicable:
  reason: "This task implements Gateway adapter dispatch only; it does not implement LLM structured output."
  scope: "app/infra/gateway/ and tests/infra/gateway/"
  blocked_by_task_id: "none"
  activation_task_id: "P0-DOMAIN-010b"
  expiry_condition: "Structured-output baseline becomes applicable only in LLM provider or structured-output implementation tasks."
  evidence: "CapabilityGatewayPort has no LLM provider or structured-output method."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-003b2_<timestamp>_passed.yaml"

constraints_to_carry_forward:
  - "CapabilityGatewayPort contract (app/ports/capability_gateway.py) must be satisfied exactly; do not modify app/ports/."
  - "AdapterPort contract (app/ports/adapter.py) must be used exactly for all three adapter dispatch paths."
  - "Adapter dispatch is by CapabilitySpec.target_system: oa/u8/hikvision_ivms map to injected adapters; None returns error ExecutionResult."
  - "Adapters are INJECTED into the gateway via constructor; gateway production code must not import MockOAAdapter, MockU8Adapter, or MockHikvisionIVMSAdapter directly."
  - "All adapter error injection modes from P0-DOMAIN-008e must map to the correct ErrorCode: timeout→adapter_timeout, permission_denied→upstream_permission_denied, malformed_json→adapter_payload_invalid, empty_response→adapter_empty_response, http_500→adapter_http_500, missing_required_field→adapter_missing_required_field."
  - "target_system=None must return ExecutionResult(status='no_capability_found', error_code='capability_not_found') without raising an exception; use the same status as the CapabilityRegistry not-found case in 003b1 for cross-task consistency."
  - "All short-circuit paths from 003b1 are preserved unchanged (CapabilityRegistry, IdentityMapping, PolicyGuard checks)."
  - "TracePort calls from 003b1 are preserved; no new Trace calls are required in 003b2 unless the port contract requires them."
  - "No plaintext secret/token/password/cookie/sessionid/access_token/refresh_token in Trace event attributes or ExecutionResult."
  - "No real OA/U8/Hik calls; no HTTP client imports (requests/httpx/aiohttp); no subprocess/playwright/selenium."
  - "No new Python dependencies; no __init__.py files."
  - "All P0-DOMAIN-003b0 and P0-DOMAIN-003b1 tests must remain passing after this task's changes."

deliverable:
  - app/infra/gateway/
  - tests/infra/gateway/

constraints:
  - Extend app/infra/gateway/ in-place — do not create a new parallel gateway implementation.
  - Read the existing 003b1 gateway code before making any changes; preserve all short-circuit paths.
  - Inject all three adapter instances via a dict[TargetSystem, AdapterPort] parameter named adapters; do not accept separate named parameters per adapter. The existing single adapter parameter from 003b0/003b1 may be wrapped internally as adapters[default_target_system] if needed for backward compatibility.
  - Gateway production code (app/infra/gateway/) must not import MockOAAdapter, MockU8Adapter, or MockHikvisionIVMSAdapter; integration tests may import and inject them.
  - Do not modify app/execution_fabric/mock_adapters/.
  - Do not modify app/ports/ files.
  - target_system=None must return a deterministic error ExecutionResult, not raise an exception.
  - Do not add Alembic migrations or database schema changes.
  - Do not add new Python dependencies.
  - No __init__.py files.
  - Test each adapter dispatch path independently: OA, U8, Hikvision, and None.
  - Test each error injection mode for at least one adapter (the mode list is the same across all three).
  - All 003b0 and 003b1 tests must still pass after this task's changes.

acceptance_criteria:
  - criterion: "target_system='oa' dispatches to the injected OA adapter; ExecutionResult reflects the OA adapter outcome"
    result: "pending"
    evidence: ""
  - criterion: "target_system='u8' dispatches to the injected U8 adapter; ExecutionResult reflects the U8 adapter outcome"
    result: "pending"
    evidence: ""
  - criterion: "target_system='hikvision_ivms' dispatches to the injected Hikvision adapter; ExecutionResult reflects the Hikvision adapter outcome"
    result: "pending"
    evidence: ""
  - criterion: "target_system=None returns ExecutionResult with status='no_capability_found' and error_code='capability_not_found'; adapter is not called"
    result: "pending"
    evidence: ""
  - criterion: "adapter_timeout error mode maps to ExecutionResult(status='timeout', error_code='adapter_timeout')"
    result: "pending"
    evidence: ""
  - criterion: "upstream_permission_denied error mode maps to ExecutionResult(status='denied', error_code='upstream_permission_denied')"
    result: "pending"
    evidence: ""
  - criterion: "malformed_json error mode maps to ExecutionResult(status='failed', error_code='adapter_payload_invalid')"
    result: "pending"
    evidence: ""
  - criterion: "http_500 error mode maps to ExecutionResult(status='failed', error_code='adapter_http_500')"
    result: "pending"
    evidence: ""
  - criterion: "empty_response error mode maps to ExecutionResult(status='failed', error_code='adapter_empty_response')"
    result: "pending"
    evidence: ""
  - criterion: "missing_required_field error mode maps to ExecutionResult(status='failed', error_code='adapter_missing_required_field')"
    result: "pending"
    evidence: ""
  - criterion: "Gateway production code (app/infra/gateway/) does not import MockOAAdapter, MockU8Adapter, or MockHikvisionIVMSAdapter"
    result: "pending"
    evidence: ""
  - criterion: "All P0-DOMAIN-003b0 and P0-DOMAIN-003b1 tests pass without modification"
    result: "pending"
    evidence: ""

failure_examples:
  - name: wrong_adapter_dispatched
    trigger: "CapabilitySpec.target_system='u8' but gateway calls the OA adapter instead"
    expected_result: "The U8 adapter is called; ExecutionResult reflects the U8 adapter's response"
    forbidden_shortcut: "Do not hardcode MockOAAdapter for all paths or default to OA when target_system is unrecognized"
  - name: mock_adapter_imported_in_production_code
    trigger: "app/infra/gateway/gateway.py contains 'from app.execution_fabric.mock_adapters import MockOAAdapter'"
    expected_result: "Review fails; gateway production code must work with any AdapterPort-satisfying object via injection"
    forbidden_shortcut: "Do not import Mock classes in production code and wrap them — accept AdapterPort via constructor"
  - name: error_mode_not_mapped
    trigger: "Adapter returns AdapterResult(status='error', error_code='adapter_timeout') but gateway returns ExecutionResult(status='failed', error_code='adapter_error')"
    expected_result: "Gateway maps adapter_timeout to ExecutionResult(status='timeout', error_code='adapter_timeout') preserving the exact error_code"
    forbidden_shortcut: "Do not collapse all adapter error codes to a generic adapter_error"
  - name: target_system_none_raises_exception
    trigger: "CapabilitySpec.target_system is None and gateway raises KeyError or AttributeError"
    expected_result: "Gateway returns ExecutionResult(status='no_capability_found', error_code='capability_not_found') without raising"
    forbidden_shortcut: "Do not let dict key lookup raise unhandled exception for missing target_system"

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify all 5 depends_on passed Task Records exist"
    result: "pending"
    command: "$missing = @(); foreach ($dep in @('P0-DOMAIN-003b1','P0-DOMAIN-008b','P0-DOMAIN-008c','P0-DOMAIN-008d','P0-DOMAIN-008e')) { if (-not (Get-ChildItem \"docs/phase0/task_logs/${dep}_*_passed.yaml\" -ErrorAction SilentlyContinue | Select-Object -First 1)) { $missing += $dep } }; if ($missing.Count -gt 0) { throw \"Missing depends_on Task Record(s): $($missing -join ', ')\" } else { 'PASSED' }"
    evidence: ""
  - step: "Verify all 003b0 and 003b1 tests pass before any changes"
    result: "pending"
    command: "uv run pytest tests/infra/gateway/ -v"
    evidence: ""
  - step: "Read existing app/infra/gateway/ code before editing"
    result: "pending"
    command: "Get-ChildItem app/infra/gateway/ -Filter '*.py' -Recurse | Select-Object FullName"
    evidence: ""
  - step: "Add adapter dispatch and error mapping tests (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/infra/gateway/ -v -k 'dispatch or u8 or hikvision or error_mode'"
    evidence: "Expected non-zero exit before implementation."
  - step: "Implement adapter dispatch by target_system and error mode mapping"
    result: "pending"
    command: "Test-Path app/infra/gateway/"
    evidence: ""
  - step: "Run all gateway tests including 003b0/003b1 regression (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/infra/gateway/ -v"
    evidence: ""
  - step: "Run lint and type checks"
    result: "pending"
    command: "uv run ruff check app/infra/gateway/ tests/infra/gateway/; uv run mypy app/infra/gateway/"
    evidence: ""
  - step: "Verify no MockAdapter class imported in gateway production code"
    result: "pending"
    command: "$hits = Get-ChildItem app/infra/gateway/ -Filter '*.py' -Recurse | Select-String -Pattern 'MockOAAdapter|MockU8Adapter|MockHikvision' -ErrorAction SilentlyContinue; if ($hits) { $hits | ForEach-Object { $_.Path + ':' + $_.LineNumber }; throw 'MockAdapter import detected in production code' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify all 6 adapter error modes have gateway-level ErrorCode mappings"
    result: "pending"
    command: "$errorModes = @('adapter_timeout','upstream_permission_denied','adapter_payload_invalid','adapter_empty_response','adapter_http_500','adapter_missing_required_field'); $gwFiles = Get-ChildItem app/infra/gateway/ -Filter '*.py' -Recurse; $content = ($gwFiles | Get-Content -Raw) -join ''; $missing = $errorModes | Where-Object { $content -notmatch [regex]::Escape($_) }; if ($missing) { 'Missing error code mappings: ' + ($missing -join ', '); throw 'Error code mapping incomplete' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify no __init__.py files were created"
    result: "pending"
    command: "$paths = @('app/infra/gateway','tests/infra/gateway'); $hits = foreach ($p in $paths) { if (Test-Path $p) { Get-ChildItem $p -Filter '__init__.py' -Recurse -ErrorAction SilentlyContinue } }; if ($hits) { $hits | ForEach-Object { $_.FullName }; throw '__init__.py detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify no plaintext secret pattern in staged diff"
    result: "pending"
    command: "$secretPattern = '(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|authorization|bearer|cookie|session[_-]?id)\\s*[:=]\\s*[\"'']?[^\"''<\\s]{6,}'; $hits = git diff --cached -U0 | Select-String -Pattern $secretPattern; if ($hits) { 'SECRET SCAN FAIL:'; $hits | ForEach-Object { $_.Line } } else { 'SECRET SCAN: no hits' }"
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
  - "Branch is not phase0/P0-DOMAIN-003b2"
  - "Working tree is dirty at task start"
  - "Any of the 5 depends_on passed Task Records is missing"
  - "Any forbidden path is modified"
  - "MockOAAdapter, MockU8Adapter, or MockHikvisionIVMSAdapter is imported in gateway production code"
  - "target_system=None raises an exception instead of returning error ExecutionResult"
  - "Any adapter error mode is mapped to the wrong ErrorCode"
  - "Real OA/U8/Hik call or real HTTP/browser/subprocess client is introduced"
  - "P0-DOMAIN-003b0 or P0-DOMAIN-003b1 tests are broken by this task's changes"
  - "New Python dependency added"
  - "__init__.py file created in new namespace-package directories"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
```

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm these carried-forward constraints:

1. CapabilityGatewayPort (app/ports/capability_gateway.py) is satisfied exactly; execute_capability returns ExecutionResult on all paths including target_system=None.
2. Adapter dispatch is by CapabilitySpec.target_system only: oa→OA adapter, u8→U8 adapter, hikvision_ivms→Hikvision adapter, None→error ExecutionResult.
3. All three adapters are injected via constructor; gateway production code contains no imports of MockOAAdapter, MockU8Adapter, or MockHikvisionIVMSAdapter.
4. All adapter error injection modes from P0-DOMAIN-008e map to their exact gateway ErrorCode: timeout→adapter_timeout, permission_denied→upstream_permission_denied, malformed_json→adapter_payload_invalid, empty_response→adapter_empty_response, http_500→adapter_http_500, missing_required_field→adapter_missing_required_field.
5. All short-circuit paths from 003b1 (CapabilityRegistry, IdentityMapping, PolicyGuard) are preserved unchanged.
6. All TracePort calls from 003b1 are preserved; no new Trace calls break the existing pattern.
7. No plaintext secret/token/password/cookie/sessionid/access_token/refresh_token in Trace event attributes or ExecutionResult data.
8. No real OA/U8/Hik calls; no HTTP/browser/subprocess imports in gateway production code.
9. app/ports/ files, app/execution_fabric/mock_adapters/, pyproject.toml, and uv.lock are not modified.
10. No new Python dependencies. No __init__.py files in new namespace-package directories.
11. All P0-DOMAIN-003b0 and P0-DOMAIN-003b1 tests pass without modification after this task.

## Structured-output baseline applicability

not_applicable

- reason: This task implements Gateway adapter dispatch only; it does not implement LLM structured output.
- scope: app/infra/gateway/ and tests/infra/gateway/.
- blocked_by_task_id: none.
- activation_task_id: P0-DOMAIN-010b.
- expiry_condition: Structured-output baseline becomes applicable only for LLM provider or structured-output implementation tasks.
- evidence: CapabilityGatewayPort has no LLM provider or structured-output method.
