# Phase 2 总目标、范围与任务 DAG（Lean Plan）

> 状态：**生效（轻量地图）**。本文件只承载 P2 稳定范围、现役 DAG、外部输入与活欠债；当前进度唯一见 `docs/phase2/STATUS.md`，已完成棒以 `git log --grep='phase2('` 追溯。
>
> 执行分档、红线、Review、验证与合并规则唯一见 `AGENTS.md`；**BLOCKED 外部输入未到时不启动、只解除不猜测**。已决架构与历史 supersede 唯一见 `docs/phase2/DECISIONS.md`。

## 1. P2 总目标

P2 把已完成的 **Mock/低风险 B2→B5 闭环**，推进为**至少 1 个真实系统的部门试点纵切：先只读验收，再做 1 个获批的低风险写入**；所有真实调用仍经 Gateway / Policy / Trace / Evaluator，并补齐试点必需的可信入口、账号绑定与凭证、审计与反馈、Golden、User Profile / Semantic Memory 及基础 Skill 候选治理。（`PHASE1_SPEC.md` L13-L25；蓝图 §3.2 L176-L186、§13 L2680-L2717）

- **真实但克制**：首个真实 API Adapter 为必达；第二个仅在首个稳定且雨爷选定后进入。（蓝图 §13 L2701-L2707）
- **安全可运维**：真实身份/凭证不可再用 Mock，绑定、审计与管理动作形成闭环。（蓝图 §7.4.7 L1671-L1677、§7.5.1 L1685-L1691）
- **以证据扩展**：更多 Golden、用户反馈统计和审计看板共同约束试点，不以“接口能调通”冒充完成。（蓝图 §7.6 L1752-L1786、§13 L2708-L2714）
- **进化只到候选**：P2 可有基础 Skill 候选池，但不自动生成、发布或扩大权限。（蓝图 §2.5 L110-L118、§10.3 L2207-L2253、§13 L2710）
- **安全开关看可验证事实**：安全守卫只能依赖可直接校验的协议事实或配置值，不依赖 `ENV` 这类自由文本环境标签；当前 `ENV` 只承载 testing/mock 边界，不是生产安全分流字段。（`app/config.py::ProductionSettings.from_environment`；`app/composition.py`；`app/infra/observability/noop_trace_writer.py`）

### P2 收口标准（2026-08-19 修订：必达 / 机会 / 排除三层）

原 IN 范围 9 项曾全部列为必做，其中两项卡在第三方手里，等于把收口时间交给项目外的人决定。现分三层，**只有「必达」层决定 P2 何时收口**。

**必达**（缺一不可，全部达成即收口）：

| # | 内容 | 承接 |
|---|---|---|
| 1 | 一个真实系统（OA）只读纵切，经 Gateway / Policy / Evaluator / Trace 全链 | ✅ 已完成 |
| 2 | Work Object + 最小工作台：用户看得到自己的 OA 待办、状态与数据截至时间，可标记处理痕迹 | ✅ 已完成：`P2-WORK-OBJECT-001` |
| 3 | 后台定时轮询：用户不打开页面，状态也在积累 | ✅ 已完成：`P2-OA-CREDENTIAL-POLL-001` |
| 4 | 一个获批低风险写入（OA 待办审批同意），经确认卡授权、受版本绑定保护 | 未完成：`P2-LOW-RISK-WRITE-001`，BLOCKED 于 OA 审批提交协议结构 |
| 5 | 覆盖以上路径的 Golden，含负向与边界（**「以上路径」限 Golden Runtime 观察边界**；工作台/隔离/审计归 API 与单元层，见 `docs/phase2/DECISIONS.md` 2026-08-28 口径裁决） | ◐ `P2-GOLDEN-001` 已完成；`P2-GOLDEN-002` 待低风险写入落地后执行 |

**机会**（不到位不阻塞收口）：Memory、Skill 候选、海康只读 Adapter、Port 接缝、组织目录与内部任务派发、Renderer/Schema/弱测试收尾、前端工作台、页面上下文、审计读取与反馈闭环；具体 task_id、依赖与 BLOCKED 条件只见下方现役 DAG。

**排除**（P2 明确不做）：IM 接入与 `WorkCandidate` 链路；卡片渲染器多密度渲染；装第三方执行内核（四步走第四步）；Memory 六层的后四层；Skill CI/CD 完整生命周期；原生客户端；**知识库**（个人/部门两级、权限、文档转 MD 入库）；**DB Gateway**。

> 任何「顺便把 X 也做了」的提案，先对照排除清单。依据 `docs/phase2/DECISIONS.md` 2026-08-19「P2 收口标准修订」。

## 2. 范围边界

### IN（P2 做）

| 能力/闭环 | 一句话交付与边界 | 来源 |
|---|---|---|
| 可运行的试点基线（**已拍板：P2 首个硬前置**） | 生产装配入口已接入真实 structured-output LLM、Runtime/Auth/Admin 与可信试点用户身份，Admin context 来自认证 Principal，使既有低风险主链可启动、可健康检查、可审计；不选定新框架。 | `app/main.py`；`app/api/v1/admin.py`；蓝图 §12.1.3 L2492-L2510、§12.1.5 L2538-L2549、§13 L2701-L2703 |
| 真实 API Adapter 只读纵切 | 选 1 个真实系统，把一个只读用例从请求、真实身份、Gateway、Adapter、Evaluator、Trace 跑到响应；第二个 Adapter 是可选增量。 | 蓝图 §8.1 L1794-L1808、§13 L2703-L2707、§15 L2870-L2907 |
| 真实绑定与凭证闭环 | 对选定系统落地正式 Secret 管理、真实 bind mode、基础凭证验证，以及管理员查看/筛选/解绑/重置/发送引导；~~支持 Excel/HR 导入映射~~ **已于 2026-08-20 划入 P3**（见 `docs/phase2/DECISIONS.md` 同日「Excel / HR 批量映射导入划入 P3」）——P2 只接入 OA 单一系统，逐人手工绑定即可；批量导入的价值出现在多系统多人场景。禁止导入密码这一约束在 P3 实现时继续有效。 | 蓝图 §7.4.3 L1540-L1549、§7.4.7 L1671-L1677、§7.5.2 L1693-L1717、§13 L2712-L2714 |
| 试点运营面（机会层） | 在已落地持久 Trace/查询之上提供审计看板；接收最小用户反馈并形成基础统计，不自动生成建设 backlog。 | 蓝图 §7.6 L1752-L1786、§9.2 L2064-L2082、§13 L2709-L2711；`TASK_INDEX.md` §5.1 L109-L118 |
| P2 Memory 增量（机会层） | 引入按用户隔离的 User Profile Memory，并增强制度、字段、报表口径和业务术语等 Semantic Memory；不进入 Episodic/Procedural/Knowledge Vault。 | 蓝图 §10.1 L2148-L2186、§10.2 L2188-L2205、§13 L2715 |
| 基础 Skill 候选池（机会层） | 只保存受治理、可审查、由人主动登记的候选及来源引用；候选不能自动提出、发布、执行或晋升 scope。 | 蓝图 §10.3 L2207-L2253、§13 L2710；`docs/phase2/DECISIONS.md` 2026-08-18「Skill 候选来源语义」 |
| P2 Golden 增量 | 只覆盖 Golden Runtime 观察边界；工作台、隔离与审计归 API / 单元层。负向/安全拒绝继续 100%，冻结 ID/fixture 仍须显式人批。 | 蓝图 §9.3.2 L2120-L2139、§13 L2708；`docs/phase2/DECISIONS.md` 2026-08-28「必达项 5 的完成判据口径」 |
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

### 未到位的外部输入

| 阻塞项 | 必需外部输入 | 对 P2 收口的影响 | 来源 |
|---|---|---|---|
| `P2-LOW-RISK-WRITE-001` | OA 审批提交动作的协议结构。未到位前不得猜 endpoint、字段、错误码或回滚语义。 | **唯一必达阻塞**；该棒未落地则 `P2-GOLDEN-002` 无题可冻 | `docs/phase2/STATUS.md`；`docs/phase2/DECISIONS.md` 2026-08-19「首个低风险写入」 |
| `P2-HIKVISION-ADAPTER-001` | 具体只读用例、现场版本、API/SDK、测试环境、部门级共享账号及允许设备/区域范围。 | 机会层，不阻塞 P2 收口 | 蓝图 §15 L2870-L2907 |
| `P2-MEMORY-001` | 经批准的知识语料与用户数据边界。 | 机会层，不阻塞 P2 收口 | 蓝图 §10.1-§10.2 L2148-L2205 |

## 3. 活欠债登记

