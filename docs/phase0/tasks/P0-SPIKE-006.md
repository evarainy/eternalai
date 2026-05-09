# P0-SPIKE-006 — Single-task Prompt

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
task_id: P0-SPIKE-006
title: S3-compatible Object Storage Candidate Spike
type: spike
depends_on:
  - P0-PREP-003
objective: 验证 MinIO / NAS S3 Gateway / 企业对象存储候选的最小上传、下载、权限配置和 Docker Compose 部署可行性。
deliverable:
  - docs/adr/phase0/ADR-P0-SPIKE-006-s3-compatible-storage.md
constraints:
  - 不实现正式文件管理模块
  - 不锁死具体供应商
acceptance_criteria:
  - 至少验证一个 S3-compatible 候选
  - 完成最小 put/get/delete 测试
  - 记录许可证、内网部署、备份、权限、运维风险
  - 给出 Phase 1 是否需要启用对象存储的建议
spike_code_disposition: 废弃或沉淀为 tests/utils/
touched_paths:
  - docs/adr/phase0/
  - experiments/phase0/s3_storage/
  - tests/utils/
forbidden_paths:
  - app/
```

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
