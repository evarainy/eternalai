# P0-DOMAIN-006a - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted sections 8.6.2, 8.6.8, and P0-DOMAIN-006a; do not paste or rewrite the full spec.

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
task_id: P0-DOMAIN-006a
branch: "phase0/P0-DOMAIN-006a"
title: IdentityMapping Interface Contract
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
    IdentityMappingPort controls execution identity and binding checks. Codex
    owns TDD implementation because credential-safety and multi-scope lookup
    contracts are security-sensitive. Independent review must happen in a
    separate Codex review session.

objective: >
  Define IdentityMappingPort with resolve_execution_identity and optional mapping
  query/status methods. This task defines abstract identity mapping interfaces
  only; it must not implement credential storage, control-plane behavior, gateway
  behavior, or real identity provider integration.

structured_output_baseline_applicability: "not_applicable - this task does not implement LLM structured output."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-006a_<timestamp>_passed.yaml"

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
  - app/ports/identity_mapping.py
  - tests/ports/test_identity_mapping_port.py

constraints:
  - Define IdentityMappingPort only.
  - Do not implement credential storage, OAuth, Vault, browser session handling, or target-system login.
  - resolve_execution_identity method signature must be defined from spec section 8.6.8.
  - Optional methods may include get_mapping, list_mappings, set_mock_mapping, update_status, and check_binding_required.
  - Queries must support target_system, binding_scope, account_set_id, and device_domain_id.
  - Return structure must use IdentityCheckResult and must not contain plaintext credentials or token values.
  - No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values may appear in Trace, ResponseEnvelope, fixtures, logs, reports, or Task Record evidence.

acceptance_criteria:
  - criterion: "resolve_execution_identity method signature is defined from spec section 8.6.8"
    result: "pending"
    evidence: ""
  - criterion: "Optional mapping/status methods are defined if needed by the contract tests"
    result: "pending"
    evidence: ""
  - criterion: "Query parameters support target_system, binding_scope, account_set_id, and device_domain_id"
    result: "pending"
    evidence: ""
  - criterion: "Return structure uses IdentityCheckResult and does not expose plaintext credentials or token values"
    result: "pending"
    evidence: ""
  - criterion: "No credential storage implementation is introduced"
    result: "pending"
    evidence: ""
  - criterion: "Forbidden control_plane/gateway paths are untouched"
    result: "pending"
    evidence: ""

contract_violation_examples:
  - example: "IdentityCheckResult includes plaintext credential or token value fields"
    expected_result: "Contract tests and secret scan fail."
  - example: "Only 1:1 user mapping is represented and binding_scope/account_set_id/device_domain_id are omitted"
    expected_result: "Contract tests fail."
  - example: "Credential storage implementation is added"
    expected_result: "Review fails; storage is out of scope."

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify dependency Task Record exists"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-BATCH3-PROMPTS-001_*_passed.yaml | Select-Object -First 1"
    evidence: ""
  - step: "Create identity mapping contract tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/ports/test_identity_mapping_port.py"
    evidence: "Expected non-zero before app/ports/identity_mapping.py is implemented."
  - step: "Implement abstract IdentityMappingPort contract"
    result: "pending"
    command: "Test-Path app/ports/identity_mapping.py"
    evidence: ""
  - step: "Run contract tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/ports/test_identity_mapping_port.py"
    evidence: ""
  - step: "Run lint/type checks for task-owned files"
    result: "pending"
    command: "uv run ruff check app/ports/identity_mapping.py tests/ports/test_identity_mapping_port.py; uv run mypy app/ports/identity_mapping.py"
    evidence: ""
  - step: "Verify no credential storage implementation was introduced"
    result: "pending"
    command: "$matched = Select-String -Path app/ports/identity_mapping.py -Pattern 'Vault','OAuth','sqlalchemy','redis','requests','httpx','open\\(' -Quiet; if ($matched) { throw 'Credential storage or provider implementation detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify no plaintext credential values are staged"
    result: "pending"
    command: "if (git diff --cached -U0 | Select-String -Pattern '(?i)(password|token|cookie|sessionid|access_token|refresh_token|api_key|private_key)\\s*[:=]\\s*[\"''][^\"''<][^\"''\\s]{6,}' -Quiet) { throw 'Possible plaintext secret value detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/ports/test_identity_mapping_port.py"
  - "uv run ruff check app/ports/identity_mapping.py tests/ports/test_identity_mapping_port.py"
  - "uv run mypy app/ports/identity_mapping.py"

touched_paths:
  - app/ports/identity_mapping.py
  - tests/ports/test_identity_mapping_port.py

forbidden_paths:
  - app/control_plane/
  - app/gateway/

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-006a"
  - "Working tree is dirty at task start"
  - "P0-BATCH3-PROMPTS-001 passed Task Record is missing"
  - "Any forbidden path is modified"
  - "Credential storage/provider implementation is introduced"
  - "Plaintext credential-like values are staged"
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
