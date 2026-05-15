# P0-INFRA-005 — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only as source of truth; do not paste it in full.
- docs/adr/phase0/ADR-P0-SPIKE-004-redis-arq.md — required evidence gate

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
task_id: P0-INFRA-005
branch: "phase0/P0-INFRA-005"
title: JobQueuePort Baseline with Redis/ARQ Candidate Deferred
type: infrastructure
depends_on:
  - P0-SPIKE-004
  - P0-INFRA-001
  - P0-INFRA-002
priority: P1
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "execution"
  execution_owner: "codex"
  review_owner: "claude_code_mimo"
  review_mode: "codex_review"
  method: "TDD"
  reason_for_owner_choice: "Architecture-sensitive port/adapter pattern with async job queue. Codex executes because this involves port interface design and adapter pattern; Claude Code/MiMo performs independent review. TDD because queue operations are production-path with regression risk."

objective: >
  After Spike validation, establish a no-new-dependency JobQueuePort interface
  and in-memory/mock queue baseline. Redis/ARQ remains a candidate implementation
  only and must not enter production code or dependency files in this task.
  Runtime must not depend on ARQ directly. Business workflow state must not be
  stored in the queue.

structured_output_baseline_applicability: "not_applicable — this task does not implement LLM structured output."

evidence_gate:
  required_spike: P0-SPIKE-004
  required_spike_result: passed
  required_adr: "docs/adr/phase0/ADR-P0-SPIKE-004-redis-arq.md"
  gate_condition: >
    P0-INFRA-005 can only execute if P0-SPIKE-004 Task Record shows result=passed
    and ADR-P0-SPIKE-004 records a passed or conditionally_passed decision.
    If P0-SPIKE-004 evidence is missing or failed, this task must stop and report
    the blocker. Even when the gate passes, Redis/ARQ stays candidate-only here:
    do not add arq/redis dependencies, do not edit dependency or lock files, and
    do not implement a Redis/ARQ production adapter in this task.
  evidence_verification:
    - "Find the current passed P0-SPIKE-004 Task Record via docs/phase0/task_logs/INDEX.md or `ls docs/phase0/task_logs/P0-SPIKE-004_*_passed.yaml | head -1`; confirm result=passed"
    - "Read docs/adr/phase0/ADR-P0-SPIKE-004-redis-arq.md and confirm status=accepted"

deliverable:
  - app/ports/job_queue.py
  - app/infra/job_queue/
  - tests/infra/job_queue/

minimum_interface_contract:
  module: "app/ports/job_queue.py"
  interface: "JobQueuePort"
  methods:
    - "async def enqueue(task_type: str, payload: dict[str, Any], task_id: str | None = None) -> str"
    - "async def get_status(job_id: str) -> Literal[\"queued\", \"in_progress\", \"complete\", \"failed\", \"not_found\"]"
    - "async def get_result(job_id: str) -> Any | None"

constraints:
  - Only execute when P0-SPIKE-004 ADR conclusion is passed or conditionally_passed
  - JobQueuePort must expose the exact minimum interface contract above; additional methods require explicit justification in the Task Record
  - ARQ is a candidate implementation only and is deferred to a later dependency-approved task
  - This task must not modify pyproject.toml, uv.lock, package.json, pnpm-lock.yaml, or any dependency file
  - Use only standard-library/in-repo code for the in-memory/mock queue baseline
  - Do not import arq, redis, or redis.asyncio in production or test code
  - Runtime must not depend on ARQ directly
  - Business workflow state must not be stored in the queue

acceptance_criteria:
  - criterion: "JobQueuePort interface defined"
    result: "pending"
    evidence: ""
  - criterion: "In-memory/mock implementation satisfies JobQueuePort without new dependencies"
    result: "pending"
    evidence: ""
  - criterion: "Minimal task enqueue, execute, and failure tests pass"
    result: "pending"
    evidence: ""
  - criterion: "Redis/ARQ remains candidate-only: no arq/redis imports and no dependency or lockfile changes"
    result: "pending"
    evidence: ""

