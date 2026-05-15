# P0-INFRA-001 — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only as source of truth; do not paste it in full.

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
task_id: P0-INFRA-001
branch: "phase0/P0-INFRA-001"
title: Docker Compose Single-node Baseline
type: infrastructure
depends_on:
  - P0-INFRA-008
priority: prerequisite
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "execution"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "PDR"
  reason_for_owner_choice: "Infrastructure deployment baseline task. Claude Code/MiMo executes Docker Compose configuration; Codex performs independent review."

objective: >
  Establish a Phase 0 single-node Compose placeholder baseline. Only validate that
  core-infra / app / observability profile configurations are parseable and conflict-free
  via `docker compose config`. This task does NOT start, connect to, or runtime-validate
  any actual service. Actual PostgreSQL runtime validation belongs to P0-INFRA-004.
  Observability config/deployment validation belongs to P0-INFRA-006; runtime instrumentation/trace validation is deferred to later dedicated TDD tasks.

structured_output_baseline_applicability: "not_applicable — this task does not implement LLM structured output."

profile_layering:
  core_infra: "PostgreSQL placeholder + Redis placeholder — images, ports, volumes, healthcheck stubs defined; no actual startup required"
  app: "API placeholder / image or build-target placeholder + frontend placeholder / image or build-target placeholder"
  observability: "Langfuse placeholder + OTel Collector placeholder — images, ports, env vars defined; no actual startup required"
  note: >
    Placeholders mean the service entry exists in docker-compose.yml with a valid image
    reference and configuration. They do NOT mean the service has been runtime-validated.
    Runtime validation happens in dedicated downstream tasks.

deliverable:
  - docker-compose.yml
  - infra/docker/
  - .env.example
  - README.md

constraints:
  - Do not connect to real production systems
  - Do not write real credentials
  - All secrets use placeholders
  - Do not start any Docker container in this task; `docker compose config` parse-only
  - PostgreSQL runtime validation is deferred to P0-INFRA-004
  - Observability config/deployment validation is deferred to P0-INFRA-006; runtime instrumentation/trace validation is deferred to later dedicated TDD tasks

acceptance_criteria:
  - criterion: "docker compose config passes for all profiles without errors"
    result: "pending"
    evidence: ""
  - criterion: "core-infra / app / observability profiles define expected services, images/placeholders, ports, and config without conflicts"
    result: "pending"
    evidence: ""
  - criterion: ".env.example lists required variables for POSTGRES_, REDIS_, LANGFUSE_, and OTEL_ groups"
    result: "pending"
    evidence: ""
  - criterion: "README.md append-only: new 'Docker Compose profiles' section added without overwriting existing content"
    result: "pending"
    evidence: ""

failure_examples_tested:
  - example: "Docker daemon unavailable on execution host"
    result: "not_applicable"
    evidence: "Docker availability was validated in P0-PREP-001 and confirmed in P0-SPIKE-003/004"
    not_applicable_reason: "Docker availability confirmed by prior spike evidence"
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "Compose config references undefined service or missing image"
    result: "triggered"
    evidence: "Verified by negative compose validation step: a temporary invalid compose snippet is used to prove `docker compose config` fails on invalid config"
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"

step_verification_points:
  - step: "Create docker-compose.yml with core-infra, app, and observability profiles"
    result: "pending"
    command: "docker compose config"
    evidence: ""
  - step: "Negative compose validation: prove docker compose config fails on invalid config"
    result: "pending"
    command: >
      Create a temporary invalid compose snippet (e.g. referencing a nonexistent image or
      undefined network), run `docker compose -f <temp-file> config`, confirm non-zero exit,
      then delete the temp file. Do not stage the temp file.
    evidence: ""
  - step: "Create .env.example with required variable groups"
    result: "pending"
    command: "grep -cE 'POSTGRES_|REDIS_|LANGFUSE_|OTEL_' .env.example"
    evidence: "Expected minimum count: >= 8 (at least 2 entries per group: host, port, or equivalent)"
  - step: "Create infra/docker/ directory with service configs"
    result: "pending"
    command: "test -d infra/docker"
    evidence: ""
  - step: "Append 'Docker Compose profiles' section to README.md"
    result: "pending"
    command: "grep -c 'Docker Compose profiles' README.md"
    evidence: "Expected count: >= 1"
  - step: "Stage all files and verify diff"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""

touched_paths:
  - docker-compose.yml
  - infra/docker/
  - .env.example
  - README.md

forbidden_paths:
  - app/execution_fabric/real_adapters/

stop_conditions:
  - "Working tree is not clean at task start"
  - "Forbidden paths are modified"
  - "changed_files cannot be reconciled with staged diff"
  - "Any step_verification_point command returning a non-zero exit code must stop execution, report the failing command/output, and must not continue to the next step"
```

## Structured-output baseline applicability

not_applicable — this task does not implement LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
