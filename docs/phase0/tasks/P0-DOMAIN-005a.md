# P0-DOMAIN-005a - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- docs/phase0/task_logs/P0-INFRA-006_*_passed.yaml if present through the task log index
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted sections 8.6.2, 8.6.8, and P0-DOMAIN-005a; do not paste or rewrite the full spec.

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
task_id: P0-DOMAIN-005a
branch: "phase0/P0-DOMAIN-005a"
title: Trace Interface Contract
type: interface_contract
depends_on:
  - P0-BATCH3-PROMPTS-001
  - P0-INFRA-006
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
    TracePort is a security- and audit-sensitive interface contract. Codex owns
    TDD implementation because sanitizer hooks and event contracts must be
    asserted before implementation. Independent review must happen in a separate
    Codex review session.

objective: >
  Define TracePort and trace event contract methods, including sanitizer hook
  shape before trace persistence. This task defines abstract trace interfaces
  only; it must not implement storage, OpenTelemetry instrumentation, Langfuse
  emission, gateway integration, or control-plane behavior.

structured_output_baseline_applicability: "not_applicable - this task does not implement LLM structured output."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-005a_<timestamp>_passed.yaml"

evidence_gate:
  required_task: P0-INFRA-006
  required_result: passed
  gate_condition: >
    P0-DOMAIN-005a can execute only if P0-INFRA-006 has a passed Task Record.
    If P0-INFRA-006 evidence is missing, failed, blocked, or contradictory,
    stop before modifying files.

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
  - app/ports/trace.py
  - tests/ports/test_trace_port.py

constraints:
  - Define TracePort only.
  - Do not implement trace storage, OpenTelemetry export, Langfuse export, gateway instrumentation, or control-plane behavior.
  - Define record_event(event: TraceEvent) -> None.
  - Define start_task_trace, record_step, record_policy_decision, record_gateway_call, and finalize_task_trace or equivalent abstract wrappers.
  - Define a sanitizer hook before sensitive data can be written.
  - Sanitizer contract must cover Bearer token, sessionid, access_token, refresh_token, cookie, and set-cookie patterns.
  - No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values may appear in Trace, ResponseEnvelope, fixtures, logs, reports, or Task Record evidence.

acceptance_criteria:
  - criterion: "record_event(event: TraceEvent) -> None method signature is defined from spec section 8.6.8"
    result: "pending"
    evidence: ""
  - criterion: "start_task_trace, record_step, record_policy_decision, record_gateway_call, and finalize_task_trace or equivalent wrappers are defined"
    result: "pending"
    evidence: ""
  - criterion: "Sensitive-data sanitizer hook is defined before trace writes"
    result: "pending"
    evidence: ""
  - criterion: "Sanitizer contract covers Bearer token, sessionid, access_token, refresh_token, cookie, and set-cookie patterns"
    result: "pending"
    evidence: ""
  - criterion: "No trace storage or exporter implementation is introduced"
    result: "pending"
    evidence: ""
  - criterion: "Forbidden control_plane/gateway paths are untouched"
    result: "pending"
    evidence: ""

contract_violation_examples:
  - example: "TracePort writes events to a file, database, Langfuse, or OTel exporter"
    expected_result: "Review fails; storage/export implementation is out of scope."
  - example: "Sanitizer hook is missing or placed after trace write"
    expected_result: "Contract tests fail."
  - example: "Test fixtures include actual credential-like values instead of synthetic redaction samples"
    expected_result: "Secret scan or review fails."

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify dependency Task Records exist"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-BATCH3-PROMPTS-001_*_passed.yaml | Select-Object -First 1; Get-ChildItem docs/phase0/task_logs/P0-INFRA-006_*_passed.yaml | Select-Object -First 1"
    evidence: ""
  - step: "Create sanitizer and trace contract tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/ports/test_trace_port.py"
    evidence: "Expected non-zero before app/ports/trace.py is implemented."
  - step: "Implement abstract TracePort contract"
    result: "pending"
    command: "Test-Path app/ports/trace.py"
    evidence: ""
  - step: "Run contract tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/ports/test_trace_port.py"
    evidence: ""
  - step: "Run lint/type checks for task-owned files"
    result: "pending"
    command: "uv run ruff check app/ports/trace.py tests/ports/test_trace_port.py; uv run mypy app/ports/trace.py"
    evidence: ""
  - step: "Verify no storage/export implementation was introduced"
    result: "pending"
    command: "$matched = Select-String -Path app/ports/trace.py -Pattern 'Langfuse','opentelemetry','sqlalchemy','redis','open\\(','requests','httpx' -Quiet; if ($matched) { throw 'Trace implementation dependency detected' } else { 'PASSED' }"
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
  - "uv run pytest tests/ports/test_trace_port.py"
  - "uv run ruff check app/ports/trace.py tests/ports/test_trace_port.py"
  - "uv run mypy app/ports/trace.py"

touched_paths:
  - app/ports/trace.py
  - tests/ports/test_trace_port.py

forbidden_paths:
  - app/control_plane/
  - app/gateway/

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-005a"
  - "Working tree is dirty at task start"
  - "P0-BATCH3-PROMPTS-001 passed Task Record is missing"
  - "P0-INFRA-006 passed Task Record is missing"
  - "Any forbidden path is modified"
  - "Trace storage/export implementation is introduced"
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
