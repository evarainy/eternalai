# P0-INFRA-003A — Frontend Dependency Policy and pnpm Availability Gate

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/dev/dependency_policy.md (read-only reference)
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
task_id: P0-INFRA-003A
branch: "phase0/P0-INFRA-003A"
title: Frontend Dependency Policy and pnpm Availability Gate
type: infrastructure
depends_on:
  - P0-FE-SPIKE-001
priority: P0
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "execution"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "PDR"
  reason_for_owner_choice: "Infrastructure gate task that uses P0-FE-SPIKE-001 results to finalize the frontend npm dependency allowlist and verify pnpm availability. Claude Code/MiMo executes allowlist updates and pnpm checks; Codex reviews policy changes. PDR because this is a policy/infrastructure decision task with dependency evaluation."

objective: >
  Use P0-FE-SPIKE-001 result to decide whether @ant-design/x enters the
  frontend npm allowlist. Finalize the frontend dependency allowlist for all
  frozen baseline packages. Verify pnpm availability and enterprise npm mirror
  readiness. Update docs/dev/dependency_policy.md with frontend npm entries.
  Must not create web/, package.json, or pnpm-lock.yaml.

structured_output_baseline_applicability: "not_applicable — this task does not implement LLM structured output."

deliverable:
  - docs/dev/dependency_policy.md (updated with frontend npm entries)
  - Evidence of pnpm availability and npm mirror readiness

constraints:
  - Must not create web/ directory, package.json, or pnpm-lock.yaml
  - Must use P0-FE-SPIKE-001 Task Record as evidence for @ant-design/x decision
  - All frontend npm entries must use ecosystem value "npm" in the dependency allowlist table
  - Must verify pnpm is available in the execution environment
  - Must verify enterprise npm mirror is reachable or offline cache is populated
  - Must add all frozen baseline packages to the allowlist: react, react-dom, antd, @ant-design/pro-components, @ant-design/x (if spike accepted), react-router-dom, @tanstack/react-query, zustand, orval
  - The frozen frontend baseline (React 18 + Vite + TypeScript strict + Ant Design 5.x + ProComponents 2.x + React Router + TanStack Query + Zustand + Orval) must not be modified
  - Future frontend entries must use ecosystem value "npm" and be added before any frontend manifest or lockfile is changed

acceptance_criteria:
  - criterion: "P0-FE-SPIKE-001 Task Record is referenced and its decision is incorporated"
    result: "pending"
    evidence: ""
  - criterion: "Frontend npm allowlist entries added to docs/dev/dependency_policy.md Dependency Allowlist table"
    result: "pending"
    evidence: ""
  - criterion: "@ant-design/x entry reflects P0-FE-SPIKE-001 decision (accepted, excluded, or conditional)"
    result: "pending"
    evidence: ""
  - criterion: "pnpm availability verified in execution environment"
    result: "pending"
    evidence: ""
  - criterion: "Enterprise npm mirror reachability or offline cache status documented"
    result: "pending"
    evidence: ""
  - criterion: "No web/, package.json, or pnpm-lock.yaml created"
    result: "pending"
    evidence: ""
  - criterion: "Final staged-diff evidence is recorded and Task Record changed_files exactly matches git diff --cached --name-only"
    result: "pending"
    evidence: ""

failure_examples_tested:
  - example: "P0-FE-SPIKE-001 Task Record not found or result is failed"
    result: "triggered"
    evidence: "Glob search for docs/phase0/task_logs/P0-FE-SPIKE-001_*_passed.yaml returns no results"
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "P0-FE-SPIKE-001"
    activation_task_id: "P0-FE-SPIKE-001"
    expiry_condition: "N/A"
  - example: "pnpm not available in execution environment"
    result: "triggered"
    evidence: "pnpm --version returns non-zero exit or command not found"
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "Enterprise npm mirror unreachable and no offline cache"
    result: "triggered"
    evidence: "npm ping or pnpm ping to enterprise registry returns non-zero or timeout"
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"

