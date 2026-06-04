# P0-DOMAIN-004b - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- app/ports/policy_guard.py (primary PolicyGuardPort and PolicyDecision contract; read before writing any policy implementation code)
- docs/phase0/tasks/P0-DOMAIN-004a.md (carried-forward interface contract and policy/security boundary constraints)
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted sections 8.6.8 and P0-DOMAIN-004b; do not paste or rewrite the full spec.

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
task_id: P0-DOMAIN-004b
branch: "phase0/P0-DOMAIN-004b"
title: Policy Guard Minimal Deny Skeleton
type: implementation
depends_on:
  - P0-DOMAIN-004a
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
    PolicyGuard is a policy/security boundary and a downstream Gateway
    short-circuit dependency. Codex owns TDD because the allow, deny, confirm,
    and reason-code behavior must be pinned by tests before Gateway integration
    consumes the PolicyDecision contract.

objective: >
  Implement a minimal pure-logic PolicyGuard that satisfies PolicyGuardPort,
  returns PolicyDecision for allow/deny decisions, can deny unsafe or incomplete
  requests with a reason_code, and provides enough denied-decision behavior for
  downstream Gateway short-circuit tests. This task has no database dependency
  and must not implement a full RBAC/ABAC engine, approval flow, Gateway
  integration, Trace writing, Adapter calls, or ResponseEnvelope generation.

structured_output_baseline_applicability: "not_applicable"

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-004b_<timestamp>_passed.yaml"

constraints_to_carry_forward:
  - "PolicyGuardPort contract (app/ports/policy_guard.py) must be satisfied exactly: decide is async and returns PolicyDecision, never bare bool, string, or dict"
  - "PolicyDecision is a Pydantic model with extra='forbid' and fields decision, reason_code, and required_action"
  - "PolicyDecision.decision values are limited to allow, deny, and confirm"
  - "DENY decisions must return PolicyDecision(decision='deny') with a non-empty reason_code such as role_not_allowed or policy_denied"
  - "ALLOW decisions must return PolicyDecision(decision='allow'), never a bare success flag"
  - "Confirm remains a valid Protocol outcome: high-risk confirmation, if represented, must use decision='confirm', reason_code='high_risk_action_requires_confirm', required_action='confirm'"
  - "Context Assembly, Capability Summary injection, Intent-to-Capability validation, no_capability_found, clarification_needed, validation_failed, and manual_review_needed outcomes remain downstream contracts and must not be redefined here"
  - "No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values may appear in fixtures, logs, reports, or Task Record evidence"
  - "No new Python dependencies; no __init__.py (namespace packages)"
  - "Phase 1 user-value boundary: minimal Phase 0 policy deny skeleton only, no full RBAC/ABAC, approval workflow, control plane, or Gateway integration"

deliverable:
  - app/infra/policy/
  - tests/infra/policy/

constraints:
  - Implement a minimal PolicyGuard implementation satisfying PolicyGuardPort.decide.
  - Implementation must live under app/infra/policy/ and tests must live under tests/infra/policy/.
  - PolicyGuard is pure logic: no database, no HTTP, no subprocess, no filesystem persistence, and no external policy engine.
  - Support a deterministic allow path for safe requests.
  - Support deterministic deny paths for forbidden role/capability combinations and missing or invalid policy context.
  - DENY must return PolicyDecision with decision='deny' and a meaningful reason_code; do not raise an unhandled exception for policy denial.
  - ALLOW must return PolicyDecision with decision='allow'; do not return True, 'allow', or a dict.
  - The confirm path MUST be implemented because P0-DOMAIN-003b1 Gateway short-circuit depends on PolicyDecision(decision='confirm'); return PolicyDecision(decision='confirm', reason_code='high_risk_action_requires_confirm', required_action='confirm') for the designated high-risk pattern; do not implement an approval workflow.
  - Phase 0 minimal rule skeleton (implement at minimum): (1) arguments is None → deny(reason_code='policy_denied'); (2) capability_id.startswith('admin_') → deny(reason_code='role_not_allowed'); (3) capability_id.endswith('_confirm') → confirm(reason_code='high_risk_action_requires_confirm', required_action='confirm'); (4) otherwise → allow. These rules define testable TDD inputs and are sufficient for Gateway short-circuit integration.
  - This task must not call Adapter, write Trace, generate ResponseEnvelope, or modify Gateway behavior; Gateway denial short-circuit integration is downstream.
  - Do not add new Python dependencies.
  - No __init__.py (namespace packages throughout).
  - Do not modify app/ports/policy_guard.py or any other port file.
  - Do not modify app/runtime/, app/infra/gateway/, pyproject.toml, or uv.lock.

