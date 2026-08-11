# Phase 2 总目标、范围与任务 DAG（Lean Plan）

> 状态：**生效（轻量地图）**。单人开发 + 强模型（Opus 5 / GPT-5.6）下不再走「总体计划 → 每任务 task_id 提示词 → 每任务 SPEC」那套 ceremony：本文件是范围边界 + DAG + BLOCKED 护栏的**地图**，不是逐任务合同；task_id 是路标而非正式 lane 依据，无需「Opus 实审 + 拍板生效」门即可据此开 lane。
>
> 仍然硬约束（与流程无关，不可省）：① 红线动作先问；② 密钥不进代码/日志/Trace/fixture；③ 改完跑验证（基线 **1950 passed / 0 skipped / 0 failed**；Golden Gate **27/27 passed / 0 skipped / 0 failed**；`tests/architecture/` **33 passed**，由 `P2-SMOKE-E2E-CHAIN-001` 实跑验证）；④ **BLOCKED 外部输入未到时不启动、只解除不猜测**；⑤ 命中信任边界（真实认证/凭证/外部 API）的任务仍走 Opus 评审（见 `opus-review-scope-rule`），减的是文档 ceremony，不是信任边界评审。

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

> **到位登记（截至 2026-08-07）**：内网 vLLM **URL 直连、无密钥**（P0 已测）；OA 登录、每用户复用自身 OA Session 的凭证模型、AES-256-GCM 存储/读取、晚解密与撤销/重置均已落地，不使用共享服务账号、不存密码、不静默重登。2026-08-07 已取得待办 Adapter 所需现场 HAR 与页面输入，不必为该棒再次进内网；真实 OA Live 指纹漂移比对仍须再次进内网。企业 key custody、Vault/KMS/OS secret 方案与轮换要求仍是独立外部输入。下表 ✅ 只表示对应已到位部分，剩余项继续按「只解除不猜测」执行。

| 阻塞项 | 必需外部输入 | 状态 | 来源 |
|---|---|---|---|
| 真实 LLM / 生产装配 | 内网 vLLM endpoint，以及 `max_model_len`、量化、timeout、`max_tokens`、`enable_thinking` 的实际值。 | ✅ 已到位 | `P1-PARAM-001.md` L3-L7、L24-L59 |
| 可信试点入口 | 雨爷选择最小试点认证方案，或 infra 提供现有 IAM/SSO 可接入条件；禁止继续把 `X-EternalAI-Roles` 当证明。 | ✅ OA 登录、EternalAI Session Cookie、认证 Principal 与受保护入口已落地；企业 IAM/SSO 是否提前仍开放 | `app/api/v1/auth.py`；`app/api/v1/admin.py`；蓝图 §12.1.5 L2538-L2549 |
| 首个/第二个真实 Adapter 与绑定 | 目标系统优先级、现场版本/API、测试环境、网络、账号/应用凭证、身份模式和允许用例。 | ◐ OA 代码纵切、凭证绑定与待办事宜数据源原地替换已落地；真实 OA Live 指纹漂移比对仍缺，第二个系统仍待选 | 蓝图 §15 L2870-L2907；机器本地非仓库输入 `todolist1.har`、`home.png`、`wtd.png` 的存在性由 `P2-GOV-SYNC-009` PR body `## Scope` 永久记录；`tests/contract_packs/oa/ecology9-pending-workflows-v3/profile.json`；`ADR-P0-SPIKE-005a-oa-api-auth.md` L123-L150、`ADR-P0-SPIKE-005b-u8-api-auth.md` L122-L148、`ADR-P0-SPIKE-005c-hikvision-ivms-api-auth.md` L124-L152 |
| 正式 Secret 管理 | 企业允许的 Vault/KMS/OS secret 方案、密钥责任边界与轮换要求；不填具体产品/参数。 | ✗ **仍缺**（阻塞 IDENTITY-CREDENTIAL 的 Secret 存储子块） | 蓝图 §7.4.3 L1540-L1549、§7.4.6 L1597-L1653、§7.4.7 L1677 |
| DB Gateway 真实纵切 | 业务负责人/DBA 批准的只读视图、字段/行级范围、测试数据与访问身份。 | 蓝图 §8.2 L1810-L1826、§8.7 L1926-L1947 |
| Memory 与低风险写入验收 | 经批准的知识语料/用户数据边界；以及具体写操作、测试环境、owner、回滚/补偿能力。 | 蓝图 §10.1-§10.2 L2148-L2205、§5.9 L878-L918 |

#### 欠债登记

> PR #75（`P2-REGISTRY-BOOTSTRAP-001`）在 `## 本棒新增欠债` 中原文声明“Capability Registry 无确定性部署 bootstrap 已结清”“新增欠债：无”。本表因此只更新既有 bootstrap 行，不为 #75 新增欠债行。

