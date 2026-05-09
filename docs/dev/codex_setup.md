# Codex Setup Notes for Phase 0

Codex must be treated like a coding agent that can make mistakes. Keep the repo, sandbox, approvals, and network posture explicit.

## Recommended posture

- Planning / code review: use the safest read-only or suggest mode available in your Codex version.
- Approved implementation task: use workspace-scoped write access and on-request approvals when available.
- Do not use dangerous bypass modes for this project.
- Keep network access disabled by default. Enable only for explicit documentation lookup or approved dependency verification.

## Before each task

1. Start from the repository root.
2. Confirm `AGENTS.md` is present.
3. Confirm `git status` is clean.
4. Confirm current branch is `phase0/<task_id>` or an approved task branch.
5. Provide exactly one task prompt from `docs/phase0/tasks/<task_id>.md` when available.
6. Require Plan output first and do not approve file edits until the Plan is reviewed.

## Codex development observability

Phase 0 distinguishes two layers:

- L1 application observability: OpenTelemetry + Langfuse inside the app runtime.
- L2 development observability: what Codex / Claude Code changed, tested, and decided.

For L2, the reliable sources are Git commits, CI logs, task records, and self-check YAML files. If your current Codex installation supports OpenTelemetry or JSONL run logs, you may enable them to a local collector, but this is optional and must not block Phase 0. If not enabled, record the fallback evidence in the Task Record.

## Evidence requirements

Do not accept `passed` as a bare claim. Task Records should cite evidence such as:

- `git commit sha <sha>`
- `CI run <id>, job <name>`
- command output snippet
- task log path
- self-check log path


## Context loading posture

Do not rely on a large root `AGENTS.md`. Keep root project docs compact and load task detail progressively:

1. root `AGENTS.md` for hard rules;
2. `docs/phase0/tasks/<task_id>.md` for current task context;
3. only the specific Tier 2 process references needed by the task;
4. long specs only for dispute resolution or task-prompt patching.

If the installed Codex version has project-document size limits, the compact root file prevents silent truncation from dropping critical rules.
