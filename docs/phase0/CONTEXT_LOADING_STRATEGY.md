# Phase 0 Context Loading Strategy v1.0.11

## Goal

Avoid context pollution. Codex and Claude Code should receive only the context needed for the current task, while preserving hard architectural and safety boundaries.

## Tiered loading model

### Tier 0 — Compact boot files
Tool-specific startup files:

- `AGENTS.md` — Codex / generic coding agent boot rules
- `CLAUDE.md` — Claude Code / MiMo boot rules

Each tool reads only its own boot file by default. Cross-reading the other tool's boot file is not required unless the per-task prompt or task template explicitly requests it.

These files must stay short. They contain hard rules and pointers only. They must not include long task lists, full schema sections, full specs, or full acceptance matrices.

### Tier 1 — Current task context
Primary working context for a session:

- `docs/phase0/tasks/<task_id>.md`

This file should contain the current task definition, acceptance criteria, blocking/failure examples, verification points, touched_paths, forbidden_paths, and any minimum schema/contract snippets needed by that task.

### Tier 2 — On-demand process references
Read only when the task requires them:

- `docs/phase0/TASK_INDEX.md` — dependency DAG and batch order
- `docs/phase0/BOUNDARY_CHECKLIST.md` — every-three-task checks
- `docs/dev/git_workflow.md` — branch/commit/rollback rules
- `docs/dev/package_definition.md` — package confirmation semantics
- `docs/dev/task_record_schema.yaml` — machine-readable task record schema
- `docs/dev/human_review_checklist.md` — optional human review
- `docs/dev/codex_setup.md` — Codex execution posture

### Tier 3 — Canonical long references
Consult only when resolving contradictions, generating future per-task prompts, or patching the spec:

- `docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md`
- `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md`
- `docs/blueprint/phase0_handoff_after_blueprint_freeze.md`

Do not paste Tier 3 documents into every coding-agent session.

## Per-task execution rule

For each task, provide only:

1. 本工具对应的 boot file（Codex → `AGENTS.md`；Claude Code → `CLAUDE.md`）;
2. the single `docs/phase0/tasks/<task_id>.md`;
3. `CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md`;
4. any explicitly referenced Tier 2 files needed for that task.

If the per-task prompt is incomplete, the agent must stop with `task_prompt_incomplete` and request a task-prompt patch. It must not compensate by reading unrelated sections or inventing missing contracts.

## Claude Code-specific guidance

- Avoid `@import` from root `CLAUDE.md` to long documents. Imports are reserved for short, task-specific files when a human explicitly asks.
- Use subagents for isolated checks where helpful because they have separate context windows.
- Hooks remain optional and must be reviewed before enabling.

## Codex-specific guidance

- Keep `AGENTS.md` compact because project docs may be size-limited by the installed Codex version.
- Prefer per-task prompts instead of repeatedly sending long specs.
- Use suggest/read-only mode for planning and workspace-scoped editing for approved implementation tasks.

## Maintenance rules

- Future batch preparation should generate new `docs/phase0/tasks/<task_id>.md` files before coding begins.
- Root boot files should not grow beyond compact hard rules.
- Detailed architecture changes belong in the spec or ADRs, not in root boot files.


## v1.0.11 Note

Hooks / subagent are optional enhancements only. Do not load them as mandatory blocking context. The minimal task context is: tool-appropriate boot file (AGENTS.md or CLAUDE.md) + current per-task prompt + CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md + relevant evidence templates.


## Batch 2-7 Prompt Generation Gate

Batch 0 / Batch 1 使用执行包内已有 `docs/phase0/tasks/<task_id>.md`。Batch 2-7 的 per-task prompt 在本包中尚未生成，必须在 Batch 1 完成后基于最新 ADR 结论和 Phase 0 spec 生成；缺少 per-task prompt 时，Codex / Claude Code 必须停止并输出 `task_prompt_incomplete`。
