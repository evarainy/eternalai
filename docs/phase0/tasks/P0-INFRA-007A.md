# P0-INFRA-007A — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CONTEXT_LOADING_STRATEGY.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md

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
task_id: P0-INFRA-007A
branch: "phase0/P0-INFRA-007A"
title: Frontend pnpm build approval hotfix
type: infrastructure
depends_on:
  - P0-INFRA-007
priority: hotfix
source_spec: "docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md"
task_index: "docs/phase0/TASK_INDEX.md"

objective: >
  Fix GitHub Actions Frontend CI install failure caused by pnpm 11
  strictDepBuilds blocking esbuild@0.25.12 build scripts with
  ERR_PNPM_IGNORED_BUILDS. Minimal fix: add web/pnpm-workspace.yaml
  with allowBuilds: esbuild: true. This is a targeted supply-chain
  approval for the Vite/esbuild build chain, not a blanket allowance.

deliverable:
  - web/pnpm-workspace.yaml

constraints:
  - Approve only esbuild build script
  - Do not use dangerouslyAllowAllBuilds
  - Do not disable strictDepBuilds
  - Do not change dependency versions
  - Do not change package.json or pnpm-lock.yaml
  - Do not broaden CI or dependency policy
  - Do not modify backend, runtime, gateway, adapters, database, SDUI
  - Do not modify .github/workflows/ci.yml

acceptance_criteria:
  - criterion: "pnpm install --frozen-lockfile passes"
    result: "pending"
    evidence: ""
  - criterion: "Frontend lint / typecheck / build pass"
    result: "pending"
    evidence: ""
  - criterion: "package.json and lockfile unchanged"
    result: "pending"
    evidence: ""
  - criterion: "web/pnpm-workspace.yaml contains only explicitly reviewed allowBuilds entries"
    result: "pending"
    evidence: ""
  - criterion: "GitHub Actions frontend job expected to pass after push"
    result: "pending"
    evidence: ""

touched_paths:
  - web/pnpm-workspace.yaml
  - docs/phase0/tasks/P0-INFRA-007A.md
  - docs/phase0/task_logs/
  - MANIFEST.md

forbidden_paths:
  - app/runtime/
  - app/gateway/
  - app/execution_fabric/real_adapters/
  - web/package.json (unless explicitly required)
  - web/pnpm-lock.yaml (unless explicitly required)
  - .github/workflows/ci.yml

stop_conditions:
  - "Working tree is not clean at task start"
  - "Forbidden paths are modified"
  - "pnpm-lock.yaml or package.json changed"
  - "dangerouslyAllowAllBuilds or strictDepBuilds: false present"
  - "Dependency versions changed"
```

## Execution instruction

Minimal hotfix. Create web/pnpm-workspace.yaml, verify locally, stage, output Task Record, wait for human review.
