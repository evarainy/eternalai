# AGENTS.md - Phase 2 Compact Agent Boot Rules v1.3.5

This is the always-loaded compact context for coding agents. Keep detail in the linked documents; do not turn this file into a full spec or import long specs.

## Authority and current phase
Authority, highest first: the current Goal (latest instruction plus Outcome/Constraints/Verification) > user redlines and applicable `AGENTS.md` > approved product/architecture/interface/batch/milestone documents > repository code, tests, CI, branch protection, and runtime evidence > derived plans, skills, history, and suggestions.

Phase 1 is complete. Phase 2 has landed `P2-TRACE-PERSIST-001` (persistent TracePort + Admin audit query, merge `f8eb8533`), `P2-AUTH-001` (OA login authentication + trusted authentication seam replacing self-reported identity, merge `b9f7a3c7f551f45dd975803eeeee276207ae9f8a`), `P2-PILOT-FOUNDATION-001` (real production composition and closeout hardening, merge `51af461e561d814deef5f813245520a3dae73871`, PR #46, CI run 30422795582 success), `P2-DEVENV-PREFLIGHT-001` (`scripts/check_dev_environment.py`: diagnostic tool, not a gate; not wired into conftest, CI, or existing commands; merge `39c437a56bf3da27da457fb585c72fa375872c6d`, PR #48, CI run 30429764545 success), `P2-OA-READ-CONTRACT-001` (OA Replay adapter + versioned Contract Pack + offline sanitizer, merge `89cd16e33020806c8a7a28170f93689963de5235`), `P2-READ-ADAPTER-001` (OA Live HTTP, per-user session credential reads, IdentityMapping, Live fingerprint drift comparison, and runtime closure, merge `f9526a464dbf72ad3a39d8fcb8ec750c226f880f`), `P2-FE-API-CLIENTS-001` (froze Auth / Runtime / Admin-Trace OpenAPI and Orval clients, merge `e60b388edb478f66f70d36e9853f90b43fca7686`), `P2-EOL-FREEZE-001` (`web/.gitattributes` pins frozen artifact line endings to LF, fixing false drift failures caused by Windows checkout, merge `362710de8a1e397aba68d4a3aad7aff4b8739e20`), `P2-SANITIZE-LEAK-FIX-001` (the sanitizer no longer writes sensitive values into artifacts or error echoes; the detection rule set was not reduced, merge `01fc6c459e7a8df4ec410ab0273f1fa08e3cd057`), `P2-IDENTITY-CREDENTIAL-001` (added credential revocation and reset; new calls after revocation return `identity_revoked`, merge `f8208cd0532dc5fa2ae1b2c3e60dbd0e1dabf1b8`), `P2-ADMIN-CSRF-001` (every Cookie-authenticated unsafe method requires a valid `Origin` plus the fixed custom header; missing, duplicate, or invalid values return 403, with a dynamic route-enumeration guard preventing newly added routes from being left unwired, merge `daf90f263352a14cfeb9d500b30558e7fb6ec046`), `P2-RUNTIME-RESPONSE-CONTRACT-001` (declared the existing `ResponseEnvelope` in Runtime OpenAPI and regenerated the client; response bytes are unchanged, merge `83e6ec82729a045a6d3c77039dbb952fe9bb03ff`), `P2-PILOT-ENTRY-FE-001` (wired the login page, EternalAI Session Cookie, centralized fail-closed 401 reauthentication and protected routes, and stopped sending `X-EternalAI-Roles`, merge `d3a536e91001f6f008d4dbb1b8ec321988b70b66`), `P2-CHAT-ENTRY-FE-001` (protected `/chat` plain-text conversation entry consuming the Runtime Orval client and existing authenticated session; exact `/chat` login return allowlist; no SDUI renderer or structured Action, merge `092a9095cbe4b572a8987707d2ab098887bcd123`), `P2-BE-SMALL-DEBT-001` (safe OA adapter fallback observability plus OA read placeholders in `.env.example`, without changing external `adapter_error` or Trace behavior, merge `3a901c5bcde8b9b704d70d889c3f6fc165edaa6d`), `P2-FE-TEST-FLAKE-001` (disabled Vitest file-level concurrency to remove the 5-second Ant component-test flake, preserving 81 tests, and tightened the negative `App.test.tsx` title assertion; no production code change, merge `6c47f06f3cae5af43efdbeb79a0d2bfe68f5517b`), `P2-OA-SYSMSG-PACK-001` (added `oa.list_system_messages` and the parallel `ecology9-system-messages-v1` pack; removed the transport-header exemption in favor of a 9-character substring threshold plus exact full-token matching for shorter values, merge `b55104d193862a78b7529e657ceea4639ed6e152`, PR #65), and `P2-AUTH-USERID-TYPE-001` (normalized OA integer/string `userid` once in `_required_oa_user_id`, with guards proving the same principal and exactly one credential row plus one IdentityMapping, merge `a9bf8b8fc3fbf48448ca511768fe7271d8b8a221`, CI run 30797244405 success), `P2-OA-SYSMSG-LIVE-001` (enabled live system-message routing with capability-specific configuration, model validation, and fingerprint drift handling, merge `9da2fe5a1948800f90110d5adbd033553d01a808`), `P2-OA-MSGCENTER-PROTOCOL-001` (aligned the shared OA message-center transport with conservative cursor pagination and fail-closed truncation guards, merge `c44ed56f426fd01104cf94bbb946f2baaf065efc`), `P2-SMOKE-RUNNER-001` (added the credential-safe, fail-closed intranet smoke runner, merge `caaf801fcaa011573fc5c5fe1f1d8565a2cfc287`), and `P2-SMOKE-AUTH-DIAG-001` (published pending-workflows-v2 from the real sibling capture, kept v1 byte-stable, closed the Gateway binding-scope oracle, and restored weakened assertions, merge `1bf2ba6c895fec4b847f2369f13f22879920000b`). `P2-OA-LOGIN-PARITY-001` also completed as a read-only diagnostic with no branch or commit: it found no authentication bypass because both `msgcode` and `loginstatus` are checked fail-closed, and its integer-`userid` defect is now fixed. Current `phase0/main` baseline = `1bf2ba6c895fec4b847f2369f13f22879920000b`; the formal decision/debt register is in `docs/phase2/PHASE2_PLAN.md` and includes `P2-CONFIRM-RESUME-001` (not triggered), the real-OA evidence gaps activated by `P2-OA-INTRANET-SMOKE-001`, required-check bypass hardening, repository cleanup, and four architecture decisions settled by Rainy on 2026-08-03: user-owned OA credentials plus logged per-write human confirmation; database access is not a target system and needs neither IdentityMapping nor a `db` enum/schema change, but every access must be traceable; Operations owns enterprise keys with no periodic rotation but runtime manual update required; Golden policy is the verbatim Decision 4 below. Derived work remains for an unscheduled enterprise-key runtime management surface and an unresolved Golden lifecycle-manifest lane. `P2-OA-INTRANET-SMOKE-001` is partially complete: the 2026-08-07 onsite inputs needed by the to-do adapter are available, while the real OA Live fingerprint comparison remains intranet debt. The next lane is `P2-OA-TODOLIST-ADAPTER-001`.

Current Phase 2 delivery state (2026-08-10; supersedes the preceding paragraph's baseline and next-lane pointer): `P2-OA-TODOLIST-ADAPTER-001` replaces the data source of `oa.list_pending_workflows` in place with the dedicated three-step to-do protocol and keeps exactly two OA capability IDs. The verified baseline is **1849 passed, 0 skipped, 0 failed**; Golden Gate is **27/27 passed, 0 skipped, 0 failed**. The next-lane pointer is intentionally blank: `P2-SMOKE-E2E-CHAIN-001`, the enterprise-key runtime management surface, repository cleanup, and Preselector b1-b4 are non-unique candidates whose DAG ordering belongs to GOV-SYNC. New debt: the current live smoke is Provider-level and bypasses Runtime / Gateway / Policy / Evaluator / Trace, so it cannot prove the full chain.

Decision 4 (verbatim): 负向、边界和安全拒绝用例的题面、预期、禁止项、分类及判卷契约冻结，修改需雨爷明确批准。所有既有正向题面同样不可原地改写，只能新增后继题并在题外生命周期清单中停止旧题运行。判卷契约或运行选择规则变更时，必须按同一版本包全量回放并明确披露影响。每修复一个真实缺陷，必须新增一条能在未修代码上失败、修复后通过、且走原缺陷路径的永久回归证据；缺陷属于 Golden Runtime 观察边界时才新增 Golden Task，否则放在最小且忠实的单元/集成/API/浏览器层。

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
- There is no separate local-commit Gate. Ordinary non-force task-branch push/PR/merge and CI/CD configuration or runs may proceed only when the Goal and repository policy allow them and deterministic validation, required Review, freshness, branch protection, and required checks pass. Integration into `phase0/main` must always use the PR path: push the task branch, open a PR, wait until required checks are final and green, then merge through the PR. Never locally merge and directly push `phase0/main`, even without an explicit bypass flag.
- Phase 2 does not create a separate per-task Task Record. Every PR body must contain exactly these three governance sections: `## Scope`, `## 验证结果（pytest / Golden 原始结果行 + CI run）`, and `## 本棒新增欠债`; the PR body is the durable task record bound to the commit and CI run.
- Governance sync is routed by write-lane concurrency. Class A mechanical sync is exactly the test baseline numbers, the `task_id`, the next-lane pointer, and debts newly found by that lane; with exactly one active write lane, that implementation lane must complete Class A inside its own payload commit, with no extra commit, no amend, and no force push, while with two or more active write lanes, implementation lanes must not edit the three shared governance documents for Class A and a separate GOV-SYNC batch owns it. Governance documents never record commit SHAs or CI run ids: writing one changes the value being written, and both are redundant projections of `task_id`. Trace with `git log --grep=<task_id>` (the `phase2(<task_id>):` commit convention guarantees a hit), treat GitHub as the sole authority for CI results, and keep run evidence in the PR body's verification section. Class B cross-lane decisions, blueprint-overturning ADRs, Golden policy, DAG reordering, and cross-lane debt consolidation always belong to GOV-SYNC; an implementation lane may only propagate the already-decided next `task_id` and must never choose its own successor, leaving the pointer empty and registering it for adjudication when the successor is not unique. Before opening a write lane, its launch instructions must declare serial or parallel execution and whether the lane owns Class A sync. Until the state-section refactor lands in both `CLAUDE.md` and `AGENTS.md`, parallel lanes remain under this restriction.
- Repository-owner follow-up: enable GitHub branch protection's **Do not allow bypassing the above settings** for `phase0/main`. This remains registered debt; agents must not change the setting without action-specific authorization.
- Exact action-specific authorization is required for file/directory deletion or history rewrite, secrets or `.env`, DB schema or real data, global/system changes, public release or production deployment, rebase, reset-hard, and force push. A risk label is never authorization. Never bypass hooks or branch protection.
- After every merge to `phase0/main`, check the corresponding remote GitHub Actions CI result.

## Validation commands
> **The full suite needs the fixed test database running**: Docker Desktop up, test DB healthy on
> `127.0.0.1:15432`, and `DATABASE_URL` visible to the current process (user-level env var; a process
> started before it was set will not inherit it — reopen the terminal/app). Without `DATABASE_URL` the
> DB tests **fail rather than skip** — a silent skip reads as a pass. To genuinely run without a
> database, pass `--ignore=` explicitly so the omission is visible in the command.
> Baseline: **1849 passed, 0 skipped, 0 failed**; Golden Gate **27/27 passed, 0 skipped, 0 failed** (verified locally by `P2-OA-TODOLIST-ADAPTER-001`).

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
- Standing cleanup authorization applies only inside the current task worktree and only to `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, and `.ruff_cache/`. First run `git ls-files --others --exclude-standard`; if it is empty, delete nothing. Use exact resolved targets, not wildcard-scoped recursive deletion. Do not touch `.venv/`, source/artifact files, other worktrees, or Git history. Every other file/directory deletion remains a redline requiring action-specific authorization; never stage `_scratch/` contents.
- Before closeout, `git ls-files --others --exclude-standard` must be clean or every intentional file explicitly explained.
- Do not scan or clean inside `.venv/` unless the Scope explicitly includes it.
