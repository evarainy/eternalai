# P0-DOMAIN-001a - Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/phase0/TASK_INDEX.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only for targeted sections 8.6.2, 8.6.8, and P0-DOMAIN-001a; do not paste or rewrite the full spec.

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
task_id: P0-DOMAIN-001a
branch: "phase0/P0-DOMAIN-001a"
title: Task and Session Interface Contract
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
    TaskStorePort and SessionStorePort are production-path interface contracts.
    Codex owns TDD implementation because the task defines app/ports contracts
    and model boundaries that later Runtime/Gateway work depends on. Independent
    review must happen in a separate Codex review session.

objective: >
  Define TaskStorePort, SessionStorePort, and related Task/Session contract
  types needed by Phase 0, using the common models from spec section 8.6.2 and
  method signatures from section 8.6.8. This task defines abstract contracts
  only; it must not implement a database store, runtime flow, gateway flow, or
  control-plane behavior.

structured_output_baseline_applicability: "not_applicable - this task does not implement LLM structured output."

expected_task_record: "docs/phase0/task_logs/P0-DOMAIN-001a_<timestamp>_passed.yaml"

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
  - app/ports/task_store.py
  - tests/ports/test_task_store_port.py

constraints:
  - Define abstract interfaces only.
  - Do not include concrete database, SQLAlchemy, Redis, filesystem, or in-memory repository implementation.
  - TaskRecord must use the common model shape from spec section 8.6.2; do not invent unrelated task schemas. Note: TaskRecord here means the runtime Task/Session domain contract model from spec section 8.6.2, not the Unified Task Record YAML under `docs/phase0/task_logs/`.
  - Cover create_task, get_task, update_status, append_event, create_session, and get_session.
  - Runtime, Gateway, Control Plane, and Execution Fabric code are out of scope.
  - No plaintext credential, password, token, cookie, sessionid, access_token, or refresh_token values may appear in Trace, ResponseEnvelope, fixtures, logs, reports, or Task Record evidence.

acceptance_criteria:
  - criterion: "TaskStorePort method signatures are defined from spec section 8.6.8"
    result: "pending"
    evidence: ""
  - criterion: "SessionStorePort method signatures are defined"
    result: "pending"
    evidence: ""
  - criterion: "TaskRecord-related annotations use the common model from spec section 8.6.2"
    result: "pending"
    evidence: ""
  - criterion: "create_task, get_task, update_status, append_event, create_session, and get_session are covered by contract tests"
    result: "pending"
    evidence: ""
  - criterion: "No concrete storage implementation is introduced"
    result: "pending"
    evidence: ""
  - criterion: "Forbidden runtime/gateway/control_plane/execution_fabric paths are untouched"
    result: "pending"
    evidence: ""

contract_violation_examples:
  - example: "Concrete database implementation added to app/ports/task_store.py"
    expected_result: "Review fails; only Protocol/ABC/dataclass/type contracts are allowed."
  - example: "TaskRecord is represented as an untyped dict instead of the common model"
    expected_result: "Contract tests fail."
  - example: "Runtime or Gateway imports are added to satisfy tests"
    expected_result: "Forbidden path/import boundary check fails."

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
    command: "uv run pytest tests/ports/test_task_store_port.py"
    evidence: "Expected non-zero before app/ports/task_store.py is implemented."
  - step: "Implement abstract TaskStorePort and SessionStorePort contracts"
    result: "pending"
    command: "Test-Path app/ports/task_store.py"
    evidence: ""
  - step: "Run contract tests (TDD green phase)"
    result: "pending"
    command: "uv run pytest tests/ports/test_task_store_port.py"
    evidence: ""
  - step: "Run lint/type checks for task-owned files"
    result: "pending"
    command: "uv run ruff check app/ports/task_store.py tests/ports/test_task_store_port.py; uv run mypy app/ports/task_store.py"
    evidence: ""
  - step: "Verify no concrete storage dependency was introduced"
    result: "pending"
    command: "$matched = Select-String -Path app/ports/task_store.py -Pattern 'sqlalchemy','redis','sqlite','postgres','open\\(' -Quiet; if ($matched) { throw 'Concrete storage implementation detected' } else { 'PASSED' }"
    evidence: ""
  - step: "Verify forbidden paths are not staged"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

final_test_commands:
  - "uv run pytest tests/ports/test_task_store_port.py"
  - "uv run ruff check app/ports/task_store.py tests/ports/test_task_store_port.py"
  - "uv run mypy app/ports/task_store.py"

touched_paths:
  - app/ports/task_store.py
  - tests/ports/test_task_store_port.py

forbidden_paths:
  - app/runtime/
  - app/gateway/
  - app/control_plane/
  - app/execution_fabric/

stop_conditions:
  - "Branch is not phase0/P0-DOMAIN-001a"
  - "Working tree is dirty at task start"
  - "P0-BATCH3-PROMPTS-001 passed Task Record is missing"
  - "Any forbidden path is modified"
  - "A concrete storage implementation is introduced"
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
