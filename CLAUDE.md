# CLAUDE.md — Phase 2 Compact Claude Code Memory v2.3.3

Keep this file compact. Claude Code loads project memory at session start, so detailed rules must be read on demand instead of inlined here. Do not add `@import` links to long specs.

## 权威与当前阶段
权威顺序：当前 Goal（最新指令及 Outcome/Constraints/Verification）> 用户红线和适用的 `AGENTS.md` > 已批准的产品/架构/接口/批次/里程碑文档 > 仓库代码、测试、CI、分支保护和运行证据 > 派生计划、skills、历史与建议。

Phase 1 已完成。Phase 2 已落地 `P2-TRACE-PERSIST-001`（持久化 TracePort + Admin 审计查询，merge `f8eb8533`）、`P2-AUTH-001`（OA 登录认证 + 认证接缝，替换自报身份，merge `b9f7a3c7f551f45dd975803eeeee276207ae9f8a`）、`P2-PILOT-FOUNDATION-001`（真实生产 composition 与收尾加固，merge `51af461e561d814deef5f813245520a3dae73871`，PR #46，CI run 30422795582 success）、`P2-DEVENV-PREFLIGHT-001`（`scripts/check_dev_environment.py`：diagnostic tool，not a gate；未接入 conftest、CI 或既有命令；merge `39c437a56bf3da27da457fb585c72fa375872c6d`，PR #48，CI run 30429764545 success）、`P2-OA-READ-CONTRACT-001`（OA Replay adapter + 版本化 Contract Pack + 离线脱敏器，merge `89cd16e33020806c8a7a28170f93689963de5235`）、`P2-READ-ADAPTER-001`（OA Live HTTP、每用户 Session 凭证读取、IdentityMapping、Live 指纹漂移比较与运行时闭环，merge `f9526a464dbf72ad3a39d8fcb8ec750c226f880f`）、`P2-FE-API-CLIENTS-001`（Auth / Runtime / Admin-Trace OpenAPI 与 Orval 客户端固化，merge `e60b388edb478f66f70d36e9853f90b43fca7686`）、`P2-EOL-FREEZE-001`（`web/.gitattributes` 将冻结产物行尾钉死 LF，修掉 Windows 检出导致的无漂移测试假红，merge `362710de8a1e397aba68d4a3aad7aff4b8739e20`）、`P2-SANITIZE-LEAK-FIX-001`（脱敏器不再把敏感值写入产物或错误回显；检测规则集未削减，merge `01fc6c459e7a8df4ec410ab0273f1fa08e3cd057`）、`P2-IDENTITY-CREDENTIAL-001`（新增凭证撤销与重置，撤销后新调用返回 `identity_revoked`，merge `f8208cd0532dc5fa2ae1b2c3e60dbd0e1dabf1b8`）、`P2-ADMIN-CSRF-001`（所有 Cookie 认证的非安全方法均要求合法 `Origin` + 固定自定义头，缺失、重复或错误一律返回 403，并含动态路由枚举守卫以防新增路由漏接线，merge `daf90f263352a14cfeb9d500b30558e7fb6ec046`）、`P2-RUNTIME-RESPONSE-CONTRACT-001`（把既有 `ResponseEnvelope` 声明进 Runtime OpenAPI 并重新生成客户端；响应体逐字节不变，merge `83e6ec82729a045a6d3c77039dbb952fe9bb03ff`）、`P2-PILOT-ENTRY-FE-001`（接通登录页、EternalAI Session Cookie、统一 fail-closed 401 重认证与受保护路由，并停发 `X-EternalAI-Roles`，merge `d3a536e91001f6f008d4dbb1b8ec321988b70b66`）、`P2-CHAT-ENTRY-FE-001`（受保护 `/chat` 普通文本对话入口，消费 Runtime Orval client 与既有认证会话；`loginNavigation` 白名单新增 `/chat` 精确匹配；不实现 SDUI 渲染器或结构化 Action，merge `092a9095cbe4b572a8987707d2ab098887bcd123`）、`P2-BE-SMALL-DEBT-001`（OA adapter 兜底异常补安全可观测日志，`.env.example` 补 OA 读适配三项占位配置，对外 `adapter_error` 与 Trace 行为不变，merge `3a901c5bcde8b9b704d70d889c3f6fc165edaa6d`）、`P2-FE-TEST-FLAKE-001`（关闭 Vitest 文件级并发，根治 Ant 组件测试 5s 超时 flake，测试数仍为 81；`App.test.tsx` 负向文案改为精确标题；无生产代码改动，merge `6c47f06f3cae5af43efdbeb79a0d2bfe68f5517b`）、`P2-OA-SYSMSG-PACK-001`（新增 `oa.list_system_messages` 与并列 pack `ecology9-system-messages-v1`；脱敏器删掉 transport-header 豁免，改为 9 字符子串阈值 + 短值完整 token 匹配，merge `b55104d193862a78b7529e657ceea4639ed6e152`，PR #65）和 `P2-AUTH-USERID-TYPE-001`（在 `_required_oa_user_id` 单点归一 OA 整数/字符串 `userid`，守卫测试证明 principal 相同且凭证行、IdentityMapping 各恰好 1 条，merge `a9bf8b8fc3fbf48448ca511768fe7271d8b8a221`，CI run 30797244405 success）。`P2-OA-LOGIN-PARITY-001` 也已完成纯只读诊断，无分支无 commit：代码同时检查 `msgcode` 与 `loginstatus`，未发现认证绕过；发现的整数 `userid` 缺陷现已修复。当前 `phase0/main` 基线 = `a9bf8b8fc3fbf48448ca511768fe7271d8b8a221`；正式欠债登记见 `docs/phase2/PHASE2_PLAN.md`，包括 `P2-CONFIRM-RESUME-001`（尚未触发）、由 `P2-OA-INTRANET-SMOKE-001` 激活的真实 OA 证据缺口、required-check 绕过堵漏、仓库清理和四个未决架构问题。下一棒 = `P2-OA-INTRANET-SMOKE-001`。

