# Phase 2 总目标、范围与任务 DAG（Lean Plan）

> 状态：**生效（轻量地图）**。单人开发 + 强模型（Opus 5 / GPT-5.6）下不再走「总体计划 → 每任务 task_id 提示词 → 每任务 SPEC」那套 ceremony：本文件是范围边界 + DAG + BLOCKED 护栏的**地图**，不是逐任务合同；task_id 是路标而非正式 lane 依据，无需「Opus 实审 + 拍板生效」门即可据此开 lane。
>
> 仍然硬约束（与流程无关，不可省）：① 红线动作先问；② 密钥不进代码/日志/Trace/fixture；③ 改完跑验证（基线 1458 passed / 0 skipped / 0 failed，截至 `P2-FE-TEST-FLAKE-001` / merge `6c47f06f3cae5af43efdbeb79a0d2bfe68f5517b`）；④ **BLOCKED 外部输入未到时不启动、只解除不猜测**；⑤ 命中信任边界（真实认证/凭证/外部 API）的任务仍走 Opus 评审（见 `opus-review-scope-rule`），减的是文档 ceremony，不是信任边界评审。

## 1. P2 总目标

P2 把已完成的 **Mock/低风险 B2→B5 闭环**，推进为**至少 1 个真实系统的部门试点纵切：先只读验收，再做 1 个获批的低风险写入**；所有真实调用仍经 Gateway / Policy / Trace / Evaluator，并补齐试点必需的可信入口、账号绑定与凭证、审计与反馈、Golden、User Profile / Semantic Memory 及基础 Skill 候选治理。（`PHASE1_SPEC.md` L13-L25；蓝图 §3.2 L176-L186、§13 L2680-L2717）

- **真实但克制**：首个真实 API Adapter 为必达；第二个仅在首个稳定且雨爷选定后进入。（蓝图 §13 L2701-L2707）
- **安全可运维**：真实身份/凭证不可再用 Mock，绑定、审计与管理动作形成闭环。（蓝图 §7.4.7 L1671-L1677、§7.5.1 L1685-L1691）
- **以证据扩展**：更多 Golden、用户反馈统计和审计看板共同约束试点，不以“接口能调通”冒充完成。（蓝图 §7.6 L1752-L1786、§13 L2708-L2714）
- **进化只到候选**：P2 可有基础 Skill 候选池，但不自动生成、发布或扩大权限。（蓝图 §2.5 L110-L118、§10.3 L2207-L2253、§13 L2710）

## 2. 范围边界

### IN（P2 做）

| 能力/闭环 | 一句话交付与边界 | 来源 |
|---|---|---|
| 可运行的试点基线（**已拍板：P2 首个硬前置**） | 生产装配入口已接入真实 structured-output LLM、Runtime/Auth/Admin 与可信试点用户身份，Admin context 来自认证 Principal，使既有低风险主链可启动、可健康检查、可审计；不选定新框架。 | `app/main.py`；`app/api/v1/admin.py`；蓝图 §12.1.3 L2492-L2510、§12.1.5 L2538-L2549、§13 L2701-L2703 |
| 真实 API Adapter 只读纵切 | 选 1 个真实系统，把一个只读用例从请求、真实身份、Gateway、Adapter、Evaluator、Trace 跑到响应；第二个 Adapter 是可选增量。 | 蓝图 §8.1 L1794-L1808、§13 L2703-L2707、§15 L2870-L2907 |
| 基础 DB Gateway | 只对业务负责人/DBA 批准的只读视图和注册查询能力开放，参数化、限行、超时、脱敏、审计；无批准用例时保持 BLOCKED。 | 蓝图 §8.2 L1810-L1826、§8.7 L1926-L1947、§13 L2707 |
| 真实绑定与凭证闭环 | 对选定系统落地正式 Secret 管理、真实 bind mode、基础凭证验证，以及管理员查看/筛选/解绑/重置/发送引导；支持 Excel/HR 导入映射，禁止导入密码。 | 蓝图 §7.4.3 L1540-L1549、§7.4.7 L1671-L1677、§7.5.2 L1693-L1717、§13 L2712-L2714 |
| 试点运营面 | 在已落地持久 Trace/查询之上提供审计看板；接收最小用户反馈并形成基础统计，不自动生成建设 backlog。 | 蓝图 §7.6 L1752-L1786、§9.2 L2064-L2082、§13 L2709-L2711；`TASK_INDEX.md` §5.1 L109-L118 |
| P2 Memory 增量 | 引入按用户隔离的 User Profile Memory，并增强制度、字段、报表口径和业务术语等 Semantic Memory；不进入 Episodic/Procedural/Knowledge Vault。 | 蓝图 §10.1 L2148-L2186、§10.2 L2188-L2205、§13 L2715 |
| 基础 Skill 候选池 | 只保存受治理、可审查的候选及来源引用；候选不能自动发布、执行或晋升 scope。候选如何产生留作开放问题。 | 蓝图 §10.3 L2207-L2253、§13 L2710 |
| P2 Golden 增量 | 为真实 Adapter、真实绑定、DB Gateway、隔离、反馈与审计边界补 Golden；负向/安全拒绝继续 100%，冻结 ID/fixture 仍须显式人批。 | 蓝图 §9.3.2 L2120-L2139、§13 L2708；`PHASE1_SPEC.md` L11、L62-L68 |
| 低风险写入纵切 | 只读试点通过后，选 1 个获批写操作，具备幂等、预览、确认、补偿声明、Evaluator 与审计；没有安全用例/沙箱则不排期。 | 蓝图 §5.9 L878-L918、§13 L2703 |