| item | reason | blocked_by_task_id | activation_task_id | expiry_condition | evidence |
|---|---|---|---|---|---|
| PR #73 合并时刻任务记录不合规（流程缺陷，非内容造假） | PR #73 合并时 body 的五个欠债字段名出现次数为 0，且只登记 2 条欠债；完整五字段与第 3 条欠债在合并后 1 小时 37 分才补齐。成因是启动侧把 required checks 全绿写成可合并授权，监理侧又只以前置“执行方已报收口”为条件，导致监理门落在合并之后 | 已解除；记录时点不可追溯修复 | `P2-GOV-SYNC-010` | `CLAUDE.md` 与 `AGENTS.md` 固化“合并前完整三段+欠债五字段、checks 绿不等于合并授权、监理 PASS/显式自合并授权”规则；后继棒在合并前满足 | PR #73 mergedAt `2026-08-10T15:00:29Z`、完整后补 updatedAt `2026-08-10T16:37:51Z`；#74 / #75 已在合并前写好欠债段，证明修正流程已生效 |
| PR #73 D1：fixture 期望块未纳入凭证扫描 | 凭证 forbidden 检查只扫实际 ResponseEnvelope 与完整的实际 Trace step（包括但不限于 attributes），不扫 fixture 自身的 `expected` / `then_*` 期望块。若有人把真实凭证写进 fixture 期望值，判卷器不会红。 | 无（不被阻塞，属未排期） | 待 `P2-GOV-SYNC-010` 排入 DAG | 判卷器扫描面覆盖 fixture `expected` 块后关闭 | `scripts/golden_task_assertions.py` 的 `assert_forbidden_absent` / `_assert_no_credential_values` 调用点；`scripts/golden_task_evaluator.py` 将实际 observation 与 fixture 期望分开传入；`tests/golden_tasks/fixtures/` 下 27 个 fixture 均无扫描 fixture 期望块自身的守卫 |
| PR #73 D2：AssertionError reason 回显命中字符串 | `_assert_no_credential_values` 以 `AssertionError(f"...: {value!r}")` 抛出，把命中原串写进 reason，最终由 `scripts/run_golden_tasks.py` 打进报告与 CI 输出。本棒新增 `sessionkey` / `dataKey` 后这两类值也进入回显面。 | 无 | 待 `P2-GOV-SYNC-010` | 错误消息改为只报字段路径与规则名、不回显值后关闭 | `scripts/golden_task_assertions.py` 的 `_assert_no_credential_values`；`scripts/golden_task_evaluator.py` 将 reason 写入 summary；`scripts/run_golden_tasks.py` 无条件输出 summary，CI 直接运行 `--gate`；本棒合同明令「不扩域改错误消息」故未处理，今日实际暴露为零 |
| PR #73 D3：引号包裹的键名对全部凭证 pattern 逃逸（既有共性弱点，非本棒引入） | 当 JSON 已序列化成字符串时，`{"dataKey":"v"}`、`{"password":"v"}`、`{"access_token":"v"}` 均逃逸；若输入仍是结构化 mapping，则 walker 会生成无引号的 `key: value` 并被检出。实测 password / access_token 的既有 pattern 同样逃逸，证明这是名称锚定方案对序列化 JSON 字符串的共性弱点，不是本棒引入的回归。 | 无 | 待 `P2-GOV-SYNC-010` | 改为值锚定缺席断言（脱敏器已在用的做法）后关闭 | `scripts/golden_task_assertions.py` 的 `_CREDENTIAL_PATTERNS`；核查期实测上述三种样本对当前 7 条 pattern 全部零命中且 `_assert_no_credential_values` 不抛错，其中 password / access_token 分别对应既有 pattern #6 / #5 |
| PR #74 D1：本棒 A 类治理状态尚未同步（由本棒结清） | 并行规则禁止本棒修改三份共享治理文档，因此本棒 task_id、最终实跑基线和本棒新增欠债尚未进入正式治理状态。 | `P2-GOV-SYNC-010` | `P2-GOV-SYNC-010` | `P2-GOV-SYNC-010` 将本棒 task_id、最终实跑基线与本棒新增欠债写入三份共享治理文档并合并。 | `AGENTS.md` 的 `Current Phase 2 delivery state`；`CLAUDE.md` 的 Phase 2 delivery state；`docs/phase2/PHASE2_PLAN.md` 的 Phase 2 状态段。 |
| PR #74 D2：既有 Admin OpenAPI 路径仍声明已被传输层剥离的角色 Header | 本棒仅按真实 FastAPI 路由修正两条新增 mutation；既有 curated Admin 操作仍引用 `RoleClaims`，但共享 mutator 会剥离该 Header，形成既有契约漂移。全局清理不属于本棒授权范围。 | 无（未排期） | 待 `P2-GOV-SYNC-010` 排入 DAG | 独立后继棒将所有既有 Admin OpenAPI 认证参数与可信 Session 契约对齐、重生成全部受影响 client，并通过字节漂移守卫与 CI。 | `web/openapi/admin.openapi.json` 的 `components.parameters.RoleClaims` 及既有 operations；`web/src/api/mutator.ts` 的 `customInstance`。 |
| Golden 凭证检测未覆盖 `session_key` / `data_key` 下划线变体 | #73 新增的数据源只覆盖 `sessionkey` / `datakey`，下划线变体仍可能绕过名字锚定检测 | 无（未排期） | 待独立 Golden 凭证检测后继棒 | 结构化 mapping 与序列化文本中的 `session_key` / `data_key` 均被 fail-closed 检出，且既有 pattern 与误报守卫不弱化 | PR #73 `## Scope` 与 `## 本棒新增欠债`；`P2-GOV-SYNC-010` 启动合同 B4-9 |
| PR #75 body Markdown 反引号转义损坏 | PR #75 末次编辑把全文反引号改成“反斜杠后接反引号”的字面文本，导致 GitHub inline code 与 PowerShell 围栏失效；本棒禁止回改 PR body | 需独立外部 PR 编辑动作；本棒无授权修改 #75 | 待单独处理 | GitHub 上 #75 的 inline code 与 PowerShell 围栏恢复正常且正文语义不变，或仓库 owner 明确裁决永久保留 | PR #75 当前 raw body；`P2-GOV-SYNC-010` 只读核对该字面组合共 70 处 |
| 两处 ADR 与代码冲突（待裁决） | 当前已知有两处 ADR 结论与代码事实冲突，但本棒材料不足以唯一裁决应改代码还是另立 superseding ADR；本棒禁止起草 ADR、改代码或扩大白名单 | 两处冲突的权威取舍与独立 Scope | 待分配 ADR/code alignment task_id | 两处冲突分别由现役代码或获批 superseding ADR 唯一裁决，旧结论明确退役且引用链无歧义 | `P2-GOV-SYNC-010` 启动合同 B4-8；当前台账此前无精确五字段项 |
| Adapter 超时 / 重试边界缺失（待裁决） | Adapter 尚无统一、可执行的有界 timeout / retry 合同；在真实协议的错误分类、幂等性和重试安全未确认前不能猜测默认值 | 真实协议的可重试分类、幂等性与现场参数 | 待分配 Adapter resilience task_id | 每个适用 Adapter 具备有界超时、只对已确认可重试错误重试、耗尽后保留稳定错误码与 Trace，并有永久回归测试 | `P2-GOV-SYNC-010` 启动合同 B4-8；当前台账此前无精确五字段项 |
| `WorkflowEnginePort` 接缝缺失（待裁决） | 当前缺少已登记的 `WorkflowEnginePort` 生产接缝，直接补实现会涉及 `app/ports/` 契约与全部实现/测试同步 | 最小 Port 合同与独立受监理 Scope | 待分配 WorkflowEnginePort seam task_id | 最小 `WorkflowEnginePort` 契约、生产实现、composition 与测试同棒落地，且不允许 Runtime/Workflow 绕过 Gateway / Policy / Trace | `P2-GOV-SYNC-010` 启动合同 B4-8；当前台账此前无精确五字段项 |
| 降级审计面板是否启动（待裁决） | `P2-PILOT-OPS-B-001` 可在零 schema 变更下提供降级审计面板，但是否先于其他候选启动尚无唯一产品裁决 | 雨爷对 `P2-PILOT-OPS-B-001` 的优先级裁决 | `P2-PILOT-OPS-B-001`（待裁决） | 明确启动并交付只读降级审计面板，或明确不启动并记录替代观察面 | `P2-GOV-SYNC-010` 启动合同 B4-8 / B5 候选集 |
| `FROZEN_GT_IDS` 与 Golden 运行集合拆分（待裁决） | 当前 Golden Gate 共 27 题，而 `FROZEN_GT_IDS` 与后继正向题的运行集合存在拆分；题外 lifecycle manifest 尚未拍板，不能用原地改写正向 fixture 解决 | 雨爷对 Golden 题外 lifecycle manifest / 等价载体的裁决 | 棒 B（待拍板） | 冻结集合、active 运行集合与题外生命周期载体的权威关系唯一明确，同版本包全量回放通过且既有冻结题不被原地改写 | 决定四；本表“Golden 题外生命周期清单无实现载体”；`P2-GOV-SYNC-010` 启动合同 B4-8 |
| `.env.smoke` 与当前 live 合同脱节 | 共享文件仍缺 9 个 live 硬必填键、含 3 个废键，且 pending `CONTRACT_PACK_DIR` 仍指向 v2；本棒只落代码侧 fail-closed 检测与显式修复能力，启动合同禁止实际改该文件 | 修改 `.env` / `.env.smoke` 属红线，仍须动作级专项授权；本实现棒不得代替用户执行修复 | 待 GOV-SYNC 分配修复动作 task_id（不得自行造号） | 获得专项授权后：缺失 9 键由 `prepare` 默认只追加、不覆盖且无需 flag；删除 3 个废键及将精确 v2→v3 仅可显式运行 `prepare --repair-smoke-env`；无 flag 遇废键或 stale pack 必须 fail-closed、零写入；既有凭证保持不变；随后 `prepare` / `verify` 均通过代码侧一致性检查 | `scripts/smoke/environment.py` 的三类稳定错误码与原子 repair；`tests/scripts/test_smoke_runner.py` 的缺键/废键/stale/凭证保持/回滚证据；本棒未修改共享 `.env.smoke` |
| `P2-OA-INTRANET-SMOKE-001` 现场验收（部分完成） | 2026-08-07 的待办 HAR 已支持直接发布 v3 脱敏 Contract Pack 和实现三步协议；本棒证明了 replay 与 live 共用的代码路径，但离线结构证据仍不等于 2 个 OA capability 的真实 Live 指纹漂移比对，也不自动补齐 Cookie 冷启动与真实 `/chat` 端到端证据 | `P2-OA-INTRANET-SMOKE-001` 剩余现场窗口 | `P2-OA-INTRANET-SMOKE-001` | 真实 smoke 判定最小必需请求头、以全新 Cookie 状态证明冷启动链路，并完成 2 个 OA capability 的真实 Live 指纹漂移比对与真实 `/chat` 端到端 | `tests/contract_packs/oa/ecology9-pending-workflows-v3/profile.json` 明示 `source_kind=sanitized_capture`；`scripts/smoke/full_chain.py::run_full_chain_check` 的 live 分支尚未在内网执行；整数 `userid` 缺陷已修复 |
| `P2-SMOKE-E2E-CHAIN-001` 全链 smoke 缺失（代码侧已结项） | 既有 live smoke 直接构造 Provider 并绕过 Runtime / Gateway / Policy / Evaluator / Trace；本棒已闭合代码绕过链，但真实 OA 现场证据仍由上一行独立承载 | 已解除；真实 Live 验收不在本棒本地能力范围 | `P2-SMOKE-E2E-CHAIN-001` | replay 通过真实生产 composition、受保护登录与 Runtime HTTP 入口覆盖两个 OA capability，11 类 Trace 齐全；live 复用同一核心且任何未接线、子进程异常或缺事件均 fail-closed；Provider 证据不与全链活数据做一致性断言 | `scripts/smoke/full_chain.py::run_full_chain_check`；`scripts/smoke/runner.py::_run_full_chain_subprocess`；`tests/runtime/test_pilot_foundation_e2e.py::test_replay_oa_provider_runs_through_complete_runtime_chain`；`tests/scripts/test_smoke_full_chain.py` |
| Phase 2 后继 DAG 指针（待裁决） | `P2-SMOKE-E2E-CHAIN-001` 之后存在多个非唯一候选；实现棒按治理规则不得自行挑选企业密钥、OPS、仓库清理或 Preselector 等后继 | GOV-SYNC 的 Class B DAG 裁决 | 待 GOV-SYNC 分配 task_id（不得自行造号） | GOV-SYNC 选出唯一后继并在 `AGENTS.md`、`CLAUDE.md`、本文件逐字同步；裁决前指针保持空 | `AGENTS.md` / `CLAUDE.md` 的治理同步规则；`P2-SMOKE-E2E-CHAIN-001` 启动合同 §0、§3.2 与本文件候选/欠债表 |
| Python 3.16 移除旧式 Windows asyncio event-loop policy 的兼容欠债 | 全量与定向测试持续产生 `WindowsSelectorEventLoopPolicy` / `set_event_loop_policy` 弃用 warning；本棒修改到相关 E2E 文件但修复属于兼容性重构，不是本棒缺陷路径 | Python 3.16 兼容方案与独立测试基础设施 Scope | 待 GOV-SYNC 排期的 Python 3.16 compatibility lane | Windows 测试改用仍受支持的事件循环构造方式，相关 DB/E2E 测试在目标 Python 版本无这些弃用 warning，且既有行为不弱化 | `uv run pytest` 收口原始 warning；`tests/runtime/test_pilot_foundation_e2e.py:50-51` 及其他 PostgreSQL 测试同类设置 |
| 内网现场操作卡尚未同步额外只读 OA 轮次 | 新 `verify` 为全链结果和 Provider 协议证据各做独立读取，因此相较旧 Provider-only 流程会额外执行一轮只读 OA 检查；不能以两次活数据相等作断言 | 主窗口更新机器本地内网现场操作卡；该文件不进本棒仓库 Scope | 外部操作，无仓库 task_id | 内网现场操作卡明确额外只读轮次、两条证据独立及禁止数据相等断言，现场人员按新调用量级执行 | `scripts/smoke/runner.py::_build_report` 的“现场调用变化/证据边界”；雨爷在本棒追加约束中要求收口报告明确披露 |
| `CapabilityGateway.assert_production_wiring` 当前固定要求 `adapters["oa"]` | 当前生产纵切只有 OA，固定键能忠实堵住本棒静默漏接线；未来若引入非 OA 生产组合，守卫需由 composition 提供必需 adapter 集合，而不能继续把 OA 当通用不变量 | 第二个真实生产 Adapter 的已决 composition 设计 | 对应第二 Adapter composition lane（未排期） | 守卫按已决生产组合验证明确的必需 adapter key 集合，同时继续强制 Registry / Identity / Policy / Trace 非空，并保留破坏式回归测试 | `app/infra/gateway/capability_gateway.py::CapabilityGateway.assert_production_wiring`；`tests/runtime/test_runtime_composition.py::test_production_composition_rejects_incomplete_gateway_wiring` |
| Phase 2 「下一棒」指针裁决（已结项） | Provider 级 smoke 是内网前置证据缺口，且 `.env.smoke` 脱节可在同一 E2E 收口面处理；其紧迫性高于其余非唯一候选 | 已解除 | `P2-GOV-SYNC-010` | 三份治理文档把唯一后继逐字同步为 `P2-SMOKE-E2E-CHAIN-001` | 本文件 §4 第 14 项；`AGENTS.md` / `CLAUDE.md` 当前交付状态段 |
| OA 长会话心跳保活缺失 | OA 登录技术对接文档步骤 6 要求“启动心跳保活”，当前 `app/` 没有心跳实现，长会话可能因 OA Session 超时而中断 | 尚未排期 | `P2-OA-SESSION-KEEPALIVE-001` | `app/` 实现有界、可停止且不泄漏凭证的 OA Session 心跳，并以长会话测试证明续期与失败行为 | `OA登录技术对接文档.md` §4.1、§4.2；本棒只登记，不实现 |
| `phase0/main` 直推可隐式绕过 required checks | `P2-AUTH-USERID-TYPE-001` 本地合并后普通直推主分支时，两个 required checks 尚未完成，GitHub 回显 `Bypassed rule violations`；执行者未传 bypass 参数，账号权限仍允许绕过 | 外部操作：雨爷修改 GitHub 分支保护（无 task_id） | 每次集成均由 boot rules 强制走 PR | `phase0/main` 打开 **Do not allow bypassing the above settings**，且后续集成均在 required checks 最终全绿后通过 PR 合并 | 违规推送的事后 CI run `30797244405` 最终成功，但事后变绿不追溯消除违规；PR #65 在两个 required checks 成功后合并，是正确样板 |
| 仓库卫生清理 | 当前盘点为 79 个 worktree、238 条分支（本地 85 + 远端 153）；部分 worktree 仍可能保有独有文件、未暂存修改或未合入 commit，删除均属红线 | 本地部分由已另开的 `P2-REPO-CLEANUP-001` 逐项处理；远端分支删除仍待雨爷单独授权 | `P2-REPO-CLEANUP-001`（仅本地部分） | 每个本地候选均经逐项复核与授权边界处置；远端 153 条分支只有在雨爷另行专项授权后才可删除 | `P2-GOV-SYNC-010` 启动合同 B4-5；本棒不删除 worktree、分支、scratch 或 Git 历史 |
| 四个架构决定 —— 雨爷 2026-08-03 已全部拍板 | 原四项架构欠债已解除，决定立即约束后续实现；决定四于 2026-08-09 完成诊断后修订措辞 | 已解除 | 决定立即生效；决定四措辞由 `P2-GOV-SYNC-009` 同步 | 三份治理文档保持同一决定四；后续改题、判卷与缺陷回归按该契约执行 | 1. 写操作凭证与问责：用用户自己的 OA 凭证执行，OA 审批记录上是用户本人；每次写操作必须人工确认，AI 拟好、用户点确认才执行，确认动作本身留痕；与 `P2-CONFIRM-RESUME-001` 方向一致。<br>2. 数据库不算“目标系统”：不进外部系统名册、不做 IdentityMapping、不加 `db` 枚举值，因此不需要 schema 变更；但 AI 对数据库的每次访问必须在 Trace 里可查。<br>3. 企业级密钥：责任人为运维；纯内网不设定期轮换，但后台必须能随时手动更新。<br>4. 负向、边界和安全拒绝用例的题面、预期、禁止项、分类及判卷契约冻结，修改需雨爷明确批准。所有既有正向题面同样不可原地改写，只能新增后继题并在题外生命周期清单中停止旧题运行。判卷契约或运行选择规则变更时，必须按同一版本包全量回放并明确披露影响。每修复一个真实缺陷，必须新增一条能在未修代码上失败、修复后通过、且走原缺陷路径的永久回归证据；缺陷属于 Golden Runtime 观察边界时才新增 Golden Task，否则放在最小且忠实的单元/集成/API/浏览器层。 |
| 企业密钥运行时管理面与出站地址治理 | 当前企业密钥只在环境变量中，更新一次需要重启；出站地址仍有硬编码投影，运行时管理面若不带地址 allowlist 会形成 SSRF 风险 | 尚未排期；需独立安全边界 Scope 与监理窗口 | 决定三已生效 | 后台支持不重启即可手动更新企业密钥；出站地址改为受治理配置并由 allowlist fail-closed 拒绝越界目标 | 雨爷 2026-08-03 决定三；硬编码投影位于 `app/config.py`、对应 `tests/.../test_config.py` 及 `CLAUDE.md` / `AGENTS.md`；本棒只登记，不改代码 |
| Golden 策略诊断与治理措辞修订（已结项） | `P2-GOLDEN-POLICY-001` 已完成只读诊断，旧行“尚未形成拆棒方案”的原因及 expiry 已消失 | 已解除 | `P2-GOV-SYNC-009` | 三份治理文档同步修订决定四；本棒 PR body 的 `## Scope` 永久记录三处修订理由，计划决策区登记两条否定结论 | `AGENTS.md:10`、`CLAUDE.md:10` 的逐字决定四；`P2-GOV-SYNC-009` PR body `## Scope` |
| `P2-CONFIRM-RESUME-001` 维持自触发 | 当前没有证据表明需要主动实现 confirm/resume；提前做会扩张未触发范围 | `P2-LOW-RISK-WRITE-001` 的实际用例与确认语义 | `P2-CONFIRM-RESUME-001`（仅命中自触发条件时） | 真实低风险写入用例触发该能力，或阶段决策明确其不再需要 | 2026-07-24 已决：本阶段不主动做；`P2-LOW-RISK-WRITE-001` 仅在命中自触发条件时依赖它 |
| Golden 题外生命周期清单无实现载体 | 既有正向 fixture 不得原地修改，但当前 runner 会运行所有 `GT-*.json`，无法表达“保留但停止运行” | 雨爷尚未拍板报告 §7 棒 B；manifest schema 属明确设计选择，且可能构成 Class B 新校验语义 | 棒 B（待拍板） | 建立题外 lifecycle manifest，Gate 只跑 active 集合，并完成同版本包全量回放；若不开棒 B，则须拍板其他实现载体 | `scripts/golden_task_evaluator.py:85`；`AGENTS.md:10`、`CLAUDE.md:10` 的逐字决定四 |
| 历史 OA Rehearse 比较对象错配（已结项） | 2026-08-05 欠债草案误判冻结 pack 缺字段；2026-08-06 查实根因是 `_run_rehearsal` 将 pack 式指纹与 live 式指纹错配。保留本行是为防止后续按旧前提重新登记 | 已解除 | `P2-SMOKE-AUTH-DIAG-001` | `_run_rehearsal` 已改为重新生成 pack 后做 pack↔pack 比较，并在 `1bf2ba6` 现场输入只读复算得到 `matches=True`、`added=0 removed=0 changed=0`、`sha_matches=True`；冻结 system-message pack 不重建。剩余 OA Live 指纹现场比对继续作为独立欠债保留 | `scripts/smoke/runner.py:395-419`；修复 merge `1bf2ba6c895fec4b847f2369f13f22879920000b`；`P2-GOV-SYNC-009` PR body `## Scope` |
| 历史 Smoke 报告 `smoke_result_20260804_144116.md` / `smoke_result_20260805_120405.md` 属于旧版 verify 伪绿 | pending 未归一化时旧 verify fail-open，报告中的成功不能证明真实 pending 能力或 `/chat` 成功；两份机器本地原报告不是仓库证据，本棒不改不删 | 无；要求保全机器本地原证据 | `P2-GOV-SYNC-009` 标注 | 后续由 fail-closed 的有效 smoke 取代其验收用途；两份机器本地原报告永久标记为无效证据且不改写 | `scripts/smoke/runner.py:2126-2143`；`tests/scripts/test_smoke_runner.py:623-656,1907-1953`；修复 merge `1bf2ba6c895fec4b847f2369f13f22879920000b` |
| `.github` PR body 三段式模板缺失 | 当前没有仓库级填写兜底；本棒 Scope 不含 `.github/`，不得中途扩域。模板只能兜底，`gh pr create --body` 可绕过 | 新的 `.github/` Scope lane | 尚未排期的 PR 治理加固 | `.github/pull_request_template.md` 提供三个固定标题并验证常规创建路径；文档明确它不是强制门禁 | `AGENTS.md:45`、`CLAUDE.md:45` 的 Phase 2 PR body 规则；基线 `1bf2ba6` 的 `.github/` 目录盘点无 `pull_request_template.md` |
| PR body 三段式缺少 CI 强制校验 | 文档和模板都不能阻止遗漏，CI 是唯一“忘了也会被拦下”的形态 | 新的 `.github/workflows` Scope；若设 required check，还需仓库 owner 按规则授权配置 | 尚未排期的 PR body CI gate | CI 对缺失三段的 PR fail-closed、对完整三段通过，并纳入 `phase0/main` required checks | `AGENTS.md:45`、`CLAUDE.md:45` 的 Phase 2 PR body 规则；基线 `1bf2ba6` 的 `.github/workflows/ci.yml:1-139` 包含 `pull_request` 触发，但无 PR body 三段校验 |
| `CLAUDE.md` 与 `AGENTS.md` 状态段结构改造 | 两份文件的状态段都用单一长段落罗列全部已落地棒，随棒数增长而不可读，并行 lane 同改该段必然冲突 | 无硬依赖；需独立治理结构 lane | 尚未排期的治理结构改造 lane | 两处状态段各自只保留当前基线数字 / 下一棒 `task_id` 的一行指针（不含 SHA），明细移入 `PHASE2_PLAN.md` 一棒一行的追加式表格并通过并行合并验证；**两处都落地后**才解除治理同步分流规则对并行 A 类同步的限制，只改一处不解除 | `CLAUDE.md:8`；`AGENTS.md:8`；本文件 §4 第 11 项；`P2-GOV-SYNC-009` PR body `## 本棒新增欠债` |
| Capability Preselector 整层缺失 | 生产代码把 active capability 按安全化 ID 字典序取前 8 条，没有规则/标签/Policy/相关性预筛；能力超过 8 条后尾部能力不可见 | 恢复链尚未排期；b3 依赖 b2 | b3：相关性 Top-K；b4：Embedding 增强 | 生产链按用户/组织/Policy 范围生成相关性 Top-K，且超限或低置信度显式处理，不再因字典序造成永久不可见 | `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md:1050-1052,1057,1064-1068,1074`；`app/knowledge/basic_knowledge.py:21,121-136`；`scripts/smoke/capabilities.py:24-27`；`scripts/smoke/runner.py:1609-1661` |
| Planner 注入内容未恢复蓝图摘要契约 | 当前只注入 capability ID/type/target/status 和输入参数键结构，不含短摘要、输出摘要、version、owner、risk；管理员描述对模型选能力不起作用 | **b1 硬前置**：自由文本 prompt-safe 校验 | b2 | 经 b1 收紧后，恢复有界短摘要、owner、risk、version 与必要 schema 摘要，并以负向测试证明不注入未经批准的原文或授权语义 | `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md:1057`；`app/knowledge/basic_knowledge.py:211-223`；`app/runtime/runtime.py:433-469`；`docs/phase1/tasks/P1-B5-002.md:22-24` |
| Capability 合同无条件按字典序截断 | `MAX_CAPABILITY_CONTRACTS = 8` 后无条件 break，不基于相关性或实际 Context Budget，且上限来源未记录 | b2 | b3 | 改为相关性 Top-K；超限、低置信度和未覆盖能力均有显式状态与回归测试 | `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md:1057,1064-1074`；`app/knowledge/basic_knowledge.py:21,127-136`；`tests/knowledge/test_basic_knowledge.py:200-221`；commit `cb6438abae8790be5f94e0a5dc8e2b4123b5253d` |
| Registry `output_schema` 未参与返回侧校验 | 该字段在生产 `app/` 只用于模型、持久化和 Admin 展示；Gateway 只校验入参，Registry 契约无法约束返回 payload | 无硬依赖，独立可并；尚未排期 | `output_schema` 返回侧校验派生项 | 执行返回在 Gateway 或等价可信边界按 Registry schema fail-closed 校验；错误码、Trace 与无敏感回显均有测试 | `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md:965,1161`；`app/ports/capability_registry.py:26,28`；`app/infra/persistence/capability_registry/schema.py:29,35`；`app/infra/gateway/capability_gateway.py:442-490`；`app/evaluator/terminal.py:46-65` |
| 冷启动必备字段缺失；pin 建议尚未实现 | 硬要求的 `trigger_examples`、`aliases`、`scope/allowed_departments` 不在 CapabilitySpec/DB；pin 也未实现，但它属于建议级而非硬要求 | 必备字段涉及 Capability contract 与 DB schema，须另开获批 lane；pin 另行价值拍板 | 尚未排期的冷启动 contract lane | 必备三字段进入模型、持久化、Admin/导入与选择链并有测试；pin 单独拍板实现或明确不采纳，**不作为硬字段欠债结清门槛** | `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md:1076-1085`；建议级 pin `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md:1088-1093`；`app/ports/capability_registry.py:20-38`；`app/infra/persistence/capability_registry/schema.py:9-65` |
| 管理员自由文本缺 prompt-safe 校验 | `name`、`short_description`、`intent_tags` 等缺长度、字符集和 prompt-safe 约束；后端为开放字符串；前端 `name`、`owner`、`short_description` 只有 required，`intent_tags` 无表单规则 | 无硬依赖 | b1 | 后端成为权威校验点，具备有界长度、允许字符/规范化和 prompt-safe 处理，前端镜像约束，含可执行负向测试 | `app/admin/registry.py:50-70`；`tests/infra/persistence/capability_registry/test_capability_spec_validation.py:90-109`；`web/src/pages/admin/RegistryPage.tsx:252-316`；`app/runtime/runtime.py:463` |
| Admin 只能建/启/停，不能修改能力 | Port 已有 `update`，但 Admin API 没有 update 路由，已创建 metadata 无正式修订路径 | 管理员编辑语义及公开 Admin API contract 尚未拍板 | 尚未排期的 Registry 编辑 lane | 授权、审计、并发语义明确的更新 API/管理面落地，或明确决定不开放并提供受治理替代路径 | `app/api/v1/admin.py:168-241`；`app/ports/capability_registry.py:52` |
| Capability Registry 无确定性部署 bootstrap（已结项） | 迁移和生产 `app/` 不灌数；仓库此前仅有 canonical OA 预置定义，实际入库依赖人工执行 `--apply` | 已解除 | `P2-REGISTRY-BOOTSTRAP-001` | 显式 apply 已具备 durable 审计，独立只读 verify 在缺失/失活/契约不匹配时失败闭合，真实 PostgreSQL 用例证明幂等与事务回滚，且 startup Registry DML 守卫保持生产启动不写 Registry | PR #75 `## Scope`；`scripts/manage_oa_capabilities.py`；`tests/scripts/test_manage_oa_capabilities.py`；`tests/architecture/` startup Registry DML 守卫 |
| schema digest 手填且后端不校验一致性 | UI 要求人工填写两份 digest，Admin 服务原样转 draft 入库；生产 `app/` 没有 canonical schema 重算/比对 | 无硬依赖，可独立；尚未排期 | Registry digest 一致性校验 lane | 后端按 canonical JSON 计算 digest 或拒绝不一致值；create/update/import 全路径覆盖，UI 不再是权威 digest 来源，并有 mismatch 负向测试 | `web/src/pages/admin/RegistryPage.tsx:292-305`；`app/admin/registry.py:50-76,214-220`；`docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md:4755-4757`；`scripts/smoke/capabilities.py:30-37` |