每个 write lane 只承载一个明确 Goal、一个 Scope 和一个隔离 worktree/branch；新 scope 开新 lane。历史 V4 prompt/Task Record 保持原意，新工作以当前 Goal 和本文件为准。

## Project at a glance
- **EternalAI**: 政府/企业 AI Agent 运行时平台，自然语言驱动对接 OA、用友 U8、海康 iVMS
- **架构**: 六边形 (Ports/Adapters)，`app/ports/` = Protocol 接口，`app/infra/` = 实现；`app/ports/` 不得依赖 `app/infra/`
- **后端**: Python + uv + FastAPI + uvicorn 0.51.0（真实进程入口 `python -m app.server`）| **前端**: React 18 + Vite + Ant Design 5.x
- **数据层**: PostgreSQL 18 + pgvector, Redis + ARQ, MinIO (S3)
- **LLM**: vLLM raw JSON mode；默认 `http://34.74.11.38:8011/v1` + `glm-4.7`，URL / model / 采样参数均可由 env 覆盖；禁止引入 instructor / PydanticAI（见 `docs/phase0/PHASE1_TECHNICAL_BASELINE.md` §3.1）
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
- 不设独立 local-commit Gate。普通非强制任务分支 push/PR/merge、CI/CD 配置修改和 CI 运行，仅在 Goal 与仓库规则允许，且确定性验证、所需 Review、freshness、branch protection、required checks 均通过时执行。集成到 `phase0/main` 一律走 PR：push 任务分支 → 开 PR → 等 required checks 最终全绿 → 通过 PR 合并。即使没有显式 bypass 参数，也永不本地合完直推 `phase0/main`。
- 仓库 owner 待办：为 `phase0/main` 的 GitHub 分支保护打开 **Do not allow bypassing the above settings**。本项是已登记欠债；没有专项授权时 agent 不得修改该设置。
- 删除文件/目录或改写历史、secrets/`.env`、DB schema/真实数据、全局/系统变更、公开发布/生产部署、rebase、reset-hard、force push，均需对应动作的专项授权；风险标签不构成授权。不得绕过 hooks 或 branch protection。
- 每次 merge 到 `phase0/main` 后检查对应的 remote GitHub Actions CI 结果。

## Validation commands
> **全量测试需要固定测试库在跑**：Docker Desktop 启动，测试库 healthy 于 `127.0.0.1:15432`，
> 且当前进程能看到 `DATABASE_URL`（用户级环境变量；进程若早于设置时启动则继承不到，重开终端/应用即可）。
> 缺 `DATABASE_URL` 时 DB 测试**失败而不是跳过**——静默跳过会被读成通过。
> 确实要跳过就显式 `--ignore=`，让省略在命令里看得见。基线：**1497 passed, 0 skipped, 0 failed**（含 25 个 Golden 参数化用例；取自 P2-AUTH-USERID-TYPE-001 / merge `a9bf8b8fc3fbf48448ca511768fe7271d8b8a221` 的 CI run 30797244405 实测日志）。

```powershell
uv run pytest
uv run python scripts/check_dev_environment.py --start-full-tests  # 后台全量测试；日志/状态写入 _scratch/
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
- 常设清理授权只适用于当前任务 worktree 内的 `__pycache__/`、`*.pyc`、`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`。动手前先跑 `git ls-files --others --exclude-standard`；为空就不删除。只对已解析的精确目标操作，不用通配范围递归删除；不碰 `.venv/`、源码/产物、其他 worktree 或 git 历史。除此之外的文件/目录删除仍是红线，必须取得对应专项授权；不得暂存 `_scratch/` 内容。
- 收口前 `git ls-files --others --exclude-standard` 必须为空；若有意保留，须逐项解释。
- Scope 未明确包含时，不扫描或清理 `.venv/`。