| item | reason | blocked_by_task_id | activation_task_id | expiry_condition | evidence |
|---|---|---|---|---|---|
| Admin OpenAPI 仍声明已被传输层剥离的角色 Header | 既有 curated Admin 操作仍引用 `RoleClaims`，但共享 mutator 会剥离该 Header，形成既有契约漂移 | 无（未排期） | 待雨爷分配真实承担 task_id | 独立后继棒将既有 Admin OpenAPI 认证参数与可信 Session 契约对齐、重生成受影响 client，并通过字节漂移守卫与 CI | `web/openapi/admin.openapi.json` 的 `components.parameters.RoleClaims`；`web/src/api/mutator.ts::customInstance` |
| PR #75 body Markdown 反引号转义损坏 | PR #75 末次编辑把全文反引号改成“反斜杠后接反引号”的字面文本，导致 GitHub inline code 与 PowerShell 围栏失效；本轮未现场验证 GitHub raw body，不把历史记录猜成已修 | 需独立外部 PR 编辑动作；本棒无授权修改 #75 | 待单独处理 | GitHub 上 #75 的 inline code 与 PowerShell 围栏恢复正常且正文语义不变，或仓库 owner 明确裁决永久保留 | PR #75 raw body；本轮未现场验证 |
| 两处 ADR 与代码冲突（待裁决） | 历史材料称有两处冲突，但没有列明对象，证据不足以裁决应改代码还是新增 superseding ADR | 两处冲突的精确对象、权威取舍与独立 Scope | 待分配 ADR/code alignment task_id | 两处冲突分别由现役代码或获批 superseding ADR 唯一裁决，旧结论明确退役且引用链无歧义 | `P2-GOV-SYNC-010` 启动合同 B4-8；本轮仍未获得两处对象的精确证据 |
| Adapter 超时 / 重试边界缺失（待裁决） | Adapter 尚无统一、可执行的有界 timeout / retry 合同；在真实协议的错误分类、幂等性和重试安全未确认前不能猜测默认值 | 真实协议的可重试分类、幂等性与现场参数 | 待分配 Adapter resilience task_id | 每个适用 Adapter 具备有界超时、只对已确认可重试错误重试、耗尽后保留稳定错误码与 Trace，并有永久回归测试 | `P2-GOV-SYNC-010` 启动合同 B4-8；当前台账此前无精确五字段项 |
| `P2-CONFIRM-RESUME-001` 仍按自触发，非 Workflow action 无真实 pending/resume | 当前只接通 Workflow pending；普通高风险 action/query 不建立 `_pending_workflows`，前端也无法区分可恢复性，点击后只能 fail-closed 为 `no_pending_action` | 出现获批的非 Workflow 高风险用例，或阶段决策明确不需要 | `P2-CONFIRM-RESUME-001`（仅命中自触发条件时） | 以不可变请求、HumanGate、版本绑定与一次性 claim 建立真实 pending/resume，并让前端依据服务端事实区分可恢复性；不得由前端猜 capability type | `app/runtime/runtime.py` 的 Workflow pending 分支；`web/src/components/ConfirmCard.tsx`；`docs/phase2/DECISIONS.md` 既有自触发裁决 |
| `WorkflowEnginePort` 接缝缺失（待裁决） | 当前缺少已登记的 `WorkflowEnginePort` 生产接缝，直接补实现会涉及 `app/ports/` 契约与全部实现/测试同步 | 最小 Port 合同与独立受监理 Scope | 待分配 WorkflowEnginePort seam task_id | 最小 `WorkflowEnginePort` 契约、生产实现、composition 与测试同棒落地，且不允许 Runtime/Workflow 绕过 Gateway / Policy / Trace | `P2-GOV-SYNC-010` 启动合同 B4-8；当前台账此前无精确五字段项 |
| Golden 题外 lifecycle manifest 尚未裁决 | 既有正向 fixture 不得原地改写，但 runner 会运行全部 `GT-*.json`，无法表达“保留但停止运行”；`FROZEN_GT_IDS`、active 集合与题外生命周期载体的关系尚未拍板 | 雨爷对 lifecycle manifest / 等价载体的裁决 | 棒 B（待拍板） | 建立获批的题外生命周期载体，Gate 只跑 active 集合并完成同版本包全量回放；既有冻结题不被原地改写 | `scripts/golden_task_evaluator.py`；`docs/phase2/DECISIONS.md` 决定四 |
| `.env.smoke` 与当前 live 合同缺少仓库守卫 | 2026-08-13 现场 `verify` 已通过，证明当次进程配置可用，但 smoke 环境加载仍只检查键存在，仓库测试也不读取真实 `.env.smoke` 的合同；本棒按红线不读取该文件，不能把一次现场成功外推为静态配置永久正确 | 修改 `.env` / `.env.smoke` 属红线；值语义与真实文件合同需独立、无值的检查边界 | `P2-SMOKE-CONFIG-CONTRACT-001` | smoke 对 URL/scheme、数据库目标、Contract Pack 目录等已决配置语义 fail-closed，并以只读、无值的契约测试证明真实 smoke 键集合受守护；不得输出或修改凭证值 | `scripts/smoke/environment.py::load_runtime_environment`、`_missing_runtime_keys`、`_validate_contract_pack_dirs`；`tests/runtime/test_pilot_foundation_e2e.py::test_http_replay_full_chain_sends_session_cookie_and_recovers_principal` |
| full-chain smoke 以 origin 字典序隐式选择 base URL | `run_full_chain_check` 使用 `sorted(csrf_allowed_origins)[0]` 选择客户端 base URL；当前共享配置两项均为 HTTP，行为正确，但当未来出现混合 scheme 或顺序变化时，Cookie transport 将隐式依赖字典序，配置意图不够显式 | 显式 smoke base-origin 合同或经批准的确定性选择规则尚未裁决；本棒明令不得修改当前选择逻辑 | 待 GOV-SYNC 分配 task_id（不得自行造号） | full-chain 使用显式且校验过的 smoke base origin，或使用获批的确定性选择规则；混合 scheme 与顺序变化均有测试，Cookie transport 与所选 scheme 一致且不削弱 CSRF | `scripts/smoke/full_chain.py::run_full_chain_check` 的 `sorted(settings.csrf_allowed_origins)[0]`；`P2-SESSION-COOKIE-TRANSPORT-001` 启动合同明确要求只登记不修改 |
| Python 3.16 移除旧式 Windows asyncio event-loop policy 的兼容欠债 | 全量与定向测试持续产生 `WindowsSelectorEventLoopPolicy` / `set_event_loop_policy` 弃用 warning；本棒修改到相关 E2E 文件但修复属于兼容性重构，不是本棒缺陷路径 | Python 3.16 兼容方案与独立测试基础设施 Scope | 待 GOV-SYNC 排期的 Python 3.16 compatibility lane | Windows 测试改用仍受支持的事件循环构造方式，相关 DB/E2E 测试在目标 Python 版本无这些弃用 warning，且既有行为不弱化 | `uv run pytest` 收口原始 warning；`tests/runtime/test_pilot_foundation_e2e.py:50-51` 及其他 PostgreSQL 测试同类设置 |
| `CapabilityGateway.assert_production_wiring` 当前固定要求 `adapters["oa"]` | 当前生产纵切只有 OA，固定键能忠实堵住本棒静默漏接线；未来若引入非 OA 生产组合，守卫需由 composition 提供必需 adapter 集合，而不能继续把 OA 当通用不变量 | 第二个真实生产 Adapter 的已决 composition 设计 | 对应第二 Adapter composition lane（未排期） | 守卫按已决生产组合验证明确的必需 adapter key 集合，同时继续强制 Registry / Identity / Policy / Trace 非空，并保留破坏式回归测试 | `app/infra/gateway/capability_gateway.py::CapabilityGateway.assert_production_wiring`；`tests/runtime/test_runtime_composition.py::test_production_composition_rejects_incomplete_gateway_wiring` |
| OA 长会话心跳保活缺失 | OA 登录技术对接文档步骤 6 要求“启动心跳保活”，当前 `app/` 没有心跳实现，长会话可能因 OA Session 超时而中断 | 尚未排期 | `P2-OA-SESSION-KEEPALIVE-001` | `app/` 实现有界、可停止且不泄漏凭证的 OA Session 心跳，并以长会话测试证明续期与失败行为 | `OA登录技术对接文档.md` §4.1、§4.2；本棒只登记，不实现 |
| `phase0/main` 分支保护缺 up-to-date 与禁止 bypass 两项 | 非 fresh PR 仍可能可合并，且有权限账号可绕过 required checks；外部当前状态本轮未现场读取，不宣称已开启 | 仓库 owner 权限，agent 无专项授权不得修改 | 雨爷在 GitHub 分支保护设置中开启 | **Require branches to be up to date before merging** 与 **Do not allow bypassing the above settings** 均开启，且非 fresh PR / bypass 由 GitHub 自动拦截 | `AGENTS.md`「仓库 owner 待办」；`docs/phase2/DECISIONS.md` 2026-08-20「并行 lane 的测试库约束」 |
| 仓库卫生候选需逐项复核 | 分支或 worktree 可能仍有独有文件、未暂存修改或未合入 commit；数量易漂移，且删除属于红线 | 逐项只读复核与动作级专项授权 | `P2-REPO-CLEANUP-001`（仅已授权范围） | 每个候选均证明无唯一未集成内容并取得对应删除授权 | `git worktree list --porcelain`；`git branch --format`；`git branch -r --format` |
| `.github` PR body 三段式模板缺失 | 当前没有仓库级填写兜底；模板只能兜底，`gh pr create --body` 仍可绕过 | 新的 `.github/` Scope lane | 尚未排期的 PR 治理加固 | `.github/pull_request_template.md` 提供三个固定标题并验证常规创建路径；文档明确它不是强制门禁 | `AGENTS.md:27-28`；`.github/` 当前无 `pull_request_template.md` |
| PR body 三段式缺少 CI 强制校验 | 文档和模板都不能阻止遗漏，CI 是唯一“忘了也会被拦下”的形态 | 新的 `.github/workflows` Scope；若设 required check，还需仓库 owner 按规则授权配置 | 尚未排期的 PR body CI gate | CI 对缺失三段的 PR fail-closed、对完整三段通过，并纳入 `phase0/main` required checks | `AGENTS.md:27-28`；`.github/workflows/ci.yml` 有 PR 触发但无 PR body 三段校验 |
| `_scratch/` 决策产物仍未纳入版本控制 | 本棒只改项目级治理文档单一权威结构；启动合同明确把既有 `_scratch/` 决策产物归档延后，当前仍缺版本历史、跨机器可见性和生命周期状态 | 需独立获批的决策产物盘点与归档 Scope；本棒禁止移动既有 `_scratch/` 文件 | 待 GOV-SYNC 分配决策产物归档 task_id | 逐项区分临时文件、可入库决定/ADR/报告与原始敏感素材；获批的持久产物进入 `docs/phase2/` 或 ADR 目录并更新引用，敏感素材继续留在仓库外，任何删除另取专项授权 | `_scratch/治理文档信息架构_输入规格.md` §1.4；`P2-GOVDOC-SSOT-001` 启动合同 Out of scope；`.gitignore` 的 `_scratch/` 规则 |
| Capability Preselector 整层缺失 | 生产代码把 active capability 按安全化 ID 字典序取前 8 条，没有规则/标签/Policy/相关性预筛；能力超过 8 条后尾部能力不可见 | 恢复链尚未排期；b3 依赖 b2 | b3：相关性 Top-K；b4：Embedding 增强 | 生产链按用户/组织/Policy 范围生成相关性 Top-K，且超限或低置信度显式处理，不再因字典序造成永久不可见 | `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md:1050-1052,1057,1064-1068,1074`；`app/knowledge/basic_knowledge.py:21,121-136`；`scripts/smoke/capabilities.py:24-27`；`scripts/smoke/runner.py:1609-1661` |
| Planner 注入内容未恢复蓝图摘要契约 | 当前只注入 capability ID/type/target/status 和输入参数键结构，不含短摘要、输出摘要、version、owner、risk；管理员描述对模型选能力不起作用 | **b1 硬前置**：自由文本 prompt-safe 校验 | b2 | 经 b1 收紧后，恢复有界短摘要、owner、risk、version 与必要 schema 摘要，并以负向测试证明不注入未经批准的原文或授权语义 | `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md:1057`；`app/knowledge/basic_knowledge.py:211-223`；`app/runtime/runtime.py:433-469`；`docs/phase1/tasks/P1-B5-002.md:22-24` |
| Capability 合同无条件按字典序截断 | `MAX_CAPABILITY_CONTRACTS = 8` 后无条件 break，不基于相关性或实际 Context Budget，且上限来源未记录 | b2 | b3 | 改为相关性 Top-K；超限、低置信度和未覆盖能力均有显式状态与回归测试 | `app/knowledge/basic_knowledge.py::MAX_CAPABILITY_CONTRACTS`；`tests/knowledge/test_basic_knowledge.py`；历史以 `git log --grep=P1-B5-002` 追溯 |
| Registry `output_schema` 未参与返回侧校验 | 本棒已把 `output_schema` 接入 `ResponseEnvelope` 外露塑形，但 projector 只执行结构投影与类型筛选，不执行 `required`、`const`、`enum`、`format` 等业务有效性，也不把返回合同违规转换为执行失败、错误码和 Trace；因此原欠债保持未结项 | 无硬依赖，独立可并；尚未排期 | `output_schema` 返回侧校验派生项 | 执行返回在 Gateway 或等价可信边界按 Registry schema fail-closed 完整校验；错误码、Trace 与无敏感回显均有测试，且不把外露 projector 冒充完整 validator | `app/runtime/response_projection.py::project_response_data` 明确只做外露塑形；`tests/runtime/test_response_projection.py` 覆盖投影边界；`docs/phase2/DECISIONS.md` 2026-08-30「校验边界」；原始依据仍见冻结蓝图 §6.4 / §8.1 与 `app/infra/gateway/capability_gateway.py` |
| Capability Registry `get(id)` 对 `status=disabled` 仍返回 | 挂起任务的 Human Gate 路径已用 exact binding 在 resume 前拒绝 missing / disabled / schema 漂移；无人门 checkpoint 与 exact binding 已通过后的执行窗口按保存的 task snapshot 收口。本棒不改变 Registry 生命周期语义，其他调用方仍可能把 disabled 记录当普通查询结果 | 无（不阻塞本棒）；需先裁定 Registry 查询与生命周期边界 | 待 GOV-SYNC 分配 Capability Registry lifecycle task_id | Registry 的查询接口或所有调用边界对 disabled 记录形成唯一、fail-closed 的现役语义，且 active / disabled / missing 均有调用方级回归；不得破坏挂起任务按保存 snapshot 投影的版本一致性 | `app/infra/persistence/capability_registry/repository.py::get`；`app/workflow/engine.py::_definition_version_bindings`；`tests/runtime/test_runtime_user_action.py::test_human_gate_registry_change_fails_exact_binding_before_resume` 与 `test_no_gate_resume_uses_saved_projection_snapshot_after_registry_change` |
| 冷启动必备字段缺失；pin 建议尚未实现 | 硬要求的 `trigger_examples`、`aliases`、`scope/allowed_departments` 不在 CapabilitySpec/DB；pin 也未实现，但它属于建议级而非硬要求 | 必备字段涉及 Capability contract 与 DB schema，须另开获批 lane；pin 另行价值拍板 | 尚未排期的冷启动 contract lane | 必备三字段进入模型、持久化、Admin/导入与选择链并有测试；pin 单独拍板实现或明确不采纳，**不作为硬字段欠债结清门槛** | `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md:1076-1085`；建议级 pin `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md:1088-1093`；`app/ports/capability_registry.py:20-38`；`app/infra/persistence/capability_registry/schema.py:9-65` |
| 管理员自由文本缺 prompt-safe 校验 | `name`、`short_description`、`intent_tags` 等缺长度、字符集和 prompt-safe 约束；后端为开放字符串；前端 `name`、`owner`、`short_description` 只有 required，`intent_tags` 无表单规则 | 无硬依赖 | b1 | 后端成为权威校验点，具备有界长度、允许字符/规范化和 prompt-safe 处理，前端镜像约束，含可执行负向测试 | `app/admin/registry.py:50-70`；`tests/infra/persistence/capability_registry/test_capability_spec_validation.py:90-109`；`web/src/pages/admin/RegistryPage.tsx:252-316`；`app/runtime/runtime.py:463` |
| Admin 只能建/启/停，不能修改能力 | Port 已有 `update`，但 Admin API 没有 update 路由，已创建 metadata 无正式修订路径 | 管理员编辑语义及公开 Admin API contract 尚未拍板 | 尚未排期的 Registry 编辑 lane | 授权、审计、并发语义明确的更新 API/管理面落地，或明确决定不开放并提供受治理替代路径 | `app/api/v1/admin.py:168-241`；`app/ports/capability_registry.py:52` |
| canonical Capability 定义变更缺少部署同步检查点 | `P2-OA-TODOLIST-ADAPTER-001` 修改 `oa.list_pending_workflows` canonical 定义后，试运行库仍保留精确 predecessor；当前交付链没有自动检查或同步点，直到 smoke `rehearse` 的 Registry preflight 才 fail-closed 暴露 | 无（未排期）；需明确 canonical 变更后的部署同步责任与检查点 | 待 GOV-SYNC 分配 task_id | canonical 定义变更在进入 smoke 前必须完成显式同步与独立 `--verify`，或由等价的 fail-closed gate 阻断未同步目标环境，不再依赖 `rehearse` 才首次发现漂移 | `scripts/smoke/capabilities.py::expected_oa_capabilities`；`scripts/manage_oa_capabilities.py::_plan_registry_management` 与 `--verify`；`scripts/smoke/runner.py::_run_capability_registry_preflight`；`P2-REGISTRY-DOC-FIX-001` PR body 验证结果 |
| smoke 对源码版本新鲜度无自证 | `_REQUIRED_RUNTIME_KEYS` 是当前源码内清单；从旧 worktree 运行 `prepare` 时不会认识后加必填键，曾实际出现 `missing=none` 但后续链路仍失败的假绿。现场操作卡用人工核对主干规避过一次，仓库仍无确定性诊断 | 无硬依赖；不得把远端可达性或 dirty 状态当授权 | `P2-SMOKE-FRESHNESS-DIAG-001` | smoke 启动输出无敏感值的当前源码版本标识，并在目标运行版本明显落后已知主干时显式告警或 fail-closed；离线、detached 与未配置 remote 的行为有明确测试 | `scripts/smoke/environment.py::_REQUIRED_RUNTIME_KEYS`；`scripts/smoke/runner.py::main`；`tests/scripts/test_smoke_runner.py` |
| `_read_selected_form()` 只解码值、不解码字段名 | `postData.params` 分支仍构造 `(name, unquote_plus(value))`，而 text 分支 `parse_qsl` 同时解码名和值；三个调用方在编码字段名下均因缺字段返回 `None` 并 fail-closed 为 `todo_list_entry_not_found`，严重度低但语义不一致 | 无硬依赖；需独立 HAR 解析 Scope | `P2-HAR-SELECTED-FORM-DECODE-001` | params/text 两分支统一执行一次字段名和值解码，保持重复键、空白、控制字符与缺字段 fail-closed，并有走公开入口的忠实回归 | `scripts/smoke/har.py::_read_selected_form`；`scripts/smoke/har.py::extract_todo_list_contract`；`tests/scripts/test_smoke_runner.py` |
| `OA_SYSTEM_MESSAGES_CATEGORY_ID` 默认值缺少可追溯出处 | smoke prepare 在 `_desired_smoke_values` 中直接写入类别 ID 字符串，真实现场已证明服务端接受该配置并返回结构化响应，但仓库没有说明类别选择的业务/协议来源 | OA 类别语义的权威说明或已批准现场记录；不得根据值形态猜测 | `P2-OA-SYSMSG-CATEGORY-CONTRACT-001` | 仓库内以无业务值泄漏的方式记录类别 ID 的权威来源、适用版本与变更流程，配置与测试引用同一合同，不再依赖无出处字面量 | `scripts/smoke/environment.py::_desired_smoke_values`；`app/config.py` 的 `oa_system_messages_category_id`；`tests/scripts/test_smoke_runner.py` |
| smoke 事后报告仍打印出发前操作说明 | `_build_report` 在 `verify` 已完成后仍追加“给雨爷的现场操作”，内容是 `start` / 浏览器登录 / 再跑 `verify` 的出发前指引，语义时态错误但不影响证据字段或安全判定 | 无硬依赖；纯报告文案 Scope | `P2-SMOKE-REPORT-PHASE-COPY-001` | 事后报告只保留结果解释与下一步处置，出发前说明只出现在现场操作卡或命令前提示；报告安全守卫与现有结构化结果不变 | `scripts/smoke/runner.py::_build_report`；`tests/scripts/test_smoke_runner.py` 的报告文本断言 |
| schema digest 手填且后端不校验一致性 | UI 要求人工填写两份 digest，Admin 服务原样转 draft 入库；生产 `app/` 没有 canonical schema 重算/比对 | 无硬依赖，可独立；尚未排期 | Registry digest 一致性校验 lane | 后端按 canonical JSON 计算 digest 或拒绝不一致值；create/update/import 全路径覆盖，UI 不再是权威 digest 来源，并有 mismatch 负向测试 | `web/src/pages/admin/RegistryPage.tsx:292-305`；`app/admin/registry.py:50-76,214-220`；`docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md:4755-4757`；`scripts/smoke/capabilities.py:30-37` |
| `@ant-design/x` 依赖策略理由过期 | 依赖策略仍写 antd 5 不兼容，但现役基线已是 antd 6；真实阻塞只剩未获批准的企业镜像或离线缓存 | 无硬依赖；**不阻塞 `P2-FE-WORKBENCH-001`**，其首版 Dock 只用 antd 6 原生组件 | 引入 `@ant-design/x` 的前端棒 | exclusion_reason 更新为当前真实阻塞，或整行移入允许表，且 `scripts/check_dependencies.py` 通过 | `docs/dev/dependency_policy.md` 的 `@ant-design/x` 行；`docs/phase2/DECISIONS.md` 2026-08-27「工作台首版 AI Dock 不使用 `@ant-design/x`」 |
| `UserAction` 无 reject / cancel，且 Runtime 无对应拒绝终态 | 现役合同只支持 confirm；`HumanGatePort` 可表达 `rejected`，但 Runtime 无拒绝入口与对应 Task 终态 | 需先裁定用户拒绝后的 Task 终态；不得顺带扩展已落地单值合同 | `P2-USER-ACTION-REJECT-001` | 以获批判别式合同端到端支持 reject / cancel，决定记录、Task 终态与 Trace 同棒闭合，负向路径保持 fail-closed | `app/contracts/sdui/models.py::UserAction`；`app/ports/human_gate.py::HumanGateDecisionRecord`；`app/ports/runtime.py::RuntimePort.handle_user_action` |
| 薄查询层尚差第二个真实生产消费者 | `QueryTable` / `useTableQuery` 已落地，分页、筛选、排序均有测试，但严格 Scope 下只有 `WorkObjectsPage` 消费；为凑第二页去改三个 Admin 页会越界。`LightweightTable` 经核实从未承担生产职责，只是两个 Admin 测试的 antd `Table` mock | 无硬依赖；待 GOV-SYNC 把任一第二个真实生产列表页面绑定到明确 task_id | `P2-FE-WORKBENCH-001` | 除 `WorkObjectsPage` 外，至少一个真实生产页面直接消费 `QueryTable` / `useTableQuery`，并有分页、筛选、排序的改动路径测试；同时 `LightweightTable` 继续仅限测试 mock、生产引用数为 0。满足后才关闭 | `web/src/shared/ui/QueryTable.tsx`、`web/src/shared/query/useTableQuery.ts`、`web/src/shared/ui/__tests__/QueryTable.test.tsx`；`rg -n "LightweightTable" web/src` 仅命中其自身和两个 Admin 测试 |
| RJSF 紧凑密度适用性未验 | 2026-08-14 RJSF + antd 6 PoC 结论为「有条件通」，验证矩阵只覆盖标准密度表单；2026-08-18「前端技术栈与多 Surface 渲染的衔接」要求三种密度分别渲染，紧凑密度下 RJSF 的适用性未验证 | 无硬依赖 | 引入 RJSF 的前端棒 | 紧凑密度下 RJSF 的可用性有实测结论，或明确裁定紧凑密度不使用 RJSF | `docs/phase2/DECISIONS.md` 2026-08-18「前端 ADR 草案不进仓库，硬约束就地固化」第 6 项与同日「前端技术栈与多 Surface 渲染的衔接」 |
| secure context 两条取得路径内网均未实测 | File System Access、Service Worker / PWA 安装与 `navigator.clipboard` 需 secure context；取得途径有「HTTPS + 内部 CA」与「Chrome 企业策略 `OverrideSecurityRestrictionsOnInsecureOrigin`」两条，均未在内网实测 | 需内网现场 | 本地 capability provider 或 PWA 相关棒 | 内网机器上 `window.isSecureContext` 与 `'showOpenFilePicker' in window` 均为 `true`，或明确裁定放弃这三项能力 | `docs/phase2/DECISIONS.md` 2026-08-18「本地 capability provider 的边界澄清」 |
| 其余 SDUI 卡片缺生产可达性与 payload 合同 | `binding_required_card` 无 Runtime 产出分支；`operator_handback_card` / `binding_required_card` 的生产 payload 仍为空 | 无硬依赖 | 待 GOV-SYNC 分配 | 两种卡都有获批的生产可达性与 payload 合同及端到端测试，或明确裁定移除/不需要；不得把参数值或凭证带入 ResponseEnvelope | `app/runtime/runtime.py::_build_envelope`；`app/infra/sdui/response_envelope_builder.py` |
| resume 再次确认缺不可变人话摘要与字段名 | `_PendingWorkflow` 只保留 capability_id；恢复后再次 `waiting_user` 的 `operation_summary` 与 `field_names` 会静默降级为空 | pending 状态如何安全保留或重新取得不可变 Capability 展示元数据 | `P2-CONFIRM-RESUME-001` | resume 后再次确认从确定来源得到与原 capability 绑定的非空人话摘要和正确字段名，不新增反向依赖、不带出参数值 | `app/runtime/runtime.py::_PendingWorkflow`、`_resume_pending_workflow`、`_operation_summary`、`_build_envelope` |
| `datakey` / `data_key` 凭证 marker 层间不对称 | Response Envelope builder 的 `_CREDENTIAL_MARKER` 与 Golden 判卷侧的凭证字段名数据源对 `datakey` / `data_key` 的覆盖不一致，同一凭证形态在不同层的脱敏结果可能不同 | 无硬依赖；跨层凭证 pattern 统一需独立 Scope | 待 GOV-SYNC 分配 | 两侧对同一组凭证字段名的覆盖一致，并有跨层一致性测试；不得通过放宽任一侧达成一致 | `app/infra/sdui/response_envelope_builder.py` 的 `_CREDENTIAL_MARKER`；`scripts/golden_task_assertions.py` 的 OA 凭证字段名数据源；`_scratch/P2-SDUI-CONFIRM-PAYLOAD-001_报告.md` 的非阻断观察 |
| Work Object 列表截断的可见集合不确定 | `list_for_assignee` 为 `LIMIT 201` 且无 `ORDER BY`（DB 测试刻意断言无 `ORDER BY`，以避开 D-6 的服务端排序停点）。超过 200 条时具体呈现哪 200 条在请求间可能变化；溢出横幅使截断显式可见，非静默失真，但被隐藏的集合是任意的 | 与服务端排序合同同属一个设计面，D-6 已裁定首版不做 | 出现第二个真实列表消费者、需抽取共享查询层时一并处理 | 列表具备确定性排序，或经裁定在当前数据规模下无需处理并记录理由 | `app/infra/persistence/work_object/postgresql.py::list_for_assignee`；`tests/db/test_work_object_migration.py`；PR #100 body |
| OA 中已办结的事项在本系统永不老化 | 后台轮询已能定时拉取并 upsert 当前 pending 载荷，但 OA 已办结事项从载荷消失后，现行存储仍保留最后快照与越来越旧的 `source_fetched_at`，不自动清理或标记。符合「只存上次看到的样子」的既有裁定且界面显示数据时间，但尚无消失项对账语义 | 需先裁定消失项应老化、归档还是明确标记；本棒不得代替产品语义裁决 | 待 GOV-SYNC 分配，不自行造 task_id | 本系统能识别在 OA 侧已消失的事项并给出获批呈现，不再无限期把陈旧快照当作当前 pending 项 | `app/credential_polling.py::CredentialPollingService`；`app/api/v1/work_objects.py::WorkObjectService.sync_for_background`；`docs/phase2/DECISIONS.md` 2026-08-19「Work Object 与 OA 的状态同步策略」第 3 项 |
| 外键在 `tasks` 侧安装 RI 触发器（红线边界情况，记录用） | `work_objects` 对 `tasks` 建立外键引用，迁移中无 `ALTER TABLE tasks`，但 PostgreSQL 会在被引用侧安装引用完整性触发器。按「只准新增；ALTER/DROP 现存表是停点」的严格读法值得留痕。**判定为不违规**：`tasks` 的列与约束一个未动，downgrade 可干净移除 | 无；本条为边界情况记录，非待办 | 无需激活；后续棒遇同类外键可直接引用本条判定 | 该红线条款被显式澄清（明确外键引用是否属「新增」），或经复核确认无需澄清 | `alembic/versions/*_work_objects.py`；PR #100 body |
| `system_scope` 执行身份缺少独立生产路径，海康开工会撞上 | 冻结蓝图要求 system_scope（部门级共享账号）「不要求用户绑定、只做资源 Policy」，但现行生产路径中 system_scope 同样走 `IdentityMappingPort` 的 active 检查，且 `PostgreSQLOAIdentityMapping` 只接受 `target_system == "oa"` 且 `execution_identity == "user_delegated"`。海康 iVMS 正是部门级共享账号（蓝图 §15.3 预判为 `system_credential_with_policy` + Policy Guard 控制设备/区域），按当前实现无法通过身份检查 | 需与 `P2-HIKVISION-ADAPTER-001` 的外部输入一并设计；改动触及 Gateway 身份检查分支，属 A 档 | `P2-HIKVISION-ADAPTER-001` 开工前确认，不得开工后才发现 | system_scope 能力在不要求用户绑定的前提下通过 Gateway，由 Policy Guard 承担设备与区域范围控制，并有负向测试证明越域被拒 | `app/infra/gateway/capability_gateway.py::CapabilityGateway.execute`；`app/infra/identity/postgresql.py::PostgreSQLOAIdentityMapping`；`_scratch/账号映射覆盖核查_结论.md` A33 |
| `reset_mapping` 与 `revoke_mapping` 生产实现完全相同，设计意图待核实 | 两者均调用 `_mutate_mapping`，都将 `revoked_at` 置为当前时间并返回 `_revoked_result`，Admin 页面上两个按钮效果无差别。语义上二者本应不同——撤销通常意味着永久作废、重置意味着允许用户立即重新绑定。**当前不产生错误行为**，可能是本阶段有意等价、API 先留位；在不知设计意图的情况下修改反而可能改错 | 需确认当初分设两个 API 的设计意图 | 待确认意图后决定是否修正；若确认应有区别，由凭证相关棒承接 | 二者语义差异被明确（或明确记录为有意等价），且 Admin 界面的按钮行为与语义一致 | `app/infra/identity/postgresql.py::revoke_mapping`、`reset_mapping`、`_mutate_mapping`；`_scratch/账号映射覆盖核查_结论.md` A25 |
| 「当前步骤」只由 `handling_mark` 承担，OA `source_status` 未纳入投影判定 | `source_status` 是 OA 返回的自由文本，不可枚举、不可校验；让它参与办理入口分流会形成拼写变化即可 fail-open 的安全分流 | 缺少可枚举且可真实校验的 OA 当前步骤协议事实；现有自由文本不得作为安全输入 | 未来取得稳定 OA 步骤枚举与协议合同的独立 A 类任务 | 有协议事实支撑的步骤值进入严格类型与映射，未知值 fail-closed，并有拼写漂移、未知值负向测试 | `app/ports/work_object_handling.py::project_handling_action` 明确不接收 `source_status`；`tests/ports/test_work_object_handling.py` |
| 四选一真实动作仍未接线 | `P2-FE-WORKBENCH-001` 只消费后端确定性 `handling_action` 投影并提供动作承载位；跳转、AI 起草、自助办理需要各自获批合同、授权与错误处理，当前仍不得顺手接线 | 待 GOV-SYNC 为获批的跳转、起草、自助、只读动作合同分配并绑定 task_id | `P2-FE-WORKBENCH-001` | 四个 action 分别接入获批的跳转、起草、自助或只读说明路径，并以端到端测试证明前端只消费后端投影、不自行重判 | `web/src/pages/WorkObjectsPage.tsx` 当前四种文案统一打开详情承载位；`web/src/pages/__tests__/WorkObjectsPage.test.tsx` 证明每行只渲染一个后端投影动作 |
| 现有能力全部保持默认 `manual`，生产数据尚不覆盖 `ai_draft` / `self_serve` | fail-closed 初值要求本棒不擅自提升既有 capability 自动化程度，也不虚构办理映射；两条路径当前仅由测试构造覆盖 | 尚无获批的低风险写入 capability 及其精确 Work Object selector | `P2-LOW-RISK-WRITE-001` | 至少一个生产 capability 经独立审批声明 `assisted` 或 `full` 并具备 exact selector，真实数据覆盖对应入口且安全负向门禁通过 | `scripts/manage_oa_capabilities.py` 的 canonical specs 保持三个新字段默认值；`tests/api/test_work_objects.py` 的 `ai_draft` / `self_serve` 构造测试 |
| Capability 审计边界指纹已分层为旧字段集与全字段两族 | 四组历史 hex 是对当时生产行旧字段的人工批准观测；未来增删 `CapabilitySpec` 字段若静默沿用任一族，会让历史识别停摆或让新字段脱离变更检测 | 无当前阻塞；未来任何字段变更都必须先显式裁定审计边界 | 任一增删 `CapabilitySpec` 字段的任务（具体 task_id 随该棒确定） | 每次字段变更都明确决定历史识别字段集与全字段变更检测的归属，四组人工批准常量不由新代码自证重算，前向守卫同步更新 | `scripts/manage_oa_capabilities.py::_LEGACY_FINGERPRINT_FIELDS`、`_legacy_exact_capability_fingerprint`、`_exact_capability_fingerprint`；`tests/scripts/test_manage_oa_capabilities.py::test_legacy_fingerprint_field_set_and_capability_fields_are_explicit` |
| 后台本地故障缺少独立持久终态 | `mark_non_counted_failure` 对数据库、解密或未知本地故障只保留 `poll_failure_count` 并进入 `retrying`；这些故障不得冒充四类可计数上游失败，也不能误标为密码无效或借 `revoked_at` 撤销用户 Session。当前获批 schema 没有能准确表达该状态的终态，因此固定基础间隔的 fail-closed 重试可能长期持续 | 新增独立本地故障终态所需的凭证表 schema 专项授权与用户告警语义，当前未获；不得在本棒追加 migration | 待 GOV-SYNC 分配本地故障终态棒 | 独立的非认证本地故障状态与告警合同获批并持久化；数据库/解密/未知故障停止无人值守重试且不累加 `poll_failure_count`、不写认证 `invalid`、不撤销 Session | `app/credential_polling.py::CredentialPollingService._run_candidate`；`app/infra/auth/postgresql.py::mark_non_counted_failure`；P2-OA-CREDENTIAL-POLL-001 第二轮 Opus finding Low #2 |
| 泛微官方应用 Token 接口尚在改造，暂不可用 | 官方 Token 认证（`appid` + 应用密钥 + 系统公钥换短期令牌，业务接口携带加密 `userid` 代表具体用户读数据）是比保存用户密码更干净的路径，且与既有 `IdentityMappingPort` 天然契合。雨爷 2026-08-19 确认许可证可以拿到，但相关接口仍在改造中 | OA 侧接口改造进度，非本系统可控 | 接口改造完成后重新评估切换；`P2-OA-CREDENTIAL-POLL-001` 须为此留凭证获取接缝 | 切换到官方 Token 后不再保存用户 OA 密码，且切换只改凭证获取实现、不改调用方 | `_scratch/OA凭证与轮询可行性_结论.md` B 节第 7、8 项；`docs/phase2/DECISIONS.md` 2026-08-19「凭证模型变更」的过渡方案段 |
| 解绑后重新绑定时撤销来源不可区分 | `invalid → unbind → rebind` 序列无法区分撤销源自用户解绑、密码失效还是管理员撤销，重新绑定语义存在歧义 | 需先定撤销来源是否持久区分 | 待 GOV-SYNC 裁决归属棒 | 撤销记录能区分来源，或明确裁定不区分并说明重新绑定的确定行为 | `P2-OA-CREDENTIAL-POLL-001` 对应 PR 的 Opus 非阻断 finding |
| 部分 Identity 错误被宽泛归为认证否定 | 并非所有 Identity 层错误都等价于凭证错误，宽泛归类可能让可重试故障误入一次失败即终态路径，或反之 | 需逐类枚举 Identity 错误并定分类 | 待 GOV-SYNC 裁决归属棒 | Identity 错误分类逐项枚举，认证否定与可重试故障边界有测试 | `P2-OA-CREDENTIAL-POLL-001` 对应 PR 的 Opus 非阻断 finding |
| 跨层不变量 #3（引用不传递权限）的适用面未裁定 | 任务派发方案 §13.4 自行收窄：定下「卡片若只打开详情则不签发 `action_ref`」「普通详情 REST 动作不要求 `action_ref`」。而 `ARCHITECTURE.md` §3 第 3 条（源自 2026-08-18「卡片动作的授权边界」）对卡片动作不区分读写。核心不变量适用面的解释按 `AGENTS.md` 是停点，实现棒不得自行收窄 | 归 GOV-SYNC 裁决，不得在实现棒内决定 | 待 GOV-SYNC 立项 | 只读卡片动作与详情 REST 动作是否豁免 `action_ref` 有明确裁定并回写 `ARCHITECTURE.md` | `_scratch/P2-GOV-SYNC-023_待落盘裁决.md` 二、第 13 项 |
| cursor 分页合同未做 | 2026-08-27 从 `P2-INTERNAL-WO-MODEL-001` 移出：cursor 需绑定 `AuthorizedWorkObjectScope` 摘要，而该对象是 `SCOPE-001` 的产物，放在第一棒属倒置依赖；且 D-6 已裁定首版不做服务端分页 | 无阻塞（当前为有界批次，够用） | `P2-INTERNAL-WO-SCOPE-001` 完成后重新评估 | 出现真实分页需求且 scope 对象已存在，或明确裁定长期不做 | `docs/phase2/DECISIONS.md` 2026-08-27「cursor 分页合同移出 MODEL-001」 |
| `msw` 未获依赖治理层批准 | `ARCHITECTURE.md` 的「已定未装」属架构选择层；`docs/dev/dependency_policy.md` 的 npm allowlist 无 `msw`，亦无承载安装的 task_id。**不得据「架构已定」直接安装** | 无阻塞——现有前端组件测试用 `vi.mock` 直接替换 Orval 生成客户端，contract-first 开发不需要它 | 真正需要网络层拦截的那一棒 | 该棒把 `msw` 写入 allowlist 并同步 manifest/lockfile，或裁定长期不引入 | `docs/phase2/DECISIONS.md` 2026-08-27「cursor 分页合同移出 MODEL-001」的顺带澄清；本行完整承载当前状态，相关讨论稿已弃置 |
| 问一问 / RAG 的数据查询安全边界尚未形成实施合同 | 目标态要求自然语言只选择获批指标与维度，经身份和行列级授权、可解释查询计划、受控 Data Capability / DB Gateway、结果校验后再由 AI 解释；大模型不得自由生成并执行生产 SQL，也不得发明业务口径 | 问一问 / RAG 属 P3，当前尚无正式 task_id；P2 排除知识库与 DB Gateway | 待 GOV-SYNC 分配 | 问一问 / RAG 棒以获批语义层和受控查询能力落地上述链路，未知指标、越权范围和自由 SQL 均 fail-closed | 本行完整承载目标态安全链与否定边界，相关讨论稿已弃置 |
| 会话持久化的最小元数据与可见性合同尚未裁定 | P2 明确不扩会话表、不建会话 API；P3 在实现历史列表和冷启动恢复前，仍需裁定稳定标识、owner、purpose、visibility、organization scope、context bindings、allowed capabilities、classification、source surface、retention policy 等概念字段及权限重验。目标态 schema 须容纳**会话切换与附件引用**，不得只做「列表 + 继续」的最小形态 | P3 会话持久化尚无正式 task_id | 待 GOV-SYNC 分配 | P3 会话持久化棒先裁定最小元数据、默认私人、引用权限重验与保留策略，并按「会话切换 + 附件绑定」设计 schema，再实现列表和从 `sessionId` 冷启动恢复；讨论字段名不得直接冒充获批 schema | 本行完整承载概念字段、权限重验与非批准 schema 边界，相关讨论稿已弃置；`docs/phase2/DECISIONS.md` 2026-08-27「前端信息架构与终态导航」「落地页目标态形态」 |
| 录制功能缺少 Local Worker 信任合同 | 现有 G-1 只确认本地 provider 的形态与用途；主动注册 Gateway、合法 Gateway 签名任务、本地预注册 Capability 白名单、禁止通用命令执行，以及失联 / 版本过旧 / 签名失败 / 清单不一致时拒绝下发等合同仍一条未落。此前实现本地抓包会形成没有信任边界的本地执行端点 | G-1 最小信任合同与 Gateway 接线尚未裁定 | 录制功能那一棒（待 GOV-SYNC 分配） | Local Worker 的注册、签名、白名单、失效与拒绝合同获批并有负向测试；录制路径只能经合法 Gateway / Policy / Trace / Evidence，且不存在通用命令执行端点 | `docs/phase2/ARCHITECTURE.md` §7.3 G-1、§8 E-4；`docs/phase2/DECISIONS.md` 2026-08-27「外部系统接入、录制路径与任务交办插槽应用」 |
| 浏览器扩展作为录制入口的信任边界未裁决 | host permissions 可读取获批域名下的全部内容，覆盖面比 Local Capability Provider 更宽；麒麟 V10 + Chrome 101 的分发、更新、吊销未验证，且扩展如何服从 Identity / Policy / Gateway / Trace / Evidence 尚未定义。G-1 当前仍完全空白，不能把扩展当设计前提 | Local Worker 信任合同 G-1 与浏览器扩展专项架构裁决 | 待 GOV-SYNC 裁决 | 明确批准或排除浏览器扩展；若批准，须限定 host permissions、建立分发/更新/吊销与 Gateway 全链合同，并以敏感内容不进入模型及越权拒绝的负向测试证明 | `docs/phase2/ARCHITECTURE.md` §7.3 G-1；`docs/phase2/DECISIONS.md` 2026-08-27「外部系统接入、录制路径与任务交办插槽应用」 |
| 低风险写入真实确认链与业务预览仍未闭合 | 当前生产 OA 能力不可产出真实确认卡；业务对象预览键的名称、结构、折叠语义与凭证扫描接入未定。仓库未发现 HAR 专用清洗脚本，未脱敏素材仍须单次人工授权读取；但该脚本不是当前唯一阻塞 | OA 审批提交协议结构未知；不得读取未获授权的原始素材 | `P2-LOW-RISK-WRITE-001`，完成后由 `P2-GOLDEN-002` 冻结结构化 action 路径 | 真实 OA 审批同意产出确认卡，业务预览键经设计并纳入凭证守卫，结构化 action 完成现场验收与 Golden；是否需要 HAR 分析在开棒前按现役素材规则重新判断 | `app/infra/policy/minimal_policy_guard.py::MinimalPolicyGuard.decide`；`app/runtime/models.py::ConfirmCardPayload`；`AGENTS.md` 不可协商规则 9 |
| 用户反馈闭环无生产面 | `app/` 当前无 `feedback` 生产符号，蓝图 §13 仍把最小用户反馈与基础统计列为 Phase 2 待交付；该面不是被技术前置阻塞，而是尚未开工，本棒不得顺带实现 | 无（未开工） | `P2-FEEDBACK-LOOP-001` | 最小反馈写入、用户隔离、基础统计与负向测试按获批 Scope 落地，且不自动生成建设 backlog | `rg -n -i feedback app -g '*.py'` 无匹配；蓝图 §7.6、§13；本文件「试点运营面」 |
| Admin 审计读取角色与对象授权缺失 | 独立盘点结论为 C：四个证据读取动作只认 `admin`；`app/api/v1/admin.py::_request_context` 丢弃 `app/ports/auth.py::Principal.org_ctx`，`app/admin/registry.py::AdminRegistryService._authorize` 构造的管理面 Policy 上下文只有角色。实施复核又确认调用者 `Principal.org_ctx` 只能证明自身租户，`app/ports/task_store.py::TaskRecord` 无租户字段，仓库不存在从目标 task/session/user 标识解析可信目标租户的服务端来源；同租户跨用户正向与跨租户同标识负向在归属落地前不能同时满足，首次开棒已零改动停手 | 可信目标租户来源未定，恢复路径待雨爷裁定 | `P2-AUDIT-READ-AUTHZ-001` | `admin` 单独访问四个证据动作均在资源前 403；独立只读审计角色只通过读取动作门且不获得管理动作；`app/ports/auth.py::Principal.org_ctx` 原样进入 `app/admin/registry.py::AdminRequestContext`，可信主体租户进入 `app/ports/policy_guard.py::ManagementPlanePolicyContext`；目标租户具备服务端可信来源后，同租户跨用户读取成功且跨租户同标识不可见，调用方标识不得成为授权 scope | `docs/phase2/DECISIONS.md` 2026-08-30「Admin 审计读取面的对象级授权模型」；`app/api/v1/admin.py::_request_context`；`app/admin/registry.py::AdminRequestContext`、`app/admin/registry.py::AdminRegistryService._authorize`；`app/ports/auth.py::PrincipalOrgContext`；`app/ports/task_store.py::TaskRecord`；`app/ports/policy_guard.py::ManagementPlanePolicyContext`；`app/infra/policy/minimal_policy_guard.py::MinimalPolicyGuard.decide` |
| Trace 持久读取模型无可信归属，响应顶层字段未 fail-closed | `app/ports/trace.py::TraceEvent`、`app/ports/trace.py::TracePersistedEvent` 与现役 Trace 表均无可信租户/用户归属；`app/infra/observability/postgresql_trace.py::PostgreSQLTraceReader._list_events` 只按调用方资源标识过滤；service-only Task 关联覆盖不了 `app/runtime/runtime.py::RuntimeImpl.handle_user_action` 生成的无 Task 动作 Trace；`app/admin/evidence.py::AdminTracePersistedView.from_record` 只清洗属性映射，顶层自由字符串仍原样返回 | `P2-AUDIT-READ-AUTHZ-001`；另需 Trace 表 schema 变更专项授权与历史行回填专项授权，两项均尚未获得 | `P2-AUDIT-TRACE-SCOPE-001` | 新 Trace 由可信执行上下文固化非空租户/用户归属；`app/ports/trace.py::TraceQueryPort` 三种查询均强制可信租户 scope，reader SQL 无条件带租户谓词且同租户跨用户可读、跨租户不可见；历史不可归属行 fail-closed，回填不得猜默认值或删除无法归属行；顶层凭证形状在构造或写入前拒绝；原始 reader 结果不得直接外露 | `docs/phase2/DECISIONS.md` 2026-08-30「Admin 审计读取面的对象级授权模型」；`app/ports/trace.py::TraceEvent`、`app/ports/trace.py::TracePersistedEvent`、`app/ports/trace.py::TraceQueryPort`；`app/infra/observability/postgresql_trace.py::PostgreSQLTraceWriter.record_event`、`app/infra/observability/postgresql_trace.py::PostgreSQLTraceReader._list_events`；`app/runtime/runtime.py::RuntimeImpl.handle_user_action`；`app/admin/evidence.py::AdminTracePersistedView.from_record`；`alembic/versions/20260723_090000_trace_events.py::upgrade` |
| 冻结守卫对新增题面的保护不对称 | `test_frozen_and_append_only_goldens_all_pass` 以字面 `_PRE_GOLDEN_001_FROZEN` 保护 `P2-GOLDEN-001` 之前的 25 题，同时删 id 与 fixture 会变红；GT-029～GT-033 不在该字面集合内，同时删 id 与文件时该守卫仍绿（现由 `test_fixture_schema.py` 字面列表与 `test_runner_cli.py` 计数在同包内兜住）。Opus 评审非阻断发现 | 无（不被任务阻塞） | 下一个新增 GT 题面的棒 | 新增题面与既有题面受同等强度的「同时删 id 与文件即变红」保护，且守卫仍为字面集合、不用推导 | `tests/golden_tasks/test_golden_tasks.py` 的 `_PRE_GOLDEN_001_FROZEN`；`P2-GOLDEN-001` Opus 评审非阻断意见第 1 条 |
| GT-030 只覆盖 allowlist 为空的变体 | GT-030 以「不声明 `displayable_argument_fields`（默认空集）」违反 allowlist 条件，未覆盖「allowlist 非空但不含该字段」。二者违反同一条件且反事实成立，但后者能额外杀掉「allowlist 非空即全展示」这一变异，当前仅由 GT-033 间接兜住。Opus 评审非阻断发现 | 无（不被任务阻塞） | `P2-GOLDEN-002` 或下一个触及 allowlist 的棒 | 存在覆盖「allowlist 非空但不含该字段」的题面，或以变异测试证明现有题面已杀掉该变异 | `tests/golden_tasks/fixtures/GT-030.json`；`P2-GOLDEN-001` Opus 评审非阻断意见第 3 条 |
| 组织身份无来源，租户维度全链未启用（**第二租户的硬前置**） | 病根是 `app/infra/auth/oa.py` 以 `PrincipalOrgContext()` 空构造用户身份，取默认值 `"default"`，全仓无任何真实租户来源；`app/runtime/runtime.py::RuntimeImpl.handle_user_message` 的 `tenant_id="default"` 只是下游症状。session HMAC 不绑 tenant，`TaskRecord` / `SessionRecord` 无 tenant，pending 与 claim 键亦不含 tenant。当前用户、会话、request digest、binding manifest 四层隔离均在，**为零的只是租户这一维** | 无（不被任务阻塞） | `P2-TENANT-IDENTITY-001` | 认证适配器取得真实组织身份且租户维度全链启用并有跨租户负向测试。**在此之前不得启用第二个租户**——否则相同 `ai_user_id` 与 session 组合可能共用 pending 键，轻则互相顶掉待确认，重则旧的裸确认文本命中另一租户的 pending | `app/infra/auth/oa.py` 的 `PrincipalOrgContext()` 空构造；`app/ports/auth.py::PrincipalOrgContext.tenant_id` 默认 `"default"`；`app/infra/human_gate/in_memory.py::_decision_matches_request` 与 `app/infra/human_gate/postgresql.py::PostgreSQLHumanGate.record_decision` 的严格 tenant 比较 |
| pending 与 Workflow checkpoint 跨进程不存活，且尚无正式 generation 状态机 | 本棒只在现有进程内为 `_pending_workflows` 的发布、三处删除与再次等待替换补对象身份 CAS；进程重启后未过期确认仍安全地退化为 `no_pending_action`，但可用性与统一代际迁移仍未闭合；**另：若适配器异常穿透 `handle_user_action`，该 pending 与其 claim key 会永久保留，致该会话后续所有新 workflow 发布被 claim 守卫拒绝为 `internal_error` 直至进程重启**；`_claimed_pending_confirmations` 亦按进程无界增长（每次结构化动作一条，永不释放），该增长是「claim 一次性」不变量的直接代价，须与持久化方案一并解决 | `P2-USER-ACTION-SEAM-001` 已完成进程内五站点 CAS | `P2-CONFIRM-DURABILITY-001` | pending / checkpoint 跨进程持久化，所有同键状态迁移由正式 generation 合同原子约束，claim 记录具备有界生命周期（过期或随终态释放）且不破坏一次性语义，并有重启与竞态回归 | `app/runtime/runtime.py::_pending_workflows`、`_publish_pending_workflow`、`_compare_and_swap_pending_workflow`；`tests/runtime/test_runtime_user_action.py` 的五站点竞态回归 |
| Golden harness 不驱动结构化 action 路径 | 现有 Golden evaluator 只调用 `RuntimeImpl.handle_user_message`；本棒 Golden Gate 只能证明既有自由文本路径未回归，对新增 `/runtime/action` 与 `handle_user_action` 零覆盖 | 低风险写入路径尚未落地，且本棒禁止改 Golden fixture / evaluator | `P2-GOLDEN-002` | 获批的增量 Golden 通过真实结构化 action 入口冻结低风险写入的正向、负向与边界，且不改写 `P2-GOLDEN-001` 题面 | `scripts/golden_task_evaluator.py` 的 `handle_user_message` 调用；`app/runtime/runtime.py::RuntimeImpl.handle_user_action`；`P2-USER-ACTION-SEAM-001` 验证声明 |
| 结构化 action 的 claim 后拒绝分支不终结底层 Task | `handle_user_action` 在 `_ActionStaleError` / `_ActionAlreadyClaimedError` / `VersionBindingMismatchError` 三条拒绝分支上只记 action Trace，不像自由文本路径那样调用 `_finish_version_binding_failure`；pending 已弹出、checkpoint 已丢弃，但原 Task 仍停在 `running` 且其 trace 从未终结。同一缺口的另一面：action Trace 是独立两事件链路，其合成 `task_id` 从不写入 `tasks` 表（`trace_events` 无外键故可持久化），审计时无法由该 task_id 回溯到任务记录。属审计与生命周期缺口，不是安全缺口。`P2-USER-ACTION-SEAM-001` Opus 评审非阻断发现 3 | 无（不被任务阻塞） | `P2-CONFIRM-RESUME-001` | 结构化拒绝分支与自由文本路径的 Task 终态和 trace 终结行为一致，并有断言 Task 不再停留 `running` 的回归 | `app/runtime/runtime.py::RuntimeImpl.handle_user_action` 的拒绝分支；同文件 `_finish_version_binding_failure` |
| 损坏 binding payload 由优雅信封退化为 500 | 本棒为拆开 `action_stale` 与 `action_version_conflict`，把 `_resume_pending_workflow` 外层 `except` 收窄为只捕获三类 action 异常；该 `try` 内仅存的 `HumanGateConflictError` 生产者是 PG `_get_manifest` 读到损坏 `bindings` 负载，现在会逸出为 500 且不弹 pending、不丢 checkpoint，此前会得到优雅的版本绑定信封。病态路径，改动前后均无测试。`P2-USER-ACTION-SEAM-001` Opus 评审非阻断发现 4 | 无（不被任务阻塞） | `P2-CONFIRM-DURABILITY-001` | 损坏 binding payload 得到确定性的 fail-closed 信封（而非 500），pending 与 checkpoint 一致清理，并有针对该路径的负向测试 | `app/runtime/runtime.py::_resume_pending_workflow` 的外层 `except`；`app/infra/human_gate/postgresql.py::_get_manifest` |
| `P2-SDUI-RENDERER-002` 四项收尾 | 安全导航合同、target_system 单一权威和记录计数降级口径已于 2026-08-30 裁定；待实现 OA 链接 origin + 路径段前缀白名单、payload target 闭集/一致性、部分列表原因与下一步、确认卡合法 `<dl>` | `P2-SDUI-SCHEMA-001`（先完成具名合同与 Orval 生成物；不再因“无人裁决” BLOCKED） | `P2-SDUI-RENDERER-002` | 绝对 HTTP(S) 与根相对 OA 链接只按部署白名单放行，`//host`、`/\host`、`\host`、非 HTTP(S)、路径穿越、编码/控制字符和后缀嫁接反证齐全；拒绝时保留记录并给原因 + 下一步；payload target 复用生成闭集且 ui/payload 冲突 fail-closed；计数不一致展示合法行但醒目标不完整；`<dl>` 结构合法 | `web/src/contracts/runtimeProjection.ts::projectConfirmCard`、`web/src/contracts/runtimeProjection.ts::projectPendingWorkflows`、`web/src/contracts/runtimeProjection.ts::projectSystemMessages`；`web/src/components/RecordsList.tsx::RecordsList`；`web/src/components/ConfirmCard.tsx::ConfirmCard`；`docs/phase2/DECISIONS.md` 2026-08-27「低数字素养用户的界面硬约束」、2026-08-30「`CapabilitySpec.output_schema` 是规范化输出暨 `ResponseEnvelope` 外露合同」的渲染边界段、2026-08-30「SDUI 导航、跨语言合同与两棒排期」 |
| 契约层反向依赖 Runtime 层且无守卫 | `app/contracts/sdui/models.py` 现 import `app/runtime/models.py::ConfirmCardPayload`，使 `app/ports/response_envelope.py` 传递依赖 `app.runtime`；今日无环，但架构门禁未覆盖 `app.contracts` 的依赖方向 | 无（不被任务阻塞） | `P2-SDUI-RENDERER-002` | `tests/architecture/test_import_boundaries.py` 覆盖 `app.contracts` 的依赖方向，且故意制造 `app.runtime` 到 `app.contracts.sdui` 的反向 import 会使测试变红 | `app/contracts/sdui/models.py` 的 `from app.runtime.models import ConfirmCardPayload`；`tests/architecture/test_import_boundaries.py` 现有规则集不含 `app.contracts` |
| `build_confirm_card` 的 payload 形参已不可能成功 | `app/infra/sdui/response_envelope_builder.py::build_confirm_card` 仍声明 `payload: dict \| None = None` 并执行 `payload or {}`；空 payload 已必然校验失败，再被兜底异常处理静默降级为失败信封 | 无（不被任务阻塞） | `P2-SDUI-RENDERER-002` | `build_confirm_card` 的 payload 成为必填参数或具备等价类型约束，使误用在类型检查期暴露而非运行期静默降级；并有测试证明缺 payload 的调用不再走失败信封路径 | `app/infra/sdui/response_envelope_builder.py::build_confirm_card` 的签名、`payload or {}` 与兜底异常处理 |
| `/action` 对不合规 data 返回 500 | `app/api/v1/runtime.py` 的 `ActionResponseEnvelope.model_validate(envelope.model_dump())` 在 `data` 不是恰好两键时抛未捕获 `ValidationError`；现役生产路径不可达，但病态 Runtime 实现接入后会得到 500 而非确定性信封 | 无（不被任务阻塞） | `P2-SDUI-RENDERER-002` | 不合规 action data 得到确定性 fail-closed 信封而非 500，并有针对该路径的负向测试 | `app/api/v1/runtime.py` 的 `ActionResponseEnvelope.model_validate(...)`；`tests/runtime/test_runtime_response_contract.py::BaselineRuntime.handle_user_action` |
| `UIComponentType` 成为悬空权威 | `app/contracts/sdui/models.py::UIComponentType` 仍含 `confirm_card`，但已不再作为任何模型字段的类型，同时仍经 `app/ports/response_envelope.py::__all__` 对外导出 | 无（不被任务阻塞） | `P2-SDUI-RENDERER-002` | `UIComponentType` 重新成为实际类型权威或从导出面移除；导出的闭集与实际可用值保持一致 | `app/contracts/sdui/models.py::UIComponentType`；`app/ports/response_envelope.py::__all__` |
| `UIComponent` 与 `ConfirmCard` 无公共字段直接守卫 | `ConfirmCard` 不再继承 `UIComponent`，`target_system`、`reason_code`、`action` 等公共字段改为复制；现有精确字段与生成物守卫只能间接发现单边漂移 | 无（不被任务阻塞） | `P2-SDUI-RENDERER-002` | 存在直接断言两模型公共字段集一致的守卫，且单边加字段会使其变红 | `app/contracts/sdui/models.py::UIComponent`；`app/contracts/sdui/models.py::ConfirmCard` |
| outcome 守卫方向性测试作用于 helper 而非合同文件 | `test_outcome_sync_guard_*` 对合成元组调用 `_assert_outcome_sequences_match`，没有直接作用于真实前后端合同文件；逻辑成立，但与真实合同读取路径隔了一层 | 无（不被任务阻塞） | `P2-SDUI-RENDERER-002` | 方向性用例直接作用于真实合同文件内容，或有等价证据证明 helper 与文件读取路径同源 | `tests/contracts/test_user_action_outcome_sync.py` 中 `_assert_outcome_sequences_match` 的调用方式 |
| Action 响应负向测试变量名误导 | `tests/runtime/test_runtime_api.py::test_action_response_envelope_rejects_missing_extra_or_flattened_data` 中名为 `valid_envelope` 的变量实际承载故意不合规的数据 | 无（不被任务阻塞） | `P2-SDUI-RENDERER-002` | 变量名与其承载内容一致 | `tests/runtime/test_runtime_api.py::test_action_response_envelope_rejects_missing_extra_or_flattened_data` |
| `InMemoryHumanGate` 幂等语义比生产宽松 | `app/infra/human_gate/in_memory.py::InMemoryHumanGate.record_decision` 用对象相等判幂等（`existing == decision` 即静默返回旧对象），而生产 `app/infra/human_gate/postgresql.py::PostgreSQLHumanGate.record_decision` 用 `AND decision IS NULL` 谓词强制认领一次性。两条 decision 除 `decided_at` 外字段全同，本机相邻 `datetime.now(UTC)` 采样 99,894/100,000 对相等，故第二次认领被误判为幂等写入，`app/runtime/runtime.py::RuntimeImpl._resume_pending_workflow` 收不到 `HumanGateConflictError`，最终返回 `accepted`。fake 比生产宽松，该测试永远抓不到认领一次性的回归 | 无（不被任务阻塞） | 待 GOV-SYNC 分配 | `InMemoryHumanGate` 的认领一次性语义与生产 SQL 谓词等价（按 request_id 判是否已有 decision，而非按对象相等），且有确定性回归：两次 decision 的 `decided_at` 相同时仍必须得到 already-claimed | `app/infra/human_gate/in_memory.py::InMemoryHumanGate.record_decision`；`app/infra/human_gate/postgresql.py::PostgreSQLHumanGate.record_decision`；`app/runtime/runtime.py::RuntimeImpl._resume_pending_workflow`；`tests/runtime/test_runtime_user_action.py::test_existing_human_gate_decision_maps_to_already_claimed` |
| 凭证存储的宽泛 `except` 掩盖根因 | `app/infra/auth/postgresql.py::PostgreSQLCredentialStore.load` 用宽泛 `except Exception` 把驱动层异常统一包装成 `CredentialStoreError`。本机 Windows 默认 `ProactorEventLoop` 与 psycopg async 不兼容，`InterfaceError` 因此被伪装成「凭证无法加载」，排查时无法区分「连接建立失败」「行不存在」「解密失败」三种完全不同的原因 | 无（不被任务阻塞） | 待 GOV-SYNC 分配 | `load` 区分驱动/连接层异常与凭证层异常；对调用方的外层错误码不变（不泄露细节），但根因可从日志或异常链定位；并有测试证明连接失败不再被报成凭证无法加载 | `app/infra/auth/postgresql.py::PostgreSQLCredentialStore.load`；`tests/db/test_credential_password_polling_migration.py::test_existing_oa_session_survives_upgrade_and_remains_loadable` |
| 并行棒共用测试库跑 migration 会互相破坏 | 多根并行 lane 共用 `127.0.0.1:15432` 的 `eternalai_test`。任一 lane 跑 alembic 往返后，`alembic_version` 可能停在其他分支才有的版本，导致其余 lane 的 `tests/db/` 报 `CommandError: Can't locate revision`。2026-08-31 实际发生一次，五个用例受影响，协调窗口 downgrade 后恢复 | 无（不被任务阻塞） | 待 GOV-SYNC 分配 | 并行 lane 的 migration 测试互不干扰（独立库、独立 schema 或串行化），且有证明：一根 lane 跑完 alembic 往返后，其余 lane 的 `tests/db/` 仍全绿 | `scripts/reset_test_db.py`；`docs/phase2/DECISIONS.md` 2026-08-20「并行 lane 的测试库约束」与 2026-08-31「共享测试库未被污染，两个偶发失败是本机 Windows 环境缺陷」 |
| 监理棒被中止会把故障注入留在工作区 | 监理做门禁反证时会临时改生产代码再恢复。进程被中止或异常退出时，注入会残留——2026-08-31 实际发生**两次**（ORGDIR 分别残留 `departmentidspan` 字段与 SQL 关系谓词），均由协调窗口手动 `git checkout --` 发现并恢复。若未发现，下一根棒在该 worktree 跑出的测试结果不可信 | 无（不被任务阻塞） | 待 GOV-SYNC 分配 | 监理棒在首次注入前先记录可还原基线（如 `git stash` 或还原清单），使中止后可自动或一键恢复；并有证明：模拟中止后工作区能回到交付态 | `docs/phase2/DECISIONS.md` 2026-08-31「流程违规记录」第三节；两次残留的实测记录见本行 reason |
| Dock 落地页清空上下文后提示语残留 | `web/src/stores/aiDockStore.ts::clearPageContext` 在落地页留下「页面上下文已移除」提示；以干净会话重新进入 `/work-objects` 时走不清 notice 的分支，导致 Dock 一边显示「上下文已移除」一边实际已绑定工作事项。用户可见的状态错配，非安全问题。`P2-PAGE-CONTEXT-CONTRACT-001` Opus 最终轮非阻断发现 1 | 无（不被任务阻塞） | 待 GOV-SYNC 分配 | 重新绑定页面上下文时 `contextNotice` 与实际绑定状态一致，并有覆盖「落地页清空 → 再进事项页」路径的测试 | `web/src/stores/aiDockStore.ts::clearPageContext` |
| `createGeneralPageContext` 名不副实 | 它只清 `work_object_refs`，「通用」会话仍携带此前打开事项的 `source_refs` 与 `allowed_capabilities`；后续切换事项仍触发 `pageBindingChanged` 并清空通用会话对话。另：`sessionContextMode` 除 `clearSession()` 外永不回到 `page`。`P2-PAGE-CONTEXT-CONTRACT-001` Opus 最终轮非阻断发现 2 | 无（不被任务阻塞） | 待 GOV-SYNC 分配 | 通用会话不再携带来源页的 `source_refs` / `allowed_capabilities`，或该函数改名以如实反映语义；`sessionContextMode` 有从 `general` 回到 `page` 的正常路径 | `web/src/stores/aiDockStore.ts::createGeneralPageContext` 与同文件 `sessionContextMode` |
| 页面上下文注册失败会掀掉页面 | `web/src/app/usePageContextRegistration.ts::registerPageContext` 在任一后端派生值不满足 TS 合同时于 `useEffect` 内抛错——例如非 `Z` 结尾的 `source_fetched_at`、或命中 15/18 位数字凭证形状的 `source_ref`。它 fail-closed，但**以崩溃页面的方式**而非降级为「无上下文」。真实 OA 数据存在这两种形状。`P2-PAGE-CONTEXT-CONTRACT-001` Opus 最终轮非阻断发现 3 | 无（不被任务阻塞） | 待 GOV-SYNC 分配 | 注册失败降级为「无上下文」并给出用户可见说明，页面不崩溃；并有针对上述两种真实数据形状的回归 | `web/src/app/usePageContextRegistration.ts::registerPageContext` |

