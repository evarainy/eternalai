# P0-STYLE-001 — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Out-of-band notice

P0-STYLE-001 is an out-of-band style/governance maintenance task, not part of Batch DAG, and intentionally not added to TASK_INDEX.md because TASK_INDEX currently has no rules/out-of-band section.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/CONTEXT_LOADING_STRATEGY.md
- docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md

## Global hard rules

- Execute only this task_id.
- Do not modify frozen blueprint files.
- Do not modify any existing task prompts except this file (docs/phase0/tasks/P0-STYLE-001.md).
- Do not modify docs/phase0/CONTEXT_LOADING_STRATEGY.md.
- Do not modify TASK_INDEX.md.
- Do not modify CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md.
- Do not add unapproved dependencies.
- Do not commit or push.
- Do not create docs/phase0/REPOSITORY_CONTEXT_MAP.md in this task.
- Do not redesign MANIFEST.md / TASK_INDEX.md / CONTEXT_LOADING_STRATEGY.md in this task.
- Stop after Unified Task Record and wait for human confirmation.

## Non-goals / deferred tasks

- Do not create docs/phase0/REPOSITORY_CONTEXT_MAP.md.
- Do not redesign MANIFEST.md / TASK_INDEX.md / CONTEXT_LOADING_STRATEGY.md.
- Do not create formatter/linter/security tool configuration files.
- Do not create database migration / SQL schema / ORM model / database config.
- Do not create OpenAPI schema / API route / capability registry schema / plugin protocol.

## Follow-up: P0-NAV-001

After P0-STYLE-001 is completed, a separate P0-NAV-001-style task should create a repository context map / progressive loading guide, likely docs/phase0/REPOSITORY_CONTEXT_MAP.md. P0-STYLE-001 does not create that navigation document.

## Task YAML

```yaml
task_id: P0-STYLE-001
title: Define coding style and quality baseline for multi-tool development
type: documentation
objective: >
  建立轻量、可执行的代码风格与质量基线文档 (docs/phase0/CODING_STYLE_BASELINE.md)，
  降低多 AI 工具（Claude Code / MiMo / Codex）协作中的风格漂移和无关改动。
  同步创建本 task prompt，并更新 MANIFEST.md 和 ROLE_AND_METHOD_GUARDRAILS.md 指针。
  本文档是 compact coding style and quality baseline，不是安全手册、
  DevSecOps 接入规格、完整 ORM 规范、完整 API 规范或插件协议规范。
  本文档通过 selective loading / progressive disclosure 方式应用，
  不要求每个任务全文加载。
constraints:
  - 不修改 docs/blueprint/ 冻结文件
  - 不修改 app/、runtime/、gateway/、execution_fabric/
  - 不修改业务 ADR
  - 不修改依赖文件 / lock files（pyproject.toml、requirements.txt、uv.lock、package.json、pnpm-lock.yaml）
  - 不修改 TASK_INDEX.md
  - 不修改 docs/phase0/CONTEXT_LOADING_STRATEGY.md
  - 不修改 CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
  - 不修改已有 task prompts（P0-PREP-001/002/003, P0-SPIKE-001~007, P0-RULES-001/002）
  - 不新增依赖
  - 不新增 formatter/linter/security tool 配置文件
  - 不新增 database migration / SQL schema / ORM model / database config
  - 不新增 OpenAPI schema / API route / capability registry schema / plugin protocol
  - 不创建 docs/phase0/REPOSITORY_CONTEXT_MAP.md（留待 P0-NAV-001）
  - 不重新设计 MANIFEST.md / TASK_INDEX.md / CONTEXT_LOADING_STRATEGY.md
  - MANIFEST.md 只允许添加新文档条目，不重排或重构已有内容
  - 不 commit / push / merge
acceptance_criteria:
  - AC-1: docs/phase0/CODING_STYLE_BASELINE.md 存在且包含全部 16 个 section 标题
  - AC-2: docs/phase0/CODING_STYLE_BASELINE.md 包含 Section applicability guide
  - AC-3: docs/phase0/CODING_STYLE_BASELINE.md 包含 selective loading / progressive disclosure 原则
  - AC-4: docs/phase0/CODING_STYLE_BASELINE.md 包含 Database / schema style baseline
  - AC-5: docs/phase0/CODING_STYLE_BASELINE.md 包含 API / interface style baseline
  - AC-6: docs/phase0/CODING_STYLE_BASELINE.md 包含 Extension point / capability plug-in style baseline
  - AC-7: docs/phase0/CODING_STYLE_BASELINE.md 包含 Security / privacy baseline
  - AC-8: docs/phase0/CODING_STYLE_BASELINE.md 包含 Dependency / supply-chain / license baseline
  - AC-9: docs/phase0/CODING_STYLE_BASELINE.md 包含 Configuration / environment baseline
  - AC-10: docs/phase0/CODING_STYLE_BASELINE.md 包含 Error handling / logging / observability baseline
  - AC-11: docs/phase0/CODING_STYLE_BASELINE.md 包含 P0-NAV-001 边界声明
  - AC-12: docs/phase0/tasks/P0-STYLE-001.md 存在且包含完整 task YAML
  - AC-13: ROLE_AND_METHOD_GUARDRAILS.md 的 Code Style Discipline 章节包含对 CODING_STYLE_BASELINE.md 的轻量引用，措辞含 selective loading
  - AC-14: MANIFEST.md 包含 docs/phase0/tasks/P0-STYLE-001.md 和 docs/phase0/CODING_STYLE_BASELINE.md
  - AC-15: Task Record 生成且 YAML 合法
  - AC-16: changed_files 与 git diff --cached --name-only 完全一致
  - AC-17: 未修改 forbidden paths
  - AC-18: 未创建 REPOSITORY_CONTEXT_MAP.md
  - AC-19: package_confirmation 使用真实 not_applicable
  - AC-20: 未新增 formatter/linter/security tool 配置、database migration、SQL schema、ORM、OpenAPI、API route、capability registry、plugin protocol
touched_paths:
  - docs/phase0/tasks/P0-STYLE-001.md
  - docs/phase0/CODING_STYLE_BASELINE.md
  - MANIFEST.md
  - docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md
  - docs/phase0/task_logs/P0-STYLE-001_*.yaml
forbidden_paths:
  - docs/blueprint/
  - docs/phase0/CONTEXT_LOADING_STRATEGY.md
  - docs/phase0/TASK_INDEX.md (unless has rules/out-of-band section)
  - CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
  - app/
  - runtime/
  - gateway/
  - execution_fabric/
  - pyproject.toml
  - requirements.txt
  - uv.lock
  - package.json
  - pnpm-lock.yaml
  - existing task prompts (except new P0-STYLE-001.md)
  - docs/phase0/REPOSITORY_CONTEXT_MAP.md (not created in this task)
validation:
  - git status --short
  - git diff --cached --name-only
  - git diff --cached --stat
  - Task Record YAML safe_load
  - Select-String checks for all 16 CODING_STYLE_BASELINE.md section titles
  - Select-String checks for selective loading / progressive disclosure keywords
  - Select-String checks for Database / API / Extension point / Security / Dependency / Config / Observability / AI collaboration content
  - Select-String check for P0-NAV-001 boundary statement
  - Select-String check for ROLE_AND_METHOD_GUARDRAILS.md pointer
  - Select-String check for MANIFEST.md new entries
  - Test-Path check REPOSITORY_CONTEXT_MAP.md does not exist
  - git diff --cached --name-only compared with Task Record changed_files
  - forbidden path check
```

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.
