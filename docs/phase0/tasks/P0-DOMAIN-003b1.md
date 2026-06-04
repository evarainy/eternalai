# P0-DOMAIN-003b1 - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- app/ports/capability_gateway.py (primary contract: CapabilityGatewayPort, ExecutionResult, ErrorCode, ExecutionStatus)
- app/ports/policy_guard.py (PolicyGuardPort, PolicyDecision, PolicyDecisionValue)
- app/ports/trace.py (TracePort, TraceEvent, TraceEventType, TraceEventStatus)
- app/ports/identity_mapping.py (IdentityMappingPort, IdentityCheckResult, IdentityBindStatus)
- app/ports/capability_registry.py (CapabilityRegistryPort, CapabilitySpec)
- docs/phase0/tasks/P0-DOMAIN-003b0.md (what this task EXTENDS — read before editing any gateway code)
- app/infra/gateway/ (the 003b0 implementation to extend — read existing code before writing)
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted Gateway short-circuit and P0-DOMAIN-003b1 context; do not paste or rewrite the full spec.

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
task_id: P0-DOMAIN-003b1
branch: "phase0/P0-DOMAIN-003b1"
title: Gateway Short-circuit Skeleton
type: implementation
depends_on:
  - P0-DOMAIN-003b0
  - P0-DOMAIN-002b
  - P0-DOMAIN-004b
  - P0-DOMAIN-005b
  - P0-DOMAIN-006b
  - P0-DOMAIN-011b
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
    Gateway is the critical orchestration path. Adding CapabilityRegistry lookup,
    IdentityMapping precheck, PolicyGuard check, and Trace integration as short-circuit
    steps before the adapter call is architecture-sensitive. TDD required because each
    short-circuit path (no_capability_found, identity failure, policy_denied, confirm_required)
    must be proven independently before 003b2 adds full multi-adapter execution.

objective: >
  Extend app/infra/gateway/ (from P0-DOMAIN-003b0) to add four pre-adapter checks:
  (1) CapabilityRegistry lookup — short-circuit with no_capability_found if capability absent;
  (2) IdentityMapping precheck — short-circuit with identity error if binding not active;
  (3) PolicyGuard check — short-circuit with policy_denied or confirm_required if not allowed;
  (4) Trace write — record gateway_pre_recorded before adapter call and gateway_post_recorded
  after, on both success and short-circuit paths.
  The adapter is called only when all three pre-checks pass. All 003b0 pass-through tests
  must remain passing. This task does not implement the final multi-adapter execution
  path (deferred to 003b2) and must not call real OA/U8/Hik systems.

structured_output_baseline_applicability: "not_applicable"
structured_output_baseline_not_applicable:
  reason: "This task implements Gateway short-circuit orchestration only; it does not implement LLM structured output."
  scope: "app/infra/gateway/ and tests/infra/gateway/"
  blocked_by_task_id: "none"
  activation_task_id: "P0-DOMAIN-010b"
  expiry_condition: "Structured-output baseline becomes applicable only in LLM provider or structured-output implementation tasks."
  evidence: "CapabilityGatewayPort has no LLM provider or structured-output method."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-003b1_<timestamp>_passed.yaml"

constraints_to_carry_forward:
  - "CapabilityGatewayPort contract (app/ports/capability_gateway.py) must be satisfied exactly; do not modify app/ports/."
  - "execute_capability returns ExecutionResult(status, data, error_code, trace_id); all short-circuit paths must return ExecutionResult."
  - "Short-circuit order is fixed: CapabilityRegistry.get → IdentityMapping.resolve_execution_identity → PolicyGuard.decide → Trace pre-record → Adapter → Trace post-record."
  - "Adapter must NOT be called if any pre-check short-circuits."
  - "CapabilityRegistry.get returns None for unknown capability_id → short-circuit ExecutionResult(status='no_capability_found', error_code='capability_not_found')."
  - "IdentityCheckResult.bind_status not 'active' → short-circuit ExecutionResult with matching ErrorCode: unbound→identity_unbound, expired→identity_expired, revoked→identity_revoked, needs_binding_scope→needs_binding_scope, verification_failed→identity_unbound (Phase 0 treats verification_failed as effectively unbound; Phase 1 may distinguish this)."
  - "PolicyDecision.decision='deny' → short-circuit ExecutionResult(status='denied', error_code='policy_denied')."
  - "PolicyDecision.decision='confirm' → short-circuit ExecutionResult(status='waiting_user', error_code='confirm_required')."
  - "TracePort.record_gateway_call must be called with status='ok' before the adapter call (gateway_pre_recorded phase)."
  - "TracePort.finalize_task_trace must be called after the adapter returns (or after any short-circuit) to record the final outcome."
  - "TracePort must NEVER receive plaintext secret, token, password, cookie, sessionid, access_token, or refresh_token values in event attributes."
  - "CapabilitySpec.target_system and CapabilitySpec.execution_identity must be passed to IdentityMapping.resolve_execution_identity."
  - "No real OA/U8/Hik calls; no HTTP client imports (requests/httpx/aiohttp); no subprocess/playwright/selenium."
  - "No new Python dependencies; no __init__.py files."
  - "All P0-DOMAIN-003b0 tests must remain passing after this task's changes."

