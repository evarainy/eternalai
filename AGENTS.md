# AGENTS.md - Phase 2 Compact Agent Boot Rules v1.3.2

This is the always-loaded compact context for coding agents. Keep detail in the linked documents; do not turn this file into a full spec or import long specs.

## Authority and current phase
Authority, highest first: the current Goal (latest instruction plus Outcome/Constraints/Verification) > user redlines and applicable `AGENTS.md` > approved product/architecture/interface/batch/milestone documents > repository code, tests, CI, branch protection, and runtime evidence > derived plans, skills, history, and suggestions.

Phase 1 is complete. Phase 2 has landed `P2-TRACE-PERSIST-001` (persistent TracePort + Admin audit query, merge `f8eb8533`), `P2-AUTH-001` (OA login authentication + trusted authentication seam replacing self-reported identity, merge `b9f7a3c7f551f45dd975803eeeee276207ae9f8a`), `P2-PILOT-FOUNDATION-001` (real production composition and closeout hardening, merge `51af461e561d814deef5f813245520a3dae73871`, PR #46, CI run 30422795582 success), `P2-DEVENV-PREFLIGHT-001` (`scripts/check_dev_environment.py`: diagnostic tool, not a gate; not wired into conftest, CI, or existing commands; merge `39c437a56bf3da27da457fb585c72fa375872c6d`, PR #48, CI run 30429764545 success), `P2-OA-READ-CONTRACT-001` (OA Replay adapter + versioned Contract Pack + offline sanitizer, merge `89cd16e33020806c8a7a28170f93689963de5235`), `P2-READ-ADAPTER-001` (OA Live HTTP, per-user session credential reads, IdentityMapping, Live fingerprint drift comparison, and runtime closure, merge `f9526a464dbf72ad3a39d8fcb8ec750c226f880f`), `P2-FE-API-CLIENTS-001` (froze Auth / Runtime / Admin-Trace OpenAPI and Orval clients, merge `e60b388edb478f66f70d36e9853f90b43fca7686`), `P2-EOL-FREEZE-001` (`web/.gitattributes` pins frozen artifact line endings to LF, fixing false drift failures caused by Windows checkout, merge `362710de8a1e397aba68d4a3aad7aff4b8739e20`), and `P2-SANITIZE-LEAK-FIX-001` (the sanitizer no longer writes sensitive values into artifacts or error echoes; the detection rule set was not reduced, merge `01fc6c459e7a8df4ec410ab0273f1fa08e3cd057`). Current `phase0/main` baseline = `01fc6c459e7a8df4ec410ab0273f1fa08e3cd057`; the only remaining debt is `P2-CONFIRM-RESUME-001` (not triggered), see `docs/phase1/TASK_INDEX.md` §5.1; next task = `P2-RUNTIME-RESPONSE-CONTRACT-001` (declare the existing `ResponseEnvelope` in Runtime OpenAPI for generated-client typing).

Each write lane uses one explicit Goal, one Scope, and one isolated worktree/branch; a new scope opens a new lane. Historical V4 prompts and Task Records keep their original meaning, but new work follows the current Goal and this file.

## Project at a glance
- **EternalAI**: Government/enterprise AI Agent runtime — natural language driven, integrates OA, Yonyou U8, Hikvision iVMS
- **Architecture**: Hexagonal (Ports/Adapters). `app/ports/` = Protocol interfaces, `app/infra/` = implementations; `app/ports/` must never depend on `app/infra/`
- **Backend**: Python + uv + FastAPI + uvicorn 0.51.0 (real process entry point: `python -m app.server`) | **Frontend**: React 18 + Vite + Ant Design 5.x
- **Data**: PostgreSQL 18 + pgvector, Redis + ARQ, MinIO (S3)
- **LLM**: vLLM raw JSON mode; default `http://34.74.11.38:8011/v1` + `glm-4.7`, with URL / model / sampling parameters overridable by env; do not introduce instructor or PydanticAI (see `docs/phase0/PHASE1_TECHNICAL_BASELINE.md` §3.1)
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

## Git, Review, and authorization
- **Main branch**: `phase0/main` (not `main`)
- **Task branch**: `phase2/<task_id>`
- **Commit**: `phase2(<task_id>): <short description>`
- **Merge**: `merge phase2(<task_id>): <short description>`
- Q0-Q3 controls Review strength, not human stops. Human stops come from reserved redline actions; scope expansion; new or changed architecture, framework, public contract/API/protocol, trust boundary, or core invariant; a material unresolved choice; stricter repository rules; or batch/milestone acceptance. An internal `app/ports/` change that preserves the architecture is not, by itself, a stop.
- There is no separate local-commit Gate. Ordinary non-force push/PR/merge and CI/CD configuration or runs may proceed only when the Goal and repository policy allow them and deterministic validation, required Review, freshness, branch protection, and required checks pass.
- Exact action-specific authorization is required for file/directory deletion or history rewrite, secrets or `.env`, DB schema or real data, global/system changes, public release or production deployment, rebase, reset-hard, and force push. A risk label is never authorization. Never bypass hooks or branch protection.
- After every merge to `phase0/main`, check the corresponding remote GitHub Actions CI result.

## Validation commands
> **The full suite needs the fixed test database running**: Docker Desktop up, test DB healthy on
> `127.0.0.1:15432`, and `DATABASE_URL` visible to the current process (user-level env var; a process
> started before it was set will not inherit it — reopen the terminal/app). Without `DATABASE_URL` the
> DB tests **fail rather than skip** — a silent skip reads as a pass. To genuinely run without a
> database, pass `--ignore=` explicitly so the omission is visible in the command.
> Baseline: **1351 passed, 0 skipped, 0 failed** (25 Golden parametrized cases; as of P2-SANITIZE-LEAK-FIX-001 / merge `01fc6c459e7a8df4ec410ab0273f1fa08e3cd057`).

```bash
uv run pytest
uv run pytest tests/ports/test_capability_gateway_port.py
uv run ruff check .
uv run mypy app/
uv run python scripts/check_dependencies.py
uv run pytest tests/architecture/
uv run python scripts/check_weak_tests.py tests/ports/<test_file>.py
uv run python scripts/run_golden_tasks.py --gate  # required for every later implementation task
git diff --cached --name-only
git diff --cached --stat
git diff --cached --check
git ls-files --others --exclude-standard
```

## Read on demand / source pointers
- Phase 1: `docs/phase1/TASK_PROMPT_TEMPLATE.md`, `docs/phase1/TASK_INDEX.md`, `docs/phase1/tasks/<task_id>.md`, `docs/phase1/task_logs/`, `docs/phase1/ROLE_POLICY.md`
- Legacy record schema: `docs/dev/task_record_schema.yaml`
- Cross-stage: `docs/phase0/CONTEXT_LOADING_STRATEGY.md`, `docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md`, `docs/phase0/REPOSITORY_CONTEXT_MAP.md`, `docs/phase0/CODING_STYLE_BASELINE.md`, `docs/phase0/BOUNDARY_CHECKLIST.md`
- Canonical long spec, only when needed: `docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md`

## Non-negotiable hard rules
1. Do not modify `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md`.
2. `app/ports/` contracts may change only when the design needs it: use the smallest contract, record the reason, and update every implementation and test in the same change. Never build a workaround for a contract that should be fixed.
3. Never weaken tests to get green (`assert True`, empty `pass`, broad skip, or deleted assertions); fix the code or stop and report. A failure path must not report success or lose its error code. Never regress session/tenant/user isolation. `FROZEN_GT_IDS` and golden fixtures require explicit human approval.
4. Never put plaintext password/token/cookie/sessionid/access_token/refresh_token in Trace, ResponseEnvelope, expected fixtures, logs, task records, or reports.
5. Do not use `not_applicable` to hide a failed check; it requires reason, blocked_by_task_id, activation_task_id, expiry_condition, and evidence.
6. A downstream descriptor's existence never releases its dependency gate. Golden negative/boundary paths must pass 100%, including GT-012 multi-binding scope clarification.

## Scratch/temp and artifact review rules
- Goal snapshots, Candidate Manifests, Recovery Indexes, Review evidence, and summaries live outside the repo under `$CODEX_RUNS_ROOT`, falling back to `$CLAUDE_CODEX_SCRATCH_ROOT/v5-runs`; repo-local `_scratch/` is only for manual temporary files.
- Before staging, remove `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, and `.ruff_cache/`; never stage `_scratch/` contents.
- Before closeout, `git ls-files --others --exclude-standard` must be clean or every intentional file explicitly explained.
- Do not scan or clean inside `.venv/` unless the Scope explicitly includes it.
