# Phase 2 当前状态

- 当前治理基线 task_id：`P2-GOV-SYNC-063`（C 档；同步 2026-09-14 已合并的四根审计修复棒、组织目录方案进度与新环境欠债，并纠正审计 #17 的 Workflow 接缝事实）。
- 当前实现候选 task_id：`P2-AUDIT-DEPT-DEFAULT-001`（A 档、串行，承担 A 类同步；本地候选验证完成，独立 Monitor r2 PASS，待 grok 评审与 required checks，未 push/合并）。上一已合并功能基线为 `P2-ORGDIR-PERSON-SYNC-001`。
- 本棒完成情况：已按显式跨部门允许集合收窄派发授权，候选验证与变异反证完成；独立 Monitor 三轮结论为 r1 监理自建用例问题未完成、r2 PASS，代码 grok 评审桥与 required checks 尚待完成。
- 当前实现后继指针：`P2-FE-DISPATCH-WIRING-001`；组织目录候选与本棒草稿隔离候选均须先完成独立审查与集成。组织身份集成仍指向 `P2-TENANT-IDENTITY-001`，聊天回退仍指向 `P2-RUNTIME-DIRECT-ANSWER-001`；内部任务生命周期与附件按 `PHASE2_PLAN.md` 现役 DAG 独立承接。
- 本棒新增欠债指针：`PHASE2_PLAN.md` 活欠债表中的“上线前真实跨部门允许名单待提供”和“派发名单漂移诊断的实际部署消费未验收”；后继绑定与旧 fail-open 残余由 GOV-SYNC 承接。

## 已登记验证基线

### 本棒候选验证

- 本棒候选验证（2026-09-14 / `P2-AUDIT-DEPT-DEFAULT-001`）：后端 `3533 passed, 0 failed, 0 skipped`，Golden `34/34 passed`，架构 `124 passed`，mypy `127 source files`，Ruff 通过；定向 `348 passed`、迁移回归与 policy 定向 `11 passed`、生产变异 `19/19` 被捕获，十个改动测试文件弱测试检查通过。全量、Golden、架构与 mypy 均有对应证据，前端未改且沿用主干现值。

### 草稿隔离待审候选

- `P2-AUDIT-DRAFT-ISOLATION-001`（A 档；2026-09-14）：两类草稿改为当前认证会话内存快照，启动仅移除两固定旧键且不读取旧值；刷新、退出、有效 401、认证换代与页面生命周期失效后不恢复。独立 Monitor 三轮最终 r3 PASS；当前为待合并候选，grok 评审桥与 required checks 尚待完成；不替换下方已合并基线。
- 2026-09-14 / r2 前端全量 `638 passed`（组件 333、单元 302、OpenAPI 3），BindingsPage 独立两次均 `13 passed`；r1 的 lint、build、Golden `34/34`、九个新改测试弱测试检查通过，初轮最终 typecheck 通过。后端沿用同日 r1 的 `3346 passed, 1 failed`，唯一失败为已登记的 `InMemoryHumanGate` 幂等语义用例，按续派裁定以 CI 为准继续；r2 未运行后端或连接测试库的测试，不将该结果写成后端全绿。
- 已决后继为 `P2-FE-DISPATCH-WIRING-001`：本棒须先合并，后继开工前按获批方案修订旧本机恢复/C6/P5，消费捕获会话 token 的内存接口；组织目录棒的现役前置保留。服务端草稿、发布与审核义务继续记原欠债。本棒与组织目录棒共享两份状态文档，集成时须核对各自候选事实；不据此重排 DAG。

### 已合并来源

以下每一条结果都带来源棒与日期：后端、Golden、架构、mypy 与 Ruff 采用 **2026-09-14 / `P2-ORGDIR-PERSON-SYNC-001`** 最终整体实现的本机实测；独立 Monitor 三轮后已按当次人工裁决完成扩域修复与指定验证补齐，不开第四轮；grok 尚待执行，不能视作独立 Monitor PASS、合并或生产验收。前端全量沿用下列已合并棒的历史结果；本棒另做生成客户端的定向验证。解释器获取欠债另列活欠债。

