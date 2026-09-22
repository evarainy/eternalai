# Phase 2 当前状态

- 当前治理基线 task_id：`P2-GOV-SYNC-066`（C 档、串行；2026-09-22 同步本批已合并事实、评审桥非阻断欠债及已决后继，承担 A 类机械同步）。
- 当前已合并实现：`P2-AUDIT-LOGOUT-002`（PR #207）与 `P2-WO-COMPLETED-MERGE-001`（PR #206）；前者已完成最后一轮独立监理与静态评审桥，后者已完成已办结查询版本合并修复。
- 最近已合并实现：`P2-AUDIT-EVAL-POSTCOND-001`（PR #199）与 `P2-AUDIT-WO-LIFECYCLE-001`（PR #200）。两棒均已取得独立 Monitor r1 PASS、Opus 评审桥 PASS，PR checks 与对应 merge Actions 均 success；证据留各 PR。
- 当前已合并实现：`P2-FE-BUNDLE-SPLIT-001`（PR #209，B 档；路由级代码分割、认证后壳层与 Dock 首开按需加载）。PR checks 全绿且无新增依赖；当前后继指针为 `P2-WO-ERROR-CONTRACT-001`。组织身份 `P2-TENANT-IDENTITY-001`、聊天回退 `P2-RUNTIME-DIRECT-ANSWER-001` 的既有指针保留，其他依赖与阻塞沿现役 DAG。
- 审计 #1：`P2-AUDIT-LOGOUT-002` 已合并服务端当前票据持久吊销、旧票据/等价别名重放拒绝，以及前端确认成功后的旧身份数据清理与当前代空身份查询。旧 `P2-AUDIT-LOGOUT-001` 未交付的历史不作为新棒证据；重开裁决仍见 `DECISIONS.md` 2026-09-20。

## 已登记验证基线

### 最近合并基线（2026-09-22，来源 P2-AUDIT-LOGOUT-002 最终候选）

后端、架构与 Golden 三项为 LOGOUT-002 最终候选的既有验证基线；该棒已合并，Golden 行如实注明其来自 r2 实测。前端最终全量及其他定向证据见合并棒 PR，未在本治理棒重跑。

- pytest：`4086 passed, 0 failed, 0 skipped`（固定测试库后端全量，113 warnings；exit=0、后台 status=passed，P4 observer authoritative PASS；无 `--ignore=`）。Windows access violation 按 2026-09-10 裁决记录。
- `tests/architecture/`：`135 passed`（本轮定向及后端全量覆盖；A 类文档同步后另做 SSOT 定向验证）。
- Golden Gate：`34/34 passed, 0 skipped, 0 failed`（本棒 r2，positive 13/13、negative/boundary 21/21；相关生产路径未再改变）。
- 前端最终全量 998 项通过（组件 480、单元 513、OpenAPI 5），入口 exit=0；各组件文件独立进程，原 N2 断言/时限未改。
- 本轮前端 lint/typecheck/build、改动测试逐文件弱测试检查通过；Ruff、mypy（137 source files）沿用合并棒既有证据。新增认证、吊销、并发/故障、缓存代次、迟到结果及真实生成接口均有定向证据；生产变异与全前缀判据自身反证均已闭环，逐项证据留 PR。
- 仓库迁移 head：`20260920_180000`，父为 `20260920_120000`。固定测试库收口无吊销残留并回到 `20260920_120000`；已过期/未过期行拒降与空态往返沿用未改迁移的 r2 实证。本治理棒不查数据库，不宣称测试库当前版本。
- 合成真实浏览器已证明服务器注销、另一标签页/旧票据拒绝和刷新仍为登录页；未访问真实 OA。真实目录 Source、真实 OA 账号、OA 已办源与生产部署仍未验收；D1 过期吊销清理、离岗交接及跨刷新未决命令恢复见 `PHASE2_PLAN.md`。

