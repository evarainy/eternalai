# P0-PREP-001 — Single-task Prompt

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
task_id: P0-PREP-001
title: Repository and Environment Readiness Check
type: preparation
objective: 只读检查当前仓库、运行环境、内网依赖条件和硬件条件，为 Phase 0 执行做准备。
constraints:
  - 只读检查为主
  - 不修改主蓝图
  - 不新增生产代码
  - 不安装未知依赖
acceptance_criteria:
  - 输出当前仓库根目录、分支、git status
  - 检查 Codex / Claude Code 可用配置：sandbox / approval / network posture；记录是否启用 Codex OTel 或替代审计手段（Git + CI + Task Record）
  - 检查 Python / uv / Node / pnpm / Docker / Docker Compose 是否可用
  - 检查 GPU / CUDA / vLLM 相关条件是否存在；没有则记录阻塞项，不编造结果
  - 检查内网 PyPI / npm 镜像配置是否存在；没有则记录阻塞项
  - 输出 repo_env_audit.md
touched_paths:
  - docs/phase0/repo_env_audit.md
forbidden_paths:
  - app/
  - web/
  - enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md
```

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
