# Phase 2 当前状态

- 当前治理基线 task_id：`P2-GOV-SYNC-ASTRA-001`（C 档；开发助手规则与 skills 收敛，A/B/C 自审采用 high / medium / 无自审门禁；独立 Monitor → Opus 与项目红线保留）。
- 当前实现基线 task_id：`P2-FE-VISUAL-REFACTOR-001`；上一治理棒 `P2-GOV-SYNC-054`。返修经过用 Git 追溯；本治理棒未重跑生产测试，以下数字保留原实测日期。

## 已登记验证基线

| 检查 | 原实测结果 | 来源日期 / task_id |
|---|---|---|
| 全量 pytest | 2595 passed, 108 warnings, 0 skipped, 0 failed；未用 `--ignore=` | 2026-09-01 / `P2-TASK-TENANT-COLUMN-001` |
| 后端定向 pytest（contracts/runtime/api） | 429 passed, 81 warnings, 0 failed | 2026-09-01 / `P2-TASK-TENANT-COLUMN-001` |
| 前端 `pnpm --dir web test` | 453 passed, 31 文件, 0 failed, 0 skipped | 2026-09-04 / `P2-FE-VISUAL-REFACTOR-001` |
| Golden | 32/32 passed；negative 20/20、positive 12/12；0 skipped、0 failed | 2026-09-01 / `P2-TASK-TENANT-COLUMN-001` |
| `tests/architecture/` | 112 passed | 2026-09-04 / `P2-FE-VISUAL-REFACTOR-001` |

## 必达链与阻塞

- 必达五项：OA 只读纵切、Work Object + 最小工作台、后台轮询已完成；低风险写入未完成；Golden 部分完成（`P2-GOLDEN-001` 已完成）。
- 唯一剩余必达链：`P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002`。前者 **BLOCKED** 于 OA 审批提交协议结构，输入未到不开棒、不猜协议。
- Golden 只覆盖 Runtime 观察边界；工作台/隔离/审计由 API 与单元层验证。范围裁决见 `docs/phase2/DECISIONS.md`。

## 当前实现与机会层指针

- Task 从可信 `Principal` / `PrincipalOrgContext` 固化非空 tenant_id，缺失/空白租户在 SQL 写入前失败；Admin Task/event/关联 Binding 按 Task 租户列过滤，关闭逐 Task Trace 查询放大。升级前 Task 的 tenant_id 保持 NULL 并对 Admin fail-closed；Trace reader 与孤立动作 Trace 的租户合同不变。
- `P2-TASK-TENANT-COLUMN-001` 的合成 migration 往返验证保留行数与 task_id 集合；该棒开工时连接库 tasks=0、distinct task_id=0 属 **2026-09-01 历史实测**，本棒未查询数据库；更早的 115/115 也不作当前事实或回填依据。
- 组织目录集成后继：`P2-TENANT-IDENTITY-001`。现有 tasks 切片不覆盖真实组织身份来源、sessions、identity binding、目录镜像；剩余 scope 须独立授权。`P2-INTERNAL-WO-SCOPE-001` 仍 BLOCKED 于唯一主负责人可信来源。
- 前端已完成导航/顶栏/浮动面板、玻璃拟态 theme、三套底图切换、`@ant-design/x` AI 助手页及模糊层数量/落点守卫；1280×800 输入框可见。字体跟随已批准画板，聊天问候语独立；不再采用已废止的正文 19px / 辅助 16px 下限。
- 已处理 fixed 背景滚动开销、焦点环归属和按钮/输入框边界；2026-09-04 剩余光栅对照中 GPU 路径未复现卡顿，软件光栅开销归于画板规定的大面模糊。本棒不新增性能结论。
- 前端后继 `P2-FE-DISPATCH-FORM-001` / `P2-FE-APPS-001` 并列，依赖已完成的视觉棒。AppShell 手写 CSS module 的 antd Layout/Menu 欠债保留；顶栏部门/姓名/头像仍缺真实数据源，依赖 `P2-USER-PROFILE-READ-001`，头像取图三项未知仍待输入。
- 机会层 task_id、依赖、BLOCKED 条件和活欠债只见 `docs/phase2/PHASE2_PLAN.md` 的现役 DAG；分配 ID 不等于排期，不重排必达链。
