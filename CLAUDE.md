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
- **任务分支**: `phase1/<task_id>` (例: `phase1/P1-XXX-001`；旧 `phase0/<task_id>` 仅历史/补丁)
- **Commit message**: `phase1(<task_id>): <简要描述>`
- **Merge message**: `merge phase1(<task_id>): <简要描述>`
- 每次 merge 到 phase0/main 后检查 remote GitHub Actions CI

## Validation commands
```powershell
# 单元测试（全量）
uv run pytest
# 指定测试
uv run pytest tests/ports/test_capability_gateway_port.py
# Lint
uv run ruff check app/ tests/
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

## Phase 1 rules (当前阶段)
- Phase 1 implementation in progress. Phase 0 interface contracts in `app/ports/` are FROZEN: do not edit without explicit task authorization + human approval.
- Hexagonal boundary holds: `app/ports/` must not depend on `app/infra/`.
- LLM baseline = Qwen + vLLM raw JSON. Do NOT introduce instructor / PydanticAI (rejected by ADR, internal-validated; see `docs/phase0/PHASE1_TECHNICAL_BASELINE.md` §3.1).
- `P1-GATE-001` landed: implementation `0e9d48e`, merge `7beebbd`; prompt hardening landed at `6c5f85a`. CI success: https://github.com/evarainy/eternalai/actions/runs/28843469212
- Later implementation tasks must run `uv run python scripts/run_golden_tasks.py --gate`.
- Active governance-repair order: `P1-WORKFLOW-002-REPAIR-001 -> P1-CI-ALIGN-001 -> P1-OBS-001 -> P1-RUNTIME-ENTRY-001`. Descriptor presence alone never releases a dependency. `P1-SPEC-001` remains the independent B2 hard prerequisite.
- Phase 1 role / Review / risk policy: `docs/phase1/ROLE_POLICY.md`; active V5 work uses Q0-Q3 rather than a universal independent-Review floor.
- The unified YAML Task Record and per-task prompt are V4 legacy formats. Existing records and the final `P1-WORKFLOW-V5-001` migration evidence keep their original meaning; a new V5 Goal does not generate them.
- One native Goal may handle multiple task IDs and auto-next. Each write lane remains isolated and owns one explicit Goal and a single Scope; a new scope requires a new lane/Worker Contract.
- Q0-Q3 controls Review strength. Risk alone does not create a human Gate; required stops come only from reserved redline actions; scope expansion; new or changed architecture, framework, public contract/API/protocol, trust boundary, or core invariant; a material unresolved choice; a stricter target repository Gate; or batch/milestone acceptance.
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