##### 能力注入恢复派生工作（不进 DAG、不排期）

| 派生项 | 依赖 |
|---|---|
| b1：管理员自由文本加长度、字符集、prompt-safe 校验 | 无 |
| b2：短摘要、owner、risk、version 等恢复进 Planner 候选摘要 | **硬依赖 b1** |
| b3：字典序截断改为相关性 Top-K，并显式报告超限/低置信度 | 依赖 b2 |
| b4：Embedding 召回；生产目前只有 pgvector 扩展，无向量业务表/列和 Retrieval Port | 依赖 b3 |
| `output_schema` 返回侧校验 | 独立，可与 b1–b4 并行 |

管理员 pin 属蓝图建议级，不与 `trigger_examples`、`aliases`、`scope/allowed_departments` 三个“必须具备”字段按同一强度登记。

当前 `tests/contract_packs/oa/ecology9-pending-workflows-v1/profile.json` 的 `source_kind` 为 `"synthetic"`，v2 为 `"derived_from_sibling_capture"`，v3 为直接现场原料脱敏后的 `"sanitized_capture"`。v3 能证明已捕获协议的离线结构和权威计数一致，但仍不能替代目标能力真实 OA Live 指纹漂移比对或全链现场验收。

- **仓库卫生清理待办**：当前复核结果、历史证据纠偏、激活条件与逐项授权边界见上表“仓库卫生清理”；本棒未执行任何清理。

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
| `P2-OA-SYSMSG-PACK-001` | 新增 `oa.list_system_messages` 与并列 `ecology9-system-messages-v1` pack；脱敏器以 9 字符子串阈值和短值完整 token 匹配取代 transport-header 豁免。 | `P2-OA-READ-CONTRACT-001` | Q3 | ✅ 已落地（merge `b55104d193862a78b7529e657ceea4639ed6e152`，PR #65） |
| `P2-AUTH-USERID-TYPE-001` | 在 `_required_oa_user_id` 单点归一 OA 整数/字符串 `userid`，守卫同一 principal 且凭证行、IdentityMapping 各恰好 1 条。 | `P2-AUTH-001` | Q2 | ✅ 已落地（merge `a9bf8b8fc3fbf48448ca511768fe7271d8b8a221`，CI run 30797244405） |
| `P2-OA-SYSMSG-LIVE-001` | 启用 `oa.list_system_messages` Live 路由，按 capability 分离配置、响应模型与指纹漂移处理。 | `P2-OA-SYSMSG-PACK-001` | Q3 | ✅ 已落地（merge `9da2fe5a1948800f90110d5adbd033553d01a808`） |
| `P2-OA-MSGCENTER-PROTOCOL-001` | 对齐共享 OA 消息中心传输、保守 cursor 分页与 fail-closed 截断守卫。 | `P2-OA-SYSMSG-LIVE-001` | Q3 | ✅ 已落地（merge `c44ed56f426fd01104cf94bbb946f2baaf065efc`） |
| `P2-SMOKE-RUNNER-001` | 新增不泄漏凭证、结构化报告且 fail-closed 的内网 smoke runner。 | `P2-OA-MSGCENTER-PROTOCOL-001` | Q3 | ✅ 已落地（merge `caaf801fcaa011573fc5c5fe1f1d8565a2cfc287`） |
| `P2-SMOKE-AUTH-DIAG-001` | 以真实同源抓包发布 pending-workflows-v2、保持 v1 逐字节不变、关闭 Gateway binding-scope oracle，并恢复被弱化断言。 | `P2-SMOKE-RUNNER-001` | Q3 | ✅ 已落地（merge `1bf2ba6c895fec4b847f2369f13f22879920000b`） |
| `P2-OA-INTRANET-SMOKE-001` | 完成真实 OA 现场 smoke；2026-08-07 已取得待办 Adapter 输入，目标能力 Live 指纹漂移比对、Cookie 冷启动和真实 `/chat` 仍保留。 | `P2-SMOKE-AUTH-DIAG-001` | Q3 | ◐ 部分完成；剩余项需再次进内网 |
| `P2-OA-TODOLIST-ADAPTER-001` | 保留 `oa.list_pending_workflows` ID，把数据源原地替换为待办事宜三步协议，建立六字段业务模型、权威计数完整性断言与 v3 Contract Pack；不启用 `output_schema` 返回校验。 | `P2-SMOKE-AUTH-DIAG-001` | Q3 | ✅ 本棒交付完成；真实 Live 指纹仍由 `P2-OA-INTRANET-SMOKE-001` 单独保留 |
| `P2-GOLDEN-CREDENTIAL-PATTERN-001` | 在 `scripts/golden_task_assertions.py` 增加单一、可扩展的 OA 凭证字段名数据源，并以大小写不敏感的第七条 pattern 覆盖 `sessionkey` / `datakey`；既有六条 pattern 逐字保留，无删除、无弱化、无长度或熵阈值。 | `P2-OA-TODOLIST-ADAPTER-001`（与本轮另两棒并行） | Q3 | ✅ 已落地 |
| `P2-PILOT-OPS-A-001` | 后端权威校验落在 `CapabilitySpec` 与 `AdminCapabilityCreate`；前端 `RegistryPage` 仅镜像同一约束。Admin OpenAPI 仅新增 `POST /bindings/{binding_id}/revoke` 与 `POST /bindings/{binding_id}/reset`；两条新路径只声明 `BindingId`，认证继续使用可信 Session Cookie 与既有 CSRF 边界。 | `P2-OA-TODOLIST-ADAPTER-001`（与本轮另两棒并行） | Q3 | ✅ 已落地 |
| `P2-REGISTRY-BOOTSTRAP-001` | 首次使用默认或覆盖审计目录时，逐级建立并持久化全部缺失祖先：POSIX 根到叶创建、叶到根同步父目录；Windows 逐层 write-through 发布且不替换既有目录。新增真实 PostgreSQL 隔离用例，证明两次 apply 的第二次仅 Select 且逐字段不变，并证明中途失败由真实事务整体回滚、零残留、退出码非零。 | `P2-OA-TODOLIST-ADAPTER-001`（与本轮另两棒并行） | Q3 | ✅ 已落地 |
| `P2-SMOKE-E2E-CHAIN-001` | replay 与 live 复用同一生产 composition、受保护登录和 Runtime HTTP 全链；两个 OA capability 各自验证归一化 ResponseEnvelope 与 11 类 Trace。生产 Gateway 接线和子进程边界均 fail-closed；Provider 协议证据保持独立。`.env.smoke` 增加只读漂移检测与默认关闭的显式 repair，但共享文件不在本棒修改。 | `P2-REGISTRY-BOOTSTRAP-001` | Q3 | ✅ 本棒交付完成；当前基线 task_id；真实 OA Live 证据与实际 `.env.smoke` 修复仍按欠债表承载 |
| `P2-DB-GATEWAY-001` | 一个获批只读视图的注册查询能力完成 Policy、限行、脱敏、审计纵切。 | `P2-IDENTITY-CREDENTIAL-001` | Q3 | 是：DBA/业务批准视图 |
| `P2-PILOT-OPS-001` | 交付绑定管理/映射导入、审计看板和最小反馈统计的试点运营面。 | `P2-READ-ADAPTER-001` | Q3 | ◐ `P2-PILOT-OPS-A-001` 已落地；`P2-PILOT-OPS-B-001` / `P2-PILOT-OPS-C-001` 仍待裁决，其中 C 涉及 DB schema 专项授权 |
| `P2-MEMORY-001` | User Profile 与增强 Semantic Memory 在用户/部门 scope 内可用且不串数据。 | `P2-PILOT-FOUNDATION-001` | Q3 | 是：数据边界/语料 |
| `P2-SKILL-CANDIDATE-001` | 基础 Skill 候选可登记、审查、拒绝，且不能自动发布或执行。 | `P2-PILOT-OPS-001` | Q3 | 是：候选来源语义待拍板 |
| `P2-GOLDEN-001` | 冻结 P2 新 Golden，覆盖真实只读、绑定、DB、隔离、审计与反馈负向边界。 | `P2-READ-ADAPTER-001`、`P2-DB-GATEWAY-001`、`P2-PILOT-OPS-001`、`P2-MEMORY-001`、`P2-SKILL-CANDIDATE-001` | Q3 | 否（需显式 fixture 人批） |
| `P2-LOW-RISK-WRITE-001` | 一个获批低风险写操作完成幂等、预览、确认、补偿、评测与审计。 | `P2-GOLDEN-001`；若命中自触发条件再依赖 `P2-CONFIRM-RESUME-001` | Q3 | 是：写用例/沙箱/授权 |