acceptance_criteria:
  - criterion: "Minimal PolicyGuard satisfies PolicyGuardPort Protocol (duck-type check: has async decide method with the required signature)"
    result: "pending"
    evidence: ""
  - criterion: "Safe request path returns PolicyDecision(decision='allow') and never returns bare bool, string, or dict"
    result: "pending"
    evidence: ""
  - criterion: "Forbidden role/capability path returns PolicyDecision(decision='deny', reason_code='role_not_allowed')"
    result: "pending"
    evidence: ""
  - criterion: "Missing or invalid policy context returns PolicyDecision(decision='deny', reason_code='policy_denied') instead of raising an unhandled exception"
    result: "pending"
    evidence: ""
  - criterion: "Every DENY result has a non-empty reason_code"
    result: "pending"
    evidence: ""
  - criterion: "High-risk confirmation MUST be implemented (not optional); returns PolicyDecision(decision='confirm', reason_code='high_risk_action_requires_confirm', required_action='confirm') for the designated high-risk input pattern without implementing approval flow"
    result: "pending"
    evidence: ""
  - criterion: "DENY result is directly consumable by downstream Gateway short-circuit tests: this task returns the decision object only and does not call Adapter, write Trace, or generate ResponseEnvelope"
    result: "pending"
    evidence: ""
  - criterion: "No database, HTTP client, subprocess, external policy engine, new dependency, or __init__.py is introduced"
    result: "pending"
    evidence: ""

