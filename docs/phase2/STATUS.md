# Phase 2 当前状态

- 当前治理基线 task_id：`P2-GOV-SYNC-DECISIONS-055`（C 档；09-04 / 09-06 / 09-07 裁决落盘并全量复扫；连同既有开发助手治理条目共 11 条，本棒承担 A 类同步）。
- 当前实现基线 task_id：`P2-FE-PAGE-CONTRACT-001`（B 档；串行单 lane，承担 A 类同步）。本棒交付既有交办页、软件中心与新建表单的合同守卫、空态收口与命名守卫；页面主体由 `P2-FE-VISUAL-REFACTOR-001` 建成。候选待 PR 集成，未合并。

## 已登记验证基线

以下后端、Golden、architecture 三行来源于 **2026-09-07 / `P2-RUNTIME-NO-CAPABILITY-COPY-001`** 的实测复核；本棒未重跑这三项全量检查，不将旧数字标为当前实测。前端两行来源为 **2026-09-08 / `P2-FE-PAGE-CONTRACT-001`** 的实测复核。

- pytest：`2832 passed, 143 warnings`（较进入本棒的 2798 增加 34；0 skipped，0 failed；未使用 `--ignore=`）
- Golden Gate：`34/34 passed, 0 skipped, 0 failed`（negative 21/21，positive 13/13；新增 GT-034/035，既有 32 题与冻结集合不变）
- `tests/architecture/`：`112 passed`（含 `api_no_infra_imports` 规则）
- 后端定向 pytest：`312 passed, 3 warnings, 0 failed`（`tests/runtime/`、`tests/knowledge/`、`tests/infra/llm/`）；Golden 定向 `451 passed`，最终意图与会话记忆定向分别 `20 passed`、`8 passed`。
- 前端全量 `pnpm --dir web test`：`487 passed, 0 failed, 0 skipped`（32 个测试文件；较进入本棒的 471 增加 16，全部为本棒新增的形态与命名守卫断言；2026-09-08 / `P2-FE-PAGE-CONTRACT-001` 实测复核）
- 前端本棒定向（2026-09-08 / `P2-FE-PAGE-CONTRACT-001`）：交办完整目录 `29 passed`（2 个文件），软件中心完整目录 `45 passed`（2 个文件），模糊层预算 `19 passed`、控件边界 `38 passed`；合计 6 个文件、`131 passed`，均无 `-t` 过滤，0 failed、0 skipped。37 条故障注入逐条实跑，均「断言变红 → 恢复字节后变绿」。
- ruff、mypy（112 个源文件）、依赖检查（53 项，零新增）与 20 个改动测试文件的弱测试检查均通过；四类变异均断言变红，恢复后同组测试全绿。

## 必达链与阻塞

- 必达五项：OA 只读纵切、Work Object + 最小工作台、后台轮询已完成；低风险写入未完成；Golden 部分完成（`P2-GOLDEN-001` 已完成）。
- 唯一剩余必达链：`P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002`。前者 **BLOCKED** 于 OA 审批提交协议结构，输入未到不开棒、不猜协议。
- Golden 只覆盖 Runtime 观察边界；工作台/隔离/审计由 API 与单元层验证。范围裁决见 `docs/phase2/DECISIONS.md`。

## 当前实现摘要

