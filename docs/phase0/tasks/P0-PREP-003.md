# P0-PREP-003 — Single-task Prompt

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
task_id: P0-PREP-003
title: Freeze Spec v1.0.11 Placement Check
type: preparation
depends_on:
  - P0-PREP-002
objective: 确认冻结蓝图和 Phase 0 v1.0.11 spec 已放入 docs/blueprint/，后续任务只引用这两个源文件，不直接改写。
acceptance_criteria:
  - docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md 存在
  - docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md 存在
  - 输出 spec_freeze_check.md，声明不再直接修改冻结蓝图和 v1.0.11 spec
  - 若缺文件，停止执行并要求人工补充，不得自行重写
touched_paths:
  - docs/phase0/spec_freeze_check.md
forbidden_paths:
  - docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md
  - docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md
```

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
