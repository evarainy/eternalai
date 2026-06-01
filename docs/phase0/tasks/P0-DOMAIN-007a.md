# P0-DOMAIN-007a - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted sections 8.6.2, 8.6.4, 8.6.8, and P0-DOMAIN-007a; do not paste or rewrite the full spec.

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
task_id: P0-DOMAIN-007a
branch: "phase0/P0-DOMAIN-007a"
title: Runtime Interface Contract
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
    RuntimePort is a core runtime boundary. Codex owns TDD implementation
    because handle_user_message input/output typing controls the later main
    chain and user-value boundary. Independent review must happen in a separate
    Codex review session.

objective: >
  Define RuntimePort with handle_user_message contract. The input must include
  channel, ai_user_id, session_id, message, and client_capabilities, and the
  output must use ResponseEnvelope. This task defines an abstract runtime
  interface only; it must not implement business logic, gateway calls, or runtime
  orchestration.

structured_output_baseline_applicability: "not_applicable - this task does not implement LLM structured output."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-007a_<timestamp>_passed.yaml"

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
  - app/ports/runtime.py
  - tests/ports/test_runtime_port.py

constraints:
  - Define RuntimePort only.
  - Do not implement business logic, context assembly, LLM calls, gateway calls, adapter calls, policy checks, identity checks, trace writes, or session persistence.
  - handle_user_message method signature must be defined from spec section 8.6.8.
  - Input must include channel, ai_user_id, session_id, message, and client_capabilities.
  - Output must use ResponseEnvelope.
  - No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values may appear in Trace, ResponseEnvelope, fixtures, logs, reports, or Task Record evidence.

acceptance_criteria:
  - criterion: "handle_user_message method signature is defined from spec section 8.6.8"
    result: "pending"
    evidence: ""
  - criterion: "Input includes channel, ai_user_id, session_id, message, and client_capabilities"
    result: "pending"
    evidence: ""
  - criterion: "Output annotation uses ResponseEnvelope"
    result: "pending"
    evidence: ""
  - criterion: "No runtime business logic is implemented"
    result: "pending"
    evidence: ""
  - criterion: "Forbidden runtime/gateway paths are untouched"
    result: "pending"
    evidence: ""

contract_violation_examples:
  - example: "RuntimePort returns dict instead of ResponseEnvelope"
    expected_result: "Contract tests fail."
  - example: "handle_user_message omits client_capabilities"
    expected_result: "Contract tests fail."
  - example: "Runtime orchestration logic is implemented"
    expected_result: "Review fails; only the interface contract is allowed."

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify dependency Task Record exists"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-BATCH3-PROMPTS-001_*_passed.yaml | Select-Object -First 1"
    evidence: ""
  - step: "Create runtime contract tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/ports/test_runtime_port.py"
    evidence: "Expected non-zero before app/ports/runtime.py is implemented."
  - step: "Implement abstract RuntimePort contract"
    result: "pending"
    command: "Test-Path app/ports/runtime.py"
    evidence: ""
  - step: "Run contract tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/ports/test_runtime_port.py"
    evidence: ""
  - step: "Run lint/type checks for task-owned files"
    result: "pending"
    command: "uv run ruff check app/ports/runtime.py tests/ports/test_runtime_port.py; uv run mypy app/ports/runtime.py"
    evidence: ""
  - step: "Verify no runtime implementation logic was introduced"
    result: "pending"
    command: "$matched = Select-String -Path app/ports/runtime.py -Pattern 'OpenAI','CapabilityGateway','TaskStore','TracePort\\(','PolicyGuard','IdentityMapping','requests','httpx' -Quiet; if ($matched) { throw 'Runtime implementation dependency detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/ports/test_runtime_port.py"
  - "uv run ruff check app/ports/runtime.py tests/ports/test_runtime_port.py"
  - "uv run mypy app/ports/runtime.py"

touched_paths:
  - app/ports/runtime.py
  - tests/ports/test_runtime_port.py

forbidden_paths:
  - app/runtime/
  - app/gateway/

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-007a"
  - "Working tree is dirty at task start"
  - "P0-BATCH3-PROMPTS-001 passed Task Record is missing"
  - "Any forbidden path is modified"
  - "Runtime business logic is implemented"
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
