# CLAUDE.md — Phase 0 Compact Claude Code Memory v1.0.11

Keep this file compact. Claude Code loads project memory at session start, so detailed rules must be read on demand instead of inlined here. Do not add `@import` links to long specs.

## Read first
- Context loading strategy: `docs/phase0/CONTEXT_LOADING_STRATEGY.md`
- Current task prompt: `docs/phase0/tasks/<task_id>.md`
- Task template: `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md`

## Claude Code reminders
- Phase 0 only; no Phase 1 implementation.
- One session turn = one `task_id`.
- Plan first; wait for human approval before edits.
- Use Explore/Plan/read-only behavior for investigation when possible.
- Optional `.claude/agents/` and hooks are enhancements only; they are not blocking acceptance requirements.
- If task context is incomplete, stop and ask for a task-prompt patch instead of guessing.
