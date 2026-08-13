# CLAUDE.md — Phase 2 Compact Claude Code Memory v2.3.5

Keep this file compact. Claude Code loads project memory at session start, so detailed rules must be read on demand instead of inlined here. Do not add `@import` links to long specs.

## 权威与当前阶段
权威顺序：当前 Goal（最新指令及 Outcome/Constraints/Verification）> 用户红线和适用的 `AGENTS.md` > 已批准的产品/架构/接口/批次/里程碑文档 > 仓库代码、测试、CI、分支保护和运行证据 > 派生计划、skills、历史与建议。

Phase 1 已完成。Phase 2 已落地 `P2-TRACE-PERSIST-001`（持久化 TracePort + Admin 审计查询，merge `f8eb8533`）、`P2-AUTH-001`（OA 登录认证 + 认证接缝，替换自报身份，merge `b9f7a3c7f551f45dd975803eeeee276207ae9f8a`）、`P2-PILOT-FOUNDATION-001`（真实生产 composition 与收尾加固，merge `51af461e561d814deef5f813245520a3dae73871`，PR #46，CI run 30422795582 success）、`P2-DEVENV-PREFLIGHT-001`（`scripts/check_dev_environment.py`：diagnostic tool，not a gate；未接入 conftest、CI 或既有命令；merge `39c437a56bf3da27da457fb585c72fa375872c6d`，PR #48，CI run 30429764545 success）、`P2-OA-READ-CONTRACT-001`（OA Replay adapter + 版本化 Contract Pack + 离线脱敏器，merge `89cd16e33020806c8a7a28170f93689963de5235`）、`P2-READ-ADAPTER-001`（OA Live HTTP、每用户 Session 凭证读取、IdentityMapping、Live 指纹漂移比较与运行时闭环，merge `f9526a464dbf72ad3a39d8fcb8ec750c226f880f`）、`P2-FE-API-CLIENTS-001`（Auth / Runtime / Admin-Trace OpenAPI 与 Orval 客户端固化，merge `e60b388edb478f66f70d36e9853f90b43fca7686`）、`P2-EOL-FREEZE-001`（`web/.gitattributes` 将冻结产物行尾钉死 LF，修掉 Windows 检出导致的无漂移测试假红，merge `362710de8a1e397aba68d4a3aad7aff4b8739e20`）、`P2-SANITIZE-LEAK-FIX-001`（脱敏器不再把敏感值写入产物或错误回显；检测规则集未削减，merge `01fc6c459e7a8df4ec410ab0273f1fa08e3cd057`）、`P2-IDENTITY-CREDENTIAL-001`（新增凭证撤销与重置，撤销后新调用返回 `identity_revoked`，merge `f8208cd0532dc5fa2ae1b2c3e60dbd0e1dabf1b8`）、`P2-ADMIN-CSRF-001`（所有 Cookie 认证的非安全方法均要求合法 `Origin` + 固定自定义头，缺失、重复或错误一律返回 403，并含动态路由枚举守卫以防新增路由漏接线，merge `daf90f263352a14cfeb9d500b30558e7fb6ec046`）、`P2-RUNTIME-RESPONSE-CONTRACT-001`（把既有 `ResponseEnvelope` 声明进 Runtime OpenAPI 并重新生成客户端；响应体逐字节不变，merge `83e6ec82729a045a6d3c77039dbb952fe9bb03ff`）、`P2-PILOT-ENTRY-FE-001`（接通登录页、EternalAI Session Cookie、统一 fail-closed 401 重认证与受保护路由，并停发 `X-EternalAI-Roles`，merge `d3a536e91001f6f008d4dbb1b8ec321988b70b66`）、`P2-CHAT-ENTRY-FE-001`（受保护 `/chat` 普通文本对话入口，消费 Runtime Orval client 与既有认证会话；`loginNavigation` 白名单新增 `/chat` 精确匹配；不实现 SDUI 渲染器或结构化 Action，merge `092a9095cbe4b572a8987707d2ab098887bcd123`）、`P2-BE-SMALL-DEBT-001`（OA adapter 兜底异常补安全可观测日志，`.env.example` 补 OA 读适配三项占位配置，对外 `adapter_error` 与 Trace 行为不变，merge `3a901c5bcde8b9b704d70d889c3f6fc165edaa6d`）、`P2-FE-TEST-FLAKE-001`（关闭 Vitest 文件级并发，根治 Ant 组件测试 5s 超时 flake，测试数仍为 81；`App.test.tsx` 负向文案改为精确标题；无生产代码改动，merge `6c47f06f3cae5af43efdbeb79a0d2bfe68f5517b`）、`P2-OA-SYSMSG-PACK-001`（新增 `oa.list_system_messages` 与并列 pack `ecology9-system-messages-v1`；脱敏器删掉 transport-header 豁免，改为 9 字符子串阈值 + 短值完整 token 匹配，merge `b55104d193862a78b7529e657ceea4639ed6e152`，PR #65）和 `P2-AUTH-USERID-TYPE-001`（在 `_required_oa_user_id` 单点归一 OA 整数/字符串 `userid`，守卫测试证明 principal 相同且凭证行、IdentityMapping 各恰好 1 条，merge `a9bf8b8fc3fbf48448ca511768fe7271d8b8a221`，CI run 30797244405 success）、`P2-OA-SYSMSG-LIVE-001`（启用系统消息 Live 路由、按 capability 分离配置、模型校验与指纹漂移处理，merge `9da2fe5a1948800f90110d5adbd033553d01a808`）、`P2-OA-MSGCENTER-PROTOCOL-001`（对齐共享 OA 消息中心传输、保守 cursor 分页与 fail-closed 截断守卫，merge `c44ed56f426fd01104cf94bbb946f2baaf065efc`）、`P2-SMOKE-RUNNER-001`（新增不泄漏凭证且 fail-closed 的内网 smoke runner，merge `caaf801fcaa011573fc5c5fe1f1d8565a2cfc287`）和 `P2-SMOKE-AUTH-DIAG-001`（以真实同源抓包发布 pending-workflows-v2、保持 v1 逐字节不变、关闭 Gateway binding-scope oracle 并恢复被弱化断言，merge `1bf2ba6c895fec4b847f2369f13f22879920000b`）。`P2-OA-LOGIN-PARITY-001` 也已完成纯只读诊断，无分支无 commit：代码同时检查 `msgcode` 与 `loginstatus`，未发现认证绕过；发现的整数 `userid` 缺陷现已修复。当前 `phase0/main` 基线 = `P2-SMOKE-E2E-CHAIN-001`；正式决定/欠债登记见 `docs/phase2/PHASE2_PLAN.md`，包括 `P2-CONFIRM-RESUME-001`（尚未触发）、由 `P2-OA-INTRANET-SMOKE-001` 激活的真实 OA 证据缺口、required-check 绕过堵漏、仓库清理，以及雨爷于 2026-08-03 拍板的四项架构决定：写操作使用用户自己的 OA 凭证并逐次人工确认留痕；数据库不算目标系统，不做 IdentityMapping、不加 `db` 枚举或 schema 变更，但每次访问必须进入 Trace；企业密钥由运维负责，纯内网不设定期轮换，但后台必须支持运行时随时手动更新；Golden 策略以本段下方逐字「决定四」为准。派生工作仍有尚未排期的企业密钥运行时管理面，以及尚未拍板的 Golden 题外生命周期清单棒。`P2-OA-INTRANET-SMOKE-001` 已部分完成：2026-08-07 现场输入已足够启动待办 Adapter，真实 OA Live 指纹漂移比对仍是内网欠债。下一棒指针暂留空，等待 GOV-SYNC 做 B 类 DAG 裁决。

