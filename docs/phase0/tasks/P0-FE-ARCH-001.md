# P0-FE-ARCH-001 — SDUI Renderer Architecture Baseline

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md
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
task_id: P0-FE-ARCH-001
branch: "phase0/P0-FE-ARCH-001"
title: SDUI Renderer Architecture Baseline
type: architecture
depends_on:
  - P0-INFRA-003
priority: P0
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "architecture_documentation"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "PDR"
  reason_for_owner_choice: "Architecture baseline documentation task requiring design decisions for SDUI Renderer. Claude Code/MiMo writes the PDR baseline document; Codex reviews for completeness and architectural soundness. PDR because this defines architecture baseline: plan, alternatives, risks, and recommendation."

objective: >
  Create docs/phase0/SDUI_RENDERER_ARCHITECTURE_BASELINE.md as the PDR
  architecture baseline for the future SDUI Renderer. Define ComponentRegistry,
  ActionDispatcher, FallbackRenderer, schema_version strategy, and user_action
  boundaries. This is architecture definition only, not full card library
  implementation. Do not create web/src/sdui/** code unless a later task
  explicitly authorizes implementation. Reference FRONTEND_AI_CODING_GUIDELINES.md
  for frontend conventions. SDUI/frontend architecture work must follow the
  guide's current frontend baseline, lean-mode development rules, and
  non-baseline exclusions.

structured_output_baseline_applicability: "not_applicable — this task does not implement LLM structured output."

deliverable:
  - docs/phase0/SDUI_RENDERER_ARCHITECTURE_BASELINE.md
  - MANIFEST.md registration for docs/phase0/SDUI_RENDERER_ARCHITECTURE_BASELINE.md
  - docs/phase0/task_logs/P0-FE-ARCH-001_<timestamp>_passed.yaml

constraints:
  - Must not implement full SDUI card library
  - Must not create web/src/sdui/** code in this task unless a later task explicitly authorizes code implementation
  - Must define ComponentRegistry interface: component type key, registration contract, lookup behavior, and unknown component handling
  - Must define ActionDispatcher interface: allowed action envelope, dispatch contract, side-effect boundary, and boundary with Zustand/TanStack Query
  - Must define FallbackRenderer interface: unknown card type handling, graceful degradation, telemetry/logging limits, and user-safe display behavior
  - Must define schema_version strategy: version field, compatibility checks, migration/ignore behavior, and rejection criteria
  - Must define user_action boundary: what SDUI cards can request, what they cannot trigger, and how backend/gateway actions remain outside renderer trust
  - Must reference FRONTEND_AI_CODING_GUIDELINES.md for frontend conventions
  - Must reference the frozen frontend baseline: React 18 + Vite + TypeScript strict + Ant Design 5.x + ProComponents 2.x
  - The frozen frontend baseline must not be modified
  - Architecture decisions must be documented as PDR (plan, alternatives, risks, recommendation)
  - Must not add dependencies, change lockfiles, add CI, add Docker, add database, add Redis/ARQ, add Langfuse, or implement backend integration

acceptance_criteria:
  - criterion: "docs/phase0/SDUI_RENDERER_ARCHITECTURE_BASELINE.md created and registered in MANIFEST.md by this task"
    result: "pending"
    evidence: ""
  - criterion: "ComponentRegistry interface defined (type, registration contract, lookup behavior)"
    result: "pending"
    evidence: ""
  - criterion: "ActionDispatcher interface defined (action types, dispatch contract, boundary with Zustand)"
    result: "pending"
    evidence: ""
  - criterion: "FallbackRenderer interface defined (unknown card type handling, graceful degradation)"
    result: "pending"
    evidence: ""
  - criterion: "schema_version strategy defined (versioning contract, backward compatibility approach)"
    result: "pending"
    evidence: ""
  - criterion: "user_action boundaries defined (what actions SDUI cards can trigger, what they cannot)"
    result: "pending"
    evidence: ""
  - criterion: "Alternatives considered and documented for each design decision"
    result: "pending"
    evidence: ""
  - criterion: "No web/src/sdui/** code created"
    result: "pending"
    evidence: ""
  - criterion: "Architecture references FRONTEND_AI_CODING_GUIDELINES.md conventions"
    result: "pending"
    evidence: ""
  - criterion: "Minimal architecture acceptance checks are listed in the baseline document"
    result: "pending"
    evidence: ""
  - criterion: "Final staged-diff evidence is recorded and Task Record changed_files exactly matches git diff --cached --name-only"
    result: "pending"
    evidence: ""

failure_examples_tested:
  - example: "ComponentRegistry interface is ambiguous about runtime vs static registration"
    result: "not_applicable"
    evidence: ""
    not_applicable_reason: "Architecture documentation task; interface clarity is verified through review, not runtime testing."
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "ActionDispatcher boundary with Zustand is not clearly defined"
    result: "not_applicable"
    evidence: ""
    not_applicable_reason: "Architecture documentation task; boundary clarity is verified through review."
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"

step_verification_points:
  - step: "Preflight: confirm branch and clean working tree"
    result: "pending"
    command: "git branch --show-current && git status --short"
    evidence: ""
  - step: "Read FRONTEND_AI_CODING_GUIDELINES.md for reference"
    result: "pending"
    command: "Test-Path docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md"
    evidence: ""
  - step: "Read P0-INFRA-003 Task Record for frontend skeleton context"
    result: "pending"
    command: "Get-ChildItem docs/phase0/task_logs/P0-INFRA-003_*_passed.yaml"
    evidence: ""
  - step: "Create SDUI Renderer architecture baseline document"
    result: "pending"
    command: "Test-Path docs/phase0/SDUI_RENDERER_ARCHITECTURE_BASELINE.md"
    evidence: ""
  - step: "Register SDUI Renderer architecture baseline in MANIFEST.md"
    result: "pending"
    command: "Select-String -Path MANIFEST.md -Pattern 'docs/phase0/SDUI_RENDERER_ARCHITECTURE_BASELINE.md'"
    evidence: ""
  - step: "Define ComponentRegistry interface"
    result: "pending"
    command: "Select-String -Path docs/phase0/SDUI_RENDERER_ARCHITECTURE_BASELINE.md -Pattern 'ComponentRegistry'"
    evidence: ""
  - step: "Define ActionDispatcher interface"
    result: "pending"
    command: "Select-String -Path docs/phase0/SDUI_RENDERER_ARCHITECTURE_BASELINE.md -Pattern 'ActionDispatcher'"
    evidence: ""
  - step: "Define FallbackRenderer interface"
    result: "pending"
    command: "Select-String -Path docs/phase0/SDUI_RENDERER_ARCHITECTURE_BASELINE.md -Pattern 'FallbackRenderer'"
    evidence: ""
  - step: "Define schema_version strategy"
    result: "pending"
    command: "Select-String -Path docs/phase0/SDUI_RENDERER_ARCHITECTURE_BASELINE.md -Pattern 'schema_version'"
    evidence: ""
  - step: "Define user_action boundaries"
    result: "pending"
    command: "Select-String -Path docs/phase0/SDUI_RENDERER_ARCHITECTURE_BASELINE.md -Pattern 'user_action'"
    evidence: ""
  - step: "List minimal architecture acceptance checks"
    result: "pending"
    command: "Select-String -Path docs/phase0/SDUI_RENDERER_ARCHITECTURE_BASELINE.md -Pattern 'acceptance check','architecture acceptance'"
    evidence: ""
  - step: "Verify no web/src/sdui/** code created"
    result: "pending"
    command: "if (Test-Path web\\src\\sdui) { throw 'web/src/sdui must not be created by this task' }"
    evidence: ""
  - step: "Verify no forbidden paths modified"
    result: "pending"
    command: "git diff --name-only"
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
  - step: "Final dependency manifest and lockfile scan"
    result: "pending"
    command: "if (git diff --cached --name-only | Select-String -Pattern '(^|/)package.json$','(^|/)pnpm-lock.yaml$','(^|/)package-lock.json$','(^|/)yarn.lock$','^pyproject.toml$','^uv.lock$') { throw 'Dependency manifest or lockfile changed' }"
    evidence: ""
  - step: "Verify Task Record changed_files matches staged diff exactly"
    result: "pending"
    command: "$recordFile = Get-ChildItem docs/phase0/task_logs/P0-FE-ARCH-001_*_passed.yaml | Sort-Object LastWriteTime -Descending | Select-Object -First 1; $staged = @(git diff --cached --name-only); $lines = Get-Content $recordFile.FullName; $start = [Array]::IndexOf($lines, 'changed_files:'); $end = [Array]::IndexOf($lines, 'touched_paths_confirmed: true'); if ($start -lt 0 -or $end -le $start) { throw 'changed_files section not found' }; $record = @($lines[($start + 1)..($end - 1)] | Where-Object { $_ -match '^  - ' } | ForEach-Object { $_ -replace '^  - ', '' }); if ($staged.Count -ne $record.Count) { throw 'changed_files count mismatch' }; for ($i = 0; $i -lt $staged.Count; $i++) { if ($staged[$i] -ne $record[$i]) { throw 'changed_files order mismatch' } }"
    evidence: ""

touched_paths:
  - MANIFEST.md
  - docs/phase0/SDUI_RENDERER_ARCHITECTURE_BASELINE.md
  - docs/phase0/task_logs/P0-FE-ARCH-001_<timestamp>_passed.yaml

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
  - docs/phase0/task_logs/** except docs/phase0/task_logs/P0-FE-ARCH-001_<timestamp>_passed.yaml

stop_conditions:
  - "Working tree is not clean at task start"
  - "P0-INFRA-003 Task Record not found or result is not passed"
  - "Forbidden paths are modified"
  - "web/src/sdui/** code created without explicit later-task authorization"
  - "changed_files cannot be reconciled with staged diff"
  - "Task Record changed_files does not exactly match git diff --cached --name-only after staging"
  - "Commit, push, or merge is attempted before independent staged review"
```

## Structured-output baseline applicability

not_applicable — this task does not implement LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
