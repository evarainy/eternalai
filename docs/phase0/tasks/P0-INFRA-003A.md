# P0-INFRA-003A — Frontend Dependency Policy and pnpm Availability Gate

Use this instead of pasting the full Phase 0 spec.

## Required context

Progressive loading — read only what is needed for the current step:
- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/task_logs/P0-FE-SPIKE-001_20260518_211335_blocked.yaml (carry-forward evidence)
- docs/dev/dependency_policy.md (target for update)
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
  reason_for_owner_choice: "Infrastructure gate task that uses P0-FE-SPIKE-001 blocked result as exclusion evidence to finalize the frontend npm dependency allowlist and verify pnpm availability. Claude Code/MiMo executes allowlist updates and pnpm checks; Codex reviews policy changes. PDR because this is a policy/infrastructure decision task with dependency evaluation."

objective: >
  Use P0-FE-SPIKE-001 blocked Task Record as authoritative exclusion evidence:
  @ant-design/x is not allowlisted, Ant Design 6 is not adopted, and the
  React 18 + Ant Design 5.x + ProComponents 2.x baseline is preserved.
  Finalize the frontend dependency allowlist for all frozen baseline packages
  (including vite and typescript). Verify pnpm availability and enterprise npm
  mirror readiness. Update docs/dev/dependency_policy.md with frontend npm
  entries. Must not create web/, package.json, or pnpm-lock.yaml.
  This task is a dependency-policy and tooling-availability gate; it must not
  implement the frontend skeleton.

structured_output_baseline_applicability: "not_applicable — this task does not implement LLM structured output."

deliverable:
  - docs/dev/dependency_policy.md (updated with frontend npm entries)
  - Evidence of pnpm availability and npm mirror readiness

constraints:
  - Must not create web/ directory, package.json, or pnpm-lock.yaml
  - Must use the specific merged Task Record P0-FE-SPIKE-001_20260518_211335_blocked.yaml as authoritative exclusion evidence for @ant-design/x. If a different or conflicting P0-FE-SPIKE-001 record is found, stop for human review.
  - P0-FE-SPIKE-001 blocked result is authoritative exclusion evidence: do not allowlist @ant-design/x, do not adopt Ant Design 6, do not switch to React 19
  - All frontend npm entries must use ecosystem value "npm" in the dependency allowlist table
  - Must verify pnpm is available in the execution environment with exact command/exit-code/output evidence
  - Must verify enterprise npm mirror is reachable or offline cache is populated; public npmjs.org is reference-only and not enterprise evidence
  - Must add frozen baseline packages to the allowlist: react, react-dom, vite, typescript, antd, @ant-design/pro-components, react-router-dom, @tanstack/react-query, zustand, orval
  - Do not add @ant-design/x to the Dependency Allowlist table; if documented at all, it must be in a separate non-allowlist note (e.g. "Excluded frontend candidates") that scripts/check_dependencies.py cannot parse as an allowed dependency row
  - The frozen frontend baseline (React 18 + Vite + TypeScript strict + Ant Design 5.x + ProComponents 2.x + React Router + TanStack Query + Zustand + Orval) must not be modified
  - Future frontend entries must use ecosystem value "npm" and be added before any frontend manifest or lockfile is changed

acceptance_criteria:
  - criterion: "P0-FE-SPIKE-001 Task Record is referenced and its decision is incorporated as exclusion evidence"
    result: "pending"
    evidence: ""
  - criterion: "Frontend npm allowlist entries added to docs/dev/dependency_policy.md Dependency Allowlist table for frozen baseline (react, react-dom, vite, typescript, antd, @ant-design/pro-components, react-router-dom, @tanstack/react-query, zustand, orval)"
    result: "pending"
    evidence: ""
  - criterion: "@ant-design/x is not in the Dependency Allowlist table; documented only in a separate non-allowlist note if at all"
    result: "pending"
    evidence: ""
  - criterion: "pnpm availability verified with exact command/exit-code/output evidence"
    result: "pending"
    evidence: ""
  - criterion: "Enterprise npm mirror reachability or offline cache status documented; public npmjs.org classified as reference-only"
    result: "pending"
    evidence: ""
  - criterion: "No web/, package.json, or pnpm-lock.yaml created"
    result: "pending"
    evidence: ""
  - criterion: "Final staged-diff evidence is recorded and Task Record changed_files exactly matches git diff --cached --name-only"
    result: "pending"
    evidence: ""