failure_examples:
  - name: forbidden_operation_allows
    trigger: "request_context role or policy context indicates the user is not allowed to execute the capability"
    expected_result: "PolicyDecision(decision='deny', reason_code='role_not_allowed')"
    forbidden_shortcut: "Returning allow and relying on Gateway to re-check policy"
  - name: deny_missing_reason
    trigger: "PolicyGuard denies a request"
    expected_result: "PolicyDecision(decision='deny') with a non-empty reason_code such as role_not_allowed or policy_denied"
    forbidden_shortcut: "Returning PolicyDecision(decision='deny', reason_code=None)"
  - name: missing_policy_context_raises
    trigger: "Required request_context or policy input is missing or malformed"
    expected_result: "PolicyDecision(decision='deny', reason_code='policy_denied')"
    forbidden_shortcut: "Raising an unhandled KeyError, AttributeError, ValueError, or returning a bare string"
  - name: high_risk_confirm_collapsed
    trigger: "Policy context marks a capability as high-risk and confirmation-required"
    expected_result: "PolicyDecision(decision='confirm', reason_code='high_risk_action_requires_confirm', required_action='confirm')"
    forbidden_shortcut: "Treating confirm as allow or deny, or implementing a full approval workflow in this task"

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify P0-DOMAIN-004a passed Task Record exists"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-DOMAIN-004a_*_passed.yaml | Select-Object -First 1"
    evidence: ""
  - step: "Create policy guard tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/infra/policy/"
    evidence: "Expected non-zero exit before implementation exists."
  - step: "Implement minimal pure-logic PolicyGuard"
    result: "pending"
    command: "Test-Path app/infra/policy/"
    evidence: ""
  - step: "Run policy guard tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/infra/policy/ -v"
    evidence: ""
  - step: "Run port contract regression test"
    result: "pending"
    command: "uv run pytest tests/ports/test_policy_guard_port.py -v"
    evidence: ""
  - step: "Run lint and type checks"
    result: "pending"
    command: "uv run ruff check app/infra/policy/ tests/infra/policy/; uv run mypy app/infra/policy/"
    evidence: ""
  - step: "Verify pure-logic boundary"
    result: "pending"
    command: "$files = Get-ChildItem app/infra/policy/ tests/infra/policy/ -Filter '*.py' -Recurse; $hits = $files | Select-String -Pattern 'sqlalchemy','psycopg','asyncpg','requests','httpx','aiohttp','subprocess','os\\.system','casbin','openpolicyagent'; if ($hits) { $hits | ForEach-Object { \"$($_.Path):$($_.LineNumber):$($_.Line)\" }; throw 'Forbidden integration import or call detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify no __init__.py files were created"
    result: "pending"
    command: "$created = Get-ChildItem app/infra/policy/,tests/infra/policy/ -Filter '__init__.py' -Recurse -ErrorAction SilentlyContinue; if ($created) { $created.FullName; throw '__init__.py detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "$forbidden = @('app/ports/','app/runtime/','app/infra/gateway/','pyproject.toml','uv.lock'); $changed = git diff --cached --name-only; $violations = $changed | Where-Object { $path = $_; $forbidden | Where-Object { $path -eq $_ -or $path.StartsWith($_) } }; if ($violations) { $violations; throw 'Forbidden path staged' } else { 'PASSED' }"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/infra/policy/ tests/ports/test_policy_guard_port.py -v"
  - "uv run ruff check app/infra/policy/ tests/infra/policy/"
  - "uv run mypy app/infra/policy/"

touched_paths:
  - app/infra/policy/
  - tests/infra/policy/

forbidden_paths:
  - app/ports/
  - app/runtime/
  - app/infra/gateway/
  - pyproject.toml
  - uv.lock

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-004b"
  - "Working tree is dirty at task start"
  - "P0-DOMAIN-004a passed Task Record is missing"
  - "Any forbidden path is modified"
  - "New Python dependency is added"
  - "Database, HTTP, subprocess, filesystem persistence, or external policy engine is introduced"
  - "__init__.py is created"
  - "DENY returns bare bool, string, dict, or PolicyDecision without reason_code"
  - "ALLOW returns bare bool, string, or dict instead of PolicyDecision"
  - "Gateway, Runtime, Adapter, Trace, or ResponseEnvelope behavior is implemented in this task"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
```

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm these carried-forward constraints:

1. PolicyGuardPort contract (app/ports/policy_guard.py) is satisfied exactly: decide is async and returns PolicyDecision.
2. PolicyDecision values remain limited to allow, deny, and confirm; this task does not modify the port.
3. DENY returns PolicyDecision with a non-empty reason_code, never a bare bool, string, or dict.
4. ALLOW returns PolicyDecision, never a bare bool, string, or dict.
5. Any confirm behavior is minimal and typed only; no approval workflow or control-plane behavior is implemented.
6. PolicyGuard is pure logic: no database, HTTP, subprocess, filesystem persistence, or external policy engine.
7. Gateway short-circuit integration remains downstream; this task does not call Adapter, write Trace, generate ResponseEnvelope, or modify Gateway/Runtime.
8. No new Python dependencies. No __init__.py files. No plaintext credential-like values in fixtures, logs, reports, or Task Record evidence.
9. Phase 1 user-value boundary is preserved: minimal Phase 0 deny skeleton only, not a full RBAC/ABAC engine.

## Structured-output baseline applicability

not_applicable - this task does not implement LLM structured output. It must not change the Phase 1 baseline and must not reopen instructor or PydanticAI default decisions.
