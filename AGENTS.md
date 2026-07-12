# AGENTS.md - Phase 1 Compact Agent Boot Rules v1.1.0

This file is intentionally short. It is the always-loaded compact boot context for Codex and other generic coding agents. Do not expand it into a full spec.
Process choreography (plan/review/gate mechanics) lives in the `codex-claude` workflow skill; this file is boot context plus fail-closed floor only.

## Phase
Phase 1 active. The main branch is still `phase0/main`; Phase 1 task branches use `phase1/<task_id>`.

Phase 1 rule authority, highest first:
1. Current task prompt: `docs/phase1/tasks/<task_id>.md`
2. `CLAUDE.md`
3. `docs/phase1/*`
4. `docs/dev/task_record_schema.yaml`

Phase 0 context-loading, style, boundary, and role/method files may be reused only when the Phase 1 prompt or docs say they are cross-stage support. They are not the current Phase 1 task authority.

## Project at a glance
- **EternalAI**: Government/enterprise AI Agent runtime — natural language driven, integrates OA, Yonyou U8, Hikvision iVMS
- **Architecture**: Hexagonal (Ports/Adapters). `app/ports/` = Protocol interfaces, `app/infra/` = implementations
- **Backend**: Python + uv + FastAPI | **Frontend**: React 18 + Vite + Ant Design 5.x
- **Data**: PostgreSQL 18 + pgvector, Redis + ARQ, MinIO (S3)
- **LLM**: Qwen + vLLM raw JSON mode (baseline; instructor/PydanticAI rejected by ADR — do not introduce; see docs/phase0/PHASE1_TECHNICAL_BASELINE.md §3.1)
- **Observability**: OpenTelemetry + Langfuse
- **Full tech stack decisions**: `docs/phase0/REPOSITORY_CONTEXT_MAP.md` Section 9

## Key directories
```
app/ports/          Protocol interfaces (TaskStore, CapabilityRegistry, CapabilityGateway, JobQueue...)
app/infra/          Interface implementations
app/api/v1/         FastAPI routes
app/db/             Database config/session/health check
tests/              Mirrors app/ structure + tests/architecture/ (import boundary, weak test checker)
docs/phase1/        Phase 1 plan, spec, task index, task template, task logs
docs/phase1/tasks/  Phase 1 per-task prompt files
docs/phase1/task_logs/  Phase 1 unified Task Records (YAML)
web/                Frontend (React 18 + Vite + Ant Design 5.x)
experiments/        Spike experiment code (never enters production)
infra/docker/       Docker Compose templates
```

## Git workflow
- **Main branch**: `phase0/main` (not `main`)
- **Task branch**: `phase1/<task_id>` (e.g. `phase1/P1-SPEC-001`)
- **Commit message**: `phase1(<task_id>): <short description>`
- **Merge message**: `merge phase1(<task_id>): <short description>`
- There is no separate local-commit human Gate. Ordinary non-force push, PR/merge, and CI may proceed only when the current task contract and repo policy allow them, deterministic validation and required independent Review pass, freshness remains bound, and branch protection/required checks are satisfied. Gate 2 is post-integration result acceptance, not Git/CI authorization.
- Exact authorization is mandatory for every R3 operation — file/directory or history deletion, secrets or `.env`, DB schema/real data, global/system changes, public release/production deployment, rebase, reset-hard, and force push. Never bypass hooks or branch protection.
- Check remote GitHub Actions CI after every merge to phase0/main

## Validation commands
```bash
# Full test suite
uv run pytest
# Single test file
uv run pytest tests/ports/test_capability_gateway_port.py
# Lint
uv run ruff check app/ tests/
# Type check
uv run mypy app/
# Dependency compliance
uv run python scripts/check_dependencies.py
# Import boundary + weak test checker
uv run pytest tests/architecture/
# Weak test single file
uv run python scripts/check_weak_tests.py tests/ports/<test_file>.py
# Golden task gate after P1-GATE-001
uv run python scripts/run_golden_tasks.py --gate
```

## Source pointers
- Phase 1 task template: `docs/phase1/TASK_PROMPT_TEMPLATE.md`
- Phase 1 task DAG: `docs/phase1/TASK_INDEX.md`
- Phase 1 per-task prompts: `docs/phase1/tasks/<task_id>.md`
- Phase 1 task logs: `docs/phase1/task_logs/`
- Task record schema: `docs/dev/task_record_schema.yaml`
- Cross-stage context loading strategy: `docs/phase0/CONTEXT_LOADING_STRATEGY.md`
- Cross-stage boundary checklist: `docs/phase0/BOUNDARY_CHECKLIST.md`
- Cross-stage role and method guardrails: `docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md`
- Canonical long spec, consult only when needed: `docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md`

## Non-negotiable hard rules
1. Execute exactly one `task_id` per lane/state. Auto-next may only open a new lane from a user-approved finite queue after dependencies and required result stops are satisfied.
2. Start with the matching per-task prompt. Do not load or paste the full spec unless resolving a contradiction.
3. Keep review/detail tier (`method_profile.risk_tier`) separate from controller risk (`R0`-`R3`) and `automation_class`; human stops come from the current task's `required_stops`, not from habit or the low/medium/high label alone.
4. Do not modify `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md`.
5. `app/ports/` is frozen; do not edit it unless the task explicitly authorizes it and a human approves.
6. Do not introduce instructor or PydanticAI. Baseline remains Qwen + vLLM raw JSON mode.
7. `P1-GATE-001` has landed. Later implementation tasks must run `scripts/run_golden_tasks.py --gate`.
8. Do not weaken tests to pass: no `assert True`, empty `pass`, broad skip, or deleted assertions.
9. Do not store plaintext password/token/cookie/sessionid/access_token/refresh_token values in Trace, ResponseEnvelope, fixtures expected output, logs, task records, or reports.
10. Work on one task branch: `phase1/<task_id>`.
11. Do not use `not_applicable` to hide a failed check; every `not_applicable` requires reason, blocked_by_task_id, activation_task_id, expiry_condition, and evidence.
12. Active governance-repair order: `P1-WORKFLOW-002-REPAIR-001 -> P1-CI-ALIGN-001 -> P1-OBS-001 -> P1-RUNTIME-ENTRY-001`. A downstream descriptor existing does not release its dependency gate. `P1-SPEC-001` remains the independent B2 hard prerequisite.

## Scratch/temp and artifact review rules
- Workflow-skill runtime scratch lives outside the repo (`$CLAUDE_CODEX_SCRATCH_ROOT`); repo-local `_scratch/` is for manual/non-skill temp files only.
- Verify no temp/cache artifacts (`__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `_scratch/` contents) are staged.
- Verify untracked files are either intentionally ignored or cleaned before closeout.
- Verify `git ls-files --others --exclude-standard` is clean, or only shows intentional files that are explicitly explained.
- Verify Task Record `changed_files` exactly matches `git diff --cached --name-only`.
- Verify artifact lifecycle and cleanup timing are recorded in the Task Record.
- Treat `.venv/` internals as out of scope unless task scope explicitly says otherwise.

## Completion
Use the unified Task Record unless the current task explicitly narrows allowed paths and forbids creating one. Package confirmation is not tied to mandatory human diff review. Record `package_confirmation_status`, `package_scope`, and `package_evidence` when applicable.

- Golden Task negative/boundary paths must pass 100%, including GT-012 multi-binding scope clarification.
