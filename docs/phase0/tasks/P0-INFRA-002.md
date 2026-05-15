# P0-INFRA-002 — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only as source of truth; do not paste it in full.

## Read on demand

Load only when needed. Do not full-load all ADRs, all task logs, or the full frozen blueprint by default.

- P0-INFRA-008 dependency policy output (`docs/dev/dependency_policy.md` and `pyproject.toml` if it exists) — required to preserve dependency source / mirror / allowlist configuration.
- `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` — if Task Record schema details are needed.
- `docs/phase0/PHASE1_TECHNICAL_BASELINE.md` — only if structured-output wording needs confirmation.
- Relevant blueprint sections or ADRs — only if a specific decision is unclear.

## Global hard rules

- Execute only this task_id.
- Start this task only after all depends_on tasks have been reviewed, approved, and merged to the Phase 0 base branch.
- Do not modify frozen blueprint files.
- Do not implement Phase 1 features.
- Do not add unapproved dependencies.
- Do not weaken tests to pass.
- Stop after Unified Task Record and wait for human confirmation.
- No commit, no push, no merge.

## Task YAML

```yaml
task_id: P0-INFRA-002
branch: "phase0/P0-INFRA-002"
title: Python uv + FastAPI Backend Skeleton
type: infrastructure
depends_on:
  - P0-INFRA-008
priority: prerequisite
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "execution"
  execution_owner: "codex"
  review_owner: "separate_session"
  review_mode: "codex_review"
  method: "TDD"
  reason_for_owner_choice: >
    Codex owns TDD implementation of the FastAPI backend skeleton because this is
    production-path infrastructure code with health endpoint and test baseline.
    Independent review is done in a separate session to maintain review independence.

objective: >
  Establish Python 3.12, uv, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic,
  pytest, Ruff, mypy backend baseline. Only skeleton and health check — no business logic.

structured_output_baseline_applicability: "not_applicable — this task does not implement LLM structured output."

expected_task_record: "docs/phase0/task_logs/P0-INFRA-002_<timestamp>_passed.yaml"
task_record_requirement: >
  The Unified Task Record must follow docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
  and must match the final staged diff exactly (changed_files == git diff --cached --name-only).

deliverable:
  - pyproject.toml
  - uv.lock
  - app/main.py
  - app/api/v1/health.py
  - tests/test_health.py

constraints:
  - Skeleton and health check only
  - No business logic implementation
  - Use enterprise intranet PyPI mirror or offline cache; AI must not add uncached dependencies
  - Python dependencies must be selected from known pyproject.toml range; new dependencies must record reason, version range, and intranet mirror availability
  - uv.lock changes must be documented in the task log
  - If pyproject.toml already exists from P0-INFRA-008, preserve its dependency source / mirror / allowlist configuration and modify it incrementally. Do not overwrite or recreate it blindly. If pyproject.toml does not exist, create only the minimal file required by this task and document why.

acceptance_criteria:
  - criterion: "uv sync succeeds under intranet mirror or offline cache"
    result: "pending"
    evidence: ""
  - criterion: "uv run pytest succeeds"
    result: "pending"
    evidence: ""
  - criterion: "uv run ruff check succeeds"
    result: "pending"
    evidence: ""
  - criterion: "uv run mypy app succeeds"
    result: "pending"
    evidence: ""

failure_examples_tested:
  - example: "Health endpoint returns 500 or route is missing before implementation"
    result: "triggered"
    evidence: "First TDD pytest run (before implementing the health endpoint) produces a failing test — e.g. ImportError or AssertionError for the missing endpoint. This is expected TDD evidence, not a blocking failure."
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "uv sync fails due to missing intranet mirror or uncached dependency"
    result: "not_applicable"
    evidence: "Dependency policy enforced by P0-INFRA-008 allowlist checker"
    not_applicable_reason: "Dependency policy and allowlist checker from P0-INFRA-008 guard against this"
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"

step_verification_points:
  - step: "Inspect existing pyproject.toml from P0-INFRA-008 (if present) and preserve dependency source configuration"
    result: "pending"
    command: "test -f pyproject.toml && echo 'EXISTS_PRESERVE_INCREMENTAL' || echo 'NOT_EXISTS_CREATE_MINIMAL'"
    evidence: ""
  - step: "Create/update pyproject.toml with Python backend dependencies"
    result: "pending"
    command: "test -f pyproject.toml"
    evidence: ""
  - step: "Run uv sync"
    result: "pending"
    command: "uv sync"
    evidence: ""
  - step: "Create tests/test_health.py FIRST (TDD red phase)"
    result: "pending"
    command: "test -f tests/test_health.py"
    evidence: ""
  - step: "Run uv run pytest — expect failure (TDD red phase: health endpoint not yet implemented)"
    result: "pending"
    command: "uv run pytest tests/test_health.py -x"
    evidence: "Expected non-zero exit. This is the TDD red-phase evidence — a failing test before implementation, not a blocking failure."
  - step: "Implement app/main.py and app/api/v1/health.py (TDD green phase)"
    result: "pending"
    command: "test -f app/main.py && test -f app/api/v1/health.py"
    evidence: ""
  - step: "Re-run uv run pytest — expect pass (TDD green phase)"
    result: "pending"
    command: "uv run pytest"
    evidence: ""
  - step: "Run uv run ruff check"
    result: "pending"
    command: "uv run ruff check"
    evidence: ""
  - step: "Run uv run mypy app"
    result: "pending"
    command: "uv run mypy app"
    evidence: ""
  - step: "Stage all files and verify diff"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

touched_paths:
  - pyproject.toml
  - uv.lock
  - app/
  - tests/

forbidden_paths:
  - app/runtime/
  - app/gateway/
  - app/control_plane/
  - app/ports/
  - app/execution_fabric/
  - app/domain/
  - web/
  - app/execution_fabric/real_adapters/

stop_conditions:
  - "Working tree is not clean at task start"
  - "uv sync fails under intranet mirror constraints"
  - "Forbidden paths are modified"
  - "changed_files cannot be reconciled with staged diff"
  - "Any step_verification_point command returning a non-zero exit code (except the expected TDD red-phase pytest failure) must stop execution, report the failing command/output, and must not continue to the next step"
```

## Structured-output baseline applicability

not_applicable — this task does not implement LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
