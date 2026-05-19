# P0-FE-GUIDE-001 — Frontend AI Coding Guidelines

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/dev/dependency_policy.md (read-only reference for frozen baseline)
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
- Any execution/pass claim must include the exact command, exit code, and evidence output in the Task Record.
- Independent staged review is required before any commit, push, or merge.

## Task YAML

```yaml
task_id: P0-FE-GUIDE-001
branch: "phase0/P0-FE-GUIDE-001"
title: Frontend AI Coding Guidelines
type: documentation
depends_on:
  - P0-INFRA-003B
priority: P0
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "documentation"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "not_applicable"
  reason_for_owner_choice: "Documentation task: creating frontend AI coding guidelines based on frozen baseline and dependency policy. Claude Code/MiMo writes the guideline document; Codex reviews for completeness and consistency. Method is not_applicable because this is documentation, not code implementation."

objective: >
  Create docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md as the canonical frontend
  AI coding guideline. Cover AntD layout conventions, ProComponents usage patterns,
  token/CSS Module styling (avoiding arbitrary inline styles), Orval-generated
  client usage, TanStack Query/Zustand boundary rules, Vitest/React Testing
  Library conventions, and the prohibition of handwritten business fetch/axios
  calls. Must not create frontend code or dependency files.

structured_output_baseline_applicability: "not_applicable — this task does not implement LLM structured output."

deliverable:
  - docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md
  - MANIFEST.md registration for docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md

constraints:
  - Must not create frontend implementation code (no web/ directory)
  - Must not create or modify dependency files (package.json, pnpm-lock.yaml)
  - Must reference the frozen frontend baseline: React 18 + Vite + TypeScript strict + Ant Design 5.x + ProComponents 2.x + React Router + TanStack Query + Zustand + Orval
  - Must cover: AntD layout, ProComponents usage, token/CSS Module styling, no arbitrary inline styles, Orval generated client usage, TanStack Query/Zustand boundaries, Vitest/React Testing Library conventions, no handwritten business fetch/axios
  - Must be written as actionable rules for AI coding agents, not general developer documentation
  - Ant Design X is not adopted for current Phase 1 (P0-FE-SPIKE-001 was blocked; P0-INFRA-003A excluded @ant-design/x from the allowlist). Do not include Ant Design X usage guidelines.

acceptance_criteria:
  - criterion: "docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md created and registered in MANIFEST.md"
    result: "pending"
    evidence: ""
  - criterion: "Guidelines cover AntD layout conventions"
    result: "pending"
    evidence: ""
  - criterion: "Guidelines cover ProComponents 2.x usage patterns"
    result: "pending"
    evidence: ""
  - criterion: "Guidelines cover token/CSS Module styling with prohibition of arbitrary inline styles"
    result: "pending"
    evidence: ""
  - criterion: "Guidelines cover Orval-generated client usage"
    result: "pending"
    evidence: ""
  - criterion: "Guidelines cover TanStack Query / Zustand boundary rules"
    result: "pending"
    evidence: ""
  - criterion: "Guidelines cover Vitest / React Testing Library conventions"
    result: "pending"
    evidence: ""
  - criterion: "Guidelines explicitly prohibit handwritten business fetch/axios calls"
    result: "pending"
    evidence: ""
  - criterion: "No frontend implementation code or dependency files created"
    result: "pending"
    evidence: ""
  - criterion: "Final staged-diff evidence is recorded and Task Record changed_files exactly matches git diff --cached --name-only"
    result: "pending"
    evidence: ""

failure_examples_tested:
  - example: "Guidelines reference outdated Ant Design version or incorrect component API"
    result: "not_applicable"
    evidence: ""
    not_applicable_reason: "Documentation task; guidelines reference the frozen baseline version. Correctness is verified through review, not runtime testing."
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "Guidelines document is too vague for AI agent to follow"
    result: "not_applicable"
    evidence: ""
    not_applicable_reason: "Documentation quality is verified through review. Each section must contain concrete rules with do/don't examples."
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"

step_verification_points:
  - step: "Preflight: confirm branch and clean working tree"
    result: "pending"
    command: "git branch --show-current && git status --short"
    evidence: ""
  - step: "Locate P0-INFRA-003B Task Record for reference"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-FE-SPIKE-001_*_blocked.yaml, Get-ChildItem docs/phase0/task_logs/P0-INFRA-003B_*_passed.yaml"
    evidence: ""
  - step: "Create docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md"
    result: "pending"
    command: "Test-Path docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md"
    evidence: ""
  - step: "Register frontend guidelines in MANIFEST.md"
    result: "pending"
    command: "Select-String -Path MANIFEST.md -Pattern 'docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md'"
    evidence: ""
  - step: "Verify guideline covers required sections"
    result: "pending"
    command: "Select-String -Path docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md -Pattern 'AntD','ProComponents','CSS Module','inline style','Orval','TanStack','Zustand','Vitest','fetch','axios'"
    evidence: ""
  - step: "Verify no web/ or dependency files created"
    result: "pending"
    command: "if (Test-Path web) { throw 'web/ must not be created' }; if (git diff --cached --name-only | Select-String -Pattern '(^|/)package.json$','(^|/)pnpm-lock.yaml$','(^|/)package-lock.json$','(^|/)yarn.lock$') { throw 'Dependency manifest or lockfile changed' }"
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
    command: "if (git diff --cached --name-only | Select-String -Pattern '^web/','(^|/)package.json$','(^|/)pnpm-lock.yaml$','(^|/)package-lock.json$','(^|/)yarn.lock$','^docs/blueprint/','^docs/dev/dependency_policy.md$','^scripts/check_dependencies.py$','^pyproject.toml$','^uv.lock$','^docker-compose.yml$','^app/','^CLAUDE.md$','^AGENTS.md$','^\\.github/') { throw 'Forbidden staged path detected' }"
    evidence: ""
  - step: "Verify Task Record changed_files matches staged diff exactly"
    result: "pending"
    command: "$recordFile = Get-ChildItem docs/phase0/task_logs/P0-FE-GUIDE-001_*_passed.yaml | Sort-Object LastWriteTime -Descending | Select-Object -First 1; $staged = @(git diff --cached --name-only); $lines = Get-Content $recordFile.FullName; $start = [Array]::IndexOf($lines, 'changed_files:'); $end = [Array]::IndexOf($lines, 'touched_paths_confirmed: true'); if ($start -lt 0 -or $end -le $start) { throw 'changed_files section not found' }; $record = @($lines[($start + 1)..($end - 1)] | Where-Object { $_ -match '^  - ' } | ForEach-Object { $_ -replace '^  - ', '' }); if ($staged.Count -ne $record.Count) { throw 'changed_files count mismatch' }; for ($i = 0; $i -lt $staged.Count; $i++) { if ($staged[$i] -ne $record[$i]) { throw 'changed_files order mismatch' } }"
    evidence: ""

touched_paths:
  - MANIFEST.md
  - docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md
  - docs/phase0/task_logs/P0-FE-GUIDE-001_<timestamp>_passed.yaml

forbidden_paths:
  - web/**
  - package.json
  - pnpm-lock.yaml
  - package-lock.json
  - yarn.lock
  - docs/blueprint/**
  - docs/dev/dependency_policy.md
  - scripts/check_dependencies.py
  - pyproject.toml
  - uv.lock
  - docker-compose.yml
  - app/**
  - .github/**
  - CLAUDE.md
  - AGENTS.md
  - docs/phase0/task_logs/** except docs/phase0/task_logs/P0-FE-GUIDE-001_<timestamp>_passed.yaml

stop_conditions:
  - "Working tree is not clean at task start"
  - "P0-INFRA-003B Task Record not found or result is not passed"
  - "Forbidden paths are modified"
  - "Frontend code or dependency files created"
  - "changed_files cannot be reconciled with staged diff"
  - "Task Record changed_files does not exactly match git diff --cached --name-only after staging"
  - "Commit, push, or merge is attempted before independent staged review"
```

## Structured-output baseline applicability

not_applicable — this task does not implement LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
