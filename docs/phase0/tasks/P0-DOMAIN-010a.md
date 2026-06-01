# P0-DOMAIN-010a - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- docs/phase0/PHASE1_TECHNICAL_BASELINE.md
- docs/adr/phase0/ADR-P0-SPIKE-001-qwen-structured-output.md
- docs/adr/phase0/ADR-P0-SPIKE-002-instructor-vllm-stability.md
- docs/adr/phase0/ADR-P0-SPIKE-007-pydantic-ai-evaluation.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted sections 8.6.2, 8.6.8, and P0-DOMAIN-010a; do not paste or rewrite the full spec.

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
task_id: P0-DOMAIN-010a
branch: "phase0/P0-DOMAIN-010a"
title: LLM Provider and Structured Output Port Contract
type: interface_contract
depends_on:
  - P0-BATCH3-PROMPTS-001
  - P0-SPIKE-001
  - P0-SPIKE-002
  - P0-SPIKE-007
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
    LLMProviderPort and StructuredOutputPort define structured-output and
    provider boundaries. Codex owns TDD implementation because the task is
    architecture-sensitive and must preserve the Phase 1 baseline without adding
    wrapper-library dependencies. Independent review must happen in a separate
    Codex review session.

objective: >
  Define LLMProviderPort and StructuredOutputPort interfaces, including complete
  or chat abstraction, parse_to_schema contract, structured error types, and
  trace metadata. This task defines interfaces only. It must not bind to OpenAI
  SDK, instructor, PydanticAI, DashScope, vLLM, or any concrete provider
  implementation.

structured_output_baseline_applicability: >
  applicable_as_contract_boundary - preserve the Phase 1 baseline from
  PHASE1_TECHNICAL_BASELINE.md: raw OpenAI-compatible SDK, response_format
  {"type":"json_object"}, Pydantic model_validate, and Literal enum validation.
  Do not implement the SDK binding in this task.

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-010a_<timestamp>_passed.yaml"

evidence_gate:
  required_tasks:
    - P0-SPIKE-001
    - P0-SPIKE-002
    - P0-SPIKE-007
  gate_condition: >
    P0-SPIKE-001 must have accepted/passed evidence for raw OpenAI-compatible
    SDK plus Pydantic validation. P0-SPIKE-002 may be failed evidence, but its
    accepted result must be reflected as negative evidence against instructor as
    the default wrapper. P0-SPIKE-007 has failed evidence for PydanticAI as a
    wrapper candidate; its failed result must be reflected as negative evidence
    against PydanticAI as the default wrapper. If any evidence source is missing
    or contradictory, stop before modifying files.

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
  - app/ports/llm_provider.py
  - app/ports/structured_output.py
  - tests/ports/test_llm_structured_output_ports.py

constraints:
  - Define interfaces only.
  - Do not bind to OpenAI SDK, instructor, PydanticAI, DashScope, vLLM, or other concrete provider implementation.
  - Do not add dependencies or change dependency manifests/lockfiles.
  - LLMProviderPort supports complete, chat, or equivalent abstraction.
  - StructuredOutputPort supports parse_to_schema.
  - Return structures support error type and trace metadata.
  - Preserve the baseline: raw OpenAI-compatible SDK with response_format {"type":"json_object"}, Pydantic model_validate, and Literal enum validation.
  - Do not reopen instructor or PydanticAI default decisions.
  - No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values may appear in Trace, ResponseEnvelope, fixtures, logs, reports, or Task Record evidence.

acceptance_criteria:
  - criterion: "LLMProviderPort supports complete, chat, or equivalent abstraction"
    result: "pending"
    evidence: ""
  - criterion: "StructuredOutputPort supports parse_to_schema"
    result: "pending"
    evidence: ""
  - criterion: "Return structures support error type and trace metadata"
    result: "pending"
    evidence: ""
  - criterion: "No concrete provider SDK binding is introduced"
    result: "pending"
    evidence: ""
  - criterion: "No dependency manifest or lockfile changes are made"
    result: "pending"
    evidence: ""
  - criterion: "Phase 1 structured-output baseline is preserved"
    result: "pending"
    evidence: ""

