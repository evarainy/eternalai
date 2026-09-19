# Phase 2 当前状态

- 当前治理基线 task_id：`P2-GOV-SYNC-064`（C 档、串行；2026-09-17 同步已定裁决、分段交付、后继指针及九条非阻断欠债，承担 A 类机械同步；本棒不产生新的架构裁决）。
- 当前实现 task_id：`P2-AUDIT-EVAL-POSTCOND-001`（A 档、串行，承担 A 类同步；实现与本地验证完成，待独立 Monitor/静态评审；未 push/合并）。
- 本棒完成情况：`oa.read_overview/1.1.0` 以两步独立不可变观察执行 `oa_read_overview_v1` 确定性保真核验；核验失败阻断 Task/Response 成功与成功 Memory，上游失败保留原码。Trace 单条事件分别记录终态汇总和业务核验；其他 capability 为 not_evaluated。未扩大为 OA 源真实性、审批后态或真实部署验收。
- 当前实现后继指针：留空（本棒没有已决且唯一的后继）；`P2-AUDIT-WO-LIFECYCLE-001`、组织身份集成 `P2-TENANT-IDENTITY-001`、聊天回退 `P2-RUNTIME-DIRECT-ANSWER-001` 的既有指针保留，不重排现役 DAG。

- 审计 #1 `P2-AUDIT-LOGOUT-001`：**停摆待裁决**。三轮监理已用满；第 3 轮 FAIL 的 B2 为未变异候选自带前端用例 `App.test.tsx::logout_refresh_and_other_tab_revalidate_with_server` 失败：`/me` 401 广播后 query 缓存应为空，实测剩 1 项，未证实数据泄漏。候选停在任务分支，未合并。

## 已登记验证基线

### 当前首例确定性后置核验候选基线（2026-09-19）

以下为 `P2-AUDIT-EVAL-POSTCOND-001` 本地候选实测；独立 Monitor、静态评审与远端 CI 尚未执行，不作为已合并基线。

- pytest：`3906 passed, 0 failed, 0 skipped`（固定测试库后端全量，113 warnings），后台最终 exit=0，P4 observer authoritative PASS；未使用 `--ignore=`。Windows access violation 按 2026-09-10 裁决记录。
- `tests/architecture/`：`133 passed`，P4 observer authoritative PASS。
- Golden Gate：`34/34 passed, 0 skipped, 0 failed`（positive `13/13`、negative/boundary `21/21`）。
- 规则/ports 定向 99 项、Workflow/编排定向 171 项、Runtime/生产/投影定向 158 项通过；补充版本/归属/接线与既有精确调用适配定向 98 项、最终新增生产 HTTP/PG 定向 15 项通过。上述集合存在交集，不相加为全量数字。
- Ruff、mypy（135 source files）、依赖检查、13 个改动测试文件逐项弱测试检查通过。无前端改动，未增加前端套餐。
- 前端基线沿用 2026-09-17 / `P2-AUDIT-OA-TODO-CONVERGE-001` 已合并证据：完整入口 `785 passed`（组件 `423`、单元 `358`、OpenAPI `4`），typecheck/lint/build 通过；本棒未重跑，不标为本次实测。
- 保留全部测试、仅回退 Runtime 门禁/步骤观察采集/pending 精确比较时分别 2/1/6 项断言变红；恢复生产文件原字节后对应全部通过。合成签名 HTTP 经真实 Runtime/Workflow/Gateway/Policy/PG Task/Registry/Trace，核验失败不补查、不写成功 Memory，Trace 故障不返回成功。
- 固定测试库 `127.0.0.1:15432` revision 核对为 `20260915_120000`；本棒无 schema 或迁移改动。未执行真实 OA/vLLM、生产登记、部署或审批后态核验。
- 已合并的 OA 待办收敛 P 段仍按雨爷 2026-09-17 接受的分段交付有效，审计 #6 的 done_when 保持 P+D；D 及真实现场验收继续受外部输入/授权阻塞。既有证据留 PR 与 Git，本棒不核销其欠债。

### 既有 Top-K 修复候选记录（2026-09-16，非本轮复测）

