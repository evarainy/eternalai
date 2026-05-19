# AGENTS.md — Phase 0 Compact Agent Boot Rules v1.0.11

This file is intentionally short. It is the always-loaded boot context for Codex and other coding agents. Do not expand it into a full spec.

## Phase
Phase 0 only. Do not implement Phase 1 features or modify the frozen blueprint.

## Source pointers
- Context loading strategy: `docs/phase0/CONTEXT_LOADING_STRATEGY.md`
- Task DAG: `docs/phase0/TASK_INDEX.md`
- Per-task prompts: `docs/phase0/tasks/<task_id>.md`
- Boundary checklist: `docs/phase0/BOUNDARY_CHECKLIST.md`
- Task record schema: `docs/dev/task_record_schema.yaml`
- Canonical long spec, consult only when needed: `docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md`
- Role and method guardrails: `docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md`

## Non-negotiable hard rules
1. Execute exactly one `task_id` per session turn; stop after Task Record and wait for human confirmation.
2. Start with the matching per-task prompt. Do not load or paste the full spec unless resolving a contradiction.
3. Output a Plan first. Do not modify files until a human approves the Plan.
4. Do not modify `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md`.
5. `P0-PREP-*` tasks are execution-pack-only preparation tasks; they create no Runtime/Gateway/Adapter capability.
6. Spike code must not enter `app/`; use `experiments/phase0/`, `docs/adr/`, `docs/research/`, or reusable `tests/utils/` only.
7. `app/runtime/` must not import `app/execution_fabric/` or concrete adapters.
8. Dependency source policy is task-scoped. Phase 0 internet-connected local development/build may use public registries only when explicitly allowed by the task prompt and dependency policy; intranet runtime uses prebuilt Docker images and must not require npm/pnpm registry access. Internal mirror/offline-cache remains required for intranet source build, intranet CI, or stricter supply-chain tasks.
9. Do not weaken tests to pass: no `assert True`, empty `pass`, broad skip, or deleted assertions.
10. Do not store plaintext password/token/cookie/sessionid/access_token/refresh_token values in Trace, ResponseEnvelope, fixtures expected output, logs, task records, or reports.
11. Work on one task branch: `phase0/<task_id>`.
12. Do not use `not_applicable` to hide a failed check; every `not_applicable` requires reason, blocked_by_task_id, activation_task_id, expiry_condition, and evidence.
13. Use the shared role/method guardrails (`docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md`) according to the current role (execution or review). Do not cross-read another tool-specific boot file unless the current task or review prompt explicitly requires it.

## Scratch/temp and artifact review rules
- Verify no temp/cache artifacts (`__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `_scratch/` contents) are staged.
- Verify untracked files are either intentionally ignored or cleaned before closeout.
- Verify `git ls-files --others --exclude-standard` is clean, or only shows intentional files that are explicitly explained.
- Verify Task Record `changed_files` exactly matches `git diff --cached --name-only`.
- Verify artifact lifecycle and cleanup timing are recorded in the Task Record.
- Treat `.venv/` internals as out of scope unless task scope explicitly says otherwise.

## Completion
Use the unified Task Record. Package confirmation is not tied to mandatory human diff review in v1.0.11. Record `package_confirmation_status`, `package_scope`, and `package_evidence`.

- Golden Task negative/boundary paths must pass 100%, including GT-012 multi-binding scope clarification.
