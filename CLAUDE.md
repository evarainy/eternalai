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
- Current mainline order: `P1-WORKFLOW-002 -> patch P1-SPEC-001 -> execute P1-SPEC-001 -> B2` (P1-ERRATA-001 / P1-WORKFLOW-001 landed).
- Phase 1 role / review / risk policy: `docs/phase1/ROLE_POLICY.md`.
- Task Record: use the unified YAML format under `docs/phase1/task_logs/` and schema `docs/dev/task_record_schema.yaml`.
- One session turn = one `task_id`, run through the global `codex-claude` workflow skill (sole process SOP; repo docs are result contracts only).
- Plan gate per ROLE_POLICY ceremony table: medium/high need a human-approved Plan before edits; low may proceed directly against the task prompt.
- Every repo-changing task requires `independent_review` (universal review floor, see ROLE_POLICY).
- No push, no merge unless a human explicitly approves (Gate 2). Local commit follows the ROLE_POLICY ceremony table (low/medium: after review PASS; high: human ack).
- Use Explore/Plan/read-only behavior for investigation when possible.
- If task context is incomplete, stop and ask for a task-prompt patch instead of guessing.
- When executing: apply Execution Guardrails. When reviewing: apply Review Guardrails.
- Skills/subagents are advisory aids only; they must not override Phase 1 rules or task prompts.

## Read on demand (not by default)
- Phase 1 task template: `docs/phase1/TASK_PROMPT_TEMPLATE.md`
- Phase 1 task index / dependency DAG: `docs/phase1/TASK_INDEX.md`
- Phase 1 task prompt: `docs/phase1/tasks/<task_id>.md`
- Phase 1 task logs: `docs/phase1/task_logs/`
- Task record schema: `docs/dev/task_record_schema.yaml`
- Cross-stage context loading strategy: `docs/phase0/CONTEXT_LOADING_STRATEGY.md` (supporting guidance, not Phase 1 task authority)
- Role and method guardrails: `docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md`
- Repository navigation map: `docs/phase0/REPOSITORY_CONTEXT_MAP.md`
- Coding style baseline: `docs/phase0/CODING_STYLE_BASELINE.md` (load relevant sections only)
- Boundary checklist: `docs/phase0/BOUNDARY_CHECKLIST.md` (cross-stage support every 3 tasks; not Phase 1 task authority)

## Scratch/temp cleanup
- Workflow-skill runtime scratch lives outside the repo (`$CLAUDE_CODEX_SCRATCH_ROOT`). Manual / non-skill temp files go in `_scratch/` only. Neither goes in `app/`, `tests/`, `docs/`, or repo root.
- Before staging: remove `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`.
- Do not scan/clean inside `.venv/`.
