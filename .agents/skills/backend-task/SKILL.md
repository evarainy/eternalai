---
name: backend-task
description: Implement or validate EternalAI Python backend changes using targeted checks and the repository's full-test triggers.
---

# EternalAI backend task

Use the current Goal and repository AGENTS.md for scope, review tier, full-test triggers and authorization. Do not add a separate approval or review process.

- Start with the changed behavior's tests and nearest contract/integration path: `uv run pytest <test-path>`, including relevant negative cases.
- Lint uses `uv run ruff check .`; `pyproject.toml` owns its exclusions. This is a read-only check. Format or fix only authorized changed files, never run repository-wide `ruff format .` or `ruff check . --fix`.
- For Python production changes run `uv run mypy app/`; avoid inventing partial type-check coverage for shared types.
- Import/registry, dependencies and changed tests trigger the corresponding architecture, dependency and weak-test checks in AGENTS.md.
- A-tier/core-boundary changes and other full-suite triggers use `uv run python scripts/check_dev_environment.py --start-full-tests`. Wait for the final worker result; starting it is not a PASS. Do not substitute a DB-skipping pytest command or alter the connection target.
- Run Golden only for the affected runtime semantics or an explicit requirement.
- Migration/schema work requires the user's action-specific authorization before changes. Read-only `uv run alembic check` is useful only when relevant to an authorized migration task; it is not a default backend gate or permission to upgrade a database.

Repair change-related targeted failures within scope and rerun affected checks. For unavailable required dependencies or full-suite failure, stop and report under AGENTS.md; do not weaken tests, hide failures or cycle retries for green.

Finish with actual commands/results and explanations for unexecuted checks. Preserve real error codes and isolation behavior.
