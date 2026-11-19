---
name: backend-task
description: Implement or validate EternalAI Python backend changes using targeted checks and the repository's full-test triggers.
---

# EternalAI backend task

Use the current Goal and repository AGENTS.md for scope, authorization and validation. Verify the changed behavior through the actual entry point, including production wiring where affected. Preserve required CI and existing test assertions.

- Select the smallest checks that cover the impact. Use `uv run ruff check <changed-path>` for affected lint and `uv run mypy app/` when affected Python types require it. Use configured exclusions; limit fixes or formatting to authorized files.
- Select existing targeted tests with `uv run pytest <test-path>` where needed. Add tests only when the current acceptance explicitly requires them. Check changed imports, registry wiring, dependencies, Golden semantics or test assertions as applicable under AGENTS.md.
- Run the local full suite only on explicit request using `uv run python scripts/check_dev_environment.py --start-full-tests`. Read the final worker result; preflight checks Docker Desktop, the fixed test DB at `127.0.0.1:15432` and `DATABASE_URL`. Report the actual blocker and do not switch connection targets or skip the DB.
- Schema and migration work requires action-specific authorization. Read-only `uv run alembic check` is available when relevant to an authorized migration task.

Repair in-scope failures at the cause and rerun the affected checks. Record actual commands, results and blockers. Preserve error codes, isolation and the distinction between static results and authorized runtime acceptance.
