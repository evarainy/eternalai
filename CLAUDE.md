# CLAUDE.md — Phase 1 Compact Claude Code Memory v2.0.0

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
docs/phase0/        Phase 0 任务 prompt、规则、样式基线、上下文加载策略
docs/phase0/tasks/  每个任务的 prompt 文件
docs/phase0/task_logs/  统一 Task Record (YAML)
web/                前端 (React 18 + Vite + Ant Design 5.x)
experiments/phase0/ Spike 实验代码 (不进生产)
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
- Task Record: reuse the Phase 0 YAML format + `task_logs/INDEX` row (Phase 1 `docs/phase1/` layout TBD in the Phase 1 Plan).
- One session turn = one `task_id`. Start with `/phase-task <task_id>`.
- Plan first; wait for human approval before edits.
- Use Explore/Plan/read-only behavior for investigation when possible.
- If task context is incomplete, stop and ask for a task-prompt patch instead of guessing.
- When executing: apply Execution Guardrails. When reviewing: apply Review Guardrails.
- Skills/subagents are advisory aids only; they must not override Phase 1 rules or task prompts.

## Read on demand (not by default)
- Context loading strategy: `docs/phase0/CONTEXT_LOADING_STRATEGY.md`
- Task template: `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md`
- Role and method guardrails: `docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md`
- Repository navigation map: `docs/phase0/REPOSITORY_CONTEXT_MAP.md`
- Task index / dependency DAG: `docs/phase0/TASK_INDEX.md`
- Coding style baseline: `docs/phase0/CODING_STYLE_BASELINE.md` (load relevant sections only)
- Boundary checklist: `docs/phase0/BOUNDARY_CHECKLIST.md` (every 3 tasks)

## Scratch/temp cleanup
- Temp files go in `_scratch/` only. Not in `app/`, `tests/`, `docs/`, or repo root.
- Before staging: remove `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`.
- Do not scan/clean inside `.venv/`.
