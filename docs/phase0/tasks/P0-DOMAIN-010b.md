# P0-DOMAIN-010b - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- docs/phase0/PHASE1_TECHNICAL_BASELINE.md
- app/ports/structured_output.py (the StructuredOutputPort contract this task implements)
- app/ports/llm_provider.py (LLMProviderPort — for context on the port boundary, do not implement)
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted section P0-DOMAIN-010b; do not paste or rewrite the full spec.

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
task_id: P0-DOMAIN-010b
branch: "phase0/P0-DOMAIN-010b"
title: Mock Structured Output Implementation
type: implementation
depends_on:
  - P0-DOMAIN-010a
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
    Mock Structured Output Implementation creates a deterministic mock that must
    satisfy StructuredOutputPort and allow Runtime/Gateway tests to run without a
    real LLM. Codex owns TDD because the deterministic-mapping and structured-failure
    paths must be fully exercised. The Phase 1 baseline (no SDK binding) must be
    preserved.

objective: >
  Implement a deterministic Mock Structured Output implementation that satisfies
  StructuredOutputPort.parse_to_schema, returns predictable StructuredOutputResult
  for registered intents, simulates structured output failure, and allows Runtime
  and Golden Task tests to run without a real LLM. Must not call a real model or
  bind to OpenAI SDK, instructor, PydanticAI, DashScope, or vLLM.

structured_output_baseline_applicability: >
  applicable_as_mock_implementation - this mock must implement the StructuredOutputPort
  interface without binding to real SDKs. The Phase 1 baseline (raw OpenAI SDK +
  json_object + model_validate) is the production path; this mock replaces only the
  parse_to_schema call in test/Phase 0 environments. Do not reopen instructor or
  PydanticAI decisions.

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-010b_<timestamp>_passed.yaml"

constraints_to_carry_forward:
  - "StructuredOutputPort contract (app/ports/structured_output.py) must be satisfied exactly"
  - "No real model calls; no openai/instructor/PydanticAI/DashScope/vLLM imports"
  - "Deterministic mapping: given test input must reliably return expected StructuredOutputResult"
  - "Failure simulation must return standard StructuredOutputResult with error field, not raise uncaught exception"
  - "No new Python dependencies; no __init__.py (namespace packages)"
  - "Phase 1 user-value boundary: mock only, no full Phase 1 workflow"

deliverable:
  - app/infra/llm/mock_structured_output/
  - tests/infra/llm/

constraints:
  - Implement MockStructuredOutputProvider satisfying StructuredOutputPort.parse_to_schema (async method).
  - Return StructuredOutputResult (never bare dict or raw model instance without wrapping).
  - Deterministic: given a registered test input, always return the expected parsed result.
  - Simulate structured output failure: return StructuredOutputResult with error field (StructuredOutputError), not raise.
  - Unknown/unregistered inputs must return StructuredOutputResult with error rather than defaulting to first registered capability.
  - Malformed-model-output simulation: return parse-failure StructuredOutputResult, not raise uncaught exception.
  - No real model calls; no openai/instructor/pydantic_ai/dashscope/vllm imports.
  - Do not add new Python dependencies.
  - No __init__.py (namespace packages throughout).
  - Do not modify app/ports/structured_output.py or app/ports/llm_provider.py.
  - Do not modify pyproject.toml or uv.lock.

acceptance_criteria:
  - criterion: "MockStructuredOutputProvider satisfies StructuredOutputPort Protocol (duck-type check)"
    result: "pending"
    evidence: ""
  - criterion: "Registered test input deterministically returns expected StructuredOutputResult with parsed field"
    result: "pending"
    evidence: ""
  - criterion: "Unknown/unregistered input returns StructuredOutputResult with error (not default to first registered)"
    result: "pending"
    evidence: ""
  - criterion: "Malformed-model-output simulation returns StructuredOutputResult with error, no uncaught exception"
    result: "pending"
    evidence: ""
  - criterion: "Runtime tests can run in no-LLM environment using this mock"
    result: "pending"
    evidence: ""
  - criterion: "No real model SDK import in implementation files"
    result: "pending"
    evidence: ""
  - criterion: "No dependency manifest or lockfile changes"
    result: "pending"
    evidence: ""

