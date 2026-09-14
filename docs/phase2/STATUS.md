# Phase 2 当前状态

- 当前治理基线 task_id：`P2-GOV-SYNC-063`（C 档；同步 2026-09-14 已合并的四根审计修复棒、组织目录方案进度与新环境欠债，并纠正审计 #17 的 Workflow 接缝事实）。
- 当前实现基线 task_id：`P2-INTERNAL-WO-DISPATCH-001`（A 档；已合并）。后端 `3337 passed, 0 failed, 0 skipped`、Golden `34/34`、mypy `121 source files`、Ruff 通过；这些基线均来自该棒最终候选的 2026-09-10 实测，经独立 Monitor 三轮复核，最终 `VERDICT=PASS`。`P2-AUDIT-TITLE-001`、`P2-AUDIT-GUARDS-001`、`P2-AUDIT-KNOWLEDGE-001`、`P2-AUDIT-VITE-001` 四根审计修复棒已合并，新增验证来源见下方。
- 当前实现后继指针：`P2-ORGDIR-PERSON-SYNC-001` 方案已通过自审与 grok 两轮评审，等待雨爷对 schema 与新增读端点合同的专项授权；其后为 `P2-FE-DISPATCH-WIRING-001`。组织身份集成仍指向 `P2-TENANT-IDENTITY-001`，聊天回退仍指向 `P2-RUNTIME-DIRECT-ANSWER-001`；内部任务生命周期与附件按 `PHASE2_PLAN.md` 现役 DAG 独立承接。

## 已登记验证基线

以下每一条结果都带来源棒与日期：后端 `3337`、Golden、mypy 与 Ruff 均来自 **2026-09-10 / `P2-INTERNAL-WO-DISPATCH-001`** 最终候选，并经独立 Monitor 三轮复核，最终 `VERDICT=PASS`；`tests/architecture/` 与前端结果分别采用本批审计修复棒的实测来源；本治理棒未重跑。解释器获取欠债另列活欠债。

- pytest：`3337 passed, 0 failed, 0 skipped`（未使用 `--ignore=`；2026-09-10 / `P2-INTERNAL-WO-DISPATCH-001` 最终候选，独立 Monitor 三轮复核，最终 `VERDICT=PASS`；本棒未重跑）。
- Golden Gate：`34/34 passed, 0 skipped, 0 failed`（negative 21/21，positive 13/13；2026-09-10 / `P2-INTERNAL-WO-DISPATCH-001` 最终候选，独立 Monitor 三轮复核，最终 `VERDICT=PASS`；本棒未重跑）。
- `tests/architecture/`：`124 passed`（2026-09-14 / `P2-AUDIT-GUARDS-001` 复测；本棒未重跑）。
- 前端全量 `pnpm --dir web test`：`585 passed`（`P2-AUDIT-VITE-001` 复测；本棒未重跑）。
- mypy：`121 source files`；Ruff：通过（2026-09-10 / `P2-INTERNAL-WO-DISPATCH-001` 最终候选，独立 Monitor 三轮复核，最终 `VERDICT=PASS`；本棒未重跑）。

## 必达链与阻塞

- 必达五项：OA 只读纵切、Work Object + 最小工作台、后台轮询已完成；低风险写入未完成；Golden 部分完成（`P2-GOLDEN-001` 已完成）。
- 唯一剩余必达链：`P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002`。前者 **BLOCKED** 于 OA 审批提交协议结构，输入未到不开棒、不猜协议。
- Golden 只覆盖 Runtime 观察边界；工作台/隔离/审计由 API 与单元层验证。范围裁决见 `docs/phase2/DECISIONS.md`。

## 当前实现摘要

