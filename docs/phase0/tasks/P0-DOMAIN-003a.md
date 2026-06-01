# P0-DOMAIN-003a - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted sections 8.6.2, 8.6.8, and P0-DOMAIN-003a; do not paste or rewrite the full spec.

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
task_id: P0-DOMAIN-003a
branch: "phase0/P0-DOMAIN-003a"
title: Capability Gateway Interface Contract
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
    CapabilityGatewayPort is a core gateway boundary. Codex owns TDD
    implementation because request_context and ExecutionResult typing determine
    later Runtime/Gateway integration safety. Independent review must happen in
    a separate Codex review session.

objective: >
  Define CapabilityGatewayPort with execute_capability contract using
  RequestOrgContext input and ExecutionResult output. This task defines the
  abstract gateway interface only and must not implement concrete gateway
  execution, runtime orchestration, or adapter calls.

structured_output_baseline_applicability: "not_applicable - this task does not implement LLM structured output."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-003a_<timestamp>_passed.yaml"

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
  - app/ports/capability_gateway.py
  - tests/ports/test_capability_gateway_port.py

constraints:
  - Define abstract interface only.
  - Do not implement concrete execution logic.
  - execute_capability request_context must use RequestOrgContext.
  - Output must use ExecutionResult, not a custom dict.
  - Do not call adapters, policies, identity mapping, trace storage, or runtime code.
  - No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values may appear in Trace, ResponseEnvelope, fixtures, logs, reports, or Task Record evidence.

acceptance_criteria:
  - criterion: "execute_capability method signature is defined from spec section 8.6.8"
    result: "pending"
    evidence: ""
  - criterion: "request_context annotation uses RequestOrgContext"
    result: "pending"
    evidence: ""
  - criterion: "Output annotation uses ExecutionResult and not custom dict"
    result: "pending"
    evidence: ""
  - criterion: "No concrete gateway execution logic is introduced"
    result: "pending"
    evidence: ""
  - criterion: "Forbidden gateway/runtime/execution_fabric paths are untouched"
    result: "pending"
    evidence: ""

contract_violation_examples:
  - example: "execute_capability returns dict instead of ExecutionResult"
    expected_result: "Contract tests fail."
  - example: "Gateway implementation logic is added"
    expected_result: "Review fails; only the abstract port is allowed."
  - example: "Adapter or runtime imports are added"
    expected_result: "Forbidden import/path check fails."

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
    command: "uv run pytest tests/ports/test_capability_gateway_port.py"
    evidence: "Expected non-zero before app/ports/capability_gateway.py is implemented."
  - step: "Implement abstract CapabilityGatewayPort contract"
    result: "pending"
    command: "Test-Path app/ports/capability_gateway.py"
    evidence: ""
  - step: "Run contract tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/ports/test_capability_gateway_port.py"
    evidence: ""
  - step: "Run lint/type checks for task-owned files"
    result: "pending"
    command: "uv run ruff check app/ports/capability_gateway.py tests/ports/test_capability_gateway_port.py; uv run mypy app/ports/capability_gateway.py"
    evidence: ""
  - step: "Verify no concrete gateway logic was introduced"
    result: "pending"
    command: "$matched = Select-String -Path app/ports/capability_gateway.py -Pattern 'requests','httpx','Adapter','PolicyGuard','IdentityMapping','TracePort\\(' -Quiet; if ($matched) { throw 'Concrete gateway dependency detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/ports/test_capability_gateway_port.py"
  - "uv run ruff check app/ports/capability_gateway.py tests/ports/test_capability_gateway_port.py"
  - "uv run mypy app/ports/capability_gateway.py"

touched_paths:
  - app/ports/capability_gateway.py
  - tests/ports/test_capability_gateway_port.py

forbidden_paths:
  - app/gateway/
  - app/runtime/
  - app/execution_fabric/

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-003a"
  - "Working tree is dirty at task start"
  - "P0-BATCH3-PROMPTS-001 passed Task Record is missing"
  - "Any forbidden path is modified"
  - "Concrete gateway execution logic is introduced"
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
