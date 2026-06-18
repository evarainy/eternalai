# P0-RULES-001 — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Out-of-band notice

P0-RULES-001 is an out-of-band rules maintenance task, not part of Batch DAG, and intentionally not added to TASK_INDEX.md because TASK_INDEX currently tracks only batch execution tasks.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/CONTEXT_LOADING_STRATEGY.md

## Global hard rules

- Execute only this task_id.
- Do not modify frozen blueprint files.
- Do not modify AGENTS.md.
- Do not modify completed task prompts (P0-PREP-001/002/003, P0-SPIKE-004/005/006).
- Do not add unapproved dependencies.
- Stop after Unified Task Record and wait for human confirmation.

## Task YAML

```yaml
task_id: P0-RULES-001
title: Align Claude/Codex tool-specific rule loading behavior
type: documentation
objective: >
  清理 legacy cross-read 要求，使工具专属规则读取行为更清晰：
  Claude Code / MiMo 默认读取 CLAUDE.md，不默认读取 AGENTS.md；
  Codex / generic coding agent 默认读取 AGENTS.md，不默认读取 CLAUDE.md。
  仓库级通用规则通过 task template / task prompt 共享，不依赖 cross-read。
constraints:
  - 不修改 AGENTS.md
  - 不修改 docs/blueprint/ 冻结文件
  - 不修改已完成 task prompts
  - 不修改 app/、runtime/、gateway/、execution_fabric/
  - 不修改业务 ADR
  - 不新增依赖
acceptance_criteria:
  - AC-1: CLAUDE.md 不再引用 AGENTS.md
  - AC-2: CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md 使用 tool-specific split 写法，无同一行同时出现 "Claude Code" 和 "AGENTS.md"
  - AC-3: CONTEXT_LOADING_STRATEGY.md Tier 0 区分工具专属 boot file
  - AC-4: 未完成 task prompts (P0-SPIKE-001/002/003/007) Required context 改为 tool-specific boot file
  - AC-5: FIRST_BATCH_TASKS.md / README_FOR_CODEX.md 使用 tool-specific wording
  - AC-6: MANIFEST.md 包含 P0-RULES-001.md
  - AC-7: Task Record 生成且 YAML 合法
touched_paths:
  - docs/phase0/tasks/P0-RULES-001.md
  - docs/phase0/tasks/P0-SPIKE-001.md
  - docs/phase0/tasks/P0-SPIKE-002.md
  - docs/phase0/tasks/P0-SPIKE-003.md
  - docs/phase0/tasks/P0-SPIKE-007.md
  - docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
  - docs/phase0/CONTEXT_LOADING_STRATEGY.md
  - docs/phase0/FIRST_BATCH_TASKS.md
  - docs/phase0/README_FOR_CODEX.md
  - docs/phase0/task_logs/P0-RULES-001_*.yaml
  - CLAUDE.md
  - MANIFEST.md
forbidden_paths:
  - docs/blueprint/
  - AGENTS.md
  - app/
  - runtime/
  - gateway/
  - execution_fabric/
```

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