- 定向 `708 passed`（含非仓库 cwd 的真实 Golden runner）、Registry/Admin `65 passed`、架构 `125 passed`、Golden `34/34`（negative/boundary `21/21`）、前端 Registry `9 passed`；Ruff、mypy `128 source files`、24 个候选改动测试文件的弱测试检查通过。
- 9 项回退/故障注入均被断言捕获并在恢复后通过：7 个评分修复点、第九候选 HTTP 接线、真实 PostgreSQL 错主体绑定。双主体成功记忆、各自真实绑定与非法会话拒绝已核对；多绑定范围使用现役内存 Identity resolver 的合成行验证，未声称 PostgreSQL OA 支持多绑定。
- 固定测试库入口后端全量 `3611 passed, 0 failed, 0 skipped`，后台最终 exit=0。Windows access violation 按 2026-09-10 裁决处理：错误绑定反证仍以断言失败、exit=1 结束，恢复后 exit=0；CI 尚未运行。
- 独立 Monitor、grok 评审桥与远端 CI 均未执行；本候选不能作为已合并或已获独立 PASS 的基线。真实 vLLM 语义召回与真实 OA E2E 未验证。

### 当前前端候选（2026-09-14）

- 当前实现候选 task_id：`P2-AUDIT-LIST-ORDER-001`（B 档、串行，承担 A 类同步；实现与本地验证完成，待 grok 评审，未 push/合并）。前置 `P2-FE-DISPATCH-WIRING-001` 已合入主干。
- 本棒完成情况：为现有有界工作事项列表增加服务端唯一默认顺序（截止时间升序且 NULL 最后、首次入库时间降序、唯一 ID 的 C collation 升序），超限时返回确定前 200 条；搜索页与工作事项页超限说明已同步。未做分页、无 schema。
- 当前实现后继指针：留空（本棒没有已决且唯一的后继）；D-6 有限替换与旧债归档由 GOV-SYNC 登记。组织身份集成仍指向 `P2-TENANT-IDENTITY-001`，聊天回退仍指向 `P2-RUNTIME-DIRECT-ANSWER-001`；内部任务生命周期与附件按现役 DAG 独立承接。

## 已登记验证基线

### 当前有界列表排序候选（2026-09-14）

- `P2-AUDIT-LIST-ORDER-001`：定向 pytest `83 passed`（`tests/infra/persistence/work_object/test_postgresql_work_object_store.py` 与 `tests/api/test_work_objects.py`，未使用 `--ignore=`）；架构 `124 passed`；mypy `127 source files`；Ruff 通过。搜索页组件 `22 passed`、工作事项页 `27 passed`（各独立 vitest 进程）；`web lint` 与 `typecheck` 通过。四个改动测试文件弱测试检查通过。去掉 `ORDER BY` 后 SQL 合同与混合权威 ID 序列断言变红，恢复后变绿。未跑全量 pytest、Golden、前端全量或 OpenAPI 重生成。

### 已合入主干的后端候选（2026-09-14）

- `P2-AUDIT-DEPT-DEFAULT-001`：已按显式跨部门允许集合收窄派发授权；后端 `3533 passed, 0 failed, 0 skipped`，Golden `34/34 passed`，架构 `124 passed`，mypy `127 source files`，Ruff 通过；定向 `348 passed`、迁移回归与 policy 定向 `11 passed`、生产变异 `19/19` 被捕获，十个改动测试文件弱测试检查通过。该候选已由本次主干合入带入当前任务分支。
- 其独立 Monitor 结论为 r2 PASS；本前端棒未重跑后端 pytest、数据库或 Golden。

### 已合入主干的前端候选（2026-09-14）

- `P2-FE-DISPATCH-WIRING-001`：前端全量覆盖 `776 passed`（组件 415、单元 358、OpenAPI 3；最终覆盖无失败/跳过）。组件来自全量入口的逐文件独立进程阶段；获准更新旧交互静态断言后，完整单元与 OpenAPI 阶段通过，未重复运行不受影响的组件。初次失败与超时单跑复核保留在 PR 证据中，不将初次全量命令写成 exit=0。
- lint / typecheck / build 通过；九个改动测试的弱测试检查通过。29 项生产变异反证均指定断言红、恢复原哈希；r1 再核对当前生产哈希一致。构建的既有 chunk 体积告警仍保留，不改阈值换绿。
- Chromium 合成页面已核对桌面 1440、窄屏 390、纽约 DST 双偏移显式选择及发布网络 body/回执；此为前端合成接线，真实 OA Source、首同步与生产端到端验收未完成。该候选已合入主干。