- pytest：`3459 passed, 0 failed, 0 skipped`（未使用 `--ignore=`；2026-09-14 / `P2-ORGDIR-PERSON-SYNC-001` 后台全量最终 exit=0）。
- Golden Gate：`34/34 passed, 0 skipped, 0 failed`（negative 21/21，positive 13/13；2026-09-14 / `P2-ORGDIR-PERSON-SYNC-001`）。
- `tests/architecture/`：`124 passed`（2026-09-14 / `P2-ORGDIR-PERSON-SYNC-001` 独立架构命令；P4 observer authoritative PASS）。
- 前端全量 `pnpm --dir web test`：`585 passed`（`P2-AUDIT-VITE-001` 复测；本棒未重跑）。
- mypy：`126 source files`；Ruff：通过（2026-09-14 / `P2-ORGDIR-PERSON-SYNC-001`）。本棒 OpenAPI `3 passed`，前端 typecheck/lint/build 通过；这些不是前端全量或页面交互实测。

## 必达链与阻塞

- 必达五项：OA 只读纵切、Work Object + 最小工作台、后台轮询已完成；低风险写入未完成；Golden 部分完成（`P2-GOLDEN-001` 已完成）。
- 唯一剩余必达链：`P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002`。前者 **BLOCKED** 于 OA 审批提交协议结构，输入未到不开棒、不猜协议。
- Golden 只覆盖 Runtime 观察边界；工作台/隔离/审计由 API 与单元层验证。范围裁决见 `docs/phase2/DECISIONS.md`。

## 当前实现摘要

- 页面合同：既有交办结构与控件闭集、附件禁用及可执行空态、草稿失败反馈、软件中心三块顺序、状态/版本整块留位、新建表单九字段与单句审核告知、可访问名称及多状态命名守卫已收口；软件弹窗恢复共享字段边界与单焦点宿主。草稿隔离待审候选将两类本机持久草稿改为会话内存，状态见上方；真实发布、附件上传、服务端草稿、软件登记审核和用户侧列表接口仍未接入。可信选择合同、截止时间时区与序列化两项欠债见 `PHASE2_PLAN.md`。
- 运行时基线沿用已合并的 `P2-RUNTIME-NO-CAPABILITY-COPY-001`：2026-09-10 实测 `http://34.74.11.38:8011/v1` HTTP 可达，`/v1/models` 返回单一 `glm-4.7`（`root=/mnt/models/GLM-4.7-Flash`，`max_model_len=200000`）；现役 provider → raw JSON mode 4 次真实推理成功，内存意图路由 3/3 通过。完整 OA E2E 尚未完成，冒烟包本地缺 `sqlalchemy`，且全链路涉及真实配置读取与持久化；`IntentRouter` 的 `match=none` 不能单独证明下游 `no_capability_found` 终态；剩余义务与边界见 `PHASE2_PLAN.md`，不得通过修改仓库 URL 或配置规避。
- 意图输出必须显式给出 `match`；`none` 为合法无匹配，进入 `no_capability_found` 且 reason 为 `no_matching_capability`，`capability` 仍要求有效能力 ID。漏字段与矛盾组合保持 `schema_invalid`；无匹配文案保留「暂未接入」「能力」，只说明当前可用能力。Golden 为合成 LLM 输出经过真实 JSON 解析器的路由证据，未实测真实 vLLM 的语义判定。聊天回退后继为 `P2-RUNTIME-DIRECT-ANSWER-001`，本棒不新增终态。
- 身份读取：`GET /api/v1/me` 与 `GET /api/v1/me/avatar` 均为零参数端点，身份来自服务端 HMAC 签名会话票据；姓名不依赖 OA 可达，未认证一律 401。
- 部门：后端代持用户自身 OA Session 读取 `orginfo`，以标准库 `html.parser` 有界解析（输入 8192 / 锚点 16 / 标签 64，部门锚点必须恰好一条）；原始 HTML 不进响应。OA 失败不改变 `authenticated`，只体现在闭集 `org_status`（`ok` / `unbound` / `expired` / `unavailable` / `unparsable`）。
- 头像：后端代理对 `messagerurl` 做六步 URL 校验与图片 MIME 白名单检查，拒绝时传输层零调用；前端只见常量路径。两个身份端点均返回 `Cache-Control: no-store`。
- 会话恢复：前端 `authStore` 初值为 `unknown`，启动时向后端确认会话且不持久化；确认前 `ProtectedRoute` / `LoginRoute` 渲染 `BootGate`，不放行、不重定向。后端不可达时保持 `unknown`，显示「连不上服务器」和重试按钮。
- 身份消费：顶栏、用户菜单、AI 助手问候语已接真实数据；缺部门时顶栏只显示姓名，缺头像时退回姓氏首字。职务仍无数据源，按 2026-09-04 裁决留位并如实说明。
- 既有验证证据：有界解析五个上界固定断言、`api_no_infra_imports` 守卫及头像响应不缓存的回归检查已落地；身份读取棒登记了 11 条变异反证与 1 条守卫接线反证，本治理棒未重跑。

