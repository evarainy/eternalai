# Phase 2 当前状态

- 当前治理基线 task_id：`P2-GOV-SYNC-063`（C 档；2026-09-14 治理同步）。本棒仅作 A 类机械同步，不重排 DAG 或裁定跨棒欠债。
- 当前实现候选 task_id：`P2-FE-DISPATCH-WIRING-001`（B 档、串行，承担 A 类同步；实现与本地验证完成，待 grok 评审，未 push/合并）。前置 `P2-ORGDIR-PERSON-SYNC-001` 与 `P2-AUDIT-DRAFT-ISOLATION-001` 均已合并，候选消费现役目录读端点与会话内存草稿。
- 本棒完成情况：按部门姓名选择、责任/可见范围只读摘要、浏览器时区与偏移、真实派发回执及页内同请求重试、刷新后结果待确认、内部事项 null 与 `view_only` 接线完成。D1 显示名与 D3 截止时间在本地候选中关闭；D2 可信选择与 D5 发布/草稿部分完成；新增 FE-G1/FE-G2，见 `PHASE2_PLAN.md`。
- 当前实现后继指针：留空（本棒没有已决且唯一的后继）；草稿解析、服务端草稿及新债后继由 GOV-SYNC 裁定。组织身份集成仍指向 `P2-TENANT-IDENTITY-001`，聊天回退仍指向 `P2-RUNTIME-DIRECT-ANSWER-001`；内部任务生命周期与附件按现役 DAG 独立承接。

## 已登记验证基线

### 当前前端候选（2026-09-14）

- `P2-FE-DISPATCH-WIRING-001`：前端全量覆盖 `776 passed`（组件 415、单元 358、OpenAPI 3；最终覆盖无失败/跳过）。组件来自全量入口的逐文件独立进程阶段；获准更新旧交互静态断言后，完整单元与 OpenAPI 阶段通过，未重复运行不受影响的组件。初次失败与超时单跑复核保留在 PR 证据中，不将初次全量命令写成 exit=0。
- lint / typecheck / build 通过；九个改动测试的弱测试检查通过。29 项生产变异反证均指定断言红、恢复原哈希；r1 再核对当前生产哈希一致。构建的既有 chunk 体积告警仍保留，不改阈值换绿。
- Chromium 合成页面已核对桌面 1440、窄屏 390、纽约 DST 双偏移显式选择及发布网络 body/回执；此为前端合成接线，真实 OA Source、首同步与生产端到端验收未完成。

### 已合并后端与草稿来源

- 后端、Golden、架构、mypy 与 Ruff 沿用 **2026-09-14 / `P2-ORGDIR-PERSON-SYNC-001`** 已合并证据：pytest `3459 passed, 0 failed, 0 skipped`，Golden `34/34`（negative 21/21、positive 13/13），架构 `124 passed`，mypy `126 source files`，Ruff 通过。本前端棒未重跑后端 pytest、数据库或 Golden。
- `P2-AUDIT-DRAFT-ISOLATION-001` 已合并：草稿仅在当前认证会话内存暂存，同代 SPA 往返可恢复；刷新、退出、有效 401、认证换代与页面生命周期失效后不恢复。本棒沿用捕获 session token 的接口，不恢复 localStorage 或无归属旧草稿。

## 必达链与阻塞

- 必达五项：OA 只读纵切、Work Object + 最小工作台、后台轮询已完成；低风险写入未完成；Golden 部分完成（`P2-GOLDEN-001` 已完成）。
- 唯一剩余必达链：`P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002`。前者 **BLOCKED** 于 OA 审批提交协议结构，输入未到不开棒、不猜协议。
- Golden 只覆盖 Runtime 观察边界；工作台/隔离/审计由 API 与单元层验证。范围裁决见 `docs/phase2/DECISIONS.md`。

## 当前实现摘要

