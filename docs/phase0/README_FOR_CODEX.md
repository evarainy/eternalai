# README_FOR_CODEX — Phase 0 Execution Pack v1.0.11

This is the entry guide for running Phase 0 with Codex / Claude Code.

v1.0.11 keeps the existing architecture, task split, and acceptance rules. It preserves the existing context-reduction strategy and adds execution-readiness fixes. Do not load the long spec for every task.

## Start here

1. Read root `AGENTS.md`.
2. If using Claude Code, also read root `CLAUDE.md`.
3. Read `docs/phase0/CONTEXT_LOADING_STRATEGY.md` once.
4. Choose one task from `docs/phase0/FIRST_BATCH_TASKS.md`.
5. Open only that task prompt: `docs/phase0/tasks/<task_id>.md`.
6. Wrap the task with `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md`.
7. Produce a Plan first; wait for confirmation before modifying files.
8. Save the Unified Task Record using `docs/dev/task_record_schema.yaml`.

## Do not over-load context

For normal task execution, do **not** paste all of these together:

- full Phase 0 spec;
- full frozen blueprint;
- full `FIRST_BATCH_TASKS.md` with the current per-task prompt;
- every dev governance file.

Use the smallest useful context: boot rules + current per-task prompt + single-task wrapper. Load `TASK_INDEX.md`, `BOUNDARY_CHECKLIST.md`, `docs/dev/*`, or the long spec only when the task explicitly needs them.

## Current execution scope

Only Batch 0 and Batch 1 are allowed from this pack:

- `P0-PREP-*`: execution-pack-only preparation tasks;
- `P0-SPIKE-*`: Spike ADR tasks.

Do not start engineering skeletons, Domain Foundation, Gateway, Runtime, Adapter, Policy, Identity, Trace, SDUI, or Golden Task runner from this first-batch pack.

`P0-PREP-*` full definitions live in `docs/phase0/tasks/P0-PREP-001.md`, `P0-PREP-002.md`, and `P0-PREP-003.md`; they are not business implementation tasks.

## Non-negotiable rules

- Do not modify the frozen main blueprint.
- Do not expand Phase 1 / Phase 2-5 specs.
- Do not connect to real production OA / U8 / Hik systems.
- Do not save real password, token, cookie, sessionid, access_token, or refresh_token values.
- Spike code must not enter `app/`.
- One session executes one `task_id` at a time.
- `not_applicable` must include reason, scope, blocked task, activation task, expiry condition, and evidence.
- Golden Task positive pass rate must be >= 80%; negative / boundary / safety rejection paths must pass 100%.
- Human review is optional / recommended, not a v1.0.11 blocking condition.
- Claude Code hooks / subagents are optional enhancements; they do not replace Task Record, ADR, CI/self-check commands, or test evidence.

## Task outputs

Each task must produce a Unified Task Record:

```text
docs/phase0/task_logs/<task_id>_<YYYYMMDD_HHMMSS>_<passed|failed>.yaml
```

Also update:

```text
docs/phase0/task_logs/INDEX.md
```

The fixed package confirmation phrase may be used only with package status/evidence recorded in the Task Record:

```text
confirmation that a fresh package was created from the current repository state
```

In v1.0.11, this phrase means the task deliverable was regenerated, organized, or recorded from the current repository state. It is not tied to mandatory human diff review.

## Failure handling

If a task cannot satisfy its acceptance criteria:

1. Stop modifying files.
2. Do not mark a failing check as `not_applicable`.
3. Output the blocked acceptance criterion and evidence.
4. Save a failed Task Record.
5. Wait for a task patch or human decision.


## Batch 2-7 prompt gate

本执行包当前只包含 Batch 0 / Batch 1 的 per-task prompt。Batch 1 完成后、Batch 2 启动前，必须先生成 Batch 2-7 的 `docs/phase0/tasks/<task_id>.md`，再继续执行。不得用主 spec 全文替代缺失的 per-task prompt。
