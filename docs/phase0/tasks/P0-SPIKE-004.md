# P0-SPIKE-004 — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- AGENTS.md
- CLAUDE.md when using Claude Code
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only as source of truth; do not paste it in full.

## Global hard rules

- Execute only this task_id.
- Do not modify frozen blueprint files.
- Do not implement Phase 1 features.
- Do not add unapproved dependencies.
- Do not weaken tests to pass.
- Stop after Unified Task Record and wait for human confirmation.

## Task YAML

```yaml
task_id: P0-SPIKE-004
title: Redis + ARQ Baseline Spike
type: spike
depends_on:
  - P0-PREP-003
objective: 验证 Redis + ARQ 在单机 Docker Compose 环境下的任务入队、执行、失败重试、超时和结果记录能力。
deliverable:
  - docs/adr/phase0/ADR-P0-SPIKE-004-redis-arq.md
constraints:
  - 不把 ARQ 写死为长期底座
  - 不实现正式业务异步任务
  - Spike 代码不得进入 app/ 正式模块
acceptance_criteria:
  - 最小任务可入队并执行
  - 失败任务可记录异常
  - 超时任务有明确错误
  - ADR 明确 Phase 1 是否启用 ARQ 作为 L1 候选
spike_code_disposition: 废弃或沉淀为 tests/utils/
touched_paths:
  - docs/adr/phase0/
  - experiments/phase0/redis_arq/
  - infra/docker/
  - tests/utils/
forbidden_paths:
  - app/runtime/
  - app/gateway/
```

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
