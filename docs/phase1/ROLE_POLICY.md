# Phase 1 Role Policy

This file is the Phase 1 source of truth for role assignment, review shape, and risk-tier ceremony.

Layering rule: repository docs define **result contracts** (what must be true when a task closes). **Process choreography** (who runs which step, packets, gate mechanics) lives in the `codex-claude` workflow skill and is not duplicated here.

## Roles & canonical agent ids

- `codex` — default executor: implementation, self-review, evidence/packet building, staging.
- `claude_code` — default reviewer: read-only Plan drafting/sanity-check and diff review.
- `human` — approves Plans (Gate 1), approves push/merge (Gate 2), owns red-line decisions.

Legacy migration table — legacy values are valid only when interpreting historical artifacts (do not backfill old prompts or records; new prompts must use canonical ids only):

| Legacy value | Maps to |
|---|---|
| `claude_code_mimo` (owner id) | `claude_code` |
| `codex_review` (review mode) | `independent_review`, or `self_review` by artifact context |
| `self_check` | `self_review` |
| `human_optional` | `human_review` if a review actually happened, else `none` |

## Defaults

- `execution_owner: codex`, `review_owner: claude_code`.
- The executor can never be the sole approver of its own work.
- A Plan may be drafted by either the executor or the reviewer; the non-author side must record a written sanity-check before human approval. Diff review remains the final defense regardless of who drafted the Plan.

## review_mode enum

- `self_review` — executor first pass; never sufficient on its own for a repo-changing task.
- `independent_review` — a reviewer independent from the executor reviews the Plan, diff, or artifact.
- `human_review` — a human performs the review or approval step.
- `none` — only for tasks producing no repo-changing artifact; requires an explicit statement in the task prompt.

**Universal review floor: every repo-changing task requires `independent_review`, regardless of risk tier.** A task prompt may downgrade this only with an explicit written reason plus human approval, and the downgrade must be recorded in the Task Record.

## risk_tier

| Task surface | Default tier |
|---|---|
| CI, gate, thresholds, frozen ids, fixtures, schema, migration | `high` |
| `app/` code, `tests/`, `web/` | `medium` |
| Docs only | `low` |
| Unspecified | `medium` |

Humans may raise a tier. Humans must not lower a tier that these rules classify as `high`.

## Ceremony by tier

| tier | Plan gate | Review | Local commit | Push / merge | Task Record |
|---|---|---|---|---|---|
| `low` | none (the task prompt is the plan) | `independent_review` of the diff | allowed after review PASS | Gate 2 human approval | one line in `TASK_INDEX.md` + pointer to review verdict |
| `medium` | one-screen Plan → human Gate 1 | `independent_review` | allowed after review PASS | Gate 2 human approval | slim YAML (see schema) |
| `high` | full Plan → human Gate 1 (outline-first for large scopes) | `independent_review` (+ optional human-triggered third vote) | only after explicit human ack | Gate 2 human approval | full YAML |
| spike (`experiments/` only) | none | none | n/a (never merged as-is) | n/a | one-page PDR/ADR only if the result is worth keeping |

Slim vs full Task Record field sets are defined in `docs/dev/task_record_schema.yaml`.

## AGENTS.md status

`AGENTS.md` is the compact boot file for generic coding agents. On conflict, the current task prompt plus this file win.