failure_examples_tested:
  # These are task-prompt placeholders. The implementation owner MUST overwrite
  # result and evidence with real execution data (triggered / not_triggered /
  # not_applicable) before the Task Record is finalized.
  - example: "P0-FE-SPIKE-001 Task Record not found"
    result: "pending"
    evidence: ""
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "P0-FE-SPIKE-001"
    activation_task_id: "P0-FE-SPIKE-001"
    expiry_condition: "N/A"
  - example: "P0-FE-SPIKE-001 Task Record found but is not the expected blocked record P0-FE-SPIKE-001_20260518_211335_blocked.yaml"
    result: "pending"
    evidence: ""
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "P0-FE-SPIKE-001"
    activation_task_id: "P0-FE-SPIKE-001"
    expiry_condition: "N/A"
  - example: "pnpm not available in execution environment"
    result: "pending"
    evidence: ""
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "Enterprise npm mirror unreachable and no offline cache"
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
  - step: "Confirm HEAD ancestry includes P0-FE-SPIKE-001 merge evidence"
    result: "pending"
    command: "git log --oneline --all | Select-String '8421eb3'"
    evidence: ""
  - step: "Locate P0-FE-SPIKE-001_20260518_211335_blocked.yaml as carry-forward evidence"
    result: "pending"
    command: "Test-Path docs/phase0/task_logs/P0-FE-SPIKE-001_20260518_211335_blocked.yaml"
    evidence: ""
  - step: "Confirm no conflicting P0-FE-SPIKE-001 record exists"
    result: "pending"
    command: "$others = Get-ChildItem docs/phase0/task_logs/P0-FE-SPIKE-001_*.yaml | Where-Object { $_.Name -ne 'P0-FE-SPIKE-001_20260518_211335_blocked.yaml' }; if ($others) { throw 'Conflicting P0-FE-SPIKE-001 record found: ' + ($others.Name -join ', ') }"
    evidence: ""
  - step: "Extract P0-FE-SPIKE-001 decision from blocked record"
    result: "pending"
    command: "Select-String -Path docs/phase0/task_logs/P0-FE-SPIKE-001_20260518_211335_blocked.yaml -Pattern '^decision:'"
    evidence: ""
  - step: "Verify pnpm availability with command/exit-code/output evidence"
    result: "pending"
    command: "Get-Command pnpm; pnpm --version; node --version; npm --version"
    evidence: ""
  - step: "Verify npm/pnpm registry config with safe secret redaction"
    result: "pending"
    command: "npm config get registry; npm config get @ant-design:registry; Test-Path .npmrc; Test-Path $env:USERPROFILE\.npmrc; if (Test-Path .npmrc) { Get-Content .npmrc | Where-Object { $_ -notmatch '_authToken|_auth|token|password|username|cookie|sessionid|access_token|refresh_token' } }"
    evidence: ""
  - step: "Update dependency_policy.md with frontend npm allowlist entries"
    result: "pending"
    command: "Select-String -Path docs/dev/dependency_policy.md -Pattern 'npm.*react','npm.*antd','npm.*pro-components'"
    evidence: ""
  - step: "Verify @ant-design/x is NOT in the Dependency Allowlist table"
    result: "pending"
    command: "$lines = Get-Content docs/dev/dependency_policy.md; $inAllowlist = $false; $found = $false; foreach ($line in $lines) { if ($line.Trim() -eq '## Dependency Allowlist') { $inAllowlist = $true; continue }; if ($inAllowlist -and $line.Trim() -match '^## ') { $inAllowlist = $false }; if ($inAllowlist -and $line -match '^\|' -and $line -match 'ant-design/x') { $found = $true } }; if ($found) { throw '@ant-design/x found in Dependency Allowlist table' }; Write-Output 'PASSED'"
    evidence: ""
  - step: "Verify frontend baseline packages are in the allowlist"
    result: "pending"
    command: "$lines = Get-Content docs/dev/dependency_policy.md; $required = @('react','react-dom','vite','typescript','antd','@ant-design/pro-components','react-router-dom','@tanstack/react-query','zustand','orval'); $inAllowlist = $false; $tableRows = @(); foreach ($line in $lines) { if ($line.Trim() -eq '## Dependency Allowlist') { $inAllowlist = $true; continue }; if ($inAllowlist -and $line.Trim() -match '^## ') { break }; if ($inAllowlist -and $line -match '^\|') { $tableRows += $line } }; if (-not $tableRows) { throw 'No table rows found in Dependency Allowlist section' }; $found = @{}; foreach ($row in $tableRows) { $cells = $row.Split('|') | ForEach-Object { $_.Trim() }; if ($cells.Count -ge 2 -and $cells[1] -eq 'npm') { $found[$cells[2].ToLower()] = $true } }; foreach ($pkg in $required) { if (-not $found.ContainsKey($pkg.ToLower())) { throw \"Missing npm allowlist entry: $pkg\" } }; Write-Output 'PASSED: all 10 baseline packages found in Dependency Allowlist table'"
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
    command: "if (git diff --cached --name-only | Select-String -Pattern '^web/','^package.json$','^pnpm-lock.yaml$','^package-lock.json$','^yarn.lock$','^docs/blueprint/','^scripts/check_dependencies.py$','^pyproject.toml$','^uv.lock$','^docker-compose.yml$','^app/','^CLAUDE.md$','^AGENTS.md$','^MANIFEST.md$','^\.gitignore$','^\\.github/') { throw 'Forbidden staged path detected' }"
    evidence: ""
  - step: "Final dependency manifest and lockfile scan"
    result: "pending"
    command: "if (git diff --cached --name-only | Select-String -Pattern '(^|/)package.json$','(^|/)pnpm-lock.yaml$','(^|/)package-lock.json$','(^|/)yarn.lock$','^pyproject.toml$','^uv.lock$') { throw 'Dependency manifest or lockfile changed' }"
    evidence: ""
  - step: "Verify Task Record changed_files matches staged diff exactly"
    result: "pending"
    command: "$recordFile = Get-ChildItem docs/phase0/task_logs/P0-INFRA-003A_*.yaml | Where-Object { $_.Name -match '_passed\\.yaml$|_blocked\\.yaml$|_failed\\.yaml$' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if (-not $recordFile) { throw 'P0-INFRA-003A Task Record not found' }; $staged = @(git diff --cached --name-only); $lines = Get-Content $recordFile.FullName; $start = [Array]::IndexOf($lines, 'changed_files:'); $end = [Array]::IndexOf($lines, 'touched_paths_confirmed: true'); if ($start -lt 0 -or $end -le $start) { throw 'changed_files section not found' }; $record = @($lines[($start + 1)..($end - 1)] | Where-Object { $_ -match '^  - ' } | ForEach-Object { $_ -replace '^  - ', '' }); if ($staged.Count -ne $record.Count) { throw 'changed_files count mismatch' }; for ($i = 0; $i -lt $staged.Count; $i++) { if ($staged[$i] -ne $record[$i]) { throw 'changed_files order mismatch' } }"
    evidence: ""

