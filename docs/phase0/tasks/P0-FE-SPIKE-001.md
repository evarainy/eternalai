# P0-FE-SPIKE-001 — Ant Design X Compatibility and Dependency Spike

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
- Any execution/pass claim must include the exact command, exit code, and evidence output in the Task Record.
- Independent staged review is required before any commit, push, or merge.

## Task YAML

```yaml
task_id: P0-FE-SPIKE-001
branch: "phase0/P0-FE-SPIKE-001"
title: Ant Design X Compatibility and Dependency Spike
type: spike
depends_on:
  - P0-INFRA-008
priority: P0
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "execution"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "PDR"
  reason_for_owner_choice: "Dependency compatibility spike requiring investigation, testing, and decision documentation. Claude Code/MiMo executes environment checks; Codex reviews ADR and decision. PDR because this is a technology selection/evaluation task."

objective: >
  Determine whether @ant-design/x is compatible with the frozen frontend baseline
  (React 18 + Ant Design 5.x + ProComponents 2.x) and available through the
  enterprise npm mirror or offline cache. Produce a decision (accept / reject /
  conditional accept) that P0-INFRA-003A can use to finalize the frontend dependency
  allowlist. Do not implement any AI chat UI. Do not create web/. Do not modify
  dependency_policy.md.

structured_output_baseline_applicability: "not_applicable — this task does not implement LLM structured output."

deliverable:
  - docs/phase0/tasks/P0-FE-SPIKE-001.md (this prompt, as required context)
  - Spike investigation notes saved as evidence in the Task Record

constraints:
  - Must not create web/ directory or any frontend implementation code
  - Must not modify docs/dev/dependency_policy.md
  - Must not add dependencies to any manifest or lockfile
  - Must not perform broad repository cleanup
  - Must not use _tmp/; _scratch/ is the only repo-local temporary workspace for later spike execution, and _scratch/ contents must not be committed
  - Must test @ant-design/x against React 18 + Ant Design 5.x + ProComponents 2.x compatibility matrix
  - Must check availability in enterprise npm mirror or offline cache
  - Must document version constraints and peer dependency conflicts
  - Must produce a clear decision: accept, reject, or conditional accept with conditions
  - The frozen frontend baseline (React 18 + Vite + TypeScript strict + Ant Design 5.x + ProComponents 2.x + React Router + TanStack Query + Zustand + Orval) must not be modified

acceptance_criteria:
  - criterion: "@ant-design/x compatibility with React 18 + Ant Design 5.x + ProComponents 2.x is tested and documented"
    result: "pending"
    evidence: ""
  - criterion: "@ant-design/x availability in enterprise npm mirror or offline cache is verified"
    result: "pending"
    evidence: ""
  - criterion: "Version constraints and peer dependency conflicts are documented"
    result: "pending"
    evidence: ""
  - criterion: "Clear decision (accept / reject / conditional accept) is recorded in Task Record"
    result: "pending"
    evidence: ""
  - criterion: "Decision includes rationale, blocking conditions (if any), and recommendation for P0-INFRA-003A"
    result: "pending"
    evidence: ""
  - criterion: "No web/ directory created, no dependency_policy.md modified, no manifests or lockfiles changed"
    result: "pending"
    evidence: ""
  - criterion: "Final staged-diff evidence is recorded and Task Record changed_files exactly matches git diff --cached --name-only"
    result: "pending"
    evidence: ""

failure_examples_tested:
  # Pre-execution placeholders only. The final Task Record must not blindly copy
  # these placeholder result/evidence values. During execution, overwrite each
  # result using real command evidence:
  # - triggered: the failure condition actually occurred and evidence proves it.
  # - not_triggered: the failure condition was tested, did not occur, and
  #   evidence proves it.
  # - not_applicable: the example truly does not apply, with complete
  #   not_applicable fields.
  # If no real command evidence exists, do not mark triggered or not_triggered.
  - example: "@ant-design/x peer dependency conflicts with Ant Design 5.x"
    result: "not_triggered"
    evidence: "Pre-execution placeholder only; final Task Record must overwrite this with real command evidence."
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "@ant-design/x not available in enterprise npm mirror or offline cache"
    result: "not_triggered"
    evidence: "Pre-execution placeholder only; final Task Record must overwrite this with real command evidence."
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "@ant-design/x requires React 19+ and is incompatible with frozen React 18 baseline"
    result: "not_triggered"
    evidence: "Pre-execution placeholder only; final Task Record must overwrite this with real command evidence."
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
  - step: "Check @ant-design/x latest version and peer dependencies"
    result: "pending"
    command: "npm view @ant-design/x version peerDependencies --registry <enterprise_mirror_or_npmjs>"
    evidence: ""
  - step: "Verify @ant-design/x compatibility with React 18"
    result: "pending"
    command: "npm view @ant-design/x peerDependencies.react"
    evidence: ""
  - step: "Verify @ant-design/x compatibility with Ant Design 5.x"
    result: "pending"
    command: "npm view @ant-design/x peerDependencies.antd"
    evidence: ""
  - step: "Check enterprise npm mirror availability"
    result: "pending"
    command: "npm view @ant-design/x --registry <enterprise_mirror>"
    evidence: ""
  - step: "Test ProComponents 2.x compatibility if @ant-design/x is resolvable"
    result: "pending"
    command: "Document ProComponents 2.x peer dependency interaction with @ant-design/x"
    evidence: ""
  - step: "Document version constraints and conflicts"
    result: "pending"
    command: "Evidence recorded in Task Record"
    evidence: ""
  - step: "Record decision: accept / reject / conditional accept"
    result: "pending"
    command: "Decision field in Task Record"
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
    command: "$staged = @(git diff --cached --name-only); if ($staged | Select-String -Pattern '^web/','^docs/dev/dependency_policy.md$','^scripts/check_dependencies.py$','(^|/)package.json$','(^|/)pnpm-lock.yaml$','(^|/)package-lock.json$','(^|/)yarn.lock$','^pyproject.toml$','^uv.lock$','^docker-compose.yml$','^docs/blueprint/','^app/','^CLAUDE.md$','^AGENTS.md$','^\\.github/') { throw 'Forbidden staged path detected' }; $badTaskLogs = @($staged | Where-Object { $_ -like 'docs/phase0/task_logs/*' -and $_ -ne 'docs/phase0/task_logs/INDEX.md' -and $_ -notmatch '^docs/phase0/task_logs/P0-FE-SPIKE-001_\\d{8}_\\d{6}_(passed|failed|blocked)\\.yaml$' }); if ($badTaskLogs.Count -gt 0) { throw 'Forbidden task log staged path detected' }"
    evidence: ""
  - step: "Final no-dependency-change check"
    result: "pending"
    command: "if (git diff --cached --name-only | Select-String -Pattern '^docs/dev/dependency_policy.md$','^scripts/check_dependencies.py$','(^|/)package.json$','(^|/)pnpm-lock.yaml$','(^|/)package-lock.json$','(^|/)yarn.lock$','^pyproject.toml$','^uv.lock$') { throw 'Dependency, policy, or lockfile changed' }"
    evidence: ""
  - step: "Verify Task Record changed_files matches staged diff exactly"
    result: "pending"
    command: "$recordFile = Get-ChildItem docs/phase0/task_logs/P0-FE-SPIKE-001_*_*.yaml | Where-Object { $_.Name -match '^P0-FE-SPIKE-001_\\d{8}_\\d{6}_(passed|failed|blocked)\\.yaml$' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1; $staged = @(git diff --cached --name-only); $lines = Get-Content $recordFile.FullName; $start = [Array]::IndexOf($lines, 'changed_files:'); $end = [Array]::IndexOf($lines, 'touched_paths_confirmed: true'); if ($start -lt 0 -or $end -le $start) { throw 'changed_files section not found' }; $record = @($lines[($start + 1)..($end - 1)] | Where-Object { $_ -match '^  - ' } | ForEach-Object { $_ -replace '^  - ', '' }); if ($staged.Count -ne $record.Count) { throw 'changed_files count mismatch' }; for ($i = 0; $i -lt $staged.Count; $i++) { if ($staged[$i] -ne $record[$i]) { throw 'changed_files order mismatch' } }"
    evidence: ""

touched_paths:
  - docs/phase0/task_logs/P0-FE-SPIKE-001_<timestamp>_passed.yaml
  - docs/phase0/task_logs/P0-FE-SPIKE-001_<timestamp>_failed.yaml
  - docs/phase0/task_logs/P0-FE-SPIKE-001_<timestamp>_blocked.yaml
  - docs/phase0/task_logs/INDEX.md

touched_paths_notes:
  - "docs/phase0/task_logs/INDEX.md may be updated only when a Task Record is produced and staged, and only to add the P0-FE-SPIKE-001 Task Record entry. No other INDEX rewrite or cleanup is allowed."

forbidden_paths:
  - web/**
  - docs/dev/dependency_policy.md
  - scripts/check_dependencies.py
  - package.json
  - pnpm-lock.yaml
  - package-lock.json
  - yarn.lock
  - pyproject.toml
  - uv.lock
  - docker-compose.yml
  - docs/blueprint/**
  - app/**
  - .github/**
  - CLAUDE.md
  - AGENTS.md
  - docs/phase0/task_logs/** except docs/phase0/task_logs/P0-FE-SPIKE-001_<timestamp>_passed.yaml, docs/phase0/task_logs/P0-FE-SPIKE-001_<timestamp>_failed.yaml, docs/phase0/task_logs/P0-FE-SPIKE-001_<timestamp>_blocked.yaml, and docs/phase0/task_logs/INDEX.md under touched_paths_notes

stop_conditions:
  - "Working tree is not clean at task start"
  - "Forbidden paths are modified"
  - "Dependency or lockfile changes detected"
  - "Cannot access npm registry (enterprise mirror or npmjs) to check @ant-design/x"
  - "@ant-design/x requires React 19+ or Ant Design 6+"
  - "Peer dependency resolution fails against React 18 + Ant Design 5.x + ProComponents 2.x"
  - "Enterprise npm mirror or offline cache is unavailable or cannot provide evidence"
  - "Command evidence cannot be produced"
  - "Continuing would require creating web/ or modifying dependency manifests or lockfiles"
  - "Continuing would require modifying docs/dev/dependency_policy.md"
  - "Continuing would require touching forbidden paths"
  - "changed_files cannot be reconciled with staged diff"
  - "Task Record changed_files does not exactly match git diff --cached --name-only after staging"
  - "Commit, push, or merge is attempted before independent staged review"
```

## Structured-output baseline applicability

not_applicable — this task does not implement LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
