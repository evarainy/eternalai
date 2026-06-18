# P0-RULES-002 — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Out-of-band notice

P0-RULES-002 is an out-of-band rules maintenance task, not part of Batch DAG, and intentionally not added to TASK_INDEX.md because TASK_INDEX currently has no rules/out-of-band section.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/CONTEXT_LOADING_STRATEGY.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md (created by this task)

## Global hard rules

- Execute only this task_id.
- Do not modify frozen blueprint files.
- Do not modify any existing task prompts except this file (docs/phase0/tasks/P0-RULES-002.md).
- Do not modify docs/phase0/CONTEXT_LOADING_STRATEGY.md unless absolutely required (stop and report if so).
- Do not add unapproved dependencies.
- Do not commit or push.
- Stop after Unified Task Record and wait for human confirmation.

## Task YAML

```yaml
task_id: P0-RULES-002
title: Add role-based execution/review guardrails and engineering method selection
type: documentation
objective: >
  建立轻量、共享的角色型 guardrails 文档，涵盖 Execution Guardrails、
  Review Guardrails、Engineering Method Selection、Code Style Discipline、
  Superpowers Usage、External Guideline Handling。
  在 CLAUDE.md / AGENTS.md / CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md 中做最小引用。
  不升级 Batch 2 / Phase 1 任务模板。
constraints:
  - 不修改 docs/blueprint/ 冻结文件
  - 不修改 app/、runtime/、gateway/、execution_fabric/
  - 不修改业务 ADR
  - 不修改依赖文件 / lock files
  - 不修改 TASK_INDEX.md
  - 不修改 docs/phase0/CONTEXT_LOADING_STRATEGY.md（除非执行中发现必须）
  - 不修改已有 task prompts（P0-PREP-001/002/003, P0-SPIKE-001~007, P0-RULES-001）
  - 不新增依赖
  - 不 commit / push / merge
  - 不升级 Batch 2 / Phase 1 任务模板为 method_profile 体系；Batch 1 完成后由单独的 P0-TEMPLATE-001 类任务负责升级模板并加入 method_profile 和 BDD/TDD/PDR evidence rules
  - 不定义完整 coding style baseline（留待 P0-STYLE-001）
  - 不复制外部 CLAUDE.md 原文
  - 不引入外部仓库依赖
acceptance_criteria:
  - AC-1: docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md 存在且包含 Execution Guardrails、Review Guardrails、Engineering Method Selection、Code Style Discipline、Superpowers Usage、External Guideline Handling
  - AC-2: CLAUDE.md 包含对 ROLE_AND_METHOD_GUARDRAILS.md 的轻量引用，且不出现 AGENTS.md 字样
  - AC-3: AGENTS.md 包含对 ROLE_AND_METHOD_GUARDRAILS.md 的轻量引用，且不出现 CLAUDE.md 字样
  - AC-4: CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md 包含短 role/method section，引用 ROLE_AND_METHOD_GUARDRAILS.md
  - AC-5: MANIFEST.md 包含 docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md 和 docs/phase0/tasks/P0-RULES-002.md
  - AC-6: Task Record 生成且 YAML 合法
  - AC-7: changed_files 与 git diff --cached --name-only 完全一致
  - AC-8: 未修改 forbidden paths
  - AC-9: 未修改已有 task prompts
  - AC-10: package_confirmation 使用真实 not_applicable
touched_paths:
  - docs/phase0/tasks/P0-RULES-002.md
  - docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
  - CLAUDE.md
  - AGENTS.md
  - docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
  - MANIFEST.md
  - docs/phase0/task_logs/P0-RULES-002_*.yaml
forbidden_paths:
  - docs/blueprint/
  - docs/phase0/CONTEXT_LOADING_STRATEGY.md
  - app/
  - runtime/
  - gateway/
  - execution_fabric/
  - TASK_INDEX.md
validation:
  - git status --short
  - git diff --cached --name-only
  - git diff --cached --stat
  - Task Record YAML safe_load
  - Select-String -Path CLAUDE.md -Pattern "AGENTS\.md" (expect no output)
  - Select-String -Path AGENTS.md -Pattern "CLAUDE\.md" (expect no output)
  - Select-String check ROLE_AND_METHOD_GUARDRAILS.md required sections
  - Select-String check template references ROLE_AND_METHOD_GUARDRAILS.md
  - git diff --cached --name-only compared with Task Record changed_files
  - forbidden path check
  - existing task prompt check
```

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