- 页面合同：既有交办结构与控件闭集、附件禁用及可执行空态、本机草稿失败反馈、软件中心三块顺序、状态/版本整块留位、新建表单九字段与单句审核告知、可访问名称及多状态命名守卫已收口；软件弹窗恢复共享字段边界与单焦点宿主。真实发布、附件上传、服务端草稿、软件登记审核和用户侧列表接口仍未接入。可信选择合同、截止时间时区与序列化两项欠债见 `PHASE2_PLAN.md`。
- 运行时基线沿用已合并的 `P2-RUNTIME-NO-CAPABILITY-COPY-001`：2026-09-10 实测 `http://34.74.11.38:8011/v1` HTTP 可达，`/v1/models` 返回单一 `glm-4.7`（`root=/mnt/models/GLM-4.7-Flash`，`max_model_len=200000`）；现役 provider → raw JSON mode 4 次真实推理成功，内存意图路由 3/3 通过。完整 OA E2E 尚未完成，冒烟包本地缺 `sqlalchemy`，且全链路涉及真实配置读取与持久化；`IntentRouter` 的 `match=none` 不能单独证明下游 `no_capability_found` 终态；剩余义务与边界见 `PHASE2_PLAN.md`，不得通过修改仓库 URL 或配置规避。
- 意图输出必须显式给出 `match`；`none` 为合法无匹配，进入 `no_capability_found` 且 reason 为 `no_matching_capability`，`capability` 仍要求有效能力 ID。漏字段与矛盾组合保持 `schema_invalid`；无匹配文案保留「暂未接入」「能力」，只说明当前可用能力。Golden 为合成 LLM 输出经过真实 JSON 解析器的路由证据，未实测真实 vLLM 的语义判定。聊天回退后继为 `P2-RUNTIME-DIRECT-ANSWER-001`，本棒不新增终态。
- 身份读取：`GET /api/v1/me` 与 `GET /api/v1/me/avatar` 均为零参数端点，身份来自服务端 HMAC 签名会话票据；姓名不依赖 OA 可达，未认证一律 401。
- 部门：后端代持用户自身 OA Session 读取 `orginfo`，以标准库 `html.parser` 有界解析（输入 8192 / 锚点 16 / 标签 64，部门锚点必须恰好一条）；原始 HTML 不进响应。OA 失败不改变 `authenticated`，只体现在闭集 `org_status`（`ok` / `unbound` / `expired` / `unavailable` / `unparsable`）。
- 头像：后端代理对 `messagerurl` 做六步 URL 校验与图片 MIME 白名单检查，拒绝时传输层零调用；前端只见常量路径。两个身份端点均返回 `Cache-Control: no-store`。
- 会话恢复：前端 `authStore` 初值为 `unknown`，启动时向后端确认会话且不持久化；确认前 `ProtectedRoute` / `LoginRoute` 渲染 `BootGate`，不放行、不重定向。后端不可达时保持 `unknown`，显示「连不上服务器」和重试按钮。
- 身份消费：顶栏、用户菜单、AI 助手问候语已接真实数据；缺部门时顶栏只显示姓名，缺头像时退回姓氏首字。职务仍无数据源，按 2026-09-04 裁决留位并如实说明。
- 既有验证证据：有界解析五个上界固定断言、`api_no_infra_imports` 守卫及头像响应不缓存的回归检查已落地；身份读取棒登记了 11 条变异反证与 1 条守卫接线反证，本治理棒未重跑。

## 组织目录与前端机会层指针

- 组织目录与身份：`P2-ORGDIR-PERSON-SYNC-001` 承接人员镜像、按部门读端点与定时同步方案；`P2-TENANT-IDENTITY-001` 仍承接更广泛的可信组织身份来源、sessions 与 identity binding。`P2-TASK-TENANT-COLUMN-001` 只完成可信租户 `tasks` 切片；目录镜像的剩余 scope 须独立授权。`P2-INTERNAL-WO-SCOPE-001` 与 `P2-INTERNAL-WO-DISPATCH-001` 已分别合并交付岗位派发授权、可见性 scope 及首次派发写端点；生命周期、前端接线、显示名语义、多 membership 与监区名单 fail-open 风险仍保留，见 `PHASE2_PLAN.md`。
- 编排接缝：`P2-AGENT-ORCH-SEAM-001` 已合并交付；`AgentOrchestrationPort` 的生产接线已收口。本状态不把它与仍未实例化的 `WorkflowEngineAdapter` 欠债混同。
- 租户切片历史：2026-09-01 开工时连接库 tasks=0、distinct task_id=0；更早的 115/115 也仅为历史快照。本治理棒未查询数据库；升级前 Task 保持 `tenant_id=NULL`，对 Admin fail-closed 不可见，不猜值、不回填。
- 前端后继：原 `P2-FE-DISPATCH-FORM-001` / `P2-FE-APPS-001` 已由 `P2-FE-PAGE-CONTRACT-001` 合并交付并关闭，不再单独开棒；页面主体来源为已完成并合并的 `P2-FE-VISUAL-REFACTOR-001`。`P2-INTERNAL-WO-DISPATCH-001` 的后端合同现已到位，前端选择、发布接线、终态、草稿持久化与显示名语义由 `P2-FE-DISPATCH-WIRING-001` 承接；不改变必达链的 BLOCKED 状态。
- 已完成视觉：导航/顶栏/浮动面板、玻璃拟态 theme、三套底图切换、`@ant-design/x` AI 助手页及可执行模糊层预算检查；字体跟随已批准画板，聊天问候语独立。历史返修过程留 Git。
- 剩余缺口：AppShell 手写 CSS module 的 antd Layout/Menu 欠债、职务来源、头像取图三项未知仍保留；用户身份读取棒另登记多部门 `orginfo` 形态、`isMobx` 取值、目录快照交叉校验、`sex` / `workcode` / `requestParams` 未消费等活欠债。
- 机会层 task_id、依赖、BLOCKED 条件和活欠债只见 `docs/phase2/PHASE2_PLAN.md` 的现役 DAG 与欠债表；分配 ID 不等于排期，不重排必达链。
