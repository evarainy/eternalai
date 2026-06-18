# P0-INFRA-003B — Frontend Toolchain / Docker Deployment-Runtime Boundary

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/task_logs/P0-INFRA-003A_20260519_001542_blocked.yaml (carry-forward evidence)
- docs/dev/dependency_policy.md (reference for frozen frontend baseline allowlist)
- scripts/check_dependencies.py (validation tool)
- docs/dev/task_record_schema.yaml if needed to confirm valid Task Record statuses

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
task_id: P0-INFRA-003B
branch: "phase0/P0-INFRA-003B"
title: Frontend Toolchain / Docker Deployment-Runtime Boundary
type: infrastructure
depends_on:
  - P0-INFRA-003A
priority: P0
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "execution"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "PDR"
  reason_for_owner_choice: >
    Infrastructure task that verifies node/npm/pnpm availability for Phase 0
    local development/build and documents the Docker deployment-runtime boundary.
    Claude Code/MiMo executes toolchain verification and policy alignment; Codex
    reviews. PDR because this resolves a policy/infrastructure gate.

objective: >
  Verify node/npm/pnpm availability for Phase 0 local development/build and
  document the Docker deployment-runtime boundary. P0-INFRA-003A established the
  frozen frontend baseline allowlist in dependency_policy.md but was blocked on
  two conditions: (1) pnpm not available; (2) no approved enterprise npm mirror
  or offline cache. The environment model has since been clarified: Phase 0
  internet-connected local development/build may use public npm registry when
  explicitly allowed by the task prompt and dependency policy; intranet runtime
  uses prebuilt Docker images and must not require npm/pnpm registry access.
  Enterprise mirror/offline cache is deferred to future intranet source build,
  intranet CI, or stricter supply-chain governance. This task validates pnpm
  availability, updates dependency_policy.md and downstream tasks to reflect the
  deployment-runtime boundary, and unblocks P0-FE-GUIDE-001 and P0-INFRA-003.
  Must not create web/, package.json, or pnpm-lock.yaml. Must not modify the
  frozen frontend baseline.

structured_output_baseline_applicability: "not_applicable — this task does not implement LLM structured output."

deliverable:
  - Evidence of node/npm/pnpm availability (pnpm via Corepack if needed)
  - docs/dev/dependency_policy.md updated with Docker deployment-runtime boundary
  - docs/phase0/tasks/P0-INFRA-003.md updated to reflect new environment model
  - docs/phase0/TASK_INDEX.md and docs/phase0/tasks/README_TASK_PROMPTS.md updated as needed
  - Task Record with all blocking conditions resolved or documented

