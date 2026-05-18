# P0-INFRA-003 — Single-task Prompt

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
task_id: P0-INFRA-003
branch: "phase0/P0-INFRA-003"
title: React 18 + Vite + Ant Design 5.x Frontend Skeleton
type: infrastructure
depends_on:
  - P0-INFRA-003A
  - P0-FE-GUIDE-001
priority: P0
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

method_profile:
  execution_role: "execution"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "TDD"
  reason_for_owner_choice: "Frontend skeleton with lint, build, and health page baseline. Claude Code/MiMo executes; Codex reviews. TDD because health page and build baseline are production-path code."

objective: >
  Establish React 18, TypeScript strict mode, Vite, Ant Design 5.x, React Router,
  TanStack Query, Zustand, pnpm frontend skeleton. Must include generate:api script
  for deterministic OpenAPI codegen against a checked-in mock OpenAPI fixture or
  stable backend OpenAPI JSON. Only provide health page, basic layout, and mock
  API call sample. Clarify ProComponents 2.x / MSW / Orval intranet mirror status.
  Does not decide Ant Design X adoption (handled by P0-FE-SPIKE-001 / P0-INFRA-003A)
  or frontend npm allowlist (handled by P0-INFRA-003A). Must reference
  docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md for frontend coding conventions.

structured_output_baseline_applicability: "not_applicable — this task does not implement LLM structured output."

deliverable:
  - web/

constraints:
  - No complete Admin Console implementation
  - No full AI chat UI implementation
  - No complete SDUI Renderer implementation
  - No backend integration beyond deterministic OpenAPI/mock API sample wiring
  - No CI changes
  - No Docker changes
  - No database changes
  - No Redis/ARQ changes
  - No Langfuse changes
  - Only health page, basic layout, and mock API call sample
  - Does not decide Ant Design X adoption — that decision is made by P0-FE-SPIKE-001 and finalized in P0-INFRA-003A
  - Does not decide frontend npm allowlist — that is finalized in P0-INFRA-003A
  - Must reference docs/phase0/FRONTEND_AI_CODING_GUIDELINES.md for all frontend coding conventions
  - Use enterprise intranet npm mirror or offline cache; AI must not add uncached dependencies
  - Frontend API client connecting to backend OpenAPI must be generated via Orval or equivalent OpenAPI codegen
  - web/package.json must include test, typecheck, and generate:api scripts; generate:api must execute deterministically, not just exist
  - Test runner must be Vitest; pnpm test script must invoke `vitest --run` for non-watch execution
  - If backend OpenAPI JSON is not stable or available, create a checked-in mock OpenAPI fixture under web/openapi/ and generate from that fixture
  - Do not hand-write fetch/axios calls for backend business API in business pages; health page static mock and pure fixture display are exempt
  - Phase 0 frontend skeleton does not require full Admin Console; must record whether ProComponents 2.x / MSW is cached in intranet npm mirror; if not cached, only record the dependency gap
  - pnpm-lock.yaml changes must be documented in the task log
  - Root dependency/lock files are forbidden: package.json, pnpm-lock.yaml, package-lock.json, yarn.lock, pyproject.toml, uv.lock
  - web/package.json and web/pnpm-lock.yaml are allowed only for the P0-INFRA-003 frontend skeleton
  - Must not modify CI, Docker, backend app/runtime code, backend tests, frozen blueprint files, or unrelated infra files

acceptance_criteria:
  - criterion: "pnpm install succeeds under intranet npm mirror or offline cache"
    result: "pending"
    evidence: ""
  - criterion: "pnpm lint succeeds"
    result: "pending"
    evidence: ""
  - criterion: "pnpm typecheck succeeds"
    result: "pending"
    evidence: ""
  - criterion: "pnpm build succeeds"
    result: "pending"
    evidence: ""
  - criterion: "TypeScript strict mode is enabled in web/tsconfig.json"
    result: "pending"
    evidence: ""
  - criterion: "pnpm test -- --run succeeds and exercises the health page route/component assertion"
    result: "pending"
    evidence: ""
  - criterion: "Frontend can access health page"
    result: "pending"
    evidence: ""
  - criterion: "pnpm generate:api executes against a deterministic OpenAPI input and rerun produces no unstaged generated-output diff"
    result: "pending"
    evidence: ""
  - criterion: "Final staged-diff evidence is recorded and Task Record changed_files exactly matches git diff --cached --name-only"
    result: "pending"
    evidence: ""

