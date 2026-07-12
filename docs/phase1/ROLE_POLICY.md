# Phase 1 Role Policy v2.1.0

This file is the Phase 1 source of truth for role assignment and repository result contracts. Process choreography, packet formats, provider calls, state transitions, and Gate mechanics live only in the canonical `codex-claude` workflow skill.

## Authority and roles

Repository authority, highest first: current task descriptor, this policy, other current `docs/phase1/` contracts, then `docs/dev/task_record_schema.yaml`. Historical prompts/records are interpreted under their original version and are never backfilled to manufacture current authorization.

- `codex` — sole controller/executor: classify, plan, build isolated candidates, validate, self-review, stage exact paths, integrate, and report.
- `claude_code` — bounded read-only independent Plan/final-diff reviewer when the controller risk requires it.
- `human` — supplies required-stop decisions, exact R3 authorization, major visual choices, and result acceptance. Human acceptance never substitutes for missing validation or Review evidence.

The executor is never the sole reviewer of a repo-changing result. `review_mode` values are `self_review | independent_review | human_review | none`; `none` requires an explicit task reason and controller-policy support.

## Three independent classification axes

### Review and record detail: `method_profile.risk_tier`

| Surface | Default | Record detail |
| --- | --- | --- |
| CI/gates/thresholds/frozen ids/fixtures/schema/migration/workflow control plane | `high` | full YAML |
| `app/`, `tests/`, `web/`, ordinary runtime behavior | `medium` | slim YAML unless task is stricter |
| Deterministic docs/format/generated output | `low` | TASK_INDEX pointer or task-required YAML |
| Unspecified | `medium` | slim YAML |

This axis controls reporting depth and may make review stricter. It does not by itself create a human stop or authorize Git.

### Operational risk: `controller_risk_tier`

| Tier | Meaning | Default independent Review |
| --- | --- | --- |
| `R0` | deterministic docs/format/generated output with no contract change | deterministic audit; no Claude by default |
| `R1` | narrow non-core code or routine UI | Claude final-diff Review |
| `R2` | runtime, algorithms, security/permissions, concurrency, public contracts, cross-module work, CI/gates, or workflow control planes | Claude Plan Review and final-diff Review |
| `R3` | one concrete reserved action | exact human authorization for that action plus all lower-tier evidence |

Risk may only stay equal or increase automatically. R3 includes delete/history rewrite, secrets or `.env`, DB schema/real data, global/system changes, public release/production deployment, rebase, reset-hard, and force push. A task that does not list an exact R3 action authorizes none.

### Human stops: `automation_class`

| Class | Required stops |
| --- | --- |
| `auto` | none by default |
| `human_pre_apply` | `human_pre_apply` before candidate content edits, then `human_result_acceptance` after integration |
| `human_pre_action` | exact approval immediately before each listed R3 action |

Defaults: R0/R1 → `auto`; R2 → `human_pre_apply`; R3 → `human_pre_action`. A current task may explicitly set R2 + `auto` only when a machine-readable task contract preserves R2 review/validation and explicitly removes human stops. Missing or ambiguous automation never enables automatic integration.

`authorization_mode` is `standard | bounded_goal_preapproval`. Bounded mode requires a fresh strict Goal/descriptor/reference/coverage closure; old or partial evidence never qualifies. `required_stops` is the authoritative task-level stop list and may be stricter than the default.

## Review and Gate result contracts

- Every repo-changing task records Codex self-review plus the controller-required independent audit/Review. R0 may use deterministic independent audit without Claude; R1 requires final Claude Review; R2 requires Plan and final Claude Reviews; R3 additionally requires exact action authorization.
- Gate 1, when present, authorizes only the exact pre-apply Plan/scope/operations. It never authorizes an unlisted path or R3 action.
- Gate 2 is post-integration result acceptance. It never authorizes commit, push, merge, CI, deployment, or rollback.
- There is no independent local-commit human Gate. A reviewed commit must preserve the reviewed parent/tree/diff evidence.

## Remote integration and CI

Ordinary non-force push, PR/merge, and CI may proceed only when the current task contract and repo policy explicitly allow them, exact staging and validation pass, required independent Review is bound and fresh, auth/mergeability are valid, branch protection is obeyed, and all actually required checks are final and green. Never use force, rebase, reset-hard, `--no-verify`, hook/protection bypass, ref deletion, or a stale Review.

Task Records distinguish task-branch/PR checks from post-merge CI and bind each to its actual SHA. A workflow that does not trigger is recorded `not_triggered`, never green. Post-merge CI required by the task must bind the merge SHA and every triggered job must pass.

## Auto-next and rollback

Auto-next may consume only a user-approved immutable queue of at most three independent tasks and always opens a new lane/state. Dependency failure, R2 result acceptance, provider/Review/validation/CI failure, or any R3 action blocks auto-next. A repair or Git revert is a new authorized task; reset/rebase/force/delete is never an automatic rollback.

## Legacy interpretation

| Historical value | Interpret as |
| --- | --- |
| `claude_code_mimo` | `claude_code` |
| `codex_review` | `independent_review` or `self_review` by artifact context |
| `self_check` | `self_review` |
| `human_optional` | `human_review` only if a review actually occurred, otherwise `none` |

`AGENTS.md` and `CLAUDE.md` are compact boot/routing files. On conflict, the current task descriptor and this policy win; the canonical controller remains the only process SOP.