constraints:
  - Must not create web/ directory, package.json, or pnpm-lock.yaml
  - Must not install frontend dependencies (no pnpm install, npm install, or yarn install)
  - Must not scaffold frontend code
  - Must not change the frozen frontend baseline (React 18 + Vite + TypeScript strict + Ant Design 5.x + ProComponents 2.x + React Router + TanStack Query + Zustand + Orval)
  - Must not allowlist @ant-design/x
  - Must not adopt Ant Design 6 or React 19
  - Must use P0-INFRA-003A_20260519_001542_blocked.yaml as carry-forward evidence
  - If a different or conflicting P0-INFRA-003A record is found, stop for human review
  - pnpm may be activated through Corepack (corepack prepare pnpm@9.15.9 --activate) if not directly available; do not run corepack enable
  - Public npm registry (https://registry.npmjs.org/) may be used for Phase 0 internet-connected local development/build when explicitly allowed by the task prompt and dependency policy. It is not enterprise mirror evidence and does not prove intranet source-build compliance.
  - Intranet runtime uses prebuilt Docker images and must not require npm/pnpm registry access at runtime.
  - Enterprise mirror/offline cache is deferred to future intranet source build, intranet CI, or stricter supply-chain governance tasks.
  - Must run python scripts/check_dependencies.py and confirm dependency_policy.md integrity

acceptance_criteria:
  - criterion: "Node/npm/pnpm availability verified (pnpm via Corepack if needed) with exact command/exit-code/output evidence"
    result: "pending"
    evidence: ""
  - criterion: "Docker deployment-runtime boundary documented in dependency_policy.md: Phase 0 local dev/build may use public npm registry; intranet runtime uses prebuilt Docker images and must not require npm/pnpm registry access"
    result: "pending"
    evidence: ""
  - criterion: "docs/dev/dependency_policy.md distinguishes Phase 0 internet-connected local development/build, intranet runtime prebuilt Docker deployment, and future intranet source build/CI supply-chain governance"
    result: "pending"
    evidence: ""
  - criterion: "docs/phase0/tasks/P0-INFRA-003.md updated to reflect new environment model: install/build in Phase 0 local dev; intranet runtime consumes prebuilt Docker images"
    result: "pending"
    evidence: ""
  - criterion: "docs/dev/dependency_policy.md allowlist remains complete (10 frozen baseline npm packages); dependency checker passes"
    result: "pending"
    evidence: ""
  - criterion: "@ant-design/x remains excluded from the Dependency Allowlist table"
    result: "pending"
    evidence: ""
  - criterion: "No web/, package.json, or pnpm-lock.yaml created"
    result: "pending"
    evidence: ""
  - criterion: "Final staged-diff evidence is recorded and Task Record changed_files exactly matches git diff --cached --name-only"
    result: "pending"
    evidence: ""

failure_examples_tested:
  - example: "P0-INFRA-003A blocked Task Record not found"
    result: "pending"
    evidence: ""
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "P0-INFRA-003A"
    activation_task_id: "P0-INFRA-003A"
    expiry_condition: "N/A"
  - example: "P0-INFRA-003A Task Record found but is not the expected blocked record P0-INFRA-003A_20260519_001542_blocked.yaml"
    result: "pending"
    evidence: ""
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "P0-INFRA-003A"
    activation_task_id: "P0-INFRA-003A"
    expiry_condition: "N/A"
  - example: "pnpm unavailable and Corepack activation fails or Corepack is unavailable"
    result: "pending"
    evidence: ""
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "Dependency policy cannot be reconciled with AGENTS.md Rule 8 deployment-runtime boundary"
    result: "pending"
    evidence: ""
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"

step_verification_points:
  - step: "Preflight: confirm branch, clean working tree, and absence of web/ and manifests"
    result: "pending"
    command: "git branch --show-current; git status --short; if (Test-Path web) { throw 'web/ already exists' }; if (Test-Path package.json) { throw 'root package.json already exists' }; if (Test-Path pnpm-lock.yaml) { throw 'root pnpm-lock.yaml already exists' }; git log --oneline --decorate -6"
    evidence: ""
  - step: "Locate P0-INFRA-003A_20260519_001542_blocked.yaml as carry-forward evidence"
    result: "pending"
    command: "Test-Path docs/phase0/task_logs/P0-INFRA-003A_20260519_001542_blocked.yaml"
    evidence: ""
  - step: "Confirm no conflicting P0-INFRA-003A record exists"
    result: "pending"
    command: "$others = Get-ChildItem docs/phase0/task_logs/P0-INFRA-003A_*.yaml | Where-Object { $_.Name -ne 'P0-INFRA-003A_20260519_001542_blocked.yaml' }; if ($others) { throw 'Conflicting P0-INFRA-003A record found: ' + ($others.Name -join ', ') }"
    evidence: ""
  - step: "Extract P0-INFRA-003A blocking conditions from blocked record"
    result: "pending"
    command: "Select-String -Path docs/phase0/task_logs/P0-INFRA-003A_20260519_001542_blocked.yaml -Pattern '^blocking_conditions:' -Context 0,3"
    evidence: ""
  - step: "Verify node/npm/pnpm availability"
    result: "pending"
    command: "node --version; npm --version; Get-Command pnpm; pnpm --version"
    evidence: ""
  - step: "Activate pnpm via Corepack if unavailable"
    result: "pending"
    command: "corepack prepare pnpm@9.15.9 --activate; corepack pnpm --version; where.exe pnpm"
    evidence: ""
  - step: "Verify npm registry config (informational — public npm is allowed for Phase 0 local dev)"
    result: "pending"
    command: "npm config get registry; npm config get @ant-design:registry; Test-Path .npmrc; Test-Path $env:USERPROFILE\.npmrc; if (Test-Path .npmrc) { Get-Content .npmrc | Where-Object { $_ -notmatch '_authToken|_auth|token|password|username|cookie|sessionid|access_token|refresh_token' } }"
    evidence: ""
  - step: "Verify node and npm versions for compatibility reference"
    result: "pending"
    command: "node --version; npm --version"
    evidence: ""
  - step: "Verify docs/dev/dependency_policy.md allowlist completeness"
    result: "pending"
    command: "Select-String -Path docs/dev/dependency_policy.md -Pattern 'npm.*react','npm.*antd','npm.*pro-components'"
    evidence: ""
  - step: "Verify @ant-design/x is NOT in the Dependency Allowlist table"
    result: "pending"
    command: "$lines = Get-Content docs/dev/dependency_policy.md; $inAllowlist = $false; $found = $false; foreach ($line in $lines) { if ($line.Trim() -eq '## Dependency Allowlist') { $inAllowlist = $true; continue }; if ($inAllowlist -and $line.Trim() -match '^## ') { $inAllowlist = $false }; if ($inAllowlist -and $line -match '^\|' -and $line -match 'ant-design/x') { $found = $true } }; if ($found) { throw '@ant-design/x found in Dependency Allowlist table' }; Write-Output 'PASSED'"
    evidence: ""
  - step: "Run dependency checker"
    result: "pending"
    command: "python scripts/check_dependencies.py"
    evidence: ""
  - step: "Verify no web/, package manifests, or lockfiles created"
    result: "pending"
    command: "if (Test-Path web) { throw 'web/ must not exist' }; if (Test-Path package.json) { throw 'root package.json must not exist' }; if (Test-Path pnpm-lock.yaml) { throw 'root pnpm-lock.yaml must not exist' }; if (Test-Path package-lock.json) { throw 'root package-lock.json must not exist' }; if (Test-Path yarn.lock) { throw 'root yarn.lock must not exist' }"
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
    command: "if (git diff --cached --name-only | Select-String -Pattern '^web/','^package.json$','^pnpm-lock.yaml$','^package-lock.json$','^yarn.lock$','^docs/blueprint/','^scripts/check_dependencies.py$','^pyproject.toml$','^uv.lock$','^docker-compose.yml$','^app/','^CLAUDE.md$','^MANIFEST.md$','^\.gitignore$','^\\.github/') { throw 'Forbidden staged path detected' }"
    evidence: ""
  - step: "Final dependency manifest and lockfile scan"
    result: "pending"
    command: "if (git diff --cached --name-only | Select-String -Pattern '(^|/)package.json$','(^|/)pnpm-lock.yaml$','(^|/)package-lock.json$','(^|/)yarn.lock$','^pyproject.toml$','^uv.lock$') { throw 'Dependency manifest or lockfile changed' }"
    evidence: ""
  - step: "Verify Task Record changed_files matches staged diff exactly"
    result: "pending"
    command: "$recordFile = Get-ChildItem docs/phase0/task_logs/P0-INFRA-003B_*.yaml | Where-Object { $_.Name -match '_passed\\.yaml$|_blocked\\.yaml$|_failed\\.yaml$' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if (-not $recordFile) { throw 'P0-INFRA-003B Task Record not found' }; $staged = @(git diff --cached --name-only); $lines = Get-Content $recordFile.FullName; $start = [Array]::IndexOf($lines, 'changed_files:'); $end = [Array]::IndexOf($lines, 'touched_paths_confirmed: true'); if ($start -lt 0 -or $end -le $start) { throw 'changed_files section not found' }; $record = @($lines[($start + 1)..($end - 1)] | Where-Object { $_ -match '^  - ' } | ForEach-Object { $_ -replace '^  - ', '' }); if ($staged.Count -ne $record.Count) { throw 'changed_files count mismatch' }; for ($i = 0; $i -lt $staged.Count; $i++) { if ($staged[$i] -ne $record[$i]) { throw 'changed_files order mismatch' } }"
    evidence: ""

touched_paths:
  - AGENTS.md (Rule 8 one-line dependency source policy edit only — do not expand beyond that)
  - docs/dev/dependency_policy.md
  - docs/phase0/tasks/P0-INFRA-003B.md
  - docs/phase0/tasks/P0-INFRA-003.md
  - docs/phase0/TASK_INDEX.md
  - docs/phase0/tasks/README_TASK_PROMPTS.md
  - docs/phase0/task_logs/P0-INFRA-003B_<timestamp>_<passed|blocked|failed>.yaml
  - docs/phase0/task_logs/INDEX.md

forbidden_paths:
  - web/**
  - package.json
  - pnpm-lock.yaml
  - package-lock.json
  - yarn.lock
  - scripts/check_dependencies.py
  - docs/blueprint/**
  - pyproject.toml
  - uv.lock
  - docker-compose.yml
  - app/**
  - .github/**
  - CLAUDE.md
  - MANIFEST.md
  - .gitignore
  - AGENTS.md (forbidden for expansion beyond the one-line Rule 8 edit explicitly allowed by this task; the existing Rule 8 edit in the current staged diff is the only allowed change)

stop_conditions:
  - "Branch is not phase0/P0-INFRA-003B"
  - "Working tree is dirty at implementation start"
  - "P0-INFRA-003A_20260519_001542_blocked.yaml not found, or a conflicting P0-INFRA-003A record exists"
  - "Forbidden paths would need edits"
  - "web/ or package manifests/lockfiles would need creation"
  - "pnpm unavailable and Corepack activation fails or Corepack is unavailable"
  - "@ant-design/x would need to be allowlisted"
  - "Ant Design 6 or React 19 would need adoption"
  - "Dependency policy and frontend baseline are inconsistent"
  - "Command/exit-code evidence cannot be produced"
  - "Dependency policy cannot be reconciled with AGENTS.md Rule 8 deployment-runtime boundary"
  - "changed_files cannot match staged diff exactly"
  - "Commit, push, or merge is attempted before independent staged review"
```

## P0-INFRA-003A carry-forward impact

- The specific merged Task Record is: `docs/phase0/task_logs/P0-INFRA-003A_20260519_001542_blocked.yaml`
- Its blocked result identifies two blocking conditions: (1) pnpm unavailable, (2) no enterprise npm mirror/cache.
- P0-INFRA-003A accomplished: frontend baseline allowlist (10 npm packages) added to `docs/dev/dependency_policy.md`; @ant-design/x documented as excluded.
- The environment model has since been clarified: Phase 0 internet-connected local development/build may use public npm registry when explicitly allowed; intranet runtime uses prebuilt Docker images and must not require npm/pnpm registry access.
- P0-INFRA-003B resolves both blocking conditions: (1) pnpm activated via Corepack; (2) enterprise mirror/cache requirement re-scoped — public npm registry is acceptable for Phase 0 local dev, and enterprise mirror/offline cache is deferred to future intranet source build, intranet CI, or stricter supply-chain governance.
- P0-FE-GUIDE-001 and P0-INFRA-003 can proceed under the clarified environment model.
- If a different or conflicting P0-INFRA-003A record is found, implementation must stop for human review.

## Docker deployment-runtime boundary

This task documents the following environment model, which replaces the previous registry classification rules:

- **Phase 0 internet-connected local development/build**: May use public npm registry (https://registry.npmjs.org/) when explicitly allowed by the task prompt and dependency policy. `pnpm install` runs in this environment.
- **Intranet runtime deployment**: Uses prebuilt Docker images. Must not run `pnpm install`/`npm install`. Must not require npm/pnpm registry access at runtime.
- **Future intranet source build / intranet CI / stricter supply-chain governance**: Enterprise npm mirror or approved offline cache is required. This is deferred to later tasks (e.g., P0-INFRA-007 when executed in intranet CI, or a dedicated supply-chain task).

Public npm registry usage is permitted for Phase 0 local development/build only. It is not enterprise mirror evidence and does not prove intranet source-build or CI compliance.

## Technical baselines to preserve

Frontend baseline:
- React 18
- Vite
- TypeScript strict
- Ant Design 5.x
- ProComponents 2.x
- React Router
- TanStack Query
- Zustand
- Orval for OpenAPI type/client generation

Do not switch current Phase 1 frontend to:
- Next.js
- React 19
- Ant Design 6
- Ant Design X
- shadcn/ui
- Tailwind

## Structured-output baseline applicability

not_applicable — this task does not implement LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