- 页面合同：既有交办结构与控件闭集、附件禁用及可执行空态、本机草稿失败反馈、软件中心三块顺序、状态/版本整块留位、新建表单九字段与单句审核告知、可访问名称及多状态命名守卫已收口；软件弹窗恢复共享字段边界与单焦点宿主。真实发布、附件上传、服务端草稿、软件登记审核和用户侧列表接口仍未接入。可信选择合同、截止时间时区与序列化两项欠债见 `PHASE2_PLAN.md`。
- 运行时基线沿用 `P2-RUNTIME-NO-CAPABILITY-COPY-001`：合法无匹配出口与终端用户短文案已合并；真实 vLLM 不可达项仍 BLOCKED，按雨爷单次授权先合并、网络恢复后补 smoke，其余取证 PASS；授权边界见 `DECISIONS.md` 2026-09-07 对应裁决。
- 意图输出必须显式给出 `match`；`none` 为合法无匹配，进入 `no_capability_found` 且 reason 为 `no_matching_capability`，`capability` 仍要求有效能力 ID。漏字段与矛盾组合保持 `schema_invalid`；无匹配文案保留「暂未接入」「能力」，只说明当前可用能力。Golden 为合成 LLM 输出经过真实 JSON 解析器的路由证据，未实测真实 vLLM 的语义判定。聊天回退后继为 `P2-RUNTIME-DIRECT-ANSWER-001`，本棒不新增终态。
- 身份读取：`GET /api/v1/me` 与 `GET /api/v1/me/avatar` 均为零参数端点，身份来自服务端 HMAC 签名会话票据；姓名不依赖 OA 可达，未认证一律 401。
- 部门：后端代持用户自身 OA Session 读取 `orginfo`，以标准库 `html.parser` 有界解析（输入 8192 / 锚点 16 / 标签 64，部门锚点必须恰好一条）；原始 HTML 不进响应。OA 失败不改变 `authenticated`，只体现在闭集 `org_status`（`ok` / `unbound` / `expired` / `unavailable` / `unparsable`）。
- 头像：后端代理对 `messagerurl` 做六步 URL 校验与图片 MIME 白名单检查，拒绝时传输层零调用；前端只见常量路径。两个身份端点均返回 `Cache-Control: no-store`。
- 会话恢复：前端 `authStore` 初值为 `unknown`，启动时向后端确认会话且不持久化；确认前 `ProtectedRoute` / `LoginRoute` 渲染 `BootGate`，不放行、不重定向。后端不可达时保持 `unknown`，显示「连不上服务器」和重试按钮。
- 身份消费：顶栏、用户菜单、AI 助手问候语已接真实数据；缺部门时顶栏只显示姓名，缺头像时退回姓氏首字。职务仍无数据源，按 2026-09-04 裁决留位并如实说明。
- 既有验证证据：有界解析五个上界固定断言、`api_no_infra_imports` 守卫及头像响应不缓存的回归检查已落地；身份读取棒登记了 11 条变异反证与 1 条守卫接线反证，本治理棒未重跑。

## 组织目录与前端机会层指针

- 组织目录集成后继：`P2-TENANT-IDENTITY-001`。`P2-TASK-TENANT-COLUMN-001` 只完成可信租户 `tasks` 切片；真实组织身份来源、sessions、identity binding、目录镜像的剩余 scope 须独立授权。`P2-INTERNAL-WO-SCOPE-001` 仍 BLOCKED 于唯一主负责人可信来源。
- 租户切片历史：2026-09-01 开工时连接库 tasks=0、distinct task_id=0；更早的 115/115 也仅为历史快照。本治理棒未查询数据库；升级前 Task 保持 `tenant_id=NULL`，对 Admin fail-closed 不可见，不猜值、不回填。
- 前端后继：原 `P2-FE-DISPATCH-FORM-001` / `P2-FE-APPS-001` 已由 `P2-FE-PAGE-CONTRACT-001` 合并交付并关闭，不再单独开棒；本棒候选待 PR 集成，页面主体来源为已完成并合并的 `P2-FE-VISUAL-REFACTOR-001`。后端合同到位后的前端接线后继不唯一，留待 GOV-SYNC 分配；不改变必达链的 BLOCKED 状态。
- 已完成视觉：导航/顶栏/浮动面板、玻璃拟态 theme、三套底图切换、`@ant-design/x` AI 助手页及可执行模糊层预算检查；字体跟随已批准画板，聊天问候语独立。历史返修过程留 Git。
- 剩余缺口：AppShell 手写 CSS module 的 antd Layout/Menu 欠债、职务来源、头像取图三项未知仍保留；用户身份读取棒另登记多部门 `orginfo` 形态、`isMobx` 取值、目录快照交叉校验、`sex` / `workcode` / `requestParams` 未消费等活欠债。
- 机会层 task_id、依赖、BLOCKED 条件和活欠债只见 `docs/phase2/PHASE2_PLAN.md` 的现役 DAG 与欠债表；分配 ID 不等于排期，不重排必达链。