> 现役状态说明：上一段只保留历史落地索引，其中的“当前基线、现场欠债、决定三、派生工作和下一棒”投影全部失效；以下一段为唯一现役答案。

当前 Phase 2 交付状态（2026-08-13）：`P2-REGISTRY-DOC-FIX-001`、`P2-SMOKE-FAILURE-CODE-001`、`P2-HAR-FORM-DECODE-001`、`P2-GOLDEN-CREDENTIAL-HARDENING-001`、`P2-HAR-READ-FORM-DECODE-001` 与 `P2-SMOKE-VERIFY-DIAGNOSTICS-001` 均已落地，本次 `P2-GOV-SYNC-011` 负责把 2026-08-11 至 08-13 的现役事实同步回治理面。真实 OA 现场 `verify` 已以最小请求头、全新 Cookie 登录状态和两个 capability 完成 Live 指纹比对，结构漂移均为 none、HTTP 均为 200、全链 2/2 passed；浏览器真实 `/chat` 返回也已人工确认，因此 `P2-OA-INTRANET-SMOKE-001` 结项，首个 OA Adapter 的现场证据到位，第二个系统仍待选。当前实跑基线为 **2065 passed, 0 skipped, 0 failed**（78 warnings）；Golden Gate 为 **27/27 passed, 0 skipped, 0 failed**（negative 16/16，positive 11/11）；`tests/architecture/` 为 **33 passed**。决定三修订为企业密钥由运维通过配置文件管理，不建设运行时管理页面；页面输入框对应的 SSRF allowlist 要求随之取消，既有 LLM 地址默认值仅属配置卫生。安全开关必须依据可校验的协议事实或配置值，不能依赖自由文本环境标签；`ENV` 只承载既有 testing/mock 边界，不是生产安全分流字段。只读泄漏面审计确认 public 仓库及 Git 历史未发现真实 OA 素材泄漏；`_scratch/oa/` 仍按原始敏感素材处置，现有 `sanitize_oa_contract_pack.py` 是 Contract Pack 生成器与双重泄漏守卫，不是 HAR 清洗器，专用导出链路建立前不得分享 HAR。下一棒指针继续留空：现场证据已解除 `P2-LOW-RISK-WRITE-001` 的内网前置，但该棒仍依赖 `P2-GOLDEN-001`，且前端 SDUI 记录列表/渲染器形态受未决前端选型影响；全部 open debt 见 `docs/phase2/PHASE2_PLAN.md` 五字段台账。