### OUT（P2 仍排除）

| 排除项 | 裁剪理由 | 重开条件 | 来源 |
|---|---|---|---|
| Controlled Exploration（含 P2 测试只读草案）、Dynamic Tool Composition | 蓝图只“允许”P2 在严格前置下试验草案，不是部门试点必达；当前主线先完成真实纵切，封闭系统也禁止未知路径探索。 | 沙箱、测试用户、只读白名单、Policy、Trace、Governance Evaluation、审批/回滚齐备，另立任务并人批；动态组合仍按 Phase 3+ 评估。 | 蓝图 §2.3 L91-L104、§6.5 L1116-L1139、§6.10 L1268-L1327 |
| 自动 Capability/Skill 生成、完整 Skill CI/CD、自动发布 | P2 只到基础候选池；完整受控进化属于 Phase 4，候选不得自动上线。 | Phase 4 治理方案、测试/评级/审批/灰度/回滚全部就绪。 | 蓝图 §10.3 L2207-L2253、§13 L2736-L2751 |
| RPA、Local Worker、IoT/视频控制主链 | 属于 Phase 3 执行织物增强；物理控制风险高。 | Phase 3 独立信任模型、设备/Worker 协议与审批机制获批。 | 蓝图 §8.3-§8.5 L1829-L1900、§13 L2719-L2733 |
| 企业级 Keycloak / LDAP / SSO 全量接入 | 蓝图排在 Phase 3；P2 只需一个不可自报角色的可信试点入口。 | 雨爷决定把企业 IAM 提前，且完成独立安全/信任边界设计。 | 蓝图 §12.1.5 L2538-L2549、§13 L2728 |
| OAuth 自动续签、定期健康检查、批量失效通知和完整轮换 | 蓝图明确为 Phase 3；P2 只做基础验证和状态总览。 | Phase 3 凭证生命周期任务。 | 蓝图 §7.4.7 L1671-L1677、§13 L2732 |
| 复杂并行 DAG、跨天长事务、外部 Workflow 引擎、LLM 改写 Workflow | 真实试点不要求扩大 Workflow 语义，提前做会产生半成品可靠性边界。 | 出现明确长流程/恢复需求，另立架构与可靠性任务。 | 蓝图 §4.3.2 L435-L454、§4.3.3 L457-L468 |
| PydanticAI / 新编排框架默认引入 | **已拍板**：维持 raw SDK 默认，不投入 PydanticAI 内网复验；它不是 P2 目标的必要条件，既有 Spike 结论为 failed with caveat。 | 出现具体需求，且雨爷再次确认。 | 蓝图 §6.11 L1368-L1374、§13 L2716；`ADR-P0-SPIKE-007-pydanticai-qwen-vllm.md` L91-L103、L194-L202 |
| `P2-CONFIRM-RESUME-001` 主动实施 | **已拍板**：维持自触发，本阶段不主动做；当前仍是安全不变量受守卫的功能欠债。 | 出现非 Workflow 高风险 `action/query` 时按既有条件自触发。 | `PHASE1_SPEC.md` S-B5.5 L243；`TASK_INDEX.md` §5.1 L109-L118 |
| Temporal/Celery/Milvus/OpenSearch/Next.js/原生 App 等平台升级 | P2 没有已证实规模触发条件，升级不会直接闭合部门试点。 | 对应规模、可靠性、门户或移动需求出现并通过 ADR。 | 蓝图 §12.2 L2552-L2603、§12.3 L2604-L2622、§13 L2719-L2767 |

