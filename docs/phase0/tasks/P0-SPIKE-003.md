# P0-SPIKE-003 — Single-task Prompt

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
task_id: P0-SPIKE-003
title: PostgreSQL 18 + pgvector >= 0.8.2 Deployment Spike
type: spike
depends_on:
  - P0-PREP-003
objective: 验证 PostgreSQL 18 与 pgvector >= 0.8.2 在 Docker Compose 单机环境下的部署、迁移、索引和最小向量查询能力。
deliverable:
  - docs/adr/phase0/ADR-P0-SPIKE-003-postgresql-pgvector.md
constraints:
  - 只验证部署与最小查询
  - 不实现正式 Memory / RAG 功能
acceptance_criteria:
  - docker compose 可启动 PostgreSQL 18
  - pgvector 版本 >= 0.8.2
  - Alembic 可创建带 vector 字段的测试表
  - 最小相似度查询通过
  - ADR 明确是否采用该组合进入 Phase 1
spike_code_disposition: 废弃或沉淀为 tests/utils/
touched_paths:
  - docs/adr/phase0/
  - experiments/phase0/postgres_pgvector/
  - infra/docker/
  - tests/utils/
forbidden_paths:
  - app/domain/
  - app/runtime/
  - app/gateway/
```

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