主链：`FOUNDATION → OA_READ_CONTRACT → READ_ADAPTER → PILOT_OPS → GOLDEN → LOW_RISK_WRITE`；已落地 OA 现场准备链为 `SYSMSG_PACK → SYSMSG_LIVE → MSGCENTER_PROTOCOL → SMOKE_RUNNER → SMOKE_AUTH_DIAG → OA_TODOLIST_ADAPTER → SMOKE_E2E_CHAIN`，其间三条并行棒 `P2-GOLDEN-CREDENTIAL-PATTERN-001`、`P2-PILOT-OPS-A-001`、`P2-REGISTRY-BOOTSTRAP-001` 已按顺序落地，`OA_INTRANET_SMOKE` 仍为部分完成。`IDENTITY_CREDENTIAL` 的其余绑定/凭证治理与只读两棒并行推进，`DB_GATEWAY`、`MEMORY` 在依赖和外部输入满足后并入 Golden，`SKILL_CANDIDATE` 从真实试点信号后启动，避免先造空池。

当前交付链推进至 `P2-SMOKE-E2E-CHAIN-001`：本棒已闭合 Provider-only smoke 的代码绕过链，replay 永久证据与 live 现场入口复用生产 Runtime → Gateway → Policy → Adapter → Evaluator → Trace 核心；生产接线、11 类 Trace 和子进程边界均 fail-closed。共享 `.env.smoke` 本棒未获修改授权，真实 Live 指纹/冷启动/`/chat` 仍由 `P2-OA-INTRANET-SMOKE-001` 承载。`P2-SMOKE-E2E-CHAIN-001` 之后不存在已决唯一后继；「下一棒」指针留空，等待 GOV-SYNC 做 Class B DAG 裁决，本实现棒不从企业密钥、OPS、仓库清理或 Preselector 等候选中自行选择。