failure_examples:
  - name: unknown_intent
    trigger: "Input does not match any registered mock intent"
    expected_result: "StructuredOutputResult with error field (no_capability_found or structured_output_failed error), not defaulting to first capability"
    forbidden_shortcut: "禁止默认映射到第一个已注册 Capability"
  - name: malformed_model_output
    trigger: "Simulated non-JSON or missing-field model output"
    expected_result: "StructuredOutputResult with error (parse_error or validation_error), no uncaught exception"
    forbidden_shortcut: "禁止静默补全字段"

step_verification_points:
  - step: "Preflight branch and clean tree"
    result: "pending"
    command: "git branch --show-current; git status --short"
    evidence: ""
  - step: "Verify P0-DOMAIN-010a passed Task Record exists"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-DOMAIN-010a_*_passed.yaml | Select-Object -First 1"
    evidence: ""
  - step: "Create mock structured output tests first (TDD red phase)"
    result: "pending"
    command: "uv run pytest tests/infra/llm/"
    evidence: "Expected non-zero exit before implementation exists."
  - step: "Implement MockStructuredOutputProvider"
    result: "pending"
    command: "Test-Path app/infra/llm/mock_structured_output/"
    evidence: ""
  - step: "Run all tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/infra/llm/ -v"
    evidence: ""
  - step: "Run lint and type checks"
    result: "pending"
    command: "uv run ruff check app/infra/llm/mock_structured_output/ tests/infra/llm/; uv run mypy app/infra/llm/mock_structured_output/"
    evidence: ""
  - step: "Verify no real SDK imports"
    result: "pending"
    command: "$files = Get-ChildItem app/infra/llm/mock_structured_output/ -Filter '*.py' -Recurse; $matched = $files | Select-String -Pattern '^import openai','^from openai','instructor','pydantic_ai','dashscope','vllm' -Quiet; if ($matched) { throw 'Real SDK import detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify no dependency manifests changed"
    result: "pending"
    command: "if (git diff --cached --name-only | Select-String -Pattern '^pyproject.toml$','^uv.lock$' -Quiet) { throw 'Dependency file changed' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/infra/llm/ -v"
  - "uv run ruff check app/infra/llm/mock_structured_output/ tests/infra/llm/"
  - "uv run mypy app/infra/llm/mock_structured_output/"

touched_paths:
  - app/infra/llm/mock_structured_output/
  - tests/infra/llm/

forbidden_paths:
  - app/runtime/
  - app/ports/
  - experiments/
  - pyproject.toml
  - uv.lock

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-010b"
  - "Working tree is dirty at task start"
  - "P0-DOMAIN-010a passed Task Record is missing"
  - "Any forbidden path is modified"
  - "Real SDK (openai/instructor/pydantic_ai/dashscope/vllm) imported in implementation"
  - "Dependency manifests or lockfiles changed"
  - "New Python dependency added"
  - "Tests fail and cannot be fixed within touched_paths"
  - "changed_files cannot be reconciled with staged diff"
```

## Phase 0 constraints checklist

The implementation Plan and Task Record must explicitly confirm these carried-forward constraints:

1. StructuredOutputPort contract (app/ports/structured_output.py) is satisfied exactly — parse_to_schema is async and returns StructuredOutputResult.
2. No real model calls; no openai/instructor/pydantic_ai/dashscope/vllm imports anywhere in touched paths.
3. Deterministic mapping: same test input always returns the same StructuredOutputResult.
4. Failure simulation returns StructuredOutputResult with error field — no uncaught exceptions.
5. Unknown inputs return an error result, not silently default to a registered capability.
6. No new Python dependencies. No __init__.py files (namespace packages).
7. Phase 1 baseline (raw OpenAI SDK + json_object + model_validate) is unchanged — this mock does not affect the production path.

## Structured-output baseline applicability

applicable_as_mock_implementation - this task creates a mock that implements StructuredOutputPort without binding to real SDKs. The Phase 1 baseline remains unchanged. Must not reopen instructor or PydanticAI default decisions.
