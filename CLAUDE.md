# CLAUDE.md — Phase 1 Compact Claude Code Memory v2.2.0

Keep this file compact. Claude Code loads project memory at session start, so detailed rules must be read on demand instead of inlined here. Do not add `@import` links to long specs.

## Project at a glance
- **EternalAI**: 政府/企业 AI Agent 运行时平台，自然语言驱动对接 OA、用友 U8、海康 iVMS
- **架构**: 六边形 (Ports/Adapters)，`app/ports/` = Protocol 接口，`app/infra/` = 实现
- **后端**: Python + uv + FastAPI | **前端**: React 18 + Vite + Ant Design 5.x
- **数据层**: PostgreSQL 18 + pgvector, Redis + ARQ, MinIO (S3)
- **LLM**: Qwen + vLLM raw JSON mode（基线；instructor/PydanticAI 非基线，内网复测结构化达标但 raw JSON 仍最优，详见 `docs/phase0/PHASE1_TECHNICAL_BASELINE.md`）
- **可观测**: OpenTelemetry + Langfuse
- **详细技术栈决策**: `docs/phase0/REPOSITORY_CONTEXT_MAP.md` Section 9

## Key directories
```
app/ports/          Protocol 接口 (TaskStore, CapabilityRegistry, CapabilityGateway, JobQueue...)
app/infra/          接口实现
app/api/v1/         FastAPI 路由
app/db/             数据库配置/会话/健康检查
tests/              镜像 app/ 结构 + tests/architecture/ (import boundary, weak test checker)
docs/phase1/        Phase 1 plan, spec, task index, task template, task logs
docs/phase1/tasks/  Phase 1 per-task prompts
docs/phase1/task_logs/  Phase 1 unified Task Records (YAML)
web/                前端 (React 18 + Vite + Ant Design 5.x)
experiments/        Spike 实验代码 (不进生产)
infra/docker/       Docker Compose 模板
```

## Git workflow
- **主分支**: `phase0/main` (Phase 1 续用此主干；不是 `main`)
- **任务分支**: Phase 2 起用 `phase2/<task_id>`；Phase 1 为 `phase1/<task_id>`（旧 `phase0/<task_id>` 仅历史/补丁）
- **Commit message**: `phase2(<task_id>): <简要描述>`（Phase 1 为 `phase1(<task_id>): ...`）
- **Merge message**: `merge phase2(<task_id>): <简要描述>`（Phase 1 为 `merge phase1(<task_id>): ...`）
- 每次 merge 到 phase0/main 后检查 remote GitHub Actions CI

## Validation commands
> **全量测试需要固定测试库在跑**：Docker Desktop 启动，测试库 healthy 于 `127.0.0.1:15432`，
> 且当前进程能看到 `DATABASE_URL`（用户级环境变量；进程若早于设置时启动则继承不到，重开终端/应用即可）。
> 缺 `DATABASE_URL` 时 DB 测试**失败而不是跳过**——静默跳过会被读成通过。
> 确实要跳过就显式 `--ignore=`，让省略在命令里看得见。基线：**1102 passed, 0 skipped, 0 failed**（含 25 个 Golden 参数化用例，截至 P2-TRACE-PERSIST-001 / merge `f8eb8533`）。

```powershell
# 单元测试（全量）
uv run pytest
# 指定测试
uv run pytest tests/ports/test_capability_gateway_port.py
# Lint（范围由 pyproject.toml 的 [tool.ruff] exclude 单一声明）
uv run ruff check .
# Type check
uv run mypy app/
# 依赖合规
uv run python scripts/check_dependencies.py
# Import boundary + weak test checker
uv run pytest tests/architecture/
# Weak test 单文件检查
uv run python scripts/check_weak_tests.py tests/ports/<test_file>.py
# Staged diff 检查 (commit 前必跑)
git diff --cached --name-only && git diff --cached --stat && git diff --cached --check
git ls-files --others --exclude-standard
```

