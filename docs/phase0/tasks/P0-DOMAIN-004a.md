# P0-DOMAIN-004a - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted sections 8.6.2, 8.6.8, and P0-DOMAIN-004a; do not paste or rewrite the full spec.

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
task_id: P0-DOMAIN-004a
branch: "phase0/P0-DOMAIN-004a"
title: Policy Guard Interface Contract
type: interface_contract
depends_on:
  - P0-BATCH3-PROMPTS-001
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
    PolicyGuardPort is a policy/security boundary. Codex owns TDD
    implementation because decisions, reason codes, and manual review outcomes
    must be typed and regression-resistant. Independent review must happen in a
    separate Codex review session.

objective: >
  Define PolicyGuardPort with decide method and PolicyDecision return contract.
  This task defines the abstract policy boundary only. It must not implement
  policy logic, role rules, control-plane behavior, or gateway integration.

structured_output_baseline_applicability: "not_applicable - this task does not implement LLM structured output."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-004a_<timestamp>_passed.yaml"

constraints_to_carry_forward:
  - "Context Assembly minimum input boundary"
  - "Capability Summary injection rules"
  - "Intent to Capability minimum validation path"
  - "structured-output failure Plan B"
  - "no_capability_found terminal state"
  - "clarification_needed terminal state"
  - "validation_failed outcome"
  - "manual_review_needed outcome"
  - "Phase 1 user-value boundary"

deliverable:
  - app/ports/policy_guard.py
  - tests/ports/test_policy_guard_port.py

constraints:
  - Define PolicyGuardPort only.
  - Do not implement strategy, role, approval, or policy evaluation logic.
  - decide method must return PolicyDecision.
  - decision values are limited to allow, deny, and confirm.
  - reason_code coverage must include role_not_allowed, policy_denied, and high_risk_action_requires_confirm.
  - No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values may appear in Trace, ResponseEnvelope, fixtures, logs, reports, or Task Record evidence.

acceptance_criteria:
  - criterion: "decide method signature is defined from spec section 8.6.8"
    result: "pending"
    evidence: ""
  - criterion: "Return structure uses PolicyDecision"
    result: "pending"
    evidence: ""
  - criterion: "decision values are limited to allow, deny, and confirm"
    result: "pending"
    evidence: ""
  - criterion: "reason_code values include role_not_allowed, policy_denied, and high_risk_action_requires_confirm"
    result: "pending"
    evidence: ""
  - criterion: "No policy logic is implemented"
    result: "pending"
    evidence: ""
  - criterion: "Forbidden control_plane/gateway paths are untouched"
    result: "pending"
    evidence: ""

contract_violation_examples:
  - example: "PolicyGuardPort returns bool instead of PolicyDecision"
    expected_result: "Contract tests fail."
  - example: "Policy evaluation logic is implemented in app/ports/policy_guard.py"
    expected_result: "Review fails; only interface/type contracts are allowed."
  - example: "manual_review_needed is replaced by an unrelated outcome"
    expected_result: "Constraint checklist review fails."

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify dependency Task Record exists"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-BATCH3-PROMPTS-001_*_passed.yaml | Select-Object -First 1"
    evidence: ""
  - step: "Create contract tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/ports/test_policy_guard_port.py"
    evidence: "Expected non-zero before app/ports/policy_guard.py is implemented."
  - step: "Implement abstract PolicyGuardPort contract"
    result: "pending"
    command: "Test-Path app/ports/policy_guard.py"
    evidence: ""
  - step: "Run contract tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/ports/test_policy_guard_port.py"
    evidence: ""
  - step: "Run lint/type checks for task-owned files"
    result: "pending"
    command: "uv run ruff check app/ports/policy_guard.py tests/ports/test_policy_guard_port.py; uv run mypy app/ports/policy_guard.py"
    evidence: ""
  - step: "Verify no policy implementation logic was introduced"
    result: "pending"
    command: "$matched = Select-String -Path app/ports/policy_guard.py -Pattern 'if .*role','if .*risk','database','sqlalchemy','requests','httpx' -Quiet; if ($matched) { throw 'Policy implementation detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/ports/test_policy_guard_port.py"
  - "uv run ruff check app/ports/policy_guard.py tests/ports/test_policy_guard_port.py"
  - "uv run mypy app/ports/policy_guard.py"

touched_paths:
  - app/ports/policy_guard.py
  - tests/ports/test_policy_guard_port.py

forbidden_paths:
  - app/control_plane/
  - app/gateway/
  - app/runtime/

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-004a"
  - "Working tree is dirty at task start"
  - "P0-BATCH3-PROMPTS-001 passed Task Record is missing"
  - "Any forbidden path is modified (control_plane, gateway, or runtime)"
  - "Policy evaluation logic is implemented"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
```

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm these carried-forward constraints:

1. Context Assembly minimum input boundary is preserved.
2. Capability Summary injection rules are not implemented or changed in this interface-only task.
3. Intent to Capability minimum validation path is not implemented or changed in this interface-only task.
4. Structured-output failure Plan B remains: raw OpenAI-compatible SDK, `response_format={"type":"json_object"}`, Pydantic `model_validate`, and `Literal[...]` enum validation. This task must not change that baseline.
5. `no_capability_found` terminal state remains available to downstream gateway/runtime tasks; this task must not redefine it.
6. `clarification_needed` terminal state remains available to downstream runtime tasks; this task must not redefine it.
7. `validation_failed` outcome remains available to downstream validation tasks; this task must not redefine it.
8. `manual_review_needed` outcome remains available to downstream policy/identity tasks; this task must not redefine it.
9. Phase 1 user-value boundary is preserved: define contracts only, no full Phase 1 workflow.

## Structured-output baseline applicability

not_applicable - this task does not implement LLM structured output. It must not change the Phase 1 baseline and must not reopen instructor or PydanticAI default decisions.