### BLOCKED（依赖外部输入，不排期）

> **到位登记（截至 2026-08-03）**：内网 vLLM **URL 直连、无密钥**（P0 已测）；OA 登录、每用户复用自身 OA Session 的凭证模型、AES-256-GCM 存储/读取、晚解密与撤销/重置均已落地，不使用共享服务账号、不存密码、不静默重登。企业 key custody、Vault/KMS/OS secret 方案与轮换要求仍是独立外部输入；真实 OA 现场浏览器 smoke 尚未执行。下表 ✅ 只表示对应已到位部分，剩余项继续按「只解除不猜测」执行。

| 阻塞项 | 必需外部输入 | 状态 | 来源 |
|---|---|---|---|
| 真实 LLM / 生产装配 | 内网 vLLM endpoint，以及 `max_model_len`、量化、timeout、`max_tokens`、`enable_thinking` 的实际值。 | ✅ 已到位 | `P1-PARAM-001.md` L3-L7、L24-L59 |
| 可信试点入口 | 雨爷选择最小试点认证方案，或 infra 提供现有 IAM/SSO 可接入条件；禁止继续把 `X-EternalAI-Roles` 当证明。 | ✅ OA 登录、EternalAI Session Cookie、认证 Principal 与受保护入口已落地；企业 IAM/SSO 是否提前仍开放 | `app/api/v1/auth.py`；`app/api/v1/admin.py`；蓝图 §12.1.5 L2538-L2549 |
| 首个/第二个真实 Adapter 与绑定 | 目标系统优先级、现场版本/API、测试环境、网络、账号/应用凭证、身份模式和允许用例。 | ◐ OA 代码纵切与凭证绑定已落地；真实 OA 现场 smoke 仍缺，第二个系统仍待选 | 蓝图 §15 L2870-L2907；`ADR-P0-SPIKE-005a-oa-api-auth.md` L123-L150、`ADR-P0-SPIKE-005b-u8-api-auth.md` L122-L148、`ADR-P0-SPIKE-005c-hikvision-ivms-api-auth.md` L124-L152 |
| 正式 Secret 管理 | 企业允许的 Vault/KMS/OS secret 方案、密钥责任边界与轮换要求；不填具体产品/参数。 | ✗ **仍缺**（阻塞 IDENTITY-CREDENTIAL 的 Secret 存储子块） | 蓝图 §7.4.3 L1540-L1549、§7.4.6 L1597-L1653、§7.4.7 L1677 |
| DB Gateway 真实纵切 | 业务负责人/DBA 批准的只读视图、字段/行级范围、测试数据与访问身份。 | 蓝图 §8.2 L1810-L1826、§8.7 L1926-L1947 |
| Memory 与低风险写入验收 | 经批准的知识语料/用户数据边界；以及具体写操作、测试环境、owner、回滚/补偿能力。 | 蓝图 §10.1-§10.2 L2148-L2205、§5.9 L878-L918 |

#### 欠债登记

| item | reason | blocked_by_task_id | activation_task_id | expiry_condition | evidence |
|---|---|---|---|---|---|
| 登录 → `/chat` → 真实 OA 只读查询的浏览器 smoke | 缺真实 OA 环境、只读 capability、身份绑定与运行配置 | 外部输入（雨爷内网采集） | `P2-OA-INTRANET-SMOKE-001` | 内网采集完成且 Live adapter 接通后立即执行 | 已完成真实浏览器传输链验证（`Secure; HttpOnly; SameSite=Lax; Path=/api/v1` 在 `http://localhost` 实际被 Chromium 接收；Vite 代理未做路径重写；CSRF 头恰好一次；Runtime POST 200）+ focused 45/45 + 前端全量 81/81；真实 HAR（登录成功 / 登录失败 / 系统消息）已由雨爷从内网导出并脱敏，采集步骤已完成；剩余必须在内网做的只有 Live 指纹漂移比对与真实 `/chat` 端到端 |