touched_paths:
  - docs/dev/dependency_policy.md
  - docs/phase0/task_logs/P0-INFRA-003A_<timestamp>_<passed|blocked|failed>.yaml
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
  - AGENTS.md
  - MANIFEST.md
  - .gitignore

stop_conditions:
  - "Branch is not phase0/P0-INFRA-003A"
  - "Working tree is dirty at implementation start"
  - "P0-FE-SPIKE-001_20260518_211335_blocked.yaml not found, or a conflicting P0-FE-SPIKE-001 record exists"
  - "Forbidden paths would need edits"
  - "web/ or package manifests/lockfiles would need creation"
  - "@ant-design/x would need to be allowlisted"
  - "Ant Design 6 or React 19 would need adoption"
  - "pnpm is unavailable or unsupported"
  - "Enterprise mirror/offline cache evidence cannot be produced when required"
  - "Public npmjs.org is the only evidence source and enterprise evidence is required"
  - "Dependency policy and frontend baseline are inconsistent"
  - "Command/exit-code evidence cannot be produced"
  - "changed_files cannot match staged diff exactly"
  - "Commit, push, or merge is attempted before independent staged review"
  # Do NOT stop merely because P0-FE-SPIKE-001 is blocked.
  # That blocked result is the reason @ant-design/x is excluded.
