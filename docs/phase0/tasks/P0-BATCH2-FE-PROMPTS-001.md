# P0-BATCH2-FE-PROMPTS-001 — Frontend Batch 2 Task Sequence and Prompt Package Update

```yaml
task_id: "P0-BATCH2-FE-PROMPTS-001"
branch: "phase0/P0-BATCH2-FE-PROMPTS-001"
title: "Frontend Batch 2 Task Sequence and Prompt Package Update"
type: "documentation"
depends_on:
  - P0-INFRA-002B
priority: P0
source_spec: "docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md"
task_index: "docs/phase0/TASK_INDEX.md"
context_strategy: "docs/phase0/CONTEXT_LOADING_STRATEGY.md"
boundary_checklist: "docs/phase0/BOUNDARY_CHECKLIST.md"

global_hard_rules:
  - "Execute only this task_id."
  - "Do not modify frozen blueprint files."
  - "Do not implement Phase 1 features."
  - "Do not add unapproved dependencies."
  - "Do not weaken tests to pass."
  - "Any execution/pass claim must include the exact command, exit code, and evidence output in the Task Record."
  - "Independent staged review is required before any commit, push, or merge."
  - "No commit, no push, no merge."

method_profile:
  execution_role: "documentation"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "not_applicable"
  reason_for_owner_choice: "Documentation/prompt-generation task: reading Phase 0 conventions, inserting frontend task sequence into TASK_INDEX.md, creating 4 new per-task prompt files, updating existing prompt and supporting docs. Claude Code/MiMo executes; Codex reviews staged diff."

summary: >
  Synchronize frontend Batch 2 task sequence and prompt materials before
  P0-INFRA-003 implementation. Insert P0-FE-SPIKE-001, P0-INFRA-003A,
  P0-FE-GUIDE-001, P0-FE-ARCH-001 into TASK_INDEX.md. Create their per-task
  prompt files. Update P0-INFRA-003 dependencies. Update README_TASK_PROMPTS.md,
  MANIFEST.md, and REPOSITORY_CONTEXT_MAP.md.

objective: >
  Create the frontend prerequisite task sequence (P0-FE-SPIKE-001 -> P0-INFRA-003A
  -> P0-FE-GUIDE-001 -> P0-INFRA-003 -> P0-FE-ARCH-001) before P0-INFRA-003
  execution. This prevents premature frontend skeleton work that lacks:
  (1) Ant Design X compatibility decision, (2) frontend dependency allowlist,
  (3) frontend AI coding guidelines, (4) SDUI Renderer architecture baseline.
  Create per-task prompts for all 4 new tasks. Update P0-INFRA-003 dependencies
  to P0-INFRA-003A + P0-FE-GUIDE-001.

acceptance_criteria:
  - criterion: "TASK_INDEX.md updated with frontend task sequence: P0-FE-SPIKE-001, P0-INFRA-003A, P0-FE-GUIDE-001, P0-FE-ARCH-001 with correct depends_on"
    result: "pending"
    evidence: ""
  - criterion: "P0-BATCH2-FE-PROMPTS-001.md task prompt file created"
    result: "pending"
    evidence: ""
  - criterion: "P0-FE-SPIKE-001.md created with Ant Design X spike scope, no web/ creation, no dependency_policy.md modification"
    result: "pending"
    evidence: ""
  - criterion: "P0-INFRA-003A.md created with frontend dependency policy scope, gates on P0-FE-SPIKE-001"
    result: "pending"
    evidence: ""
  - criterion: "P0-FE-GUIDE-001.md created with FRONTEND_AI_CODING_GUIDELINES.md deliverable path"
    result: "pending"
    evidence: ""
  - criterion: "P0-FE-ARCH-001.md created with execution_owner=claude_code_mimo, review_owner=codex, method=PDR, SDUI architecture baseline scope"
    result: "pending"
    evidence: ""
  - criterion: "P0-INFRA-003.md updated: depends_on changed to P0-INFRA-003A and P0-FE-GUIDE-001, scope narrowed to exclude Ant Design X decision and npm allowlist"
    result: "pending"
    evidence: ""
  - criterion: "Each new prompt contains method_profile, depends_on, structured-output baseline section, acceptance_criteria, failure_examples, step_verification_points, touched_paths, forbidden_paths, stop_conditions"
    result: "pending"
    evidence: ""
  - criterion: "README_TASK_PROMPTS.md updated with new prompt entries"
    result: "pending"
    evidence: ""
  - criterion: "MANIFEST.md updated with new prompt files only; future deliverables are not registered before they exist"
    result: "pending"
    evidence: ""
  - criterion: "REPOSITORY_CONTEXT_MAP.md minimally updated for frontend task sequence"
    result: "pending"
    evidence: ""
  - criterion: "No forbidden files modified (blueprint, dependency_policy, scripts, web, package.json, pnpm-lock, pyproject.toml, uv.lock, docker-compose, AGENTS, CLAUDE, app, .github)"
    result: "pending"
    evidence: ""
  - criterion: "Final staged-diff evidence is recorded and Task Record changed_files exactly matches git diff --cached --name-only"
    result: "pending"
    evidence: ""

failure_examples_tested:
  - example: "Generated prompt missing method_profile field"
    result: "not_applicable"
    evidence: ""
    not_applicable_reason: "Documentation-generation task verifies required fields through step_verification_points; no independent runtime failure path."
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "TASK_INDEX.md dependency order inconsistent with prompt depends_on"
    result: "not_applicable"
    evidence: ""
    not_applicable_reason: "Verified through cross-comparison in step_verification_points."
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"

step_verification_points:
  - step: "Preflight: confirm branch and clean working tree"
    result: "pending"
    command: "git branch --show-current && git status --short"
    evidence: ""
  - step: "Create P0-BATCH2-FE-PROMPTS-001.md"
    result: "pending"
    command: "Test-Path docs/phase0/tasks/P0-BATCH2-FE-PROMPTS-001.md"
    evidence: ""
  - step: "Create P0-FE-SPIKE-001.md"
    result: "pending"
    command: "Test-Path docs/phase0/tasks/P0-FE-SPIKE-001.md"
    evidence: ""
  - step: "Create P0-INFRA-003A.md"
    result: "pending"
    command: "Test-Path docs/phase0/tasks/P0-INFRA-003A.md"
    evidence: ""
  - step: "Create P0-FE-GUIDE-001.md"
    result: "pending"
    command: "Test-Path docs/phase0/tasks/P0-FE-GUIDE-001.md"
    evidence: ""
  - step: "Create P0-FE-ARCH-001.md"
    result: "pending"
    command: "Test-Path docs/phase0/tasks/P0-FE-ARCH-001.md"
    evidence: ""
  - step: "Update TASK_INDEX.md with frontend sequence"
    result: "pending"
    command: "Select-String -Path docs/phase0/TASK_INDEX.md -Pattern 'P0-FE-SPIKE-001','P0-INFRA-003A','P0-FE-GUIDE-001','P0-FE-ARCH-001'"
    evidence: ""
  - step: "Update P0-INFRA-003.md dependencies"
    result: "pending"
    command: "Select-String -Path docs/phase0/tasks/P0-INFRA-003.md -Pattern 'P0-INFRA-003A','P0-FE-GUIDE-001'"
    evidence: ""
  - step: "Update README_TASK_PROMPTS.md"
    result: "pending"
    command: "Select-String -Path docs/phase0/tasks/README_TASK_PROMPTS.md -Pattern 'P0-BATCH2-FE-PROMPTS-001','P0-FE-SPIKE-001','P0-INFRA-003A','P0-FE-GUIDE-001','P0-FE-ARCH-001'"
    evidence: ""
  - step: "Update MANIFEST.md"
    result: "pending"
    command: "Select-String -Path MANIFEST.md -Pattern 'P0-BATCH2-FE-PROMPTS-001.md','P0-FE-SPIKE-001.md','P0-INFRA-003A.md','P0-FE-GUIDE-001.md','P0-FE-ARCH-001.md'"
    evidence: ""
  - step: "Update REPOSITORY_CONTEXT_MAP.md minimally"
    result: "pending"
    command: "Select-String -Path docs/phase0/REPOSITORY_CONTEXT_MAP.md -Pattern 'P0-BATCH2-FE-PROMPTS-001'"
    evidence: ""
  - step: "Verify no forbidden paths in diff"
    result: "pending"
    command: "git diff --name-only"
    evidence: ""
  - step: "Verify no untracked forbidden files"
    result: "pending"
    command: "git ls-files --others --exclude-standard"
    evidence: ""
  - step: "Final branch check"
    result: "pending"
    command: "git branch --show-current"
    evidence: ""
  - step: "Final staged/working-tree status"
    result: "pending"
    command: "git status --short"
    evidence: ""
  - step: "Final staged file list"
    result: "pending"
    command: "git diff --cached --name-only"
    evidence: ""
  - step: "Final staged diff stat"
    result: "pending"
    command: "git diff --cached --stat"
    evidence: ""
  - step: "Final staged diff whitespace/path check"
    result: "pending"
    command: "git diff --cached --check"
    evidence: ""
  - step: "Final untracked-file check"
    result: "pending"
    command: "git ls-files --others --exclude-standard"
    evidence: ""
  - step: "Final forbidden-path scan"
    result: "pending"
    command: "if (git diff --cached --name-only | Select-String -Pattern '^docs/blueprint/','^docs/dev/dependency_policy.md$','^scripts/check_dependencies.py$','^web/','^package.json$','^pnpm-lock.yaml$','^package-lock.json$','^yarn.lock$','^pyproject.toml$','^uv.lock$','^docker-compose.yml$','^AGENTS.md$','^CLAUDE.md$','^app/','^\\.github/') { throw 'Forbidden staged path detected' }"
    evidence: ""
  - step: "Final dependency and lockfile scan"
    result: "pending"
    command: "if (git diff --cached --name-only | Select-String -Pattern '^docs/dev/dependency_policy.md$','^scripts/check_dependencies.py$','(^|/)package.json$','(^|/)pnpm-lock.yaml$','(^|/)package-lock.json$','(^|/)yarn.lock$','^pyproject.toml$','^uv.lock$') { throw 'Dependency, policy, or lockfile changed' }"
    evidence: ""
  - step: "Final web directory absence check"
    result: "pending"
    command: "if (Test-Path web) { throw 'web/ must not be created by this task' }"
    evidence: ""
  - step: "Verify Task Record changed_files matches staged diff exactly"
    result: "pending"
    command: "$staged = @(git diff --cached --name-only); $lines = Get-Content docs/phase0/task_logs/P0-BATCH2-FE-PROMPTS-001_*_passed.yaml; $start = [Array]::IndexOf($lines, 'changed_files:'); $end = [Array]::IndexOf($lines, 'touched_paths_confirmed: true'); if ($start -lt 0 -or $end -le $start) { throw 'changed_files section not found' }; $record = @($lines[($start + 1)..($end - 1)] | Where-Object { $_ -match '^  - ' } | ForEach-Object { $_ -replace '^  - ', '' }); if ($staged.Count -ne $record.Count) { throw 'changed_files count mismatch' }; for ($i = 0; $i -lt $staged.Count; $i++) { if ($staged[$i] -ne $record[$i]) { throw 'changed_files order mismatch' } }"
    evidence: ""

touched_paths:
  - docs/phase0/TASK_INDEX.md
  - docs/phase0/tasks/P0-BATCH2-FE-PROMPTS-001.md
  - docs/phase0/tasks/P0-FE-SPIKE-001.md
  - docs/phase0/tasks/P0-INFRA-003A.md
  - docs/phase0/tasks/P0-FE-GUIDE-001.md
  - docs/phase0/tasks/P0-FE-ARCH-001.md
  - docs/phase0/tasks/P0-INFRA-003.md
  - docs/phase0/tasks/README_TASK_PROMPTS.md
  - docs/phase0/REPOSITORY_CONTEXT_MAP.md
  - MANIFEST.md

forbidden_paths:
  - docs/blueprint/**
  - docs/dev/dependency_policy.md
  - scripts/check_dependencies.py
  - web/**
  - package.json
  - pnpm-lock.yaml
  - pyproject.toml
  - uv.lock
  - docker-compose.yml
  - AGENTS.md
  - CLAUDE.md
  - app/**
  - .github/**

stop_conditions:
  - "Branch is not phase0/P0-BATCH2-FE-PROMPTS-001 or working tree not clean at start"
  - "Forbidden paths are modified"
  - "Dependency or lockfile changes detected"
  - "Missing method_profile in any generated prompt"
  - "TASK_INDEX.md dependency order inconsistent with prompt depends_on"
  - "Task Record changed_files does not exactly match git diff --cached --name-only after staging"
  - "Commit, push, or merge is attempted before independent staged review"
```