当前 `tests/contract_packs/oa/ecology9-pending-workflows-v1/profile.json` 的 `source_kind` 为 `"synthetic"`；因此 Replay/Contract 与代码纵切不能替代真实 OA 现场验收，OA 只读纵切尚不能据此宣告完成。

- **仓库卫生清理待办**：`P2-WORKTREE-AUDIT-001` 在 `_scratch/P2-WORKTREE-AUDIT-001_清单.md` 记录了 66 个 worktree、72 个本地分支和 88 个 Phase 1 遗留 scratch 文件；其中 B 类 5 个 worktree 有未提交改动或未合入提交，包含 `P1-GOV-SYNC-001` 的 18 个已暂存文件。删除属于红线，本项待雨爷看过清单并逐项批准后激活；本棒不做任何清理。

## 3. 任务 DAG

> Q 档按 `ROLE_POLICY.md` §Q0-Q3 L30-L41。task_id 是执行路标：BLOCKED 解除且上游 `depends_on` 满足即可开 lane，无需单独审批本计划。

| task_id | 一句话交付 | depends_on | 风险档 | BLOCKED |
|---|---|---|---|---|
| `P2-PILOT-FOUNDATION-001` | 真实 LLM + 可信试点身份 + 生产 composition 让一个既有低风险请求可启动、可审计。 | `P2-TRACE-PERSIST-001`（已完成） | Q3 | ✅ 已落地（merge `51af461e`） |
| `P2-IDENTITY-CREDENTIAL-001` | OA 的绑定、正式 Secret、基础凭证验证与 Gateway 注入/阻断形成纵切。 | `P2-PILOT-FOUNDATION-001` | Q3 | 部分：OA 现场接口/凭证已到位；**仍缺正式 Secret 方案**（可先做绑定/凭证验证/注入，Secret 存储子块等方案定后落地） |
| `P2-OA-READ-CONTRACT-001` | `oa.list_pending_workflows` 的 Replay Provider 接缝、固定能力白名单、版本化 Contract Pack 与离线脱敏工具。 | `P2-PILOT-FOUNDATION-001` | Q3 | ✅ 已落地（merge `89cd16e3`；Replay/Contract 棒未连内网、未读凭证） |
| `P2-READ-ADAPTER-001` | 在已冻结的 OA Replay/Contract 接缝上补 Live HTTP、凭证读取、最小 IdentityMapping、Live 指纹漂移比较并闭合 Gateway→Adapter→Evaluator→Trace→Response；真实 OA 现场 smoke 另记欠债。 | `P2-OA-READ-CONTRACT-001` | Q3 | ✅ 代码纵切已落地（merge `f9526a4`） |
| `P2-FE-API-CLIENTS-001` | 固化 Auth / Runtime / Admin-Trace OpenAPI 与 Orval 客户端，并以真实重导出、再生成验证无漂移；不改页面、mutator 或后端行为。 | `P2-READ-ADAPTER-001` | Q2 | ✅ 已落地（merge `e60b388`） |
| `P2-ADMIN-CSRF-001` | Cookie 认证的非安全方法要求合法 `Origin` + 固定自定义头，缺失、重复或错误一律 403；含动态路由枚举守卫防止新增路由漏接线。 | `P2-AUTH-001`、`P2-IDENTITY-CREDENTIAL-001` | Q3 | ✅ 已落地（merge `daf90f263352a14cfeb9d500b30558e7fb6ec046`） |
| `P2-RUNTIME-RESPONSE-CONTRACT-001` | 把既有 `ResponseEnvelope` 声明进 Runtime OpenAPI 并重新生成客户端；响应体逐字节不变。 | `P2-FE-API-CLIENTS-001`、`P2-ADMIN-CSRF-001` | Q2 | ✅ 已落地（merge `83e6ec82729a045a6d3c77039dbb952fe9bb03ff`） |
| `P2-PILOT-ENTRY-FE-001` | 接通登录页、EternalAI Session Cookie、统一 fail-closed 401 重认证与受保护路由，并停发 `X-EternalAI-Roles`；后端零改。 | `P2-FE-API-CLIENTS-001`、`P2-AUTH-001` | Q3 | ✅ 已落地（merge `d3a536e91001f6f008d4dbb1b8ec321988b70b66`） |
| `P2-CHAT-ENTRY-FE-001` | 受保护 `/chat` 普通文本对话入口，消费 Runtime Orval client 与既有认证会话；`loginNavigation` 白名单新增 `/chat` 精确匹配；不实现 SDUI 渲染器或结构化 Action。 | `P2-PILOT-ENTRY-FE-001`、`P2-RUNTIME-RESPONSE-CONTRACT-001` | Q2 | ✅ 已落地（merge `092a9095cbe4b572a8987707d2ab098887bcd123`） |
| `P2-BE-SMALL-DEBT-001` | OA adapter 兜底异常补可观测日志，只记异常类型名、`capability_id`、内部阶段与固定分类，`exc_info=None`；对外 `adapter_error` 行为和 Trace 序列逐项不变，含敏感值 canary 测试；`.env.example` 补 OA 读适配三项占位配置，既有 `CSRF_ALLOWED_ORIGINS` 不重复添加。 | `P2-READ-ADAPTER-001` | Q1 | ✅ 已落地（merge `3a901c5bcde8b9b704d70d889c3f6fc165edaa6d`） |
| `P2-FE-TEST-FLAKE-001` | 关闭 Vitest 文件级并发，消除 4 核环境下 Ant 组件测试与 OpenAPI 生成子进程争抢调度造成的 5s 超时 flake（实测失败率 35% → 0%），保持 81 个测试，并把 `App.test.tsx` 负向文案收窄为精确标题断言；无生产代码改动。 | `P2-CHAT-ENTRY-FE-001` | Q1 | ✅ 已落地（merge `6c47f06f3cae5af43efdbeb79a0d2bfe68f5517b`） |
| `P2-DB-GATEWAY-001` | 一个获批只读视图的注册查询能力完成 Policy、限行、脱敏、审计纵切。 | `P2-IDENTITY-CREDENTIAL-001` | Q3 | 是：DBA/业务批准视图 |
| `P2-PILOT-OPS-001` | 交付绑定管理/映射导入、审计看板和最小反馈统计的试点运营面。 | `P2-READ-ADAPTER-001` | Q3 | 否（前置解除后） |
| `P2-MEMORY-001` | User Profile 与增强 Semantic Memory 在用户/部门 scope 内可用且不串数据。 | `P2-PILOT-FOUNDATION-001` | Q3 | 是：数据边界/语料 |
| `P2-SKILL-CANDIDATE-001` | 基础 Skill 候选可登记、审查、拒绝，且不能自动发布或执行。 | `P2-PILOT-OPS-001` | Q3 | 是：候选来源语义待拍板 |
| `P2-GOLDEN-001` | 冻结 P2 新 Golden，覆盖真实只读、绑定、DB、隔离、审计与反馈负向边界。 | `P2-READ-ADAPTER-001`、`P2-DB-GATEWAY-001`、`P2-PILOT-OPS-001`、`P2-MEMORY-001`、`P2-SKILL-CANDIDATE-001` | Q3 | 否（需显式 fixture 人批） |
| `P2-LOW-RISK-WRITE-001` | 一个获批低风险写操作完成幂等、预览、确认、补偿、评测与审计。 | `P2-GOLDEN-001`；若命中自触发条件再依赖 `P2-CONFIRM-RESUME-001` | Q3 | 是：写用例/沙箱/授权 |