> **顺序调整（2026-07-29；2026-08-11 更新现场状态）**：OA 只读拆成两棒。`OA_READ_CONTRACT` 先在气隙环境冻结 Replay/Live 接缝、白名单、Contract Pack 与脱敏工具；`READ_ADAPTER` 再补 Live HTTP、凭证读取、最小 IdentityMapping、现场 smoke 接线和完整运行时闭环。两棒均不等待共享 Secret 方案：已决凭证模型是每用户复用自身 OA Session，不用共享服务账号、不存密码、不静默重登。`IDENTITY_CREDENTIAL` 的其余治理与之并行。`P2-SMOKE-E2E-CHAIN-001` 已执行 2026-08-11 的唯一后继裁决并完成代码侧全链；真实 OA 现场验收仍由 `P2-OA-INTRANET-SMOKE-001` 承载，v3 `sanitized_capture` 与本地 replay 都不能代替剩余 Live 指纹、冷启动和真实 `/chat` 证据。其后无已决唯一后继，指针留空待 GOV-SYNC 裁决。

## 4. 决策与开放问题

1. **已决（2026-07-24 雨爷拍板）— A1：是。** 生产 composition、真实 LLM 和最小可信试点入口作为 P2 首个硬前置（`P2-PILOT-FOUNDATION-001`）。
2. **已决（2026-07-29 雨爷拍板；2026-08-11 状态更新）— 首个真实系统与只读用例：OA `oa.list_pending_workflows`。** Replay/Contract、Live/凭证/IdentityMapping 代码纵切、OpenAPI/Orval、CSRF、Runtime 响应契约、可信登录入口、`/chat` 普通文本入口、`oa.list_system_messages` 并列 pack、Live 路由、消息中心协议、smoke runner、鉴权诊断、待办事宜数据源原地替换与本地 replay 全链均已交付；`P2-SMOKE-E2E-CHAIN-001` 已完成 2026-08-11 的唯一后继裁决。真实 OA Live 指纹漂移、冷启动与真实 `/chat` 仍是现场欠债；第二个 Adapter 的启动条件仍开放；其后无已决唯一「下一棒」，等待 GOV-SYNC 做 Class B DAG 裁决。
3. **认证路线**：OA 登录、EternalAI Session Cookie 与认证 Principal 已落地；仅“是否把企业 IAM/SSO 从 Phase 3 提前”仍开放。
4. **DB Gateway**：Phase 2 路线图写了基础 DB Gateway，但蓝图又限定“仅无 API/报表需求时”；是否已有获批报表用例？
5. **Skill 候选池**：只允许管理员/用户手工登记，还是允许脱敏 Trace 产生“候选提议”？后者不得变成自动 Skill 生成。
6. **已决（2026-07-24 雨爷拍板）— 框架：维持 raw SDK 默认。** 不投入 PydanticAI 内网复验；除非出现具体需求且雨爷再次确认。
7. **已决（2026-07-24 雨爷拍板）— confirm 欠债：维持自触发。** `P2-CONFIRM-RESUME-001` 本阶段不主动做。
8. **低风险写入**：具体选哪个动作、何种确认/审批、是否有沙箱与可验证补偿？
9. **已决（2026-08-09 雨爷拍板）— Phase 2 不建独立 per-task Task Record 文件或 schema。** 每个 PR body 强制使用 `## Scope`、`## 验证结果（pytest / Golden 原始结果行 + CI run）`、`## 本棒新增欠债` 三段。真实问题是治理同步滞后，本次已落后 4 棒；再加 Task Record 只会把待同步产物从 3 份变 4 份。Scope、验证、merge、欠债已分别由启动提示词、CI、Git、欠债登记承载，独立 Task Record 重复；真缺口是 `_scratch/` 不进仓库导致理由不可追溯，而 PR body 永久保存并绑定 commit 与 CI run。
10. **已决与开放（2026-08-09）— Golden 策略后续。** 不开“`app/` 有 diff 就必须配 Golden fixture diff”的 checker 棒；不为凑 50–200 的题数造题，后续按契约、信任边界、外部适配面和历史缺陷覆盖矩阵增题。报告 §7 棒 B（题外 lifecycle manifest + 修改 `golden_task_evaluator.py`）是否开启仍待雨爷拍板。
11. **已决（2026-08-09 雨爷拍板）— 治理同步按串行/并行分流。** A 类机械同步 = 测试基线数字、`task_id`、「下一棒」指针及本棒新发现欠债；同一时刻仅一个 write lane 时由该实现棒在本棒 payload commit 内一次完成，同时有两个及以上 write lane 时一律不进实现棒、统一由独立 GOV-SYNC 批次棒完成。**治理文档一律不记 commit SHA 与 CI run id**：本棒写入自己的 SHA 会改变该 SHA（自指），且 SHA 与 CI run id 都是 `task_id` 的冗余投影；追溯以 `git log --grep=<task_id>` 为准（commit 规范 `phase2(<task_id>):` 与 merge 规范 `merge phase2(<task_id>):` 保证命中），CI 结果以 GitHub 为唯一权威，运行证据留在 PR body 的「验证结果」段（PR body 不是 commit，可后编辑，不自指）。本规则不追溯改写历史条目中已记录的 SHA / CI run。B 类跨棒裁决、推翻蓝图偏差的 ADR、Golden 策略、DAG 重排与跨棒欠债合并永远归 GOV-SYNC；实现棒只能机械传播已决 DAG 的下一个 `task_id`，不得自行挑选后继。开棒前必须判定串行或并行，并在启动提示词写明本棒是否承担 A 类同步。当前 `CLAUDE.md` 与 `AGENTS.md` 状态段均为单一长段落，并行修改必冲突；其结构改造另列欠债，**两处都落地后**才解除并行限制。
12. **已决（2026-08-11）— PR body 完整性与合并授权。** PR body 三段及每条欠债的 reason / blocked_by_task_id / activation_task_id / expiry_condition / evidence 必须在合并前完成；合并后补写不计合规记录。required checks 全绿是必要条件，但不构成自行合并授权：配有监理窗口的棒须先获监理 PASS，未配监理窗口的棒须由启动提示词显式授权自行合并。PR #73 的合并时点违规定性为流程缺陷导致的记录不合规，不是内容造假；#74 / #75 的合并前欠债段证明修正流程已生效。
13. **已决（2026-08-11）— 监理窗口分级。** 修改 `app/ports/` 契约、DB schema、凭证语义、Golden fixture / `FROZEN_GT_IDS` 或安全边界（认证、CSRF、脱敏、隔离）的棒必须配监理窗口；单一表面的小棒、纯文档棒、纯配置棒可跳过，改为合并后由主窗口派子智能体抽查。跳过监理的棒仍须在启动提示词显式写明合并授权。
14. **已执行（2026-08-11）— `P2-SMOKE-E2E-CHAIN-001` 已完成当日唯一后继裁决。** Provider 级 smoke 的 Runtime / Gateway / Policy / Evaluator / Trace 代码绕过链已闭合，并有本地 replay 永久证据；`.env.smoke` 缺 9 个 live 硬必填键、含 3 个废键且 pack 指针仍在 v2 的事实未被代码防线冒充为已修复，实际修改仍须专项授权。真实 Live 证据继续由现场窗口承载；其后候选不唯一，当前指针留空，等待 GOV-SYNC 做 Class B DAG 裁决。

## 5. P2 不做什么

- 不把“能连到接口”当试点完成；缺可信身份、正式凭证、审计、Evaluator 或负向 Golden 时仍是半成品。（蓝图 §3.2 L176-L186、§7.0 L1383-L1404、§13 L2701-L2717）
- 不让 Runtime、LLM、UI、Workflow 或 Skill 绕过 Capability Gateway；不让 DB Gateway 自由查生产表。（蓝图 §7.3 L1436-L1502、§14.2.1-§14.2.2 L2787-L2806）
- 不把密码、token、Cookie、session 或敏感原文写入 LLM、Memory、Skill、Trace、日志、fixture 或报告。（蓝图 §7.4.3 L1540-L1549、§14.2.3 L2808-L2817）
- 不因未绑定/凭证失效自动切换服务账号；不让自报角色获得 Admin 权限。（蓝图 §9.1.1 L2021-L2062）
- 不在外部输入缺失时编 endpoint、infra 数值、凭证模式或系统能力；BLOCKED 项只解除，不猜测。（`P1-PARAM-001.md` L52-L59、L73-L77；蓝图 §15 L2870-L2907）