### 已结项摘要（现役规则或守卫仍生效）

| 已结项事项 | 仍生效的规则 / 测试 / 守卫指针 |
|---|---|
| PR #73 任务记录与合并授权守卫 | `AGENTS.md:27-30`：PR body 三段与欠债五字段须在合并前完成；checks 绿不等于合并授权。 |
| A 档 Opus 评审桥安全摘要 | `AGENTS.md` 的 PR body 永久记录规则要求合并前记录闭集 JSON 摘要并绑定最终候选；PR #134 是合规实例。 |
| Golden fixture expectation 凭证扫描 | `scripts/golden_task_assertions.py::_assert_judgement_credentials_absent`；`tests/golden_tasks/test_runner_assertions.py`。 |
| Golden 扫描无值失败输出 | `scripts/golden_task_assertions.py::_raise_credential_failure`；无值失败回归。 |
| 引号包裹凭证键扫描 | `scripts/golden_task_assertions.py::_bounded_serialized_json_mapping`；serialized JSON 回归。 |
| `session_key` / `data_key` 变体扫描 | `_CREDENTIAL_EXACT_KEYS` / `_CREDENTIAL_MAPPING_RULES` 与对应回归。 |
| Pilot Ops B/C 工作台归并裁决 | `docs/phase2/DECISIONS.md` 2026-08-18：并进工作台；现役 DAG 见下。 |
| full-chain smoke 生产链守卫 | `scripts/smoke/full_chain.py::run_full_chain_check`；`tests/runtime/test_pilot_foundation_e2e.py` 与 `tests/scripts/test_smoke_full_chain.py`。 |
| 四项架构决定权威归属 | 唯一权威为 `docs/phase2/DECISIONS.md`；Golden 决定四仍由治理 SSOT 守卫保护。 |
| 企业密钥配置文件管理裁决 | `docs/phase2/DECISIONS.md` 决定三：运维配置文件管理，不建设运行时管理页面。 |
| Golden 策略唯一副本守卫 | `docs/phase2/DECISIONS.md` 决定四；`tests/architecture/test_governance_document_ssot.py` 守住唯一副本。 |
| OA Rehearse pack↔pack 比较守卫 | `scripts/smoke/runner.py::_run_rehearsal` 的 pack↔pack 比较及回归；保留以防按旧前提立项。 |
| CLAUDE/AGENTS 治理 SSOT 分层 | `AGENTS.md:9` 的状态/计划/决策分层；`tests/architecture/test_governance_document_ssot.py`。 |
| Runtime Capability 咽喉动态观测 | `tests/architecture/runtime_schema_observer.py` 与 `tests/architecture/test_response_output_contracts.py` 的咽喉动态观测。 |
| Registry 确定性 bootstrap | `scripts/manage_oa_capabilities.py`；`tests/scripts/test_manage_oa_capabilities.py`；startup Registry DML 守卫。 |
| Golden 敏感父键容器扫描 | `scripts/golden_task_assertions.py`；`P2-GOLDEN-CREDENTIAL-CONTAINER-001` 的 structured/serialized 回归。 |
| SDUI 记录列表与可操作确认面 | `web/src/contracts/runtimeProjection.ts`、`web/src/components/RecordsList.tsx`、`web/src/components/ConfirmCard.tsx` 及对应前端/合同测试。 |
| Work Object 模型与持久化合同 | `app/ports/work_object.py`、`app/infra/persistence/work_object/`、`tests/ports/` 与 `tests/api/test_work_objects.py`。 |
| `confirm_card` capability 参数 allowlist | `app/runtime/runtime.py::_confirm_card_payload`；`tests/runtime/test_runtime_response_content.py`。 |
| Phase 2 架构重排裁决 | 已决任务去向唯一见 `docs/phase2/DECISIONS.md`；现役 DAG 见下。 |
| Work Object 多来源模型 | `state_authority` 判别联合、migration 与 `P2-INTERNAL-WO-MODEL-001` 回归仍生效。 |
| Capability 自动化程度与办理映射 | `app/ports/capability_registry.py::CapabilitySpec`、`app/ports/work_object_handling.py` 与映射负向测试。 |
| Work Object 与 OA 状态同步语义 | `docs/phase2/DECISIONS.md` 2026-08-19；页面始终展示数据时间与同步失败。 |
| OA 登录与用户凭证边界 | `app/credential_polling.py`、`app/infra/auth/postgresql.py` 及 credential polling 回归。 |
| Task 统一版本绑定 | `app/ports/human_gate.py`、`app/workflow/engine.py` 与“v1 确认不得执行 v2”负向回归。 |
| 任务派发 migration downgrade 守卫 | `docs/phase2/DECISIONS.md` 2026-08-27：删除 internal 数据须 `ALLOW_DESTRUCTIVE_DOWNGRADE=1`；四条 downgrade 测试。 |
| 部门层级授权裁决 | `docs/phase2/DECISIONS.md` 2026-08-27：上级主负责人递归查看/跨层派发，导入防环 fail-closed；由 `P2-INTERNAL-WO-SCOPE-001` 落地负向测试。 |
| 必达项 5 的 Golden 完成口径 | `docs/phase2/DECISIONS.md` 2026-08-28：只覆盖 Runtime 观察边界；API/单元层不得弱化。 |
| `UserAction` 结构化入口 | `/api/v1/runtime/action`、`RuntimePort.handle_user_action` 与 `tests/runtime/test_runtime_user_action.py`。 |
| `/runtime/action` 端点级安全负向 | `tests/api/test_csrf.py` 与 `tests/api/test_auth.py` 的正式路由回归。 |
| 结构化/自由文本 completed 文案合同 | `tests/runtime/test_runtime_user_action.py::test_completed_action_preserves_the_text_resume_message_and_fallback`。 |
| 通用 formatter 字段来源边界 | `app/runtime/runtime.py::_format_capability_response`；任意 capability value 不得进入通用文案的负向测试。 |
| `envelope.data` fail-closed 脱敏投影 | `app/runtime/response_projection.py::project_response_data` 与递归投影/凭证属性测试。 |
| DECISIONS 历史链 supersede 指针 | `docs/phase2/DECISIONS.md` 2026-08-28 两条 supersede 指针；现役 DAG 只读本文件与 `docs/phase2/STATUS.md`。 |
| TS/TSX 弱测试确定性门禁 | `scripts/check_weak_tests.py` 已支持前端测试；`uv run python scripts/check_weak_tests.py web/src/pages/__tests__/ChatPage.test.tsx` 确定性返回 `Weak-test check passed.`。 |
| 统一工作台外壳与首个薄查询层消费者 | `web/src/App.tsx`、`web/src/app/AppShell.tsx`、`web/src/shared/ui/QueryTable.tsx`、`web/src/shared/query/useTableQuery.ts` 与对应前端测试；第二个真实生产消费者仍作为活欠债保留。 |
| SDUI 具名跨语言 exact 合同 | `ActionResponseData`、`ConfirmCardPayload`、Runtime OpenAPI / Orval 生成物及 action/confirm 合同守卫；后续非阻断发现由 `P2-SDUI-RENDERER-002` 承接。 |
| 组织目录用途边界行为守卫 | 主防线为 `tests/infra/organization_directory/test_postgresql.py::test_list_user_memberships_returns_complete_set_across_organization_values`：真实 PostgreSQL reader 必须完整返回同一用户在 `organization_id` 与 `subcompany_id` 上同时具备非空重复值、双 `NULL` 与独有值的完整 membership 列表；`tests/architecture/test_organization_directory_boundary.py` 仅为补充层。按组织或分公司折叠、非空/具体值收窄、Python 截断或私有 predicate、SQL `LIMIT` 七类反证均会使主防线变红。 |