主链：`FOUNDATION → OA_READ_CONTRACT → READ_ADAPTER → PILOT_OPS → GOLDEN → LOW_RISK_WRITE`；`IDENTITY_CREDENTIAL` 的其余绑定/凭证治理与只读两棒并行推进，`DB_GATEWAY`、`MEMORY` 在依赖和外部输入满足后并入 Golden，`SKILL_CANDIDATE` 从真实试点信号后启动，避免先造空池。

当前已落地链推进至 `CHAT_ENTRY_FE`；下一棒为 `P2-OA-SYSMSG-PACK-001` 与 `P2-OA-LOGIN-PARITY-001`（可并行），之后才是 `P2-OA-INTRANET-SMOKE-001`。原主链中 `IDENTITY_CREDENTIAL`、`DB_GATEWAY`、`PILOT_OPS` 与 `GOLDEN` 的范围/依赖偏差属于待裁决项，本次未改原规划。

> **顺序调整（2026-07-29）**：OA 只读拆成两棒。`OA_READ_CONTRACT` 先在气隙环境冻结 Replay/Live 接缝、白名单、Contract Pack 与脱敏工具；`READ_ADAPTER` 再补 Live HTTP、凭证读取、最小 IdentityMapping、现场 smoke 接线和完整运行时闭环。两棒均不等待共享 Secret 方案：已决凭证模型是每用户复用自身 OA Session，不用共享服务账号、不存密码、不静默重登。`IDENTITY_CREDENTIAL` 的其余治理与之并行。真实 OA 现场浏览器 smoke 仍由 `P2-OA-INTRANET-SMOKE-001` 单独激活，不能由 synthetic Contract Pack 或传输链 smoke 代替。