contract_violation_examples:
  - example: "openai, instructor, pydantic_ai, DashScope, or vLLM SDK implementation is imported in app/ports"
    expected_result: "Negative dependency/import check fails."
  - example: "StructuredOutputPort bypasses Pydantic model_validate and Literal enum baseline"
    expected_result: "Contract tests or review fails."
  - example: "Dependency manifests or lockfiles are changed"
    expected_result: "Forbidden path/dependency check fails."

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify dependency evidence"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-BATCH3-PROMPTS-001_*_passed.yaml | Select-Object -First 1; Get-ChildItem docs/phase0/task_logs/P0-SPIKE-001_*_passed.yaml | Select-Object -First 1; Get-ChildItem docs/phase0/task_logs/P0-SPIKE-002_*_failed.yaml | Select-Object -First 1; Get-ChildItem docs/phase0/task_logs/P0-SPIKE-007_*_failed.yaml | Select-Object -First 1; Select-String -Path docs/phase0/PHASE1_TECHNICAL_BASELINE.md -Pattern 'raw OpenAI SDK','Pydantic','Literal','instructor'"
    evidence: ""
  - step: "Create LLM/structured-output port tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/ports/test_llm_structured_output_ports.py"
    evidence: "Expected non-zero before app/ports/llm_provider.py and app/ports/structured_output.py are implemented."
  - step: "Implement abstract LLMProviderPort and StructuredOutputPort contracts"
    result: "pending"
    command: "Test-Path app/ports/llm_provider.py; Test-Path app/ports/structured_output.py"
    evidence: ""
  - step: "Run contract tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/ports/test_llm_structured_output_ports.py"
    evidence: ""
  - step: "Run lint/type checks for task-owned files"
    result: "pending"
    command: "uv run ruff check app/ports/llm_provider.py app/ports/structured_output.py tests/ports/test_llm_structured_output_ports.py; uv run mypy app/ports/llm_provider.py app/ports/structured_output.py"
    evidence: ""
  - step: "Verify no concrete provider imports were introduced"
    result: "pending"
    command: "if (Select-String -Path app/ports/llm_provider.py,app/ports/structured_output.py -Pattern '^import openai','^from openai','instructor','pydantic_ai','dashscope','vllm' -Quiet) { throw 'Concrete provider import detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify no dependency manifests or lockfiles changed"
    result: "pending"
    command: "if (git diff --cached --name-only | Select-String -Pattern '(^|/)package.json$','(^|/)pnpm-lock.yaml$','^pyproject.toml$','^uv.lock$' -Quiet) { throw 'Dependency file changed' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/ports/test_llm_structured_output_ports.py"
  - "uv run ruff check app/ports/llm_provider.py app/ports/structured_output.py tests/ports/test_llm_structured_output_ports.py"
  - "uv run mypy app/ports/llm_provider.py app/ports/structured_output.py"

touched_paths:
  - app/ports/llm_provider.py
  - app/ports/structured_output.py
  - tests/ports/test_llm_structured_output_ports.py

forbidden_paths:
  - app/runtime/
  - app/infra/llm/
  - pyproject.toml
  - uv.lock
  - package.json
  - pnpm-lock.yaml

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-010a"
  - "Working tree is dirty at task start"
  - "P0-BATCH3-PROMPTS-001 passed Task Record is missing"
  - "P0-SPIKE-001, P0-SPIKE-002, or P0-SPIKE-007 evidence is missing or contradictory"
  - "Any forbidden path is modified"
  - "Concrete provider SDK binding is introduced"
  - "Dependency manifests or lockfiles are changed"
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

applicable_as_contract_boundary - this task defines the structured-output port boundary but must not implement an SDK/provider binding. It must preserve the Phase 1 baseline and must not reopen instructor or PydanticAI default decisions.
