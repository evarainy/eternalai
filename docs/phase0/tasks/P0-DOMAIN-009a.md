# P0-DOMAIN-009a - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted sections 8.6.2, 8.6.4, 8.6.8, and P0-DOMAIN-009a; do not paste or rewrite the full spec.

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
task_id: P0-DOMAIN-009a
branch: "phase0/P0-DOMAIN-009a"
title: SDUI Response Envelope Contract
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
    ResponseEnvelope and SDUI contracts define user-facing output boundaries.
    Codex owns TDD implementation because terminal cards, one-shot confirmation,
    and action payload constraints must be asserted before frontend/runtime work.
    Independent review must happen in a separate Codex review session.

objective: >
  Define minimal ResponseEnvelope, UIComponent, confirm_card,
  operator_handback_card, binding_required_card, and user_action return
  contracts for Phase 0 static JSON Schema rendering. This task must not
  implement a renderer, dynamic form orchestrator, or multi-turn confirmation
  state machine.

structured_output_baseline_applicability: "not_applicable - this task does not implement LLM structured output."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-009a_<timestamp>_passed.yaml"

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
  - app/ports/response_envelope.py
  - app/contracts/sdui/
  - tests/contracts/sdui/test_response_envelope_contract.py

constraints:
  - Define minimal protocol and types only.
  - Do not implement a full renderer.
  - Phase 0 supports static JSON Schema rendering only.
  - Do not implement a multi-turn confirmation loop.
  - Do not implement a dynamic form orchestrator.
  - Define ResponseEnvelope and UIComponent models from spec section 8.6.4.
  - Define confirm_card, operator_handback_card, binding_required_card, and user_action return structure.
  - confirm_card action is limited to one-shot confirm and must not create a multi-turn state machine.
  - No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values may appear in Trace, ResponseEnvelope, fixtures, logs, reports, or Task Record evidence.

acceptance_criteria:
  - criterion: "ResponseEnvelope and UIComponent models are defined from spec section 8.6.4"
    result: "pending"
    evidence: ""
  - criterion: "confirm_card is defined"
    result: "pending"
    evidence: ""
  - criterion: "operator_handback_card and binding_required_card are defined"
    result: "pending"
    evidence: ""
  - criterion: "user_action return structure is defined"
    result: "pending"
    evidence: ""
  - criterion: "confirm_card action is one-shot confirm only"
    result: "pending"
    evidence: ""
  - criterion: "No renderer, dynamic form orchestrator, or multi-turn confirmation state machine is introduced"
    result: "pending"
    evidence: ""
  - criterion: "Forbidden web renderer path is untouched"
    result: "pending"
    evidence: ""

contract_violation_examples:
  - example: "web/src/sdui_renderer is created or modified"
    expected_result: "Forbidden path check fails."
  - example: "confirm_card includes multi-step confirmation state"
    expected_result: "Contract tests fail."
  - example: "ResponseEnvelope fixtures contain plaintext credential-like values"
    expected_result: "Secret scan or review fails."

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify dependency Task Record exists"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-BATCH3-PROMPTS-001_*_passed.yaml | Select-Object -First 1"
    evidence: ""
  - step: "Create SDUI contract tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/contracts/sdui/test_response_envelope_contract.py"
    evidence: "Expected non-zero before app/ports/response_envelope.py and app/contracts/sdui/ are implemented."
  - step: "Implement minimal ResponseEnvelope and SDUI contract files"
    result: "pending"
    command: "Test-Path app/ports/response_envelope.py; Test-Path app/contracts/sdui"
    evidence: ""
  - step: "Run contract tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/contracts/sdui/test_response_envelope_contract.py"
    evidence: ""
  - step: "Run lint/type checks for task-owned files"
    result: "pending"
    command: "uv run ruff check app/ports/response_envelope.py app/contracts/sdui/ tests/contracts/sdui/test_response_envelope_contract.py; uv run mypy app/ports/response_envelope.py app/contracts/sdui/"
    evidence: ""
  - step: "Verify no renderer or dynamic orchestrator was introduced"
    result: "pending"
    command: "if (git diff --cached --name-only | Select-String -Pattern '^web/src/sdui_renderer/','dynamic_form','renderer' -Quiet) { throw 'Renderer or dynamic orchestrator path detected' } else { 'PASSED' }"
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
  - "uv run pytest tests/contracts/sdui/test_response_envelope_contract.py"
  - "uv run ruff check app/ports/response_envelope.py app/contracts/sdui/ tests/contracts/sdui/test_response_envelope_contract.py"
  - "uv run mypy app/ports/response_envelope.py app/contracts/sdui/"

touched_paths:
  - app/ports/response_envelope.py
  - app/contracts/sdui/
  - tests/contracts/sdui/test_response_envelope_contract.py

forbidden_paths:
  - web/src/sdui_renderer/

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-009a"
  - "Working tree is dirty at task start"
  - "P0-BATCH3-PROMPTS-001 passed Task Record is missing"
  - "Any forbidden path is modified"
  - "Renderer, dynamic form orchestrator, or multi-turn confirmation state machine is implemented"
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