failure_examples_tested:
  - example: "Health page route or component missing before implementation"
    result: "triggered"
    evidence: "First TDD test run (before creating the health page component/route) fails via `cd web && pnpm test -- --run`; this is expected TDD evidence, not a blocking failure."
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "pnpm install fails due to missing intranet mirror or uncached dependency"
    result: "not_applicable"
    evidence: "Dependency policy enforced by P0-INFRA-008 allowlist checker"
    not_applicable_reason: "Dependency policy and allowlist checker from P0-INFRA-008 guard against this"
    not_applicable_scope: "current_phase_only"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"
  - example: "generate:api produces output that drifts from backend OpenAPI"
    result: "triggered"
    evidence: "Negative drift validation temporarily mutates generated API output or the mock OpenAPI fixture, runs the drift-check command, confirms non-zero exit, then reverts/deletes the temporary mutation."
    not_applicable_reason: "none"
    not_applicable_scope: "none"
    blocked_by_task_id: "none"
    activation_task_id: "none"
    expiry_condition: "N/A"

step_verification_points:
  - step: "Inspect existing web/package.json from prior task (if present) and preserve configuration"
    result: "pending"
    command: "if (Test-Path web\\package.json) { 'EXISTS_PRESERVE_INCREMENTAL' } else { 'NOT_EXISTS_CREATE_MINIMAL' }"
    evidence: ""
  - step: "Create web/ directory with React 18 + Vite + Ant Design skeleton and package.json test/typecheck/generate scripts"
    result: "pending"
    command: "if (!(Test-Path web)) { throw 'web directory missing' }; if (!(Test-Path web\\package.json)) { throw 'web/package.json missing' }; Select-String -Path web\\package.json -Pattern '\"test\"','vitest --run','\"typecheck\"','\"generate:api\"'"
    evidence: ""
  - step: "Run pnpm install"
    result: "pending"
    command: "cd web && pnpm install"
    evidence: ""
  - step: "Create health page test/spec FIRST (TDD red phase)"
    result: "pending"
    command: "if (!(Test-Path web\\src\\pages\\__tests__\\Health.test.tsx) -and !(Test-Path web\\src\\pages\\Health.test.tsx)) { throw 'Health page test missing' }"
    evidence: ""
  - step: "Run health page test — expect failure (TDD red phase: health page not yet implemented)"
    result: "pending"
    command: "cd web && pnpm test -- --run"
    evidence: "Expected non-zero exit. This is the TDD red-phase evidence — a failing test before implementation, not a blocking failure."
  - step: "Implement health page component and route (TDD green phase)"
    result: "pending"
    command: "Get-ChildItem -Path web\\src -Recurse -File | Select-String -Pattern 'health|Health'"
    evidence: ""
  - step: "Re-run pnpm build — expect pass (TDD green phase)"
    result: "pending"
    command: "cd web && pnpm build"
    evidence: ""
  - step: "Run health page test — expect pass (TDD green phase)"
    result: "pending"
    command: "cd web && pnpm test -- --run"
    evidence: ""
  - step: "Run pnpm lint"
    result: "pending"
    command: "cd web && pnpm lint"
    evidence: ""
  - step: "Run pnpm typecheck"
    result: "pending"
    command: "cd web && pnpm typecheck"
    evidence: ""
  - step: "Verify TypeScript strict mode"
    result: "pending"
    command: "Select-String -Path web\\tsconfig.json -Pattern '\"strict\"\\s*:\\s*true'"
    evidence: ""
  - step: "Run generate:api against deterministic OpenAPI input"
    result: "pending"
    command: "cd web && pnpm generate:api"
    evidence: ""
  - step: "Verify generated API output has no drift after rerun"
    result: "pending"
    command: |
      python -c "import textwrap; exec(textwrap.dedent(r'''
      import hashlib
      import json
      import pathlib
      import shutil
      import subprocess
      import tempfile

      repo = pathlib.Path.cwd()
      web = repo / 'web'
      generated_root = web / 'src' / 'generated'

      def snapshot() -> dict[str, str]:
          files = sorted(path for path in generated_root.rglob('*') if path.is_file())
          if not files:
              raise SystemExit('No generated API output files found under web/src/generated')
          return {
              path.relative_to(repo).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in files
          }

      pnpm = shutil.which('pnpm') or shutil.which('pnpm.cmd')
      if not pnpm:
          raise SystemExit('pnpm executable not found')

      before = snapshot()
      with tempfile.TemporaryDirectory() as tmp:
          tmpdir = pathlib.Path(tmp)
          (tmpdir / 'before.json').write_text(json.dumps(before, indent=2, sort_keys=True), encoding='utf-8')
          subprocess.run([pnpm, 'generate:api'], cwd=web, check=True)
          after = snapshot()
          (tmpdir / 'after.json').write_text(json.dumps(after, indent=2, sort_keys=True), encoding='utf-8')

      changed = sorted(
          (set(before) ^ set(after))
          | {key for key in set(before) & set(after) if before[key] != after[key]}
      )
      if changed:
          raise SystemExit('Generated API drift after pnpm generate:api:\n' + '\n'.join(changed))
      print('generated_api_no_drift')
      '''))"
    evidence: ""
  - step: "Negative generate:api drift validation"
    result: "pending"
    command: >
      Temporarily mutate a generated API client file or the mock OpenAPI fixture,
      rerun the same cross-platform Python drift check, confirm non-zero exit,
      then revert/delete the temporary mutation before staging.
    evidence: ""
  - step: "Verify health page exists"
    result: "pending"
    command: "Get-ChildItem -Path web\\src -Recurse -File | Select-String -Pattern 'health|Health'"
    evidence: ""
  - step: "Stage all files and verify diff"
    result: "pending"
    command: "git diff --cached --name-only"
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
    command: "if (git diff --cached --name-only | Select-String -Pattern '^docs/blueprint/','^docs/dev/dependency_policy.md$','^scripts/check_dependencies.py$','^app/','^services/api/tests/','^tests/','^docker-compose.yml$','^AGENTS.md$','^CLAUDE.md$','^\\.github/','^pyproject.toml$','^uv.lock$','^package.json$','^pnpm-lock.yaml$','^package-lock.json$','^yarn.lock$','^infra/','^docs/phase0/task_logs/(?!P0-INFRA-003_)') { throw 'Forbidden staged path detected' }"
    evidence: ""
  - step: "Final root dependency and lockfile scan"
    result: "pending"
    command: "if (git diff --cached --name-only | Select-String -Pattern '^package.json$','^pnpm-lock.yaml$','^package-lock.json$','^yarn.lock$','^pyproject.toml$','^uv.lock$') { throw 'Root dependency or lockfile changed' }"
    evidence: ""
  - step: "Verify Task Record changed_files matches staged diff exactly"
    result: "pending"
    command: "$recordFile = Get-ChildItem docs/phase0/task_logs/P0-INFRA-003_*_passed.yaml | Sort-Object LastWriteTime -Descending | Select-Object -First 1; $staged = @(git diff --cached --name-only); $lines = Get-Content $recordFile.FullName; $start = [Array]::IndexOf($lines, 'changed_files:'); $end = [Array]::IndexOf($lines, 'touched_paths_confirmed: true'); if ($start -lt 0 -or $end -le $start) { throw 'changed_files section not found' }; $record = @($lines[($start + 1)..($end - 1)] | Where-Object { $_ -match '^  - ' } | ForEach-Object { $_ -replace '^  - ', '' }); if ($staged.Count -ne $record.Count) { throw 'changed_files count mismatch' }; for ($i = 0; $i -lt $staged.Count; $i++) { if ($staged[$i] -ne $record[$i]) { throw 'changed_files order mismatch' } }"
    evidence: ""

touched_paths:
  - web/

forbidden_paths:
  - docs/blueprint/**
  - docs/dev/dependency_policy.md
  - scripts/check_dependencies.py except read-only validation only
  - app/**
  - services/api/tests/** unless explicitly authorized by this prompt
  - tests/** unless explicitly authorized by this prompt
  - docker-compose.yml
  - AGENTS.md
  - CLAUDE.md
  - .github/**
  - pyproject.toml
  - uv.lock
  - package.json
  - pnpm-lock.yaml
  - package-lock.json
  - yarn.lock
  - infra/** unrelated to the P0-INFRA-003 frontend skeleton
  - unrelated completed Task Records
  - docs/phase0/task_logs/** except docs/phase0/task_logs/P0-INFRA-003_<timestamp>_passed.yaml

allowed_dependency_files:
  - web/package.json
  - web/pnpm-lock.yaml

stop_conditions:
  - "Working tree is not clean at task start"
  - "pnpm install fails under intranet mirror constraints"
  - "Forbidden paths are modified"
  - "changed_files cannot be reconciled with staged diff"
  - "Any step_verification_point command returning a non-zero exit code (except expected TDD red-phase or temporary negative drift checks) must stop execution, report the failing command/output, and must not continue to the next step"
  - "Task Record changed_files does not exactly match git diff --cached --name-only after staging"
  - "Commit, push, or merge is attempted before independent staged review"
```

## Structured-output baseline applicability

not_applicable — this task does not implement LLM structured output.

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
