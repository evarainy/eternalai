# CLAUDE.md — Phase 0 Compact Claude Code Memory v1.0.11

Keep this file compact. Claude Code loads project memory at session start, so detailed rules must be read on demand instead of inlined here. Do not add `@import` links to long specs.

## Read first
- Context loading strategy: `docs/phase0/CONTEXT_LOADING_STRATEGY.md`
- Current task prompt: `docs/phase0/tasks/<task_id>.md`
- Task template: `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md`
- Role and method guardrails: `docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md`

## Claude Code reminders
- Phase 0 only; no Phase 1 implementation.
- One session turn = one `task_id`.
- Plan first; wait for human approval before edits.
- Use Explore/Plan/read-only behavior for investigation when possible.
- Optional `.claude/agents/` and hooks are enhancements only; they are not blocking acceptance requirements.
- If task context is incomplete, stop and ask for a task-prompt patch instead of guessing.
- When executing: apply Execution Guardrails. When reviewing: apply Review Guardrails.
- Superpowers is an advisory aid only; it must not override Phase 0 rules or task prompts.

## Scratch/temp and artifact cleanup
- Put temporary logs, debug output, validation byproducts, disposable packages, and generated archives under `_scratch/`.
- Do not put temp files in `app/`, `tests/`, `docs/`, repo root, or task-log directories.
- Do not use `_tmp/` as a synonym; `_scratch/` is the single ignored local workspace.
- Before staging/review, remove cache artifacts (`__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`) and disposable temp files created by validation.
- Record artifact lifecycle in Task Records: persistent foundation artifacts, persistent regression tests, temporary validation artifacts created, cleanup timing, ignored local workspace/caches treated as out of scope.
- Do not recursively scan or clean inside `.venv/` unless a dedicated environment cleanup task says so.