- 页面合同：既有交办结构与控件闭集、附件禁用、草稿失败反馈及软件中心合同保留；本棒候选已接通目录选择、派发、时区与内部只读事项。草稿沿用会话内存；解析、附件上传、服务端草稿、软件登记审核和用户侧软件列表接口仍未接入。生产选人仍受真实目录 Source/首同步阻塞。
- 运行时基线沿用已合并的 `P2-RUNTIME-NO-CAPABILITY-COPY-001`：2026-09-10 实测 `http://34.74.11.38:8011/v1` HTTP 可达，`/v1/models` 返回单一 `glm-4.7`（`root=/mnt/models/GLM-4.7-Flash`，`max_model_len=200000`）；现役 provider → raw JSON mode 4 次真实推理成功，内存意图路由 3/3 通过。完整 OA E2E 尚未完成，冒烟包本地缺 `sqlalchemy`，且全链路涉及真实配置读取与持久化；`IntentRouter` 的 `match=none` 不能单独证明下游 `no_capability_found` 终态；剩余义务与边界见 `PHASE2_PLAN.md`，不得通过修改仓库 URL 或配置规避。
- 意图输出必须显式给出 `match`；`none` 为合法无匹配，进入 `no_capability_found` 且 reason 为 `no_matching_capability`，`capability` 仍要求有效能力 ID。漏字段与矛盾组合保持 `schema_invalid`；无匹配文案保留「暂未接入」「能力」，只说明当前可用能力。Golden 为合成 LLM 输出经过真实 JSON 解析器的路由证据，未实测真实 vLLM 的语义判定。聊天回退后继为 `P2-RUNTIME-DIRECT-ANSWER-001`，本棒不新增终态。
- 身份读取：`GET /api/v1/me` 与 `GET /api/v1/me/avatar` 均为零参数端点，身份来自服务端 HMAC 签名会话票据；姓名不依赖 OA 可达，未认证一律 401。
- 部门：后端代持用户自身 OA Session 读取 `orginfo`，以标准库 `html.parser` 有界解析（输入 8192 / 锚点 16 / 标签 64，部门锚点必须恰好一条）；原始 HTML 不进响应。OA 失败不改变 `authenticated`，只体现在闭集 `org_status`（`ok` / `unbound` / `expired` / `unavailable` / `unparsable`）。
- 头像：后端代理对 `messagerurl` 做六步 URL 校验与图片 MIME 白名单检查，拒绝时传输层零调用；前端只见常量路径。两个身份端点均返回 `Cache-Control: no-store`。
- 会话恢复：前端 `authStore` 初值为 `unknown`，启动时向后端确认会话且不持久化；确认前 `ProtectedRoute` / `LoginRoute` 渲染 `BootGate`，不放行、不重定向。后端不可达时保持 `unknown`，显示「连不上服务器」和重试按钮。
- 身份消费：顶栏、用户菜单、AI 助手问候语已接真实数据；缺部门时顶栏只显示姓名，缺头像时退回姓氏首字。职务仍无数据源，按 2026-09-04 裁决留位并如实说明。
- 既有验证证据：有界解析五个上界固定断言、`api_no_infra_imports` 守卫及头像响应不缓存的回归检查已落地；身份读取棒登记了 11 条变异反证与 1 条守卫接线反证，本治理棒未重跑。

## 组织目录与前端机会层指针

- 组织目录与身份：`P2-ORGDIR-PERSON-SYNC-001` 已合并姓名镜像、零岗位归一化、候选读端点、同步状态/调度、陈旧度授权门、本人读取回落与非阻断诊断。生产 OA HTTP Source 仍缺失，首次真实目录及真实周期更新未交付；候选只有合成 Source + 真实 PG/HTTP 验收。`P2-TENANT-IDENTITY-001` 仍承接更广泛的可信组织身份来源、sessions 与 identity binding，本棒单目录仅服务 default 租户。生命周期、生产目录前置、多 membership 与监区名单风险仍见 `PHASE2_PLAN.md`。
- 编排接缝：`P2-AGENT-ORCH-SEAM-001` 已合并交付；`AgentOrchestrationPort` 的生产接线已收口。本状态不把它与仍未实例化的 `WorkflowEngineAdapter` 欠债混同。
- 租户切片历史：2026-09-01 开工时连接库 tasks=0、distinct task_id=0；更早的 115/115 也仅为历史快照。本治理棒未查询数据库；升级前 Task 保持 `tenant_id=NULL`，对 Admin fail-closed 不可见，不猜值、不回填。
- 前端后继：原 `P2-FE-DISPATCH-FORM-001` / `P2-FE-APPS-001` 已由 `P2-FE-PAGE-CONTRACT-001` 合并交付并关闭，不再单独开棒；页面主体来源为已完成并合并的 `P2-FE-VISUAL-REFACTOR-001`。`P2-INTERNAL-WO-DISPATCH-001` 的后端合同现已到位，前端选择、发布接线、结果呈现与显示名语义已由 `P2-FE-DISPATCH-WIRING-001` 形成本地候选；服务端草稿与解析义务保留待 GOV-SYNC 裁定，不改变必达链的 BLOCKED 状态。
- 已完成视觉：导航/顶栏/浮动面板、玻璃拟态 theme、三套底图切换、`@ant-design/x` AI 助手页及可执行模糊层预算检查；字体跟随已批准画板，聊天问候语独立。历史返修过程留 Git。
- 剩余缺口：AppShell 手写 CSS module 的 antd Layout/Menu 欠债、职务来源、头像取图三项未知仍保留；用户身份读取棒另登记多部门 `orginfo` 形态、`isMobx` 取值、目录快照交叉校验、`sex` / `workcode` / `requestParams` 未消费等活欠债。
- 机会层 task_id、依赖、BLOCKED 条件和活欠债只见 `docs/phase2/PHASE2_PLAN.md` 的现役 DAG 与欠债表；分配 ID 不等于排期，不重排必达链。