deliverable:
  - app/infra/gateway/
  - tests/infra/gateway/

constraints:
  - Extend app/infra/gateway/ in-place — do not create a new parallel gateway implementation.
  - Read the existing 003b0 gateway code before making any changes; preserve the pass-through path.
  - Inject CapabilityRegistryPort, IdentityMappingPort, PolicyGuardPort, and TracePort into the gateway via constructor; MockOAAdapter injection stays from 003b0.
  - Constructor parameter order must be (adapter, capability_registry, identity_mapping, policy_guard, trace_port) to maintain backward compatibility with 003b0's single-adapter constructor signature; do not reorder existing parameters.
  - Do not call real OA/U8/Hik systems; the MockOAAdapter is still used for the adapter path.
  - Do not modify app/execution_fabric/mock_adapters/.
  - Do not modify app/ports/ files.
  - If CapabilitySpec.target_system is None, skip the IdentityMapping precheck and proceed directly to PolicyGuard.decide with target_system=None; do not raise an exception or return an error ExecutionResult for this case in 003b1 — target_system=None error handling is added in 003b2.
  - Do not add Alembic migrations or database schema changes.
  - Do not add new Python dependencies.
  - Tests for async methods must use asyncio.run() in synchronous test functions; pytest-asyncio is not installed and cannot be added as a new dependency.
  - No __init__.py files.
  - Test each short-circuit path independently: (1) unknown capability_id, (2) identity binding failure, (3) policy_denied, (4) confirm_required, (5) happy-path through all checks.
  - All 003b0 tests must still pass after this task's changes.

acceptance_criteria:
  - criterion: "CapabilityRegistry.get short-circuits with ExecutionResult(status='no_capability_found', error_code='capability_not_found') for unknown capability_id; adapter is not called"
    result: "pending"
    evidence: ""
  - criterion: "IdentityMapping.resolve_execution_identity short-circuits with matching ErrorCode (identity_unbound/identity_expired/identity_revoked/needs_binding_scope) for non-active bind_status; adapter is not called"
    result: "pending"
    evidence: ""
  - criterion: "PolicyGuard.decide='deny' short-circuits with ExecutionResult(status='denied', error_code='policy_denied'); adapter is not called"
    result: "pending"
    evidence: ""
  - criterion: "PolicyGuard.decide='confirm' short-circuits with ExecutionResult(status='waiting_user', error_code='confirm_required'); adapter is not called"
    result: "pending"
    evidence: ""
  - criterion: "TracePort.record_gateway_call is called before the adapter call on the happy path; TracePort.finalize_task_trace is called on all paths including short-circuit paths"
    result: "pending"
    evidence: ""
  - criterion: "On short-circuit paths (no_capability_found, identity failure, policy_denied, confirm_required), TracePort.record_gateway_call is NOT called; only TracePort.finalize_task_trace is called to record the final outcome"
    result: "pending"
    evidence: ""
  - criterion: "Happy-path end-to-end: CapabilityRegistry returns CapabilitySpec, IdentityMapping returns active binding, PolicyGuard returns allow, adapter executes and returns ExecutionResult(status='completed')"
    result: "pending"
    evidence: ""
  - criterion: "All P0-DOMAIN-003b0 pass-through tests still pass after this task's changes"
    result: "pending"
    evidence: ""
  - criterion: "No plaintext secret/token/password value appears in TracePort event attributes in test fixtures or production code"
    result: "pending"
    evidence: ""