决定四（逐字）：负向、边界和安全拒绝用例的题面、预期、禁止项、分类及判卷契约冻结，修改需雨爷明确批准。所有既有正向题面同样不可原地改写，只能新增后继题并在题外生命周期清单中停止旧题运行。判卷契约或运行选择规则变更时，必须按同一版本包全量回放并明确披露影响。每修复一个真实缺陷，必须新增一条能在未修代码上失败、修复后通过、且走原缺陷路径的永久回归证据；缺陷属于 Golden Runtime 观察边界时才新增 Golden Task，否则放在最小且忠实的单元/集成/API/浏览器层。

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
- Phase 2 不建独立 per-task Task Record。每个 PR body 必须固定包含 `## Scope`、`## 验证结果（pytest / Golden 原始结果行 + CI run）`、`## 本棒新增欠债` 三段；PR body 是绑定 commit 与 CI run 的永久任务记录。
- PR body 三段必须在合并前全部完成；「本棒新增欠债」中的每条欠债须在合并前具备 reason、blocked_by_task_id、activation_task_id、expiry_condition、evidence 五个字段。合并后补写不计为合规任务记录。required checks 全绿只是必要条件，不构成自行合并授权：配有监理窗口的棒必须先获监理 PASS；未配监理窗口的棒，只有启动提示词显式授权时才可自行合并。
- 监理窗口分级：修改 `app/ports/` 契约、DB schema、凭证语义、Golden fixture / `FROZEN_GT_IDS` 或安全边界（认证、CSRF、脱敏、隔离）的棒必须配监理窗口；单一表面的小棒、纯文档棒、纯配置棒可跳过，改为合并后由主窗口派子智能体抽查。跳过监理仍须在启动提示词中显式写明合并授权。
- 治理同步按 write lane 的并发形态分流。A 类机械同步 = 测试基线数字、`task_id`、「下一棒」指针和本棒新发现的欠债：同一时刻仅一个 write lane 时，由该实现棒在本棒 payload commit 内一次完成，不另开 commit、不 amend、不 force push；同时有两个及以上 write lane 时，所有实现棒均不得为 A 类改动三份共享治理文档，统一交独立 GOV-SYNC 批次棒。治理文档一律不记 commit SHA 与 CI run id——写入动作本身会改变被写入的值，且二者是 `task_id` 的冗余投影；追溯用 `git log --grep=<task_id>`（commit 规范 `phase2(<task_id>):` 保证命中），CI 结果以 GitHub 为唯一权威，运行证据留在 PR body 的「验证结果」段。B 类跨棒裁决、推翻蓝图偏差的 ADR、Golden 策略、DAG 重排和跨棒欠债合并永远归 GOV-SYNC；实现棒只能机械传播已决 DAG 的下一个 `task_id`，不得自行挑选后继，不唯一时留空并登记待裁决。开棒前必须在启动提示词写明串行或并行，并声明本棒是否承担 A 类同步；在 `CLAUDE.md` 与 `AGENTS.md` 两处状态段结构改造均完成前，并行场景继续受此限制。
- 仓库 owner 待办：为 `phase0/main` 的 GitHub 分支保护打开 **Do not allow bypassing the above settings**。本项是已登记欠债；没有专项授权时 agent 不得修改该设置。
- 删除文件/目录或改写历史、secrets/`.env`、DB schema/真实数据、全局/系统变更、公开发布/生产部署、rebase、reset-hard、force push，均需对应动作的专项授权；风险标签不构成授权。不得绕过 hooks 或 branch protection。
- 每次 merge 到 `phase0/main` 后检查对应的 remote GitHub Actions CI 结果。

## Validation commands
> **全量测试需要固定测试库在跑**：Docker Desktop 启动，测试库 healthy 于 `127.0.0.1:15432`，
> 且当前进程能看到 `DATABASE_URL`（用户级环境变量；进程若早于设置时启动则继承不到，重开终端/应用即可）。
> 缺 `DATABASE_URL` 时 DB 测试**失败而不是跳过**——静默跳过会被读成通过。
> 确实要跳过就显式 `--ignore=`，让省略在命令里看得见。基线：**2065 passed, 0 skipped, 0 failed**（78 warnings）；Golden Gate **27/27 passed, 0 skipped, 0 failed**；`tests/architecture/` **33 passed**（由 `P2-GOV-SYNC-011` 实跑验证）。

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
7. 安全开关只能依赖可真实校验的协议事实或配置值；不得让 `ENV` 等自由文本环境标签承担安全分流，避免拼写错误导致 fail-open。
8. 面向单棒执行 agent 的临时作业约束不得原样写入长期交付文档；只保留经当前设计验证的永久合同，防止把一次性禁令误固化为现役规则。

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