## 组织目录与前端机会层指针

- 组织目录与身份：`P2-ORGDIR-PERSON-SYNC-001` 已合并交付姓名镜像、零岗位归一化、候选读端点、同步状态/调度、陈旧度授权门、本人读取回落与非阻断诊断。生产 OA HTTP Source 仍缺失，首次真实目录及真实周期更新未交付；既有验收只有合成 Source + 真实 PG/HTTP 验收。`P2-TENANT-IDENTITY-001` 仍承接更广泛的可信组织身份来源、sessions 与 identity binding，本棒单目录仅服务 default 租户。生命周期、前端接线、显示名语义、多 membership 与监区名单风险仍见 `PHASE2_PLAN.md`。
- 编排接缝：`P2-AGENT-ORCH-SEAM-001` 已合并交付；`AgentOrchestrationPort` 的生产接线已收口。本状态不把它与仍未实例化的 `WorkflowEngineAdapter` 欠债混同。
- 租户切片历史：2026-09-01 开工时连接库 tasks=0、distinct task_id=0；更早的 115/115 也仅为历史快照。本治理棒未查询数据库；升级前 Task 保持 `tenant_id=NULL`，对 Admin fail-closed 不可见，不猜值、不回填。
- 前端后继：原 `P2-FE-DISPATCH-FORM-001` / `P2-FE-APPS-001` 已由 `P2-FE-PAGE-CONTRACT-001` 合并交付并关闭，不再单独开棒；页面主体来源为已完成并合并的 `P2-FE-VISUAL-REFACTOR-001`。`P2-INTERNAL-WO-DISPATCH-001` 的后端合同现已到位，前端选择、发布接线、终态、草稿持久化与显示名语义由 `P2-FE-DISPATCH-WIRING-001` 承接；不改变必达链的 BLOCKED 状态。
- 已完成视觉：导航/顶栏/浮动面板、玻璃拟态 theme、三套底图切换、`@ant-design/x` AI 助手页及可执行模糊层预算检查；字体跟随已批准画板，聊天问候语独立。历史返修过程留 Git。
- 剩余缺口：AppShell 手写 CSS module 的 antd Layout/Menu 欠债、职务来源、头像取图三项未知仍保留；用户身份读取棒另登记多部门 `orginfo` 形态、`isMobx` 取值、目录快照交叉校验、`sex` / `workcode` / `requestParams` 未消费等活欠债。
- 机会层 task_id、依赖、BLOCKED 条件和活欠债只见 `docs/phase2/PHASE2_PLAN.md` 的现役 DAG 与欠债表；分配 ID 不等于排期，不重排必达链。