### 外部验收纪要（待雨爷裁）

| 记录 | 状态 | 指针 |
|---|---|---|
| `P2-OA-INTRANET-SMOKE-001` | 2026-08-13 文档登记真实 OA `verify`、两 capability Live/全链及浏览器 `/chat` 已验收；Git 无对应 `phase2(...)` 记录，不列入已完成任务索引 | `scripts/smoke/runner.py`、`scripts/smoke/full_chain.py`；是否删除本纪要待雨爷裁 |

> 未脱敏素材读取禁令唯一见 `AGENTS.md` 不可协商规则 9；历史裁决见 `docs/phase2/DECISIONS.md`。PLAN 不复制原始素材细节或现场 smoke 操作手册。

## 4. 任务索引与现役 DAG

> 完成态只以 `git log --grep='phase2(<task_id>)'` 为准；下表不保存 SHA、CI、PR 或运行证据。

### 已完成任务索引

| task_id | 完成态摘要 |
|---|---|
| `P2-PILOT-FOUNDATION-001` | 真实 LLM、可信身份与生产 composition 基线。 |
| `P2-IDENTITY-CREDENTIAL-001` | OA 绑定、凭证验证与撤销/重置闭环。 |
| `P2-OA-READ-CONTRACT-001` | OA Replay Provider、Contract Pack 与离线脱敏合同。 |
| `P2-READ-ADAPTER-001` | OA Live 只读 Adapter 全链。 |
| `P2-FE-API-CLIENTS-001` | Auth / Runtime / Admin-Trace OpenAPI 与 Orval 客户端。 |
| `P2-ADMIN-CSRF-001` | Cookie 写请求的 Origin + 自定义头 CSRF 守卫。 |
| `P2-RUNTIME-RESPONSE-CONTRACT-001` | Runtime OpenAPI 声明 ResponseEnvelope。 |
| `P2-PILOT-ENTRY-FE-001` | 登录、Session Cookie 与受保护前端入口。 |
| `P2-CHAT-ENTRY-FE-001` | 普通文本 `/chat` Runtime 入口。 |
| `P2-BE-SMALL-DEBT-001` | OA adapter 有界无值失败日志与配置占位。 |
| `P2-FE-TEST-FLAKE-001` | Vitest 文件级并发收敛。 |
| `P2-OA-SYSMSG-PACK-001` | OA 系统消息 Replay pack 与泄漏守卫。 |
| `P2-AUTH-USERID-TYPE-001` | OA userid 单点归一与唯一性守卫。 |
| `P2-OA-SYSMSG-LIVE-001` | OA 系统消息 Live 路由。 |
| `P2-OA-MSGCENTER-PROTOCOL-001` | OA 消息中心传输、cursor 与截断守卫。 |
| `P2-SMOKE-RUNNER-001` | 无凭证回显、结构化、fail-closed 的内网 smoke runner。 |
| `P2-SMOKE-AUTH-DIAG-001` | OA pending 合同与登录诊断加固。 |
| `P2-OA-TODOLIST-ADAPTER-001` | OA 待办事宜协议与 v3 Contract Pack。 |
| `P2-GOLDEN-CREDENTIAL-PATTERN-001` | Golden OA 凭证字段名规则。 |
| `P2-PILOT-OPS-A-001` | Registry 文本校验与绑定管理动作。 |
| `P2-REGISTRY-BOOTSTRAP-001` | 显式 apply / verify、durable 审计与事务回滚。 |
| `P2-SMOKE-E2E-CHAIN-001` | smoke 复用真实生产 composition 与 Runtime HTTP 全链。 |
| `P2-SESSION-COOKIE-TRANSPORT-001` | Session Cookie secure 传输合同。 |
| `P2-REGISTRY-DOC-FIX-001` | Registry bootstrap 目标指导修正。 |
| `P2-SMOKE-FAILURE-CODE-001` | full-chain 固定失败码闭集。 |
| `P2-HAR-FORM-DECODE-001` | 待办 HAR form 值解码。 |
| `P2-GOLDEN-CREDENTIAL-HARDENING-001` | 四个 judgement root 的有界凭证扫描。 |
| `P2-HAR-READ-FORM-DECODE-001` | 消息中心 HAR form 名和值解码。 |
| `P2-SMOKE-VERIFY-DIAGNOSTICS-001` | verify 失败协议与诊断保全。 |
| `P2-FE-ANTD6-001` | 前端升级 Ant Design 6 并移除 ProComponents。 |
| `P2-DECISIONS-SYNC-001` | 2026-08-17/18 决定组与规则 v2.4.1。 |
| `P2-DECISIONS-SYNC-002` | 工作台合并与候选正名裁决。 |
| `P2-SDUI-CONFIRM-PAYLOAD-001` | 确认卡五键 payload 基线。 |
| `P2-GOLDEN-001` | 冻结 GT-029～GT-033 的确认卡参数值 allowlist。 |
| `P2-ENVELOPE-MESSAGE-REDACTION-001` | ResponseEnvelope 单一外露合同与咽喉动态守卫。 |
| `P2-INTERNAL-WO-MODEL-001` | Work Object 双权威判别联合与 migration 守卫。 |
| `P2-CAPABILITY-AUTOMATION-LEVEL-001` | 办理能力自动化程度、Work Object 映射与值展示 allowlist。 |
| `P2-USER-ACTION-SEAM-001` | 结构化 `/runtime/action` 与一次性 claim。 |
| `P2-SDUI-RENDERER-001` | 记录列表、确认卡与 9 值 outcome 的前端投影。 |
| `P2-SDUI-SCHEMA-001` | Action data 与确认卡 payload 具名跨语言 exact 合同。 |
| `P2-TEST-INFRA-WEAKCHECK-001` | 弱测试门禁支持 TypeScript / TSX。 |
| `P2-FE-WORKBENCH-001` | 统一 AppShell、AI Dock、工作事项与薄查询层首个生产消费者。 |
| `P2-CONFIRM-BINDING-001` | HumanGate 与不可变 Task 版本绑定。 |
| `P2-GOLDEN-CREDENTIAL-CONTAINER-001` | Golden 敏感父键容器继承守卫。 |
| `P2-WORK-OBJECT-001` | OA Work Object 与最小工作台。 |
| `P2-OA-CREDENTIAL-POLL-001` | 用户密码绑定、加密存储与后台轮询。 |
| `P2-ORGDIR-BOUNDARY-GUARD-001` | 组织目录完整结构关系的真实 PostgreSQL 行为守卫。 |