### 已合并后端与草稿来源

- 后端、Golden、架构、mypy 与 Ruff 沿用 **2026-09-14 / `P2-ORGDIR-PERSON-SYNC-001`** 已合并证据：pytest `3459 passed, 0 failed, 0 skipped`，Golden `34/34`（negative 21/21、positive 13/13），架构 `124 passed`，mypy `126 source files`，Ruff 通过。本前端棒未重跑后端 pytest、数据库或 Golden。
- 历史 pytest：`3459 passed, 0 failed, 0 skipped`（未使用 `--ignore=`；2026-09-14 / `P2-ORGDIR-PERSON-SYNC-001` 后台全量最终 exit=0）。
- 历史 Golden Gate：`34/34 passed, 0 skipped, 0 failed`（negative 21/21，positive 13/13；2026-09-14 / `P2-ORGDIR-PERSON-SYNC-001`）。
- 历史 `tests/architecture/`：`124 passed`（2026-09-14 / `P2-AUDIT-LIST-ORDER-001` 独立架构命令；P4 observer authoritative PASS）。
- `P2-AUDIT-DRAFT-ISOLATION-001` 已合并：草稿仅在当前认证会话内存暂存，同代 SPA 往返可恢复；刷新、退出、有效 401、认证换代与页面生命周期失效后不恢复。本棒沿用捕获 session token 的接口，不恢复 localStorage 或无归属旧草稿。

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

- 组织目录与身份：`P2-ORGDIR-PERSON-SYNC-001` 已合并交付姓名镜像、零岗位归一化、候选读端点、同步状态/调度、陈旧度授权门、本人读取回落与非阻断诊断。生产 OA HTTP Source 仍缺失，首次真实目录及真实周期更新未交付；既有验收只有合成 Source + 真实 PG/HTTP 验收。`P2-TENANT-IDENTITY-001` 仍承接更广泛的可信组织身份来源、sessions 与 identity binding，本棒单目录仅服务 default 租户。生命周期、前端接线、显示名语义、多 membership 与监区名单风险仍见 `PHASE2_PLAN.md`。
- 编排接缝：`P2-AGENT-ORCH-SEAM-001` 已合并交付；`AgentOrchestrationPort` 的生产接线已收口。本状态不把它与仍未实例化的 `WorkflowEngineAdapter` 欠债混同。
- 租户切片历史：2026-09-01 开工时连接库 tasks=0、distinct task_id=0；更早的 115/115 也仅为历史快照。本治理棒未查询数据库；升级前 Task 保持 `tenant_id=NULL`，对 Admin fail-closed 不可见，不猜值、不回填。
- 前端后继：原 `P2-FE-DISPATCH-FORM-001` / `P2-FE-APPS-001` 已由 `P2-FE-PAGE-CONTRACT-001` 合并交付并关闭，不再单独开棒；页面主体来源为已完成并合并的 `P2-FE-VISUAL-REFACTOR-001`。`P2-INTERNAL-WO-DISPATCH-001` 的后端合同现已到位，前端选择、发布接线、结果呈现与显示名语义已由 `P2-FE-DISPATCH-WIRING-001` 合并交付；服务端草稿与解析义务保留待 GOV-SYNC 裁定，不改变必达链的 BLOCKED 状态。
- 已完成视觉：导航/顶栏/浮动面板、玻璃拟态 theme、三套底图切换、`@ant-design/x` AI 助手页及可执行模糊层预算检查；字体跟随已批准画板，聊天问候语独立。历史返修过程留 Git。
- 剩余缺口：AppShell 手写 CSS module 的 antd Layout/Menu 欠债、职务来源、头像取图三项未知仍保留；用户身份读取棒另登记多部门 `orginfo` 形态、`isMobx` 取值、目录快照交叉校验、`sex` / `workcode` / `requestParams` 未消费等活欠债。
- 机会层 task_id、依赖、BLOCKED 条件和活欠债只见 `docs/phase2/PHASE2_PLAN.md` 的现役 DAG 与欠债表；分配 ID 不等于排期，不重排必达链。