step_verification_points:
  - step: "Preflight: confirm branch and clean working tree"
    result: "pending"
    command: "git branch --show-current && git status --short"
    evidence: ""
  - step: "Locate P0-FE-SPIKE-001 Task Record"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-FE-SPIKE-001_*_passed.yaml"
    evidence: ""
  - step: "Extract P0-FE-SPIKE-001 decision"
    result: "pending"
    command: "Read P0-FE-SPIKE-001 Task Record decision field"
    evidence: ""
  - step: "Verify pnpm availability"
    result: "pending"
    command: "pnpm --version"
    evidence: ""
  - step: "Verify npm mirror or offline cache readiness"
    result: "pending"
    command: "Document registry configuration and availability"
    evidence: ""
  - step: "Update dependency_policy.md with frontend npm allowlist entries"
    result: "pending"
    command: "Select-String -Path docs/dev/dependency_policy.md -Pattern 'npm.*react','npm.*antd','npm.*pro-components'"
    evidence: ""
  - step: "Verify @ant-design/x entry reflects spike decision"
    result: "pending"
    command: "Select-String -Path docs/dev/dependency_policy.md -Pattern 'ant-design/x'"
    evidence: ""
  - step: "Verify no web/, package.json, or pnpm-lock.yaml created"
    result: "pending"
    command: "if (Test-Path web) { throw 'web/ must not be created' }; if (Test-Path package.json) { throw 'root package.json must not be created' }; if (Test-Path pnpm-lock.yaml) { throw 'root pnpm-lock.yaml must not be created' }"
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
    command: "if (git diff --cached --name-only | Select-String -Pattern '^web/','^package.json$','^pnpm-lock.yaml$','^package-lock.json$','^yarn.lock$','^docs/blueprint/','^scripts/check_dependencies.py$','^pyproject.toml$','^uv.lock$','^docker-compose.yml$','^app/','^CLAUDE.md$','^AGENTS.md$','^\\.github/') { throw 'Forbidden staged path detected' }"
    evidence: ""
  - step: "Final dependency manifest and lockfile scan"
    result: "pending"
    command: "if (git diff --cached --name-only | Select-String -Pattern '(^|/)package.json$','(^|/)pnpm-lock.yaml$','(^|/)package-lock.json$','(^|/)yarn.lock$','^pyproject.toml$','^uv.lock$') { throw 'Dependency manifest or lockfile changed' }"
    evidence: ""
  - step: "Verify Task Record changed_files matches staged diff exactly"
    result: "pending"
    command: "$recordFile = Get-ChildItem docs/phase0/task_logs/P0-INFRA-003A_*_passed.yaml | Sort-Object LastWriteTime -Descending | Select-Object -First 1; $staged = @(git diff --cached --name-only); $lines = Get-Content $recordFile.FullName; $start = [Array]::IndexOf($lines, 'changed_files:'); $end = [Array]::IndexOf($lines, 'touched_paths_confirmed: true'); if ($start -lt 0 -or $end -le $start) { throw 'changed_files section not found' }; $record = @($lines[($start + 1)..($end - 1)] | Where-Object { $_ -match '^  - ' } | ForEach-Object { $_ -replace '^  - ', '' }); if ($staged.Count -ne $record.Count) { throw 'changed_files count mismatch' }; for ($i = 0; $i -lt $staged.Count; $i++) { if ($staged[$i] -ne $record[$i]) { throw 'changed_files order mismatch' } }"
    evidence: ""

touched_paths:
  - docs/dev/dependency_policy.md

forbidden_paths:
  - web/**
  - package.json
  - pnpm-lock.yaml
  - package-lock.json
  - yarn.lock
  - docs/blueprint/**
  - scripts/check_dependencies.py
  - pyproject.toml
  - uv.lock
  - docker-compose.yml
  - app/**
  - .github/**
  - CLAUDE.md
  - AGENTS.md
  - docs/phase0/task_logs/** except docs/phase0/task_logs/P0-INFRA-003A_<timestamp>_passed.yaml

stop_conditions:
  - "Working tree is not clean at task start"
  - "P0-FE-SPIKE-001 Task Record not found or result is not passed"
  - "pnpm not available and cannot be verified"
  - "Forbidden paths are modified"
  - "web/, package.json, or pnpm-lock.yaml created"
  - "changed_files cannot be reconciled with staged diff"
  - "Task Record changed_files does not exactly match git diff --cached --name-only after staging"
  - "Commit, push, or merge is attempted before independent staged review"
```

## Structured-output baseline applicability

not_applicable — this task does not implement LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
