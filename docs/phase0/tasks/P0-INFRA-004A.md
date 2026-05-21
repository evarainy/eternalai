# P0-INFRA-004A — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only as source of truth; do not paste it in full.
- docs/adr/phase0/ADR-P0-SPIKE-003-postgresql-pgvector.md — required evidence gate

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
task_id: P0-INFRA-004A
branch: "phase0/P0-INFRA-004A"
title: PostgreSQL / pgvector runtime dependency unblock
type: infrastructure
depends_on:
  - P0-SPIKE-003
  - P0-INFRA-001
  - P0-INFRA-002
priority: prerequisite
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "execution"
  execution_owner: "claude_code_mimo"
  review_owner: "separate_session"
  review_mode: "codex_review"
  method: "PDR"
  reason_for_owner_choice: >
    Claude Code MiMo owns PDR execution of this narrow unblock task because it
    only amends dependency policy, manifest, lockfile, and Docker Compose image
    — no application code, no migrations, no tests. Independent Codex review
    verifies policy compliance and forbidden-path boundaries.

objective: >
  Unblock P0-INFRA-004 by adding the PostgreSQL runtime driver (psycopg[binary])
  to production dependencies, updating the dependency policy allowlist scope,
  switching Docker Compose postgres service to a pgvector-capable image, and
  validating pgvector extension availability. This task does NOT create app/db/,
  alembic/, migrations, or DB tests — those remain P0-INFRA-004 scope.

structured_output_baseline_applicability: "not_applicable — this task does not implement LLM structured output."

expected_task_record: "docs/phase0/task_logs/P0-INFRA-004A_<timestamp>_passed.yaml"

evidence_gate:
  required_spike: P0-SPIKE-003
  required_spike_result: passed
  required_adr: "docs/adr/phase0/ADR-P0-SPIKE-003-postgresql-pgvector.md"
  gate_condition: >
    P0-INFRA-004A can only execute if P0-SPIKE-003 Task Record shows result=passed
    and ADR-P0-SPIKE-003 records a passed or conditionally_passed decision.
  evidence_verification:
    - "Find the current passed P0-SPIKE-003 Task Record via docs/phase0/task_logs/INDEX.md; confirm result=passed"
    - "Read docs/adr/phase0/ADR-P0-SPIKE-003-postgresql-pgvector.md and confirm status=accepted"

deliverable:
  - Updated dependency_policy.md (psycopg scope expansion)
  - Updated pyproject.toml (psycopg[binary] dependency)
  - Updated uv.lock
  - Updated docker-compose.yml (pgvector image + volume path)
  - P0-INFRA-004A Task Record

constraints:
  - Only amend existing dependency policy, manifest, lockfile, and Docker Compose postgres service
  - Do not create app/db/, alembic/, alembic.ini, tests/db/, or any application code
  - Do not modify .env.example unless validation proves necessity
  - pgvector image must be validated via docker manifest inspect before use
  - Docker runtime validation (pg_isready, CREATE EXTENSION) is required when Docker daemon is available
  - Preserve P0-SPIKE-003 context in dependency policy

acceptance_criteria:
  - criterion: "dependency_policy.md psycopg row includes backend-runtime scope"
    result: "pending"
    evidence: ""
  - criterion: "pyproject.toml includes psycopg[binary]>=3.1,<4.0"
    result: "pending"
    evidence: ""
  - criterion: "uv.lock regenerated and uv lock --check passes"
    result: "pending"
    evidence: ""
  - criterion: "uv sync --locked succeeds"
    result: "pending"
    evidence: ""
  - criterion: "psycopg import succeeds and prints version"
    result: "pending"
    evidence: ""
  - criterion: "dependency checker passes"
    result: "pending"
    evidence: ""
  - criterion: "docker-compose.yml postgres service uses pgvector/pgvector:0.8.2-pg18 image"
    result: "pending"
    evidence: ""
  - criterion: "docker-compose.yml postgres volume target is /var/lib/postgresql"
    result: "pending"
    evidence: ""
  - criterion: "docker manifest inspect for pgvector/pgvector:0.8.2-pg18 succeeds"
    result: "pending"
    evidence: ""
  - criterion: "Compose config renders cleanly with pgvector image"
    result: "pending"
    evidence: ""
  - criterion: "PostgreSQL reaches ready state (pg_isready)"
    result: "pending"
    evidence: ""
  - criterion: "pg_available_extensions returns vector extension"
    result: "pending"
    evidence: ""
  - criterion: "CREATE EXTENSION IF NOT EXISTS vector succeeds"
    result: "pending"
    evidence: ""
  - criterion: "pg_extension.extversion recorded"
    result: "pending"
    evidence: ""
  - criterion: "No forbidden paths modified"
    result: "pending"
    evidence: ""

failure_examples_tested:
  - example: "Docker daemon unavailable for runtime validation"
    result: "not_applicable"
    evidence: "docker manifest inspect succeeded; Compose config rendered cleanly; runtime pg_isready/CREATE EXTENSION deferred due to Docker daemon not running"
    not_applicable_reason: "Docker Desktop daemon not running; manifest and config validation passed; runtime validation deferred to P0-INFRA-004 or manual follow-up"
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "Docker daemon available for runtime pgvector validation"

touched_paths:
  - docs/dev/dependency_policy.md
  - pyproject.toml
  - uv.lock
  - docker-compose.yml
  - docs/phase0/TASK_INDEX.md
  - docs/phase0/tasks/README_TASK_PROMPTS.md
  - docs/phase0/tasks/P0-INFRA-004.md
  - docs/phase0/tasks/P0-INFRA-004A.md
  - docs/phase0/task_logs/INDEX.md
  - MANIFEST.md

forbidden_paths:
  - .env.example (unless validation proves necessity)
  - app/
  - alembic/
  - alembic.ini
  - tests/db/
  - web/
  - docs/blueprint/

stop_conditions:
  - "Branch is not phase0/P0-INFRA-004A"
  - "Working tree is dirty with unrelated changes"
  - "Selected pgvector image manifest is unavailable"
  - "Compose config does not render cleanly"
  - "uv lock, uv lock --check, uv sync --locked, dependency checker, or psycopg import fails"
  - "Any forbidden path is modified"
  - "Task Record changed_files cannot be reconciled with staged diff"
  - "Any plaintext secret/token/password beyond existing placeholders appears"
  - "Execution drifts into P0-INFRA-004 Alembic/app/db scope"
```

## Structured-output baseline applicability

not_applicable — this task does not implement LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.