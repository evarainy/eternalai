# Phase 0 Git Workflow v1.0.11

## Branch strategy

- Start each `task_id` from a clean `phase0/main` or approved base branch.
- Create one task branch per task: `phase0/<task_id>`.
- Do not execute multiple tasks on the same branch unless a human explicitly approves a batch branch.
- Do not let AI agents push directly to protected branches.

## P0-PREP note

`P0-PREP-*` tasks are execution-pack-only preparation tasks. They may validate repository state and documentation placement, but they do not create Runtime / Gateway / Adapter / Golden Task capability.

## Pre-task checklist

```bash
git status --short
git branch --show-current
```

If the worktree is dirty, stop and ask whether to stash, commit, or discard.

## Commit convention

```text
phase0(<task_id>): <one-line summary>
```

The commit body should include task_id, changed file summary, tests run, CI/self-check evidence if available, and path to Task Record.

## Review rule

Human diff review is optional / recommended in v1.0.11, not a blocking acceptance requirement. If review happens, record it in:

```yaml
review:
  mode: human_optional
  reviewed_by: ""
  reviewed_at: ""
  notes: ""
```

Default allowed mode is `self_check`.

## not_applicable rule

Do not mark failed acceptance criteria as `not_applicable`. Every `not_applicable` must include reason, scope, blocked_by_task_id, activation_task_id, expiry_condition, and evidence.

## Failure and rollback

If a task fails:

1. Stop modifying files.
2. Do not commit the failed state.
3. Preserve debugging context using `git stash push -u -m "FAILED <task_id> <timestamp>"` only if a human wants to inspect the state.
4. Write a Task Failure Record in `docs/phase0/task_logs/<task_id>_<YYYYMMDD_HHMMSS>_failed.yaml`.
5. Wait for human decision: patch current task, restart task branch, or write an ADR/task patch.
6. Do not skip the failed task or hide the failure behind `not_applicable`.
