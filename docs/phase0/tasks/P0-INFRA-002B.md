# P0-INFRA-002B — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md

## Read on demand

Load only when needed.

- `.gitignore` — current ignore rules before extending.
- `docs/phase0/task_logs/P0-INFRA-002_20260518_104453_passed.yaml` — records deferred cleanup followup: "open a separate foundation/cleanup task to define _scratch/ and/or _tmp/ conventions, add .gitignore rules, and document rules in CLAUDE.md and AGENTS.md."

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
task_id: P0-INFRA-002B
branch: "phase0/p0-infra-002b-cleanup"
title: Scratch/temp workspace and artifact cleanup rules
type: documentation
depends_on:
  - P0-INFRA-002
  - P0-INFRA-002A
priority: foundation
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "documentation"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "not_applicable"
  reason_for_owner_choice: >
    This is a repository hygiene/rules task with no runtime behavior change.
    Claude Code / MiMo owns documentation implementation because the task
    modifies only .gitignore, CLAUDE.md, AGENTS.md, and creates a task prompt.
    Codex performs independent staged-diff review. Method is not_applicable
    because no code, test, or interface contract is produced.

objective: >
  Define _scratch/ as the single repository-local scratch/temp workspace.
  Add .gitignore rules for scratch, caches, and build artifacts.
  Add compact scratch/temp rules to CLAUDE.md (implementation-agent) and
  AGENTS.md (review-agent). Prevent future validation caches, temporary logs,
  debug files, disposable fixtures, generated archives, and cache directories
  from polluting the repository before P0-INFRA-003 and later tasks.

touched_paths:
  - .gitignore
  - AGENTS.md
  - CLAUDE.md
  - docs/phase0/tasks/P0-INFRA-002B.md
  - docs/phase0/task_logs/INDEX.md
  - docs/phase0/task_logs/P0-INFRA-002B_<timestamp>_passed.yaml

allowed_paths:
  - .gitignore
  - AGENTS.md
  - CLAUDE.md
  - docs/phase0/tasks/P0-INFRA-002B.md
  - docs/phase0/task_logs/INDEX.md
  - docs/phase0/task_logs/P0-INFRA-002B_<timestamp>_passed.yaml

forbidden_paths:
  - app/
  - tests/
  - pyproject.toml
  - uv.lock
  - docker-compose.yml
  - README.md
  - infra/
  - frontend skeleton paths
  - database migration paths
  - CI workflow paths
  - runtime/gateway/business adapter code paths
  - docs/dev/dependency_policy.md
  - scripts/check_dependencies.py
  - frozen blueprint files (docs/blueprint/)
  - historical Task Records except the new P0-INFRA-002B Task Record after review approval

structured_output_baseline_applicability: "not_applicable — this task does not implement LLM structured output."

expected_task_record: "docs/phase0/task_logs/P0-INFRA-002B_<timestamp>_passed.yaml"
task_record_requirement: >
  The Unified Task Record must follow docs/dev/task_record_schema.yaml
  and must match the final staged diff exactly (changed_files == git diff --cached --name-only).

deliverable:
  - docs/phase0/tasks/P0-INFRA-002B.md
  - .gitignore (extended)
  - CLAUDE.md (scratch/temp rules added)
  - AGENTS.md (review-agent scratch/temp rules added)

constraints:
  - Use _scratch/ only as the single ignored local workspace.
  - Do not introduce _tmp/ as an equivalent synonym.
  - Do not create _scratch/.gitkeep.
  - Do not commit _scratch/ contents.
  - Do not ignore source files, tests, docs, Task Records, pyproject.toml, uv.lock, or future frontend lockfiles.
  - Do not place temp files under app/, tests/, docs/, repo root, or task-log directories.
  - Do not recursively scan or clean inside .venv/ unless a dedicated environment cleanup task says so.
  - .gitignore changes must not hide source, docs, Task Records, dependency manifests, lockfiles, or intentionally versioned fixtures.