failure_examples_tested:
  - example: "JobQueuePort contract or enqueue-dequeue test fails before implementation"
    result: "triggered"
    evidence: "First TDD pytest run (before creating app/ports/job_queue.py and app/infra/job_queue/) produces a failing test — e.g. ImportError for missing port module or AssertionError for missing enqueue behavior. This is expected TDD evidence, not a blocking failure."
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "P0-SPIKE-004 evidence missing or failed"
    result: "not_applicable"
    evidence: "P0-SPIKE-004 task record shows result=passed with all acceptance criteria passed"
    not_applicable_reason: "Spike evidence gate verified: P0-SPIKE-004 passed, ADR accepted"
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "Task accidentally introduces Redis/ARQ dependency or import"
    result: "triggered"
    evidence: "Negative validation temporarily creates an unstaged file with `import arq` or `import redis`, runs the dependency/import scan, confirms non-zero exit, then deletes the temporary file before staging."
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"

step_verification_points:
  - step: "Verify P0-SPIKE-004 evidence gate"
    result: "pending"
    command: "ls docs/phase0/task_logs/P0-SPIKE-004_*_passed.yaml | head -1 | xargs grep 'result.*passed' && grep 'status.*accepted' docs/adr/phase0/ADR-P0-SPIKE-004-redis-arq.md"
    evidence: ""
  - step: "Create tests/infra/job_queue/ port contract and enqueue-dequeue behavior test FIRST (TDD red phase)"
    result: "pending"
    command: "test -d tests/infra/job_queue"
    evidence: ""
  - step: "Run queue tests — expect failure (TDD red phase: port/adapter not yet implemented)"
    result: "pending"
    command: "pytest tests/infra/job_queue/ -x"
    evidence: "Expected non-zero exit. This is the TDD red-phase evidence — a failing test before implementation, not a blocking failure."
  - step: "Create app/ports/job_queue.py with JobQueuePort interface (TDD green phase)"
    result: "pending"
    command: "test -f app/ports/job_queue.py"
    evidence: ""
  - step: "Verify JobQueuePort minimum interface contract"
    result: "pending"
    command: "grep -E 'async def enqueue\\(|async def get_status\\(|async def get_result\\(|Literal\\[.*queued.*in_progress.*complete.*failed.*not_found' app/ports/job_queue.py"
    evidence: ""
  - step: "Create app/infra/job_queue/ with in-memory/mock implementation (TDD green phase)"
    result: "pending"
    command: "test -d app/infra/job_queue"
    evidence: ""
  - step: "Re-run queue tests — expect pass (TDD green phase)"
    result: "pending"
    command: "pytest tests/infra/job_queue/"
    evidence: ""
  - step: "Verify no Redis/ARQ dependency, lockfile, or import was introduced"
    result: "pending"
    command: "git diff --cached --name-only | grep -E '^(pyproject.toml|uv.lock|package.json|pnpm-lock.yaml)$' && exit 1 || true; grep -R -i 'import arq\\|from arq\\|redis.asyncio\\|import redis' app/ tests/ && exit 1 || echo no_redis_arq_dependency_or_import"
    evidence: ""
  - step: "Negative Redis/ARQ import validation"
    result: "pending"
    command: >
      Create a temporary unstaged file containing `import arq` or `import redis`,
      run the dependency/import scan, confirm non-zero exit and an error naming
      the forbidden import, then delete the temporary file before staging.
    evidence: ""
  - step: "Stage all files and verify diff"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

touched_paths:
  - app/ports/job_queue.py
  - app/infra/job_queue/
  - tests/infra/job_queue/

forbidden_paths:
  - app/runtime/
  - app/gateway/
  - pyproject.toml
  - uv.lock
  - package.json
  - pnpm-lock.yaml

stop_conditions:
  - "P0-SPIKE-004 evidence is missing or failed"
  - "Working tree is not clean at task start"
  - "Forbidden paths are modified"
  - "changed_files cannot be reconciled with staged diff"
  - "arq/redis dependency, lockfile change, or production import is introduced"
```

## Structured-output baseline applicability

not_applicable — this task does not implement LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