## Phase 1 rules（开发已收尾；下列规则续用于 P2 与后续维护）
- Phase 1 development is complete (B2→B5 product chain + governance-repair chain landed, trunk CI green). Phase 2 has formally opened: `P2-TRACE-PERSIST-001` (persistent TracePort + Admin audit query) landed at merge `f8eb8533` (CI run 30017941828 success); one recorded debt remains — `P2-CONFIRM-RESUME-001` self-triggered (see `docs/phase1/TASK_INDEX.md` §5.1). The rules below stay in force for P2 and follow-up work. `app/ports/` contracts are no longer frozen by decree: change one when the design genuinely needs it, record why in the Task Record, and update every implementation and test in the same change. Prefer the smallest contract that fits. Never build a workaround for a contract you should have fixed — with a single maintainer a contract change is cheap and immediately visible, so process must not push the design sideways.
- These stay strict at any scope, because they fail silently: never weaken or delete a test assertion to make code pass (fix the code, or stop and report); never let a failure path report success or lose its error code; never regress session/tenant/user isolation or credential handling. `FROZEN_GT_IDS` / golden fixtures still need explicit human approval — golden is the regression net, and a weakened net is invisible.
- Hexagonal boundary holds: `app/ports/` must not depend on `app/infra/`.
- LLM baseline = Qwen + vLLM raw JSON. Do NOT introduce instructor / PydanticAI (rejected by ADR, internal-validated; see `docs/phase0/PHASE1_TECHNICAL_BASELINE.md` §3.1).
- `P1-GATE-001` landed: implementation `0e9d48e`, merge `7beebbd`; prompt hardening landed at `6c5f85a`. CI success: https://github.com/evarainy/eternalai/actions/runs/28843469212
- Later implementation tasks must run `uv run python scripts/run_golden_tasks.py --gate`.
- Historical governance-repair sequence completed: `P1-WORKFLOW-002-REPAIR-001 -> P1-CI-ALIGN-001 -> P1-OBS-001 -> P1-RUNTIME-ENTRY-001`. The general invariant remains: descriptor presence alone never releases a dependency. `P1-SPEC-001` was the independent B2 hard prerequisite and is approved/landed.
- Phase 1 role / Review / risk policy: `docs/phase1/ROLE_POLICY.md`; active V5 work uses Q0-Q3 rather than a universal independent-Review floor.
- The unified YAML Task Record and per-task prompt are V4 legacy formats. Existing records and the final `P1-WORKFLOW-V5-001` migration evidence keep their original meaning; a new V5 Goal does not generate them.
- One native Goal may handle multiple task IDs and auto-next. Each write lane remains isolated and owns one explicit Goal and a single Scope; a new scope requires a new lane/Worker Contract.
- Q0-Q3 controls Review strength. Risk alone does not create a human Gate; required stops come only from reserved redline actions; scope expansion; new or changed architecture, framework, public contract/API/protocol, trust boundary, or core invariant (an internal `app/ports/` change that preserves the existing architecture is not by itself a stop); a material unresolved choice; a stricter target repository Gate; or batch/milestone acceptance.
- There is no separate local-commit Gate. Ordinary non-force push, PR/merge, CI/CD configuration changes, and CI runs may proceed only when the current Goal and target repository policy explicitly allow them and validation, required Review, freshness, branch protection, and required checks pass. Historical Gate 2 remains post-integration result acceptance, not Git/CI authorization.
- Delete/history rewrite, secrets or `.env`, DB schema/real data, global/system changes, public release/production deployment, rebase, reset-hard, and force push need exact action-specific authorization; a risk label never supplies it. Never bypass hooks or branch protection.
- Use Explore/Plan/read-only behavior for investigation when possible.
- For a historical V4 task, incomplete task context requires a task-prompt patch instead of guessing. For V5, first investigate the applicable documents, code, tests, and history; ask only when Outcome / Constraints / Verification remains materially missing, and do not manufacture a per-task descriptor requirement.
- When executing: apply Execution Guardrails. When reviewing: apply Review Guardrails.
- Skills/subagents are advisory aids only; they must not override Goal authority, Phase 1 rules, scope, model routing, Review, or Git boundaries. New V5 Goals do not depend on the V4 command surface.

## Read on demand (not by default)
- Phase 1 V4 legacy task template: `docs/phase1/TASK_PROMPT_TEMPLATE.md`
- Phase 1 task index / dependency DAG: `docs/phase1/TASK_INDEX.md`
- Phase 1 task prompt: `docs/phase1/tasks/<task_id>.md`
- Phase 1 task logs: `docs/phase1/task_logs/`
- V4 legacy task record schema: `docs/dev/task_record_schema.yaml`
- Cross-stage context loading strategy: `docs/phase0/CONTEXT_LOADING_STRATEGY.md` (supporting guidance, not Phase 1 task authority)
- Role and method guardrails: `docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md`
- Repository navigation map: `docs/phase0/REPOSITORY_CONTEXT_MAP.md`
- Coding style baseline: `docs/phase0/CODING_STYLE_BASELINE.md` (load relevant sections only)
- Boundary checklist: `docs/phase0/BOUNDARY_CHECKLIST.md` (cross-stage support every 3 tasks; not Phase 1 task authority)

## Scratch/temp cleanup
- V5 Goal snapshots, Candidate Manifests, Recovery Indexes, Review evidence, and summaries live outside the repo under `$CODEX_RUNS_ROOT`, falling back to `$CLAUDE_CODEX_SCRATCH_ROOT/v5-runs`. Manual temp files go in `_scratch/` only. Neither goes in `app/`, `tests/`, `docs/`, or repo root.
- Before staging: remove `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`.
- Do not scan/clean inside `.venv/`.
