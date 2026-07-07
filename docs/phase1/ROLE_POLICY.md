# Phase 1 Role Policy

This file is the Phase 1 source of truth for role assignment, review shape, and risk tier policy. It is intentionally policy-only; it does not define model selection, cost strategy, multi-agent orchestration, or machine-readable lint rules.

## Roles & canonical agent ids

Canonical `execution_owner` and `review_owner` ids:

- `codex`
- `claude_code`
- `human`

`claude_code_mimo` is a deprecated id. It remains valid only when interpreting historical prompts, task records, or review artifacts. New Phase 1 prompts must not use it.

## Default assignment for Phase 1

Phase 1 defaults:

- `execution_owner: codex`
- `review_owner: claude_code`

Codex executes the task and performs self-review as the executor. Claude Code / Opus performs independent read-only review when independent review is required. Human owners approve Plans, perform merge/integration actions, and record deferred evidence when local task execution cannot produce it.

The executor cannot be the only approver for tasks that require independent review.

## review_mode enum

`review_mode` records the review process shape, not the tool or model name. Concrete actors are recorded in `execution_owner` and `review_owner`.

Allowed values for new Phase 1 prompts:

- `self_review`: the executor reviews its own work.
- `independent_review`: a reviewer independent from the executor reviews the Plan, diff, or artifact.
- `human_review`: a human reviewer performs the review or approval step.
- `none`: no review is required because the task has no repo-changing artifact or the task prompt explicitly allows no review.

`high` risk tasks must use `independent_review`.

## risk_tier

Allowed values:

- `low`
- `medium`
- `high`

Default rules:

| Task surface | Default risk_tier |
|---|---|
| CI, gate, thresholds, frozen ids, fixtures, or schema | `high` |
| `app/` code or tests | `medium` |
| Docs only | `low` |
| Unspecified | `medium` |

Humans may raise a task's risk tier. Humans must not lower a task already classified as `high` by these rules.

Review requirements:

| risk_tier | Review requirement |
|---|---|
| `low` | `self_review` is allowed unless the task prompt requires more. |
| `medium` | `independent_review` is expected for repository-changing tasks. |
| `high` | `independent_review` is required. |

## Legacy migration table

These mappings are for interpreting historical artifacts only. Do not bulk edit or backfill old prompts or task records.

| Historical value | New interpretation |
|---|---|
| `claude_code_mimo` owner id | `claude_code` |
| `codex_review` | `independent_review` when reviewer and executor are different; `self_review` when Codex reviewed its own work. |
| `self_check` | `self_review` |
| `human_optional` | `human_review` when a human actually reviewed; otherwise `none` or `self_review` according to the artifact evidence. |

Old values remain historical evidence. New prompts must use the canonical ids and `review_mode` enum in this file.

## AGENTS.md status

`AGENTS.md` is a compact boot file for generic coding agents. It is not the full Phase 1 role and review policy. When Phase 1 role or review guidance conflicts, use this `ROLE_POLICY.md` together with the current task prompt and `CLAUDE.md`.
