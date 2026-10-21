---
name: backend-task
description: Implement or validate EternalAI Python backend changes using targeted checks and the repository's full-test triggers.
---

# EternalAI backend task

Use the current Goal and repository AGENTS.md for scope, review tier, full-test triggers and authorization. Do not add a separate approval or review process.

- Default to no new test code. Use cross-model static review and authorized runtime checks; select existing tests only when needed for the changed path: `uv run pytest <test-path>`. Add minimal regression tests only under AGENTS.md's criteria, and honor any static-only development phase.
- Lint uses `uv run ruff check .`; `pyproject.toml` owns its exclusions. This is a read-only check. Format or fix only authorized changed files, never run repository-wide `ruff format .` or `ruff check . --fix`.
- For Python production changes run `uv run mypy app/`; avoid inventing partial type-check coverage for shared types.
- Import/registry, dependencies and changed tests trigger the corresponding architecture, dependency and weak-test checks in AGENTS.md.
- Run the full suite only when AGENTS.md's explicit triggers apply; a high-risk label or core-file edit alone does not trigger it. Use `uv run python scripts/check_dev_environment.py --start-full-tests` and read the final worker result. It checks Docker Desktop, the fixed test DB at `127.0.0.1:15432` and `DATABASE_URL`; trust its result. Do not substitute a DB-skipping command or alter the connection target.
- Run Golden only for the affected runtime semantics or an explicit requirement.
- Migration/schema work requires the user's action-specific authorization before changes. Read-only `uv run alembic check` is useful only when relevant to an authorized migration task; it is not a default backend gate or permission to upgrade a database.

Repair change-related targeted failures within scope and rerun affected checks. For unavailable required dependencies or full-suite failure, stop and report under AGENTS.md; do not weaken tests, hide failures or cycle retries for green.

Finish with actual commands/results and explanations for unexecuted checks. Preserve real error codes and isolation behavior.