failure_examples:
  - name: adapter_called_on_policy_denied
    trigger: "PolicyGuard.decide returns PolicyDecision(decision='deny') but gateway continues to call the adapter"
    expected_result: "Gateway returns ExecutionResult(status='denied', error_code='policy_denied') without calling the adapter"
    forbidden_shortcut: "Do not call adapter after policy denial and discard the result — the adapter must not be called at all"
  - name: trace_skipped_on_short_circuit
    trigger: "IdentityMapping precheck fails but TracePort.finalize_task_trace is not called"
    expected_result: "finalize_task_trace is called with status='blocked' and the appropriate error_code on every short-circuit path"
    forbidden_shortcut: "Do not skip TracePort calls for error paths to simplify implementation"
  - name: capability_not_found_silent
    trigger: "CapabilityRegistry.get returns None but gateway falls through to the adapter call with capability_id=None"
    expected_result: "Gateway immediately returns ExecutionResult(status='no_capability_found', error_code='capability_not_found') without calling the adapter"
    forbidden_shortcut: "Do not pass None capability_id to the adapter and let the adapter raise an error"
  - name: wrong_error_code_for_identity_status
    trigger: "IdentityCheckResult.bind_status='expired' but gateway returns error_code='identity_unbound'"
    expected_result: "Each IdentityBindStatus maps to its corresponding ErrorCode: unbound→identity_unbound, expired→identity_expired, revoked→identity_revoked, needs_binding_scope→needs_binding_scope, verification_failed→identity_unbound"
    forbidden_shortcut: "Do not use a single generic identity_unbound for all non-active bind_status values"

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify all 6 depends_on passed Task Records exist"
    result: "pending"
    command: "$missing = @(); foreach ($dep in @('P0-DOMAIN-003b0','P0-DOMAIN-002b','P0-DOMAIN-004b','P0-DOMAIN-005b','P0-DOMAIN-006b','P0-DOMAIN-011b')) { if (-not (Get-ChildItem \"docs/phase0/task_logs/${dep}_*_passed.yaml\" -ErrorAction SilentlyContinue | Select-Object -First 1)) { $missing += $dep } }; if ($missing.Count -gt 0) { throw \"Missing depends_on Task Record(s): $($missing -join ', ')\" } else { 'PASSED' }"
    evidence: ""
  - step: "Verify existing 003b0 pass-through tests pass before any changes"
    result: "pending"
    command: "uv run pytest tests/infra/gateway/ -v"
    evidence: ""
  - step: "Read existing app/infra/gateway/ code before editing"
    result: "pending"
    command: "Get-ChildItem app/infra/gateway/ -Filter '*.py' -Recurse | Select-Object FullName"
    evidence: ""
  - step: "Add short-circuit tests for each new path (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/infra/gateway/ -v -k 'short_circuit or no_capability or identity or policy'"
    evidence: "Expected non-zero exit before implementation."
  - step: "Implement short-circuit logic (CapabilityRegistry → Identity → Policy → Trace pre → Adapter → Trace post)"
    result: "pending"
    command: "Test-Path app/infra/gateway/"
    evidence: ""
  - step: "Run all gateway tests including 003b0 regression (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/infra/gateway/ -v"
    evidence: ""
  - step: "Run lint and type checks"
    result: "pending"
    command: "uv run ruff check app/infra/gateway/ tests/infra/gateway/; uv run mypy app/infra/gateway/"
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
  - "Branch is not phase0/P0-DOMAIN-003b1"
  - "Working tree is dirty at task start"
  - "Any of the 6 depends_on passed Task Records is missing"
  - "Any forbidden path is modified"
  - "Adapter is called after a policy_denied or identity short-circuit"
  - "TracePort.finalize_task_trace is not called on short-circuit paths"
  - "Plaintext secret/token value appears in TracePort event attributes"
  - "Real OA/U8/Hik call or real HTTP/browser/subprocess client is introduced"
  - "app/ports/ or app/execution_fabric/mock_adapters/ is modified"
  - "P0-DOMAIN-003b0 pass-through tests are broken by this task's changes"
  - "New Python dependency added"
  - "__init__.py file created in new namespace-package directories"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
```

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm these carried-forward constraints:

1. CapabilityGatewayPort (app/ports/capability_gateway.py) is satisfied exactly; execute_capability returns ExecutionResult on all paths.
2. Short-circuit order is enforced: CapabilityRegistry → IdentityMapping → PolicyGuard → Trace pre-record → Adapter → Trace post-record. No step is skipped or reordered.
3. Adapter is NOT called on any short-circuit path (no_capability_found, identity failure, policy_denied, confirm_required).
4. Each IdentityBindStatus value maps to the correct ErrorCode: unbound→identity_unbound, expired→identity_expired, revoked→identity_revoked, needs_binding_scope→needs_binding_scope, verification_failed→identity_unbound.
5. TracePort is called on ALL execution paths, including every short-circuit path — finalize_task_trace must always be reached.
6. TracePort sanitizer constraint from P0-DOMAIN-005b and P0-DOMAIN-011b: no plaintext secret/token/password/cookie/sessionid/access_token/refresh_token in event attributes.
7. All P0-DOMAIN-003b0 pass-through tests remain passing; this task only extends the existing gateway code.
8. No real OA/U8/Hik calls; no HTTP/browser/subprocess imports in gateway code.
9. app/ports/ files, app/execution_fabric/mock_adapters/, pyproject.toml, and uv.lock are not modified.
10. No new Python dependencies. No __init__.py files in new namespace-package directories.

## Structured-output baseline applicability

not_applicable

- reason: This task implements Gateway short-circuit orchestration only; it does not implement LLM structured output.
- scope: app/infra/gateway/ and tests/infra/gateway/.
- blocked_by_task_id: none.
- activation_task_id: P0-DOMAIN-010b.
- expiry_condition: Structured-output baseline becomes applicable only for LLM provider or structured-output implementation tasks.
- evidence: CapabilityGatewayPort has no LLM provider or structured-output method.