## 4. 决策与开放问题

1. **已决（2026-07-24 雨爷拍板）— A1：是。** 生产 composition、真实 LLM 和最小可信试点入口作为 P2 首个硬前置（`P2-PILOT-FOUNDATION-001`）。
2. **已决（2026-07-29 雨爷拍板）— 首个真实系统与只读用例：OA `oa.list_pending_workflows`。** Replay/Contract、Live/凭证/IdentityMapping 代码纵切、OpenAPI/Orval、CSRF、Runtime 响应契约、可信登录入口与 `/chat` 普通文本入口均已落地；`P2-CHAT-ENTRY-FE-001` merge = `092a9095cbe4b572a8987707d2ab098887bcd123`。当前 Contract Pack 的 `source_kind` 为 `"synthetic"`，真实 OA 现场浏览器 smoke 仍是欠债；下一棒 = `P2-OA-SYSMSG-PACK-001` 与 `P2-OA-LOGIN-PARITY-001`（可并行），之后才是 `P2-OA-INTRANET-SMOKE-001`；第二个 Adapter 的启动条件仍开放。
3. **认证路线**：OA 登录、EternalAI Session Cookie 与认证 Principal 已落地；仅“是否把企业 IAM/SSO 从 Phase 3 提前”仍开放。
4. **DB Gateway**：Phase 2 路线图写了基础 DB Gateway，但蓝图又限定“仅无 API/报表需求时”；是否已有获批报表用例？
5. **Skill 候选池**：只允许管理员/用户手工登记，还是允许脱敏 Trace 产生“候选提议”？后者不得变成自动 Skill 生成。
6. **已决（2026-07-24 雨爷拍板）— 框架：维持 raw SDK 默认。** 不投入 PydanticAI 内网复验；除非出现具体需求且雨爷再次确认。
7. **已决（2026-07-24 雨爷拍板）— confirm 欠债：维持自触发。** `P2-CONFIRM-RESUME-001` 本阶段不主动做。
8. **低风险写入**：具体选哪个动作、何种确认/审批、是否有沙箱与可验证补偿？

## 5. P2 不做什么

- 不把“能连到接口”当试点完成；缺可信身份、正式凭证、审计、Evaluator 或负向 Golden 时仍是半成品。（蓝图 §3.2 L176-L186、§7.0 L1383-L1404、§13 L2701-L2717）
- 不让 Runtime、LLM、UI、Workflow 或 Skill 绕过 Capability Gateway；不让 DB Gateway 自由查生产表。（蓝图 §7.3 L1436-L1502、§14.2.1-§14.2.2 L2787-L2806）
- 不把密码、token、Cookie、session 或敏感原文写入 LLM、Memory、Skill、Trace、日志、fixture 或报告。（蓝图 §7.4.3 L1540-L1549、§14.2.3 L2808-L2817）
- 不因未绑定/凭证失效自动切换服务账号；不让自报角色获得 Admin 权限。（蓝图 §9.1.1 L2021-L2062）
- 不在外部输入缺失时编 endpoint、infra 数值、凭证模式或系统能力；BLOCKED 项只解除，不猜测。（`P1-PARAM-001.md` L52-L59、L73-L77；蓝图 §15 L2870-L2907）
