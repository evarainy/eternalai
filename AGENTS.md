# AGENTS.md - Phase 1 Compact Agent Boot Rules v1.2.0

This file is intentionally short. It is the always-loaded compact boot context for Codex and other generic coding agents. Do not expand it into a full spec.
Process choreography uses the Codex App native Goal, subagents, worktrees, Review, Git, and CI. Repository documents define result and safety contracts; they do not depend on a V4 workflow command or custom lifecycle.

## Phase
Phase 1 development is complete: the B2→B5 product chain and governance-repair chain have landed with trunk CI green; two recorded debts carry to P2 (P2-TRACE-PERSIST-001 deployment blocker / P2-CONFIRM-RESUME-001 self-triggered — see `docs/phase1/TASK_INDEX.md` §5.1). The main branch remains `phase0/main`; the `phase1/<task_id>` branch convention stays until Phase 2 formally opens. All rules below remain active.

Phase 1 rule authority, highest first:
1. The user's latest explicit instruction in the current native Goal
2. Applicable `AGENTS.md` files and user redlines
3. Approved product, architecture, interface, batch, and milestone documents
4. Current Goal Outcome / Constraints / Verification
5. Repository code, tests, CI, branch protection, required checks, and runtime evidence
6. Derived plans and Worker Contracts
7. Skills, history, examples, and agent suggestions

Historical per-task prompts and Task Records keep their original V4 meaning. They do not become authority for a new V5 Goal.

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
- There is no separate local-commit human Gate. Ordinary non-force push, PR/merge, CI/CD configuration changes, and CI runs may proceed only when the current Goal and target repository policy allow them, deterministic validation and required Review pass, freshness remains bound, and branch protection/required checks are satisfied. Historical Gate 2 remains post-integration result acceptance, not Git/CI authorization.
- Exact action-specific authorization is mandatory for file/directory or history deletion, secrets or `.env`, DB schema/real data, global/system changes, public release/production deployment, rebase, reset-hard, and force push. A risk label never supplies that authorization. Never bypass hooks or branch protection.
- Check remote GitHub Actions CI after every merge to phase0/main

## Validation commands
> **The full suite needs the fixed test database running**: Docker Desktop up, test DB healthy on
> `127.0.0.1:15432`, and `DATABASE_URL` visible to the current process (user-level env var; a process
> started before it was set will not inherit it — reopen the terminal/app). Without `DATABASE_URL` the
> DB tests **fail rather than skip** — a silent skip reads as a pass. To genuinely run without a
> database, pass `--ignore=` explicitly so the omission is visible in the command.
> Baseline: **1071 passed, 0 skipped, 0 failed** (25 Golden parametrized cases; as of P1-B5-006 / merge 0cae8fe6).

```bash
# Full test suite
uv run pytest
# Single test file
uv run pytest tests/ports/test_capability_gateway_port.py
# Lint (scope declared once in pyproject.toml [tool.ruff] exclude)
uv run ruff check .
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
1. One native Goal may execute multiple task IDs and auto-next when dependencies, required evidence, Review, and result stops are satisfied. Each write lane still owns one explicit Goal and a single Scope; a new scope opens a new lane/contract.
2. Start from the current Goal and the minimum relevant authority. Historical V4 work still starts from its matching per-task prompt; new V5 work does not require one.
3. Risk and Q0-Q3 Review strength do not create a human Gate. Human stops come only from reserved redline actions; scope expansion; new or changed architecture, framework, public contract/API/protocol, trust boundary, or core invariant (an internal `app/ports/` change that preserves the existing architecture is not by itself a stop); a material unresolved choice; a stricter target repository Gate; or batch/milestone acceptance.
4. Do not modify `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md`.
5. `app/ports/` is not frozen by decree. Change a contract when the design genuinely needs it, record why in the Task Record, and update every implementation and test in the same change. Prefer the smallest contract that fits, and never build a workaround for a contract you should have fixed.
6. Do not introduce instructor or PydanticAI. Baseline remains Qwen + vLLM raw JSON mode.
7. `P1-GATE-001` has landed. Later implementation tasks must run `scripts/run_golden_tasks.py --gate`.
8. These stay strict at any scope, because they fail silently: do not weaken tests to pass (no `assert True`, empty `pass`, broad skip, or deleted assertions — fix the code or stop and report); do not let a failure path report success or lose its error code; do not regress session/tenant/user isolation. `FROZEN_GT_IDS` / golden fixtures still need explicit human approval.
9. Do not store plaintext password/token/cookie/sessionid/access_token/refresh_token values in Trace, ResponseEnvelope, fixtures expected output, logs, task records, or reports.
10. Every write lane works in one isolated worktree/branch and a single Scope. The current repository branch convention remains `phase1/<task_id>`.
11. Do not use `not_applicable` to hide a failed check; every `not_applicable` requires reason, blocked_by_task_id, activation_task_id, expiry_condition, and evidence.
12. Historical governance-repair sequence completed: `P1-WORKFLOW-002-REPAIR-001 -> P1-CI-ALIGN-001 -> P1-OBS-001 -> P1-RUNTIME-ENTRY-001`. The general invariant remains: a downstream descriptor existing does not release its dependency gate. `P1-SPEC-001` was the independent B2 hard prerequisite and is approved/landed.

## Scratch/temp and artifact review rules
- V5 Goal snapshots, Candidate Manifests, Recovery Indexes, Review evidence, and summaries live outside the repo under `$CODEX_RUNS_ROOT`, falling back to `$CLAUDE_CODEX_SCRATCH_ROOT/v5-runs`; repo-local `_scratch/` is for manual temp files only.
- Verify no temp/cache artifacts (`__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `_scratch/` contents) are staged.
- Verify untracked files are either intentionally ignored or cleaned before closeout.
- Verify `git ls-files --others --exclude-standard` is clean, or only shows intentional files that are explicitly explained.
- For historical V4 tasks and the final `P1-WORKFLOW-V5-001` migration only, verify Task Record `changed_files` exactly matches `git diff --cached --name-only`, and verify artifact lifecycle/cleanup timing is recorded in that Task Record.
- For V5 tasks, verify Candidate Manifest and Recovery Index pointers bind the exact candidate and evidence; each completed task emits its own Chinese result summary of at most 20 lines.
- Treat `.venv/` internals as out of scope unless task scope explicitly says otherwise.

## Completion
The unified Task Record is V4 legacy evidence. Existing records and the final `P1-WORKFLOW-V5-001` migration record keep schema v1.2.0 meaning; each completed task in a new V5 Goal uses native Goal state, external Candidate Manifest/Recovery Index pointers, Review evidence, and its own Chinese result summary of at most 20 lines instead of this heavy record. Package fields remain historical evidence where applicable.

- Golden Task negative/boundary paths must pass 100%, including GT-012 multi-binding scope clarification.
