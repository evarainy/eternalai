# P0-PREP-002 — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- AGENTS.md
- CLAUDE.md when using Claude Code
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only as source of truth; do not paste it in full.

## P0-PREP scope

This is an execution-pack-only preparation task. It must not implement Runtime, Gateway, Adapter, Golden Task runner, or any business capability.

## Global hard rules

- Execute only this task_id.
- Do not modify frozen blueprint files.
- Do not implement Phase 1 features.
- Do not add unapproved dependencies.
- Do not weaken tests to pass.
- Stop after Unified Task Record and wait for human confirmation.

## Task YAML

```yaml
task_id: P0-PREP-002
title: Phase 0 Docs Directory and Template Setup
type: preparation
depends_on:
  - P0-PREP-001
objective: 建立 Phase 0 执行文档目录和模板，确保后续 Spike ADR、task log、自检记录有固定位置。
acceptance_criteria:
  - docs/adr/phase0/ 存在
  - docs/phase0/task_logs/ 存在
  - docs/phase0/self_checks/ 存在
  - ADR_TEMPLATE.md 可直接复制使用
  - BOUNDARY_CHECKLIST.md 可直接执行
touched_paths:
  - docs/adr/phase0/
  - docs/phase0/
forbidden_paths:
  - app/
  - web/
```

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
