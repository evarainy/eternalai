# P0-DOC-SYNC-001 — Phase 0 Post-CI Documentation/Index Synchronization

## What

Synchronize Phase 0 documentation after P0-INFRA-007 and P0-INFRA-007A. Documentation/index cleanup task before P0-BATCH3-PROMPTS-001.

## Why

P0-INFRA-007 and P0-INFRA-007A are completed and remote GitHub Actions CI is green. A read-only audit found stale documentation/index entries that should be cleaned up before generating Batch 3 prompts.

## Allowed Scope

- `docs/phase0/task_logs/INDEX.md`
- `README.md`
- `docs/phase0/tasks/P0-DOC-SYNC-001.md`
- `docs/phase0/task_logs/P0-DOC-SYNC-001_<timestamp>_passed.yaml`
- `docs/phase0/TASK_INDEX.md`
- `MANIFEST.md`

## Forbidden Scope

Do not modify source code, CI behavior, dependency files, lockfiles, historical Task Record YAML content, Runtime/Gateway/Adapter/Database/SDUI files, or Ruff configuration.

## Required Work

1. Update `docs/phase0/task_logs/INDEX.md` to include missing passed records:
   - P0-INFRA-005
   - P0-INFRA-006
   - P0-INFRA-007
   - P0-INFRA-007A

2. Add a short README note explaining:
   - `web/pnpm-workspace.yaml` is intentional persistent frontend config.
   - It exists because P0-INFRA-007A fixed GitHub Actions frontend install under pnpm 11 strictDepBuilds.
   - It approves only esbuild build scripts: `allowBuilds: esbuild: true`
   - It does not use `dangerouslyAllowAllBuilds` and does not disable `strictDepBuilds`.

3. Do not edit P0-INFRA-007 historical Task Record YAML. Add an errata-style note in README that P0-INFRA-007A superseded the earlier closeout interpretation of `web/pnpm-workspace.yaml` as a local pnpm artifact.

4. Register P0-DOC-SYNC-001 in TASK_INDEX and MANIFEST.

5. Create Task Record for P0-DOC-SYNC-001.

## Validation

- `git diff --cached --name-only`
- `git diff --cached --stat`
- `git diff --cached --check`
- `git ls-files --others --exclude-standard`
- If there is a docs/index validation script, run it; otherwise record not_applicable with reason.

## Review

- No code changes
- No CI behavior changes
- No package/lockfile changes
- No historical Task Record YAML edits
- task_logs/INDEX.md entries match existing files
- README note is accurate and does not overstate supply-chain guarantees
- Task Record changed_files matches staged diff

Do not commit, push, or merge until review and human approval.
