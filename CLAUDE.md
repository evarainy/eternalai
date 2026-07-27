# CLAUDE.md — Phase 2 Compact Claude Code Memory v2.3.0

Keep this file compact. Claude Code loads project memory at session start, so detailed rules must be read on demand instead of inlined here. Do not add `@import` links to long specs.

## 权威与当前阶段
权威顺序：当前 Goal（最新指令及 Outcome/Constraints/Verification）> 用户红线和适用的 `AGENTS.md` > 已批准的产品/架构/接口/批次/里程碑文档 > 仓库代码、测试、CI、分支保护和运行证据 > 派生计划、skills、历史与建议。

Phase 1 已完成。Phase 2 已落地 `P2-TRACE-PERSIST-001`（持久化 TracePort + Admin 审计查询，merge `f8eb8533`）和 `P2-AUTH-001`（OA 登录认证 + 认证接缝，替换自报身份，merge `b9f7a3c7f551f45dd975803eeeee276207ae9f8a`）。`P2-CONFIRM-RESUME-001` 仍登记在案但尚未触发，见 `docs/phase1/TASK_INDEX.md` §5.1。

每个 write lane 只承载一个明确 Goal、一个 Scope 和一个隔离 worktree/branch；新 scope 开新 lane。历史 V4 prompt/Task Record 保持原意，新工作以当前 Goal 和本文件为准。

## Project at a glance
- **EternalAI**: 政府/企业 AI Agent 运行时平台，自然语言驱动对接 OA、用友 U8、海康 iVMS
- **架构**: 六边形 (Ports/Adapters)，`app/ports/` = Protocol 接口，`app/infra/` = 实现；`app/ports/` 不得依赖 `app/infra/`
- **后端**: Python + uv + FastAPI | **前端**: React 18 + Vite + Ant Design 5.x
- **数据层**: PostgreSQL 18 + pgvector, Redis + ARQ, MinIO (S3)
- **LLM**: Qwen + vLLM raw JSON mode；禁止引入 instructor / PydanticAI（见 `docs/phase0/PHASE1_TECHNICAL_BASELINE.md` §3.1）
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

## Git、Review 与授权
- **主分支**: `phase0/main`（不是 `main`）
- **任务分支**: `phase2/<task_id>`
- **Commit**: `phase2(<task_id>): <简要描述>`
- **Merge**: `merge phase2(<task_id>): <简要描述>`
- Q0-Q3 只控制 Review 强度，不制造人工停点。人工停点仅来自专项红线动作、扩域、新增或变更架构/框架/公共契约/API/协议/信任边界/核心不变量、重大未决选择、更严格的仓库规则或批次/里程碑验收；保持既有架构的内部 `app/ports/` 变更本身不是停点。
- 不设独立 local-commit Gate。普通非强制 push/PR/merge、CI/CD 配置修改和 CI 运行，仅在 Goal 与仓库规则允许，且确定性验证、所需 Review、freshness、branch protection、required checks 均通过时执行。
- 删除文件/目录或改写历史、secrets/`.env`、DB schema/真实数据、全局/系统变更、公开发布/生产部署、rebase、reset-hard、force push，均需对应动作的专项授权；风险标签不构成授权。不得绕过 hooks 或 branch protection。
- 每次 merge 到 `phase0/main` 后检查对应的 remote GitHub Actions CI 结果。

## Validation commands
> **全量测试需要固定测试库在跑**：Docker Desktop 启动，测试库 healthy 于 `127.0.0.1:15432`，
> 且当前进程能看到 `DATABASE_URL`（用户级环境变量；进程若早于设置时启动则继承不到，重开终端/应用即可）。
> 缺 `DATABASE_URL` 时 DB 测试**失败而不是跳过**——静默跳过会被读成通过。
> 确实要跳过就显式 `--ignore=`，让省略在命令里看得见。基线：**1147 passed, 0 skipped, 0 failed**（含 25 个 Golden 参数化用例，截至 P2-AUTH-001 / merge `b9f7a3c7f551f45dd975803eeeee276207ae9f8a`）。

```powershell
uv run pytest
uv run pytest tests/ports/test_capability_gateway_port.py
uv run ruff check .
uv run mypy app/
uv run python scripts/check_dependencies.py
uv run pytest tests/architecture/
uv run python scripts/check_weak_tests.py tests/ports/<test_file>.py
uv run python scripts/run_golden_tasks.py --gate  # 所有后续实现任务必跑
git diff --cached --name-only
git diff --cached --stat
git diff --cached --check
git ls-files --others --exclude-standard
```

## 不可协商规则
1. 禁止修改 `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md`。
2. `app/ports/` 契约只在设计确实需要时变更：采用最小契约，记录理由，并在同一改动中更新所有实现与测试；不得用 workaround 绕开本应修正的契约。
3. 不得为换绿弱化测试（`assert True`、空 `pass`、宽泛 skip、删断言）；修代码，否则停手报告。失败路径不得报成功或丢 error code；不得回归 session/tenant/user 隔离。`FROZEN_GT_IDS` / golden fixtures 必须经人工显式批准。
4. 明文 password/token/cookie/sessionid/access_token/refresh_token 不得进入 Trace、ResponseEnvelope、fixture expected、日志、Task Record 或报告。
5. 不得用 `not_applicable` 隐藏失败；每项必须带 reason、blocked_by_task_id、activation_task_id、expiry_condition 和 evidence。
6. 下游 descriptor 存在不释放依赖门。Golden negative/boundary paths 必须 100% 通过，包括 GT-012 多绑定 scope clarification。

## Read on demand (not by default)
- Phase 1: `docs/phase1/TASK_PROMPT_TEMPLATE.md`, `docs/phase1/TASK_INDEX.md`, `docs/phase1/tasks/<task_id>.md`, `docs/phase1/task_logs/`, `docs/phase1/ROLE_POLICY.md`
- Legacy record schema: `docs/dev/task_record_schema.yaml`
- Cross-stage: `docs/phase0/CONTEXT_LOADING_STRATEGY.md`, `docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md`, `docs/phase0/REPOSITORY_CONTEXT_MAP.md`, `docs/phase0/CODING_STYLE_BASELINE.md`, `docs/phase0/BOUNDARY_CHECKLIST.md`
- Canonical long spec, only when needed: `docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md`

## Scratch/temp cleanup
- Goal snapshots, Candidate Manifests, Recovery Indexes, Review evidence, and summaries live outside the repo under `$CODEX_RUNS_ROOT`, falling back to `$CLAUDE_CODEX_SCRATCH_ROOT/v5-runs`; repo-local `_scratch/` 仅放手工临时文件。
- 暂存前清理 `__pycache__/`、`*.pyc`、`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`，不得暂存 `_scratch/` 内容。
- 收口前 `git ls-files --others --exclude-standard` 必须为空；若有意保留，须逐项解释。
- Scope 未明确包含时，不扫描或清理 `.venv/`。