## Task Description

P0-INFRA-003 should not start yet because frontend prerequisites are unresolved:

1. **Ant Design X** may affect frontend dependency allowlist and should be spiked before dependency policy finalization.
2. **Frontend npm allowlist / pnpm readiness** must be handled before web/package.json and pnpm-lock.yaml are created.
3. **Frontend AI coding guidelines** should be created before substantial AI-generated frontend code.
4. **SDUI Renderer architecture** should be defined after frontend skeleton but before SDUI card implementation.

This task creates the prerequisite task sequence and their per-task prompts.

### What to create

1. **P0-BATCH2-FE-PROMPTS-001.md** (this file) — current task prompt
2. **P0-FE-SPIKE-001.md** — Ant Design X compatibility and dependency spike
3. **P0-INFRA-003A.md** — Frontend dependency policy and pnpm availability gate
4. **P0-FE-GUIDE-001.md** — Frontend AI coding guidelines
5. **P0-FE-ARCH-001.md** — SDUI Renderer architecture baseline

### What to update

6. **TASK_INDEX.md** — Insert frontend task sequence
7. **P0-INFRA-003.md** — Change dependencies, narrow scope
8. **README_TASK_PROMPTS.md** — Register new prompts
9. **MANIFEST.md** — Register new files
10. **REPOSITORY_CONTEXT_MAP.md** — Minimal update for frontend sequence

### What NOT to do

- Do not modify frozen frontend baseline (React 18 + Vite + TypeScript strict + Ant Design 5.x + ProComponents 2.x + React Router + TanStack Query + Zustand + Orval)
- Do not modify docs/blueprint/**, docs/dev/dependency_policy.md, scripts/**, web/**, app/**, .github/**
- Do not execute any implementation task
- Do not register future deliverables such as docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md before the owning task creates them
- Do not commit, push, or merge

## Structured-output baseline applicability

not_applicable — this task does not implement LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