```

## P0-FE-SPIKE-001 carry-forward impact (Section 6)

- The specific merged Task Record is: `docs/phase0/task_logs/P0-FE-SPIKE-001_20260518_211335_blocked.yaml`
- Its blocked result is authoritative exclusion evidence: @ant-design/x is excluded from the current Phase 1 npm allowlist.
- If a different or conflicting P0-FE-SPIKE-001 record is found, implementation must stop for human review.
- P0-INFRA-003A may proceed using this blocked record as evidence.
- It does not allow Ant Design 6 adoption.
- It does not reopen Ant Design X evaluation.

## Dependency version ranges (Section 7)

Use conservative known ranges only where justified:
- react: `>=18,<19`
- react-dom: `>=18,<19`
- antd: `>=5,<6`
- @ant-design/pro-components: `>=2,<3`

For vite, typescript, react-router-dom, @tanstack/react-query, zustand, and orval:
- Do not invent precise version ranges without evidence.
- Use existing authorized ranges if already present in dependency_policy.md.
- Otherwise require evidence or block/record the gap in the Task Record.

## Read-only / forbidden commands (Section 3)

Read-only inspection commands (allowed in preflight and validation):
- `git branch --show-current`
- `git status --short`
- `git log --oneline --decorate -6`
- `git diff --name-only`
- `git ls-files --others --exclude-standard`
- `Test-Path`
- `Get-Content`
- `Select-String`
- `Get-ChildItem`
- `Get-Command`
- `pnpm --version`
- `node --version`
- `npm --version`
- `npm config get registry`
- `pnpm config get registry` (if pnpm available)
- `python scripts/check_dependencies.py`

Forbidden commands in P0-INFRA-003A:
- `pnpm install`
- `npm install`
- `yarn install`
- Frontend scaffold commands
- Package/zip artifact generation

## Evidence format requirements (Section 13)

All evidence in the Task Record must include:
- Exact command run
- Exit code
- stdout/stderr summary
- Tool version where relevant (e.g. pnpm, node, npm, python)
- Environment note where relevant
- Sanitized registry classification (enterprise_mirror / approved_offline_cache / public_npmjs_reference_only / not_configured)
- Negative validation proof where applicable (e.g. "web/ does not exist")

## Task Record plan (Section 14)

P0-INFRA-003A may create exactly one final Task Record:
- `docs/phase0/task_logs/P0-INFRA-003A_<timestamp>_passed.yaml`
- `docs/phase0/task_logs/P0-INFRA-003A_<timestamp>_blocked.yaml`
- `docs/phase0/task_logs/P0-INFRA-003A_<timestamp>_failed.yaml`

Plus `docs/phase0/task_logs/INDEX.md` if required by project task-log convention.

Task Record rules:
- Create/update Task Record only after implementation evidence exists.
- Stage intended files before final changed_files calculation.
- changed_files must exactly match `git diff --cached --name-only`.
- Validate YAML with `yaml.safe_load`.
- Use duplicate-key check if available.
- Do not invent exit codes for manual review evidence.
- blocked is valid if pnpm or mirror/cache evidence cannot be produced.

## Temporary artifact plan (Section 12)

- No temp artifacts by default.
- If needed, use `_scratch/` only.
- Do not create `_tmp/`.
- Do not commit `_scratch/` contents.
- Do not commit `node_modules/`, `.vite/`, `dist/`, `build/`, `coverage/`.
- Do not commit logs, caches, local validation outputs, or archive/package artifacts.

## Expected file changes (Section 10)

For the later real implementation stage, expected persistent changed files:
- `docs/dev/dependency_policy.md`
- `docs/phase0/task_logs/P0-INFRA-003A_<timestamp>_<passed|blocked|failed>.yaml`
- `docs/phase0/task_logs/INDEX.md` if required by project task-log convention

## Implementation handoff prompt (Section 16)

The corrected task prompt should support a later implementation handoff equivalent to:

> You are executing only P0-INFRA-003A on E:\code\eternalai, branch phase0/P0-INFRA-003A.
>
> Before editing, confirm the prompt has been corrected or explicitly permits the merged P0-FE-SPIKE-001 blocked Task Record as exclusion evidence.
>
> Do not create web/, root package.json, root pnpm-lock.yaml, web/package.json, web/pnpm-lock.yaml, node_modules, frontend scaffold, or any lockfile. Do not run install commands.
>
> Use P0-FE-SPIKE-001_20260518_211335_blocked.yaml as evidence that @ant-design/x is not allowlisted and Ant Design 6 is not adopted.
>
> Update only docs/dev/dependency_policy.md plus the authorized P0-INFRA-003A Task Record and INDEX if required by convention.
>
> Add npm allowlist rows for the frozen frontend baseline only. Do not put @ant-design/x in the Dependency Allowlist table.
>
> Verify pnpm availability with exact command/exit-code/output evidence. Inspect npm/pnpm registry config safely with secret redaction. Public npmjs.org is reference-only and not enterprise evidence.
>
> Run python scripts/check_dependencies.py and final git evidence commands. Stage intended files, then make Task Record changed_files exactly match git diff --cached --name-only.
>
> No commit, push, or merge.

## Tool-selection recommendation (Section 17)

- plan / review: Codex
- implementation: Claude Code / MiMo
- optional secondary review: Kiro only if policy semantics around excluded dependencies remain ambiguous

## Structured-output baseline applicability

not_applicable — this task does not implement LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
