# Phase 2 当前状态

- 当前治理基线 task_id：`P2-GOV-SYNC-065`（C 档、串行；2026-09-20 同步已合并事实、评审桥非阻断欠债及已决后继，承担 A 类机械同步）。
- 最近已合并实现：`P2-AUDIT-EVAL-POSTCOND-001`（PR #199）与 `P2-AUDIT-WO-LIFECYCLE-001`（PR #200）。两棒均已取得独立 Monitor r1 PASS、Opus 评审桥 PASS，PR checks 与对应 merge Actions 均 success；证据留各 PR。
- 当前实现后继指针：`P2-AUDIT-LOGOUT-002` 为已决后继之一，须先更新方案并重新评审再施工；其余依赖与阻塞沿现役 DAG，不新增里程碑或重排。组织身份 `P2-TENANT-IDENTITY-001`、聊天回退 `P2-RUNTIME-DIRECT-ANSWER-001` 的既有指针保留。
- 审计 #1：旧 `P2-AUDIT-LOGOUT-001` 三轮监理 FAIL、未交付，不开第 4 轮。第 3 轮未变异用例在 `/me` 401 广播后缓存应为空而实测剩 1 项，未证实数据泄漏。2026-09-20 明确批准新 `002` 重新立项及新的三轮监理周期，换 ID 本身不产生豁免；新方案须承接生命周期吊销验收，见 `DECISIONS.md` 同日裁决。

## 已登记验证基线

### 当前已合并基线（2026-09-20，来源 P2-AUDIT-WO-LIFECYCLE-001）

以下为实现候选及其独立监理的既有证据，本治理棒未重跑业务测试，不将历史实测写成本棒实测。

- pytest：`3982 passed, 0 failed, 0 skipped`（固定测试库后端全量，113 warnings；最终 exit=0，P4 observer authoritative PASS；未使用 `--ignore=`）。Windows access violation 按 2026-09-10 裁决记录，生产反证仍以真实断言失败退出。
- `tests/architecture/`：`134 passed`。
- Golden Gate：`34/34 passed, 0 skipped, 0 failed`（positive 13/13、negative/boundary 21/21）。
- 前端最终覆盖 809 项通过（组件 439、单元 365、OpenAPI 5）；组件/单元入口通过，OpenAPI 顺序期望机械适配后单独通过。后续 StrictMode 面板回归独立 10 项通过；不把初次入口写成一次全量 exit=0。
- Ruff、mypy（136 source files）、前端 lint/typecheck/build 与 15 个改动测试文件弱测试检查通过。真实 API/PG 覆盖认证/CSRF、锁内授权、双连接竞争、同 key 重放、事务回滚、完成窗口与迁移往返/有数据拒退；合成 A/B 身份浏览器贯通发布、接单、反馈、办结和回读。
- 仓库迁移 head：`20260920_120000`。固定测试库在该实现及监理收口时均回退到 `20260915_120000`；本治理棒未查询数据库，不声明测试库当前版本。
- 真实目录 Source、真实 OA 账号、OA 已办源与退出吊销未验收；离岗交接、跨刷新未决命令恢复、吊销链复验及桥发现见 `PHASE2_PLAN.md`。

## 本批及前置已合并事实

| task_id | 已交付范围与保留边界 |
|---|---|
| `P2-AUDIT-EVAL-POSTCOND-001` | 首例 `oa.read_overview/1.1.0` 确定性后置核验已合并。2026-09-19 历史实测为后端 3906、架构 133、Golden 34/34（负向/边界 21/21），不替换上面的当前基线；通用返回校验、审批后态和真实现场验收仍未完成。 |
| `P2-AUDIT-WO-LIFECYCLE-001` | 内部事项接单、文本反馈、自行办结、活动分页和近 30 天内部完成列表已合并；仅接单人可反馈/办结，发布回执仍为初始快照，不将 OA 消失视为办结。 |
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

- 页面合同：既有交办结构与控件闭集、附件禁用、草稿失败反馈及软件中心合同保留；`P2-FE-DISPATCH-WIRING-001` 已接通目录选择、派发、时区与内部只读事项。列表保留有界稳定排序与超限说明；已合并 P 段新增完整批次消失对账、未再确认历史区、同步状态与成功空态，未接 D 可信办结源。草稿沿用会话内存；解析、附件上传、服务端草稿、软件登记审核和用户侧软件列表接口仍未接入。生产选人仍受真实目录 Source/首同步阻塞。
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
