# FIRST_BATCH_TASKS — Batch 0 + Batch 1 Index v1.0.11

This file is a **task selection index only**. It no longer embeds full task YAML.

For execution, open exactly one per-task prompt:

```text
docs/phase0/tasks/<task_id>.md
```

Do not paste this file plus the full task prompt plus the long spec into the same session. Use this file to choose the next task, then execute only the selected task with `CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md`.

## Hard boundary

- FIRST_BATCH only permits `P0-PREP-*` and `P0-SPIKE-*`.
- `P0-PREP-*` are execution-pack-only preparation tasks. They do not implement Runtime, Gateway, Adapter, Golden Task runner, or business capability.
- Complete `P0-PREP-*` before starting `P0-SPIKE-*`.
- Do not create engineering skeletons early.
- Do not implement Gateway, Runtime, Adapter, Policy, Identity, Trace, SDUI, or Golden Task runner early.
- Spike code must stay in `experiments/phase0/`, `docs/adr/`, `docs/research/`, temporary experiment paths, or `tests/utils/` when explicitly allowed. It must not enter `app/`.
- `ADR-P0-SPIKE-005a-*`, `ADR-P0-SPIKE-005b-*`, and `ADR-P0-SPIKE-005c-*` are ADR deliverable filenames under `P0-SPIKE-005`; they are not task IDs.

## Required execution wrapper

For every selected task:

1. Read `AGENTS.md` or `CLAUDE.md` boot rules.
2. Read the task prompt listed below.
3. Use `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md`.
4. Produce a Plan first.
5. Wait for confirmation before modifying files.
6. Output a Unified Task Record when done.

## Batch 0 — Repository preparation

| order | task_id | prompt | requires_gpu | note |
|---:|---|---|---|---|
| 1 | `P0-PREP-001` | `docs/phase0/tasks/P0-PREP-001.md` | no | Read-only repo/environment readiness check |
| 2 | `P0-PREP-002` | `docs/phase0/tasks/P0-PREP-002.md` | no | Phase 0 docs/template placement check |
| 3 | `P0-PREP-003` | `docs/phase0/tasks/P0-PREP-003.md` | no | Frozen spec and blueprint placement check |

## Batch 1 — Spike ADR tasks

Spike tasks may run on different machines or sessions in parallel, but **each Codex / Claude Code session still executes only one `task_id` at a time**.

If GPU / CUDA / vLLM is unavailable, start with non-GPU spikes first.

| suggested_order | task_id | prompt | requires_gpu | output |
|---:|---|---|---|---|
| 1 | `P0-SPIKE-003` | `docs/phase0/tasks/P0-SPIKE-003.md` | no | PostgreSQL + pgvector ADR |
| 2 | `P0-SPIKE-004` | `docs/phase0/tasks/P0-SPIKE-004.md` | no | Redis + ARQ ADR |
| 3 | `P0-SPIKE-005` | `docs/phase0/tasks/P0-SPIKE-005.md` | no | OA / U8 / Hik API-auth ADRs |
| 4 | `P0-SPIKE-006` | `docs/phase0/tasks/P0-SPIKE-006.md` | no | S3-compatible storage ADR |
| 5 | `P0-SPIKE-001` | `docs/phase0/tasks/P0-SPIKE-001.md` | yes | Qwen structured output ADR |
| 6 | `P0-SPIKE-002` | `docs/phase0/tasks/P0-SPIKE-002.md` | yes | instructor + vLLM ADR |
| 7 | `P0-SPIKE-007` | `docs/phase0/tasks/P0-SPIKE-007.md` | yes | PydanticAI + Qwen/vLLM ADR |

## Stop conditions

Stop and ask for a task patch if:

- The referenced per-task prompt is missing.
- The prompt conflicts with `AGENTS.md` / `CLAUDE.md` hard rules.
- The task requires files outside its allowed paths.
- The task would require writing Spike code into `app/`.
- The task depends on unavailable GPU or vendor/system access and no non-GPU alternative is listed.


## Batch 2-7 prompt gate

FIRST_BATCH 只用于选择 Batch 0 / Batch 1 任务。Batch 1 完成后、Batch 2 启动前，必须根据 Phase 0 spec 生成 Batch 2-7 的 `docs/phase0/tasks/<task_id>.md` 文件。不得在缺少 per-task prompt 的情况下执行 Batch 2-7。