acceptance_criteria:
  - criterion: ".gitignore has clear commented sections for _scratch/, Python bytecode/tool caches, venvs, root-level temp files, root-level build/coverage/package artifacts, and frontend caches"
    verification: "Inspect .gitignore diff; verify section comments and patterns are present."
  - criterion: ".gitignore does NOT ignore source files, tests, docs, Task Records, pyproject.toml, uv.lock, or frontend lockfiles"
    verification: >
      git check-ignore -v for pyproject.toml, uv.lock, package.json,
      package-lock.json, pnpm-lock.yaml, yarn.lock, docs/, tests/,
      task prompt paths, and task record paths must return exit 1 (not ignored).
  - criterion: "CLAUDE.md contains compact implementation-agent scratch/temp rules"
    verification: "Inspect CLAUDE.md diff; verify rules for _scratch/ usage, artifact lifecycle recording, and cache cleanup."
  - criterion: "AGENTS.md contains compact review-agent scratch/temp verification rules"
    verification: "Inspect AGENTS.md diff; verify rules for verifying no temp/cache artifacts staged, untracked files clean, and .venv/ out of scope."
  - criterion: "docs/phase0/tasks/P0-INFRA-002B.md exists with valid method_profile"
    verification: "File exists and contains all required YAML fields including method_profile."

failure_examples_tested:
  - example: ".gitignore accidentally ignores pyproject.toml or uv.lock"
    verification: "git check-ignore -v pyproject.toml must return exit 1."
  - example: "_scratch/.gitkeep is created"
    verification: "Test-Path _scratch/.gitkeep must be false at closeout."
  - example: "_tmp/ introduced as synonym"
    verification: >
      Validate that .gitignore does not define _tmp/ as an ignored scratch/temp workspace.
      Validate that CLAUDE.md and AGENTS.md do not introduce _tmp/ as an allowed or equivalent
      workspace convention. Prohibition text mentioning _tmp/ (e.g., "Do not introduce _tmp/ as
      an equivalent synonym") is allowed and expected.
  - example: "Cache artifacts remain staged at closeout"
    verification: "git diff --cached --name-only must not contain __pycache__, *.pyc, .pytest_cache, .mypy_cache, .ruff_cache, or _scratch/ paths."

step_verification_points:
  - step: "Confirm branch, clean tree, and e814609 ancestry"
    command: "git branch --show-current; git status --short; git merge-base --is-ancestor e814609 HEAD"
  - step: "Create docs/phase0/tasks/P0-INFRA-002B.md"
    command: "Test-Path docs/phase0/tasks/P0-INFRA-002B.md"
  - step: "Extend .gitignore"
    command: "git diff .gitignore"
  - step: "Add rules to CLAUDE.md"
    command: "git diff CLAUDE.md"
  - step: "Add rules to AGENTS.md"
    command: "git diff AGENTS.md"
  - step: "Run git check-ignore spot checks (negative — must not match)"
    command: >
      git check-ignore -v pyproject.toml uv.lock package.json
      package-lock.json pnpm-lock.yaml yarn.lock
      docs/phase0/tasks/P0-INFRA-002B.md
      docs/phase0/task_logs/P0-INFRA-002B_dummy_passed.yaml
      tests/test_health.py docs/
      All must return exit 1.
  - step: "Run git check-ignore spot check (positive — must match)"
    command: "git check-ignore -v _scratch/test.tmp must return 0."
  - step: "Verify no temp/cache artifacts staged or untracked"
    command: "git ls-files --others --exclude-standard; git diff --cached --name-only"

artifact_lifecycle_requirements:
  persistent_foundation_artifacts:
    - docs/phase0/tasks/P0-INFRA-002B.md
    - .gitignore (extended)
    - CLAUDE.md (rules added)
    - AGENTS.md (rules added)
  persistent_regression_tests: none
  temporary_validation_artifacts:
    - _scratch/test-ignore.tmp (created for ignore-effectiveness validation, removed before closeout)
  cleanup_timing: "Temporary validation file removed before review/closeout."
  explicitly_out_of_scope:
    - ".venv/ internal caches"
    - "app/ and tests/ runtime caches (handled by .gitignore patterns, not manual cleanup)"

stop_conditions:
  - "Current branch is not phase0/p0-infra-002b-cleanup"
  - "Working tree is not clean before implementation"
  - "e814609 is not in current history"
  - "Required repository files are missing in a way that changes task authority"
  - "Implementation would need to touch forbidden paths"
  - ".gitignore changes would hide source, docs, Task Records, dependency manifests, lockfiles, or intentionally versioned fixtures/artifacts"
  - "Temporary files remain staged or untracked at closeout"
  - "The initial Plan is not approved by the human"
```

## Forbidden paths

- app/
- tests/ (test files)
- pyproject.toml
- uv.lock
- docker-compose.yml
- README.md
- docs/dev/dependency_policy.md
- scripts/check_dependencies.py
- docs/blueprint/ (frozen blueprint files)
- docs/phase0/task_logs/*.yaml (historical Task Records — do not rewrite)
- frontend skeleton, database migrations, CI workflow