### 现役 DAG（仅未完成）

| task_id | depends_on | 预判档位 | BLOCKED / 边界 |
|---|---|---|---|
| `P2-LOW-RISK-WRITE-001` | P2-GOLDEN-001、P2-CONFIRM-BINDING-001、P2-SDUI-RENDERER-001、P2-ENVELOPE-MESSAGE-REDACTION-001（均已完成） | **A** | **是：OA 审批提交协议结构未知；输入到位前不开棒** |
| `P2-GOLDEN-002` | P2-GOLDEN-001、P2-LOW-RISK-WRITE-001 | **A** | 是：等待低风险写入落地；fixture 增量授权已到位不等于任务完成 |
| `P2-MEMORY-001` | P2-PILOT-FOUNDATION-001 | **A（预判）** | 是：获批知识语料与用户数据边界未到位；机会层 |
| `P2-SKILL-CANDIDATE-001` | P2-READ-ADAPTER-001 | **A（预判）** | 否；只允许人工登记，机会层 |
| `P2-HIKVISION-ADAPTER-001` | P2-READ-ADAPTER-001 | **A** | 是：现场版本、API/SDK、账号与设备/区域范围未到位；机会层 |
| `P2-PORT-SEAM-001` | 无 | **A** | 否；机会层 |
| `P2-OA-ORGANIZATION-DIRECTORY-001` | P2-WORK-OBJECT-001 | **A** | 否：接口结构已固化；不完整或 `managerid` 语义不明时 fail-closed |
| `P2-INTERNAL-WO-SCOPE-001` | P2-INTERNAL-WO-MODEL-001、P2-OA-ORGANIZATION-DIRECTORY-001、P2-ORGDIR-BOUNDARY-GUARD-001（均已完成） | **A** | **是：唯一主负责人可信来源仍缺失**。组织目录 guard 冻结已解除；`PrincipalOrgContext` 只有单值 `department_id`，`alembic/versions/` 无任何部门表，`WorkObjectRecord` 只有 assignee 维度。2026-08-31 开棒实测零改动停手。「不依赖 OA 授权模型」仍成立——依赖的是组织**结构数据**，不是 OA 的授权判定 |
| `P2-INTERNAL-WO-DISPATCH-001` | P2-INTERNAL-WO-SCOPE-001、P2-PAGE-CONTEXT-CONTRACT-001 | **A** | 依赖未完成 |
| `P2-INTERNAL-WO-ATTACHMENT-001` | P2-INTERNAL-WO-DISPATCH-001 | **A** | 是：方案限额矛盾须先裁 |
| `P2-SDUI-RENDERER-002` | P2-SDUI-RENDERER-001、P2-SDUI-SCHEMA-001（均已完成） | **A** | 否：导航合同已裁；机会层；同时承接 `P2-SDUI-SCHEMA-001` 的七条非阻断欠债 |
| `P2-PAGE-CONTEXT-CONTRACT-001` | 无 | **A** | 否；须先于真实页面上下文注册 |
| `P2-WO-SEARCH-001` | P2-INTERNAL-WO-MODEL-001、P2-PAGE-CONTEXT-CONTRACT-001 | **A** | 依赖未完成 |
| `P2-TENANT-IDENTITY-001` | P2-OA-ORGANIZATION-DIRECTORY-001、P2-AUDIT-READ-AUTHZ-001、P2-AUDIT-TRACE-SCOPE-001 | **A** | 依赖未完成；Admin 四个读取面的跨租户反证是完成前置；第二租户硬前置 |
| `P2-USER-ACTION-REJECT-001` | P2-USER-ACTION-SEAM-001 | **A** | 是：reject/cancel 后 Task 终态待裁 |
| `P2-AUDIT-READ-AUTHZ-001` | 无 | **A** | **是：不单独开棒**。2026-08-31 裁定与 `P2-AUDIT-TRACE-SCOPE-001` 合并交付——归属列落地前「同租户跨用户可读」与「跨租户不可见」不可能同时成立，单独交付会退化为「只能读自己」 |
| `P2-AUDIT-TRACE-SCOPE-001` | P2-AUDIT-READ-AUTHZ-001（联合交付）、P2-ORGDIR-BOUNDARY-GUARD-001（已完成） | **A** | 否；组织目录 guard 冻结已解除。Trace 表 schema 变更与历史行回填**已于 2026-08-31 分别获得专项授权**（授权只覆盖 Trace 表，不外溢到 tasks / sessions / binding，见 `docs/phase2/DECISIONS.md`）；与前棒合并交付；为 `P2-TENANT-IDENTITY-001` 完成硬前置 |
| `P2-FEEDBACK-LOOP-001` | 无 | **A（预判）** | 是：获批 Scope 未定义；机会层 |
| `P2-CONFIRM-DURABILITY-001` | P2-USER-ACTION-SEAM-001 | **A** | 是：持久化/generation 合同尚未设计 |

