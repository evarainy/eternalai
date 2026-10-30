# Phase 2 当前状态

- Runtime 替换 `P2-RUNTIME-AGENTSCOPE-M2-001`：PR #242 草稿，**未合并**（A 档、高风险）。S1–S7 已完成：浏览器与 D 收集接入新路径（准入、交接与收尾归属按设计 §3、§5 冻结的 M3 前置合同）、文档问答、知识注入与 `m2-1`、概览改为读、前端会话忙提示，以及新路径 Golden 正式回放。回放基线（本地实测，同一提交、同一 `uv.lock`，未连真实 OA/AS/MCP/vLLM，agentscope 回放用脚本模型）：`run_golden_tasks.py --engine agentscope --gate` 通过，total=34、计分 26（正向 10/10、负向 16/16）、not_applicable=8（GT-006、007、029～033 为 H4(d)，GT-014 为 H4(e)，均已执行并记录观察结果）、failed=0、skipped=0、GT-001～005 注入配对 5/5 通过；缺 `input_schema` 的 11 个计分题由回放进程内的空对象 schema 接缝承接，生产投影仍拒绝缺少 input_schema 的能力，fixture 与 `FROZEN_GT_IDS` 未改。legacy `run_golden_tasks.py --gate` 34/34（正向 13/13、负向 21/21）；required CI 仍只跑 legacy，`ci.yml` 未改。Golden 不覆盖 D 与续填。D 新路径收集已支持空字符串占位、空对象追问位置与故障情况，以及续填合并新增故障描述（浏览器提示词 m2-4）；已复现发布 schema 的空字符串 minLength 拒绝，现场该次触发项仍缺参数形态证据，主控重启后待真实复测，不能以脚本模型回归替代。**未做**：S8 完整开发集（`m2-1`，主控）、S9 新上下文自审、独立 Monitor、跨厂商静态审核与雨爷真实验收；D 新路径真实填写、开发库启用概览能力均须雨爷专项授权，未授权前标未实测。欠债与关闭项见 [PLAN M2 新增欠债](PHASE2_PLAN.md#runtime-替换-m2-新增欠债2026-10-29) 与 [PLAN M1B 欠债](PHASE2_PLAN.md#runtime-替换-m1b-新增欠债2026-10-28)；确认回执的接受范围等 B 类裁决待 GOV-SYNC 登记，本棒不改 DECISIONS。后继指针沿用下一条（雨爷 2026-10-28 已批顺序）：M2 合并后为 M3 `P2-RUNTIME-AGENTSCOPE-M3-001`。
- Runtime 替换 `P2-RUNTIME-AGENTSCOPE-M1B-001`：已合并。新路径具备写组提议、确认前置（文字确认与卡片共用旧路径协调器）、确认前写叶子屏障、治理 MCP 卡片同账号跨登录重建、Golden 注入接缝（`--engine agentscope`，本地非门禁）与 C1–C6 确认回归；默认入口仍为 legacy，required Golden 仍走旧路径。当前结果：完整开发集 264/276（M1A 为 260–265 区间），读题误转确认卡 0、身份违规 0；时延 p95 超标，诊断为共享模型服务负载，非本实现退化，记环境项。真实验收：2026-10-28 雨爷按 15 题清单全部通过，第 14 题已明确拒绝代他人查询、不执行任何查询；首轮用 qwen3.6-27b 因共享服务排队超时作废，改用项目默认 glm-4.7 重测，雨爷确认模型可随时切换、不影响结论。**真实写入未验收**：开发库没有可确认的治理写能力，确认链路只有 C1–C6 与独立 Monitor 证据。欠债与 Golden 合同冲突见 [PLAN M1B 欠债](PHASE2_PLAN.md#runtime-替换-m1b-新增欠债2026-10-28)，新增需求见 [PLAN 收口新增](PHASE2_PLAN.md#runtime-替换-m1-收口新增欠债与已批需求2026-10-28)。后继指针（雨爷 2026-10-28 批准，经 Astra high 评审调整）：R-APP `P2-AUTH-RENEWAL-APP-001` 已合并，当前下一棒为 M2 `P2-RUNTIME-AGENTSCOPE-M2-001`（M3 合同在 M2 浏览器接线前先固定），其后依次为 M3 `…-M3-001`、M4 `…-M4-001`、M6 `…-M6-001` 代码与退役准备、M5 `…-M5-001` 最终候选验收、M6 实际切换；R-MCP 待外部 E1。新路径 Golden 合同已批准，M2 按其回放（要求 26 题，8 题合同不适用）。

- 使用中会话续期 R-OA `P2-AUTH-RENEWAL-OA-001`：已合并，OA 会话心跳保活与本地截止延长。真实环境已见心跳返回 alive、本地截止延长；V3（前台连续使用 1 小时以上）、V4（OA 网页退出后问待办）、V5（浏览器同时登录 OA）此前在旧页面上跑，作废，待重做；V9 后台轮询在开发启动器下未运行，未验证。R-APP `P2-AUTH-RENEWAL-APP-001`（D1）已由 PR #241 合并：v3 票据按实例续期，有真实输入就续，所有标签无操作超过 idle 窗口（D1 定为 7200 秒，现场值未核对）即失效；登录与登出撤销整个旧实例；401 或重新登录后，同一账号回到原页面并恢复未发送内容（当前标签 sessionStorage 一次性快照），换账号、角色变更或主动登出不恢复、不自动重发。真实环境验收 V1–V8 未实测。R-MCP `P2-AUTH-RENEWAL-MCP-001`（待外部 E1：授权服务增加刷新令牌；D5 已决，期间 MCP 授权到期即失效、需手动重新授权）未开工，裁决见 [DECISIONS 2026-10-28](DECISIONS.md#2026-10-28--裁决使用中会话续期确认卡账号绑定与-m1-收口)，欠债见 PLAN。

- Runtime 替换 `P2-RUNTIME-AGENTSCOPE-M1A-001`：已合并。2026-10-27 真实验收 15 题：13 题完全符合，第 9 题（分页用真实多页数据覆盖不足）按如实返回判通过，第 14 题措辞不符已由 M1B 复测关闭。冻结 judge v3.1、配置 A 的完整开发集 265/276，零词面 c50/c100 与分类三项门槛未达，已裁决记欠账继续推进；留出集验收未运行。见 [PLAN 分棒及欠债](PHASE2_PLAN.md#runtime-替换-m1-已批分棒与新增欠债2026-10-27)。

- Runtime 替换 M0 已收口：`P2-RUNTIME-AGENTSCOPE-M0-001` 已由 PR #224 合并。2026-10-26 正式计数按 rev4 冻结样本 12 条各 1 次、12/12 通过（门槛由 36 次改为 12/12，统计强度限制见 PLAN）；单次模型调用时限 60 秒、整轮 120 秒、最多 4 次模型调用、零重试。计数覆盖获准的 OA 本人待办与业务 MCP 人员查询；目标环境离线验收推迟到 Docker 镜像与服务器阶段。剩余义务见 [PLAN](PHASE2_PLAN.md#runtime-替换-m0-剩余义务2026-10-26同步)，证据见 PR #224 与仓库外 `m0-runs/`。

- MCP 换绑修复：`P2-MCP-OWNER-REBIND-001` 已由 PR #229 合并并完成真实验收；同一用户重新登录后可显式重新授权 MCP，无需重启后端，其他用户仍被拒。进程重启后的授权就绪状态、拒绝提示及其他保留欠债见 PLAN。
- 普通聊天查人修复：`P2-CHAT-PERSON-FIND-INTENT-001` 已由 PR #232 合并；person_find 展示名/描述明确按姓名查询，修复候选截断导致选不中。本机开发库该能力定义已同步；真实前端对话与新增诊断日志输出尚未实测，极简问法限制见 PLAN。
- 本机开发环境（2026-10-26 授权操作记录）：开发服务已切到主线，开发库已升级至当日主线最新迁移，升级前备份留在仓库外；本批未执行服务切换或数据库操作。因缺 `d_form` 授权配置（`d_form_authorization_missing`），本机启动器暂设 `BROWSER_ENABLED=0`，D 表单浏览器纵切未接入；换机仍须按目标环境完成装配与授权，不能复制本机运行态作为验收。证据见仓库外 `controller-dispatch/sonnet_devmain_switch.out.md` 与 PR #232。

- 当前 Chat / D / 固定查询收尾 task_id：`P2-CHAT-BROWSER-READ-001`（B 档、高风险，串行集成，承担本任务 A 类机械同步）。本轮整理源码及 Browser Runtime 前置历史，经任务分支/PR/原 required checks 集成；包含 OA 登录/凭证读取修复、六项固定业务只读 MCP 及中文投影、固定文档问答和 D v30 重复确认移除/终态资源关闭接线。用户已观察到 OA 待办恢复、MCP 人员成功返回及 D 进入表单；D 最终填写、自动释放与再次调用尚未验收。原机 v30 已激活，绑定 revision29；最近模型健康检查失败。独立集成树已收口 Ruff、前端既有类型/精确请求体兼容及后端类型（Linux 目标 266 个源码文件通过），单列补齐普通 Chat 重放所缺的解密委托，并修正 Admin 服务分层及已有 runtime 接口的生成产物同步；底层身份/AAD校验保留，本地未运行测试或全量。最终 required CI、静审和敏感检查以候选 PR 证据为准，不复用旧候选结论。原机源码及 pins 未改，跨机接续及现场装配限制见 [接续说明](../dev/chat_browser_mcp_handoff.md)，真实验收义务见 PLAN；不改其他候选状态或分配新后继。

- 当前修复候选 task_id：`P2-MCP-PR218-FIX-001`（B 档、高风险、串行，承担 A 类机械同步）。PR #218 的 R1–R6 修复候选继续沿用 eva 的 v2 有界计划（独立 Sol/max 评审 PASS）；Sol/high 两轮施工后，按用户条件授权由 Astra/high 修正终审残留的整个确定终态收尾区段、取消/过期 claim 恢复与 schema 结构位置引用收集；保留既有授权复核、确认幂等和新旧代隔离修复。最终 diff 待 eva 验证。只执行修改部分与直接受影响路径的定向回归；本棒不运行全量。真实 PostgreSQL 验证受固定测试库不可达和隐式迁移未获授权阻塞，欠债见 PLAN；未启动服务、迁移、公开 push/PR/merge 或部署。不分配新后继，不把下列历史完整回归数字作为本棒实测。

- 来源候选（2026-09-30 登记）task_id：`P2-MCP-PLATFORM-001`（A 档、高风险、串行，承担 A 类机械同步）。通用多服务 MCP、逐用户 OAuth、全部 13 工具（7 读 / 6 受治理写）、持久确认与恢复、API/UI 和公司确认/上线清单已完成离线实现与本地验证。候选尚未完成独立审查链或公开集成；公开 push/PR/merge 待专项授权。公司身份、输出、外部确认、恢复资格等未知项保持可替换插口和默认拒绝；无真实业务写或生产部署。新增启用义务见 `PHASE2_PLAN.md` MCP 表，本棒不分配后继，也不解除原 OA 必达链与第二租户禁启。

- 组织身份 S1 已合并实现：`P2-TENANT-IDENTITY-001`（PR #212，2026-09-23 合并，A 档）；显式 source tenant、旧 v2 等值验票、三表复合键及全部直接消费者、pending/claim 隔离已合并，审查证据留 PR。S1 合并不核销总欠债；S2/S3 未施工、开棒标识待 GOV-SYNC 分配；全链验收前维持单租户运行，第二租户禁止启用。

- 最近已合并实现 task_id：`P2-ADMIN-EVENTS-UNIFORM-001`（B 档、高风险、串行，承担 A 类机械同步；PR #216）。2026-09-08 已批准的方向 A 已实现；新上下文自审、独立 Monitor、MiMo 静态审核、required checks 与对应 merge Actions 均通过，集成证据留 PR #216。本棒不裁定新后继。
- 当前治理同步 task_id：`P2-GOV-SYNC-071`；现役开发助手路由及本次修正见 `DECISIONS.md` 2026-09-29 与 2026-09-30 裁决。Admin events 已按 GOV-SYNC-070 分配并由 PR #216 合并；本状态同步不分配新后继。
- 当前已合并实现：`P2-AUDIT-LOGOUT-002`（PR #207）与 `P2-WO-COMPLETED-MERGE-001`（PR #206）；前者已完成最后一轮独立监理与静态评审桥，后者已完成已办结查询版本合并修复。
- 最近已合并实现：`P2-AUDIT-EVAL-POSTCOND-001`（PR #199）与 `P2-AUDIT-WO-LIFECYCLE-001`（PR #200）。两棒均已取得独立 Monitor r1 PASS、Opus 评审桥 PASS，PR checks 与对应 merge Actions 均 success；证据留各 PR。
- 当前已合并实现：`P2-FE-BUNDLE-SPLIT-001`（PR #209，B 档；路由级代码分割、认证后壳层与 Dock 首开按需加载）。PR checks 全绿且无新增依赖；已决后继 `P2-WO-ERROR-CONTRACT-001` 已于 2026-09-22 合并（PR #211）。组织身份 `P2-TENANT-IDENTITY-001`、聊天回退 `P2-RUNTIME-DIRECT-ANSWER-001` 的既有指针保留，其他依赖与阻塞沿现役 DAG。
- 审计 #1：`P2-AUDIT-LOGOUT-002` 已合并服务端当前票据持久吊销、旧票据/等价别名重放拒绝，以及前端确认成功后的旧身份数据清理与当前代空身份查询。旧 `P2-AUDIT-LOGOUT-001` 未交付的历史不作为新棒证据；重开裁决仍见 `DECISIONS.md` 2026-09-20。

## 已登记验证基线

### MCP 开发候选本地验证基线（2026-09-30 实测；尚未合并）

- 固定测试库后端完整回归 `4334 passed, 116 warnings`，exit=0、无失败或跳过；实际命令 `uv run pytest -q -p mcp_failure_observer`，默认全部集合和顺序，附加只读安全观察器。官方环境诊断 4 项通过；官方 `--start-full-tests` 后台入口未使用，替代执行方式与历史失败/因果修复证据留候选验证记录。
- 前端完整 `pnpm test`：`1027 passed, 0 failed, 0 skipped`；独立组件、OpenAPI、lint/typecheck/build 通过。Ruff、mypy（160 source files）、依赖、架构与改动测试弱检查通过；Golden `34/34`、negative/boundary `21/21`，冻结样例未改。
- 本地真实 SDK/HTTP/PG 与生产装配覆盖同用户双服务同名工具、六写确认/并发/直接调用阻断，以及三个故障点的 18 个真实子进程存活/终止/重建恢复场景。合成合同与离线证据不代替公司签收、真实账号/业务后态或上线验收；诊断会话敏感输出的既有未闭合事项另在欠债表登记。

### Admin events 已合并来源验证基线（2026-09-29 实测；PR #216）

- 固定测试库后端全量 `4173 passed, 116 warnings`，后台 `status=passed`、exit=0；定向 Admin 三文件 `68 passed`，架构 `135 passed`，三份改动测试逐文件弱测试检查、Ruff 与 mypy（137 source files）通过。Windows access violation 依 2026-09-10 裁决留原始运行证据，不冒充 CI 结果。
- 前端组件 `486 passed`（23 个独立文件）、单元 `513 passed`、OpenAPI `6 passed`，共 `1005 passed`；lint、typecheck 通过。候选只改 Admin events 的 OpenAPI 声明与页面空态，本地 build 未触发；PR #216 的 Backend 与 Frontend required checks、精确 merge SHA 对应 Actions 均 success。其余来源日期和数字仍为 2026-09-29 实测，不代表本 GOV-SYNC 棒重跑。
- 同一 audit_reader 的不存在与外租户事件响应状态、body 原始字节和稳定头一致；外租户合成 Task 确有非空事件，测量时零次 `list_events`。临时放宽租户谓词后成对字节断言变红，原字节恢复后变绿。base/candidate 在独立镜像用同一 `generate:api` 脚本生成，16 份生成物字节无新增差异；WOERR-1 仍开放。

### P2-TENANT-IDENTITY-001 S1 来源验证基线（2026-09-23 实测；PR #212 于 2026-09-23 合并）

后端、架构与 Golden 数字均为该棒 2026-09-23 实测，来源日期保持不变，不是本棒候选（2026-09-29）实测；合并事实以 PR #212 为准。

- pytest：`4170 passed, 0 failed, 0 skipped`（固定测试库后端全量，exit=0、后台 status=passed；无 `--ignore=`）。Windows access violation 按 2026-09-10 裁决记录。
- `tests/architecture/`：`135 passed`（本棒定向；全量亦覆盖。机械同步后另核 SSOT）。
- Golden Gate：`34/34 passed, 0 skipped, 0 failed`（本棒实测，positive 13/13、negative/boundary 21/21，含 GT-012）。
- Ruff、mypy（137 source files）、依赖检查与 61 个改动测试逐文件弱测试检查通过；DB/冒烟消费者定向 295 项、机械适配定向 289 项、认证/轮询定向 95 项、Runtime 确认定向 71 项、Gateway port 16 项。9 项生产变异均红，逐项精确恢复后绿。
- 新迁移仅 `sessions`、`principal_roles`、`oa_session_credentials`：空库/含 default 存量往返及三表非 default 拒降共 5 项通过；拒降时 revision/catalog/行内容不变。仓库唯一 head 为 `20260922_190000`，父为 `20260920_180000`；固定测试库已在本工作树无旁路降回开工登记的 `20260920_180000`，catalog 与行内容核对恢复。
- 本棒前端仅改 OpenAPI 导出测试的 `EXPORT_SCRIPT` 环境（补必填 `OA_SOURCE_PROFILE_ID` / `OA_TENANT_ID`，与 `.env.example`、`docker-compose.yml`、`scripts/smoke/environment.py` 同批补全，共 4 文件 9 行）；前端 lint、typecheck 通过，`test:openapi` 6 项、前端全量 1,003 项（组件 484、单元 513、OpenAPI 6）通过，由独立监理在最终候选上实测。未运行浏览器或真实 OA；既有前端合并证据见下节。S2 Work Object、S3 目录/G2、登录 membership、live roles、AAD v2、credential_ref v2 与 cache_scope 保留欠债，不以 S1 证明第二租户已可上线。

### 当前前端合并基线（2026-09-22，`P2-FE-BUNDLE-SPLIT-001`）

- 带 manifest 的 Vite 构建无 `Some chunks are larger than 500 kB` 警告；入口静态集合为 337,124 B JS + 9,657 B CSS，`index.html` 不预加载未访问页面。认证后默认 `/chat` 自动加载集合另计为 835,371 B JS + 35,636 B CSS（入口与壳层/页面闭包去重后），不将入口数字误作完整首屏下载量；目标 ARM 终端性能仍未实测。
- 前端全量为组件 484、单元 513、OpenAPI 5，共 1,002 项通过；lint、typecheck、带 manifest build、三份改动测试弱测试检查、依赖检查与架构测试均通过（来源：PR #209）。路由加载/失败、401 迟到模块、Dock 首开常驻和模糊预算有定向回归；本地受控浏览器只验证加载/失败壳层和 Dock 首开，不代替真实 OA 或目标终端验收。

## 本批及前置已合并事实

| task_id | 已交付范围与保留边界 |
|---|---|
| `P2-WORK-DISPATCH-001` | 任务派发 B01–B12、办理/审核/验收、周期、备注、附件与宿主接线已由 PR #225 合并；基线为专用测试库迁移往返及 required CI 通过，2026-10-24/25 合成数据验收已执行（后端 E01–E10、页面 E05/E06/E07），不代替真实验收；真实 E01–E10 未验收，X01/X03 与账号停用判定见 [PLAN 派发欠债](PHASE2_PLAN.md#既有活欠债)。 |
| `P2-WORK-DISPATCH-FE-REFRESH-001` | 列表/队列重新读取时同步重读已打开的办理详情，已由 PR #227 合并；合成环境两例 E05 复验与 required CI 通过，验收边界见 PR（本同步棒）正文。 |
| `P2-AUDIT-EVAL-POSTCOND-001` | 首例 `oa.read_overview/1.1.0` 确定性后置核验已合并。2026-09-19 历史实测为后端 3906、架构 133、Golden 34/34（负向/边界 21/21），不替换上面的当前基线；通用返回校验、审批后态和真实现场验收仍未完成。 |
| `P2-AUDIT-WO-LIFECYCLE-001` | 内部事项接单、文本反馈、自行办结、活动分页和近 30 天内部完成列表已合并；仅接单人可反馈/办结，发布回执仍为初始快照，不将 OA 消失视为办结。 |
| `P2-WO-COMPLETED-MERGE-001` | 已办结查询与 active/history 一样接入既有列表版本合并；专门前端回归先等 active/history 协调刷新完成，再注入旧 internal version，断言缓存每次成功更新不降版本、较新版本仍可更新且最终可见内容正确。未改合并算法、认证代次、缓存隔离或公共合同。 |
| `P2-AUDIT-LOGOUT-002` | 当前会话服务端注销与 PostgreSQL 持久吊销、票据 v2 nonce、前端注销确认与缓存 B2 合同已合并；独立监理最后一轮与静态评审桥均通过。D1 过期吊销清理与 D2 三项未决/保留项见 `PHASE2_PLAN.md`。 |
| `P2-AUDIT-CAPABILITY-TOPK-001` | 确定性 Top-K、安全摘要、完整同分组与预算边界已合并；b1/b2/b3 结项，组织 metadata、文本误伤与语义召回局限保留。 |
| `P2-AUDIT-LIST-ORDER-001` | 有界列表服务端稳定默认排序与超限说明已合并；D-6 有限例外已登记，分页和第二真实查询消费者债保留。 |
| `P2-AUDIT-WORKFLOW-WIRING-001` | 真实生产入口装配 Workflow/adapter，版本化概览经 Gateway 执行；生产装配旧债结项，不代表真实 OA 现场验收。 |
| `P2-AUDIT-DEPT-DEFAULT-001` | 显式跨部门允许集合已合并；未获准部门仅同部门派发。真实名单与部署诊断消费仍待输入/验收。 |
| `P2-FE-DISPATCH-WIRING-001` | 目录选择、时区、发布、只读摘要、页内重试与显示名接线已合并；解析、服务端草稿和真实目录前置保留。 |
| `P2-AUDIT-DRAFT-ISOLATION-001` | 草稿隔离已合并，仅当前认证会话内存暂存；刷新、退出、有效 401、认证换代及页面生命周期失效后不恢复。 |
| `P2-ORGDIR-PERSON-SYNC-001` | 姓名镜像、候选读取、调度、陈旧度授权门和诊断已合并；真实 OA Source 与首次可信目录未交付。 |

## 必达链与阻塞

- 必达五项：OA 只读纵切、Work Object + 最小工作台、后台轮询已完成；低风险写入未完成；Golden 部分完成（`P2-GOLDEN-001` 已完成）。
- 唯一剩余必达链：`P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002`。前者 **BLOCKED** 于 OA 审批提交协议结构，输入未到不开棒、不猜协议。
- Golden 只覆盖 Runtime 观察边界；工作台/隔离/审计由 API 与单元层验证。范围裁决见 `docs/phase2/DECISIONS.md`。
- Owner 待办：为 `phase0/main` 分支保护开启 **Do not allow bypassing the above settings**；修改分支保护须专项授权。

## 当前实现摘要

- 页面合同：既有交办结构与控件闭集、附件禁用、草稿失败反馈及软件中心合同保留；`P2-FE-DISPATCH-WIRING-001` 已接通目录选择、派发、时区与内部只读事项。列表保留有界稳定排序与超限说明；已办结查询与 active/history 同样经既有列表合并保留较新的 internal version。已合并 P 段新增完整批次消失对账、未再确认历史区、同步状态与成功空态，未接 D 可信办结源。草稿沿用会话内存；解析、附件上传、服务端草稿、软件登记审核和用户侧软件列表接口仍未接入。生产选人仍受真实目录 Source/首同步阻塞。
- 运行时基线沿用已合并的 `P2-RUNTIME-NO-CAPABILITY-COPY-001`：2026-09-10 实测 `http://34.74.11.38:8011/v1` HTTP 可达，`/v1/models` 返回单一 `glm-4.7`（`root=/mnt/models/GLM-4.7-Flash`，`max_model_len=200000`）；现役 provider → raw JSON mode 4 次真实推理成功，内存意图路由 3/3 通过。完整 OA E2E 尚未完成，冒烟包本地缺 `sqlalchemy`，且全链路涉及真实配置读取与持久化；`IntentRouter` 的 `match=none` 不能单独证明下游 `no_capability_found` 终态；剩余义务与边界见 `PHASE2_PLAN.md`，不得通过修改仓库 URL 或配置规避。
- 意图输出必须显式给出 `match`；完整候选的 `none` 进入 `no_capability_found`，reason 为 `no_matching_capability`；不完整候选的 `none` 为 `capability_candidates_low_confidence`。候选内标签碰撞保留冻结 `no_unique_active_candidate` 无匹配语义；候选外引用与约束矛盾拒绝执行。漏字段与矛盾组合保持 `schema_invalid`。Golden 为合成 LLM 输出经过真实 JSON 解析器的路由证据，未实测真实 vLLM 的语义判定；聊天回退后继仍为 `P2-RUNTIME-DIRECT-ANSWER-001`。
- 身份读取：`GET /api/v1/me` 与 `GET /api/v1/me/avatar` 均为零参数端点，身份来自服务端 HMAC 签名会话票据；姓名不依赖 OA 可达，未认证一律 401。
- 部门：后端代持用户自身 OA Session 读取 `orginfo`，以标准库 `html.parser` 有界解析（输入 8192 / 锚点 16 / 标签 64，部门锚点必须恰好一条）；原始 HTML 不进响应。OA 失败不改变 `authenticated`，只体现在闭集 `org_status`（`ok` / `unbound` / `expired` / `unavailable` / `unparsable`）。
- 头像：后端代理对 `messagerurl` 做六步 URL 校验与图片 MIME 白名单检查，拒绝时传输层零调用；前端只见常量路径。两个身份端点均返回 `Cache-Control: no-store`。
- 会话恢复：前端 `authStore` 初值为 `unknown`，启动时向后端确认会话且不持久化；确认前 `ProtectedRoute` / `LoginRoute` 渲染 `BootGate`，不放行、不重定向。后端不可达时保持 `unknown`，显示「连不上服务器」和重试按钮。
- 身份消费：顶栏、用户菜单、AI 助手问候语已接真实数据；缺部门时顶栏只显示姓名，缺头像时退回姓氏首字。职务仍无数据源，按 2026-09-04 裁决留位并如实说明。
- 既有验证证据：有界解析五个上界固定断言、`api_no_infra_imports` 守卫及头像响应不缓存的回归检查已落地；身份读取棒登记了 11 条变异反证与 1 条守卫接线反证，本治理棒未重跑。

## 组织目录与前端机会层指针

- 组织目录与身份：`P2-ORGDIR-PERSON-SYNC-001` 已合并交付姓名镜像、零岗位归一化、候选读端点、同步状态/调度、陈旧度授权门、本人读取回落与非阻断诊断。生产 OA HTTP Source 仍缺失，首次真实目录及真实周期更新未交付；既有验收只有合成 Source + 真实 PG/HTTP 验收。`P2-TENANT-IDENTITY-001` 的 S1 已合并交付（PR #212，2026-09-23）显式 source tenant、sessions、角色、凭证/binding 与 Runtime 索引切片；S2 Work Object、S3 目录/G2 仍待后续分段完成，现役单目录仅服务 default 租户，全链验收前不启用第二租户。内部生命周期、前端接线与显示名已合并；真实目录、多 membership 与人工授权集合维护的剩余义务见 `PHASE2_PLAN.md`。
- 编排接缝：`P2-AGENT-ORCH-SEAM-001` 已合并交付；`AgentOrchestrationPort` 的生产接线已收口。`P2-AUDIT-WORKFLOW-WIRING-001` 也已合并，生产入口已实例化 `WorkflowEngineAdapter` 并注入 Runtime；旧装配债已结项。
- 租户切片历史：2026-09-01 开工时连接库 tasks=0、distinct task_id=0；更早的 115/115 也仅为历史快照。本治理棒未重测 tasks 租户聚合；升级前 Task 保持 `tenant_id=NULL`，对 Admin fail-closed 不可见，不猜值、不回填。
- 前端后继：原 `P2-FE-DISPATCH-FORM-001` / `P2-FE-APPS-001` 已由 `P2-FE-PAGE-CONTRACT-001` 合并交付并关闭，不再单独开棒；页面主体来源为已完成并合并的 `P2-FE-VISUAL-REFACTOR-001`。`P2-INTERNAL-WO-DISPATCH-001` 的后端合同现已到位，前端选择、发布接线、结果呈现与显示名语义已由 `P2-FE-DISPATCH-WIRING-001` 合并交付；解析与服务端草稿分别保留独立义务，承担 task_id 尚待 GOV-SYNC 分配，均不再挂已完成的 FE 接线棒，不改变必达链的 BLOCKED 状态。
- 已完成视觉：导航/顶栏/浮动面板、玻璃拟态 theme、三套底图切换、`@ant-design/x` AI 助手页及可执行模糊层预算检查；字体跟随已批准画板，聊天问候语独立。历史返修过程留 Git。
- 剩余缺口：AppShell 手写 CSS module 的 antd Layout/Menu 欠债、职务来源、头像取图三项未知仍保留；用户身份读取棒另登记多部门 `orginfo` 形态、`isMobx` 取值、目录快照交叉校验、`sex` / `workcode` / `requestParams` 未消费等活欠债。
- 机会层 task_id、依赖、BLOCKED 条件和活欠债只见 `docs/phase2/PHASE2_PLAN.md` 的现役 DAG 与欠债表；分配 ID 不等于排期，不重排必达链。