### 当前前端合并基线（2026-09-22，`P2-FE-BUNDLE-SPLIT-001`）

- 带 manifest 的 Vite 构建无 `Some chunks are larger than 500 kB` 警告；入口静态集合为 337,124 B JS + 9,657 B CSS，`index.html` 不预加载未访问页面。认证后默认 `/chat` 自动加载集合另计为 835,371 B JS + 35,636 B CSS（入口与壳层/页面闭包去重后），不将入口数字误作完整首屏下载量；目标 ARM 终端性能仍未实测。
- 前端全量为组件 484、单元 513、OpenAPI 5，共 1,002 项通过；lint、typecheck、带 manifest build、三份改动测试弱测试检查、依赖检查与架构测试均通过（来源：PR #209）。路由加载/失败、401 迟到模块、Dock 首开常驻和模糊预算有定向回归；本地受控浏览器只验证加载/失败壳层和 Dock 首开，不代替真实 OA 或目标终端验收。

## 本批及前置已合并事实

| task_id | 已交付范围与保留边界 |
|---|---|
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

- 组织目录与身份：`P2-ORGDIR-PERSON-SYNC-001` 已合并交付姓名镜像、零岗位归一化、候选读端点、同步状态/调度、陈旧度授权门、本人读取回落与非阻断诊断。生产 OA HTTP Source 仍缺失，首次真实目录及真实周期更新未交付；既有验收只有合成 Source + 真实 PG/HTTP 验收。`P2-TENANT-IDENTITY-001` 仍承接更广泛的可信组织身份来源、sessions 与 identity binding，本棒单目录仅服务 default 租户。内部生命周期、前端接线与显示名已合并；真实目录、多 membership 与人工授权集合维护的剩余义务见 `PHASE2_PLAN.md`。
- 编排接缝：`P2-AGENT-ORCH-SEAM-001` 已合并交付；`AgentOrchestrationPort` 的生产接线已收口。`P2-AUDIT-WORKFLOW-WIRING-001` 也已合并，生产入口已实例化 `WorkflowEngineAdapter` 并注入 Runtime；旧装配债已结项。
- 租户切片历史：2026-09-01 开工时连接库 tasks=0、distinct task_id=0；更早的 115/115 也仅为历史快照。本治理棒未查询数据库；升级前 Task 保持 `tenant_id=NULL`，对 Admin fail-closed 不可见，不猜值、不回填。
- 前端后继：原 `P2-FE-DISPATCH-FORM-001` / `P2-FE-APPS-001` 已由 `P2-FE-PAGE-CONTRACT-001` 合并交付并关闭，不再单独开棒；页面主体来源为已完成并合并的 `P2-FE-VISUAL-REFACTOR-001`。`P2-INTERNAL-WO-DISPATCH-001` 的后端合同现已到位，前端选择、发布接线、结果呈现与显示名语义已由 `P2-FE-DISPATCH-WIRING-001` 合并交付；解析与服务端草稿分别保留独立义务，承担 task_id 尚待 GOV-SYNC 分配，均不再挂已完成的 FE 接线棒，不改变必达链的 BLOCKED 状态。
- 已完成视觉：导航/顶栏/浮动面板、玻璃拟态 theme、三套底图切换、`@ant-design/x` AI 助手页及可执行模糊层预算检查；字体跟随已批准画板，聊天问候语独立。历史返修过程留 Git。
- 剩余缺口：AppShell 手写 CSS module 的 antd Layout/Menu 欠债、职务来源、头像取图三项未知仍保留；用户身份读取棒另登记多部门 `orginfo` 形态、`isMobx` 取值、目录快照交叉校验、`sex` / `workcode` / `requestParams` 未消费等活欠债。
- 机会层 task_id、依赖、BLOCKED 条件和活欠债只见 `docs/phase2/PHASE2_PLAN.md` 的现役 DAG 与欠债表；分配 ID 不等于排期，不重排必达链。