稳定必达主链：`FOUNDATION → OA_READ_CONTRACT → READ_ADAPTER → WORK_OBJECT → GOLDEN_001 → ENVELOPE_MESSAGE_REDACTION → LOW_RISK_WRITE → GOLDEN_002`。`LOW_RISK_WRITE` 另依赖已完成的 `CONFIRM_BINDING`，并由已完成的 `CAPABILITY_AUTOMATION_LEVEL → USER_ACTION_SEAM → SDUI_RENDERER` 支链提供展示与结构化动作合同。PLAN 不声明“当前下一棒”，当前指针只见 `docs/phase2/STATUS.md`。

### 已撤销或已拆分索引

| 原 task_id | 现役去向 |
|---|---|
| `P2-DB-GATEWAY-001` | 已移出 P2；后续有真实直连需求再另立项，历史裁决见 `docs/phase2/DECISIONS.md`。 |
| `P2-PILOT-OPS-001` | 已拆：A 棒完成；绑定管理已有承载，审计/反馈由工作台与独立机会层任务承接，映射导入归 P3。 |
| `P2-INTERNAL-WORK-OBJECT-001` | 已拆为 `MODEL → SCOPE → DISPATCH → ATTACHMENT`，其中 MODEL 已完成。 |

## 5. 决策指针

已决架构与历史 supersede 唯一见 `docs/phase2/DECISIONS.md`；未决项以上方五字段活欠债为准。

## 6. P2 不做什么

- 不把“能连到接口”当试点完成；缺可信身份、正式凭证、审计、Evaluator 或负向 Golden 时仍是半成品。（蓝图 §3.2 L176-L186、§7.0 L1383-L1404、§13 L2701-L2717）
- 不让 Runtime、LLM、UI、Workflow 或 Skill 绕过 Capability Gateway；不让 DB Gateway 自由查生产表。（蓝图 §7.3 L1436-L1502、§14.2.1-§14.2.2 L2787-L2806）
- 不把密码、token、Cookie、session 或敏感原文写入 LLM、Memory、Skill、Trace、日志、fixture 或报告。（蓝图 §7.4.3 L1540-L1549、§14.2.3 L2808-L2817）
- 不因未绑定/凭证失效自动切换服务账号；不让自报角色获得 Admin 权限。（蓝图 §9.1.1 L2021-L2062）
- 不在外部输入缺失时编 endpoint、infra 数值、凭证模式或系统能力；BLOCKED 项只解除，不猜测。（`P1-PARAM-001.md` L52-L59、L73-L77；蓝图 §15 L2870-L2907）
