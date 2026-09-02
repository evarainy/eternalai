# Phase 2 当前状态

- 当前基线 task_id：`P2-FE-NAV-SHELL-001`（前端骨架棒，B 档：一级导航改五项平铺并可折叠为图标条且持久化、产品标识改为不可点纯标识、`/chat` 升为真实路由且 `/` 重定向到它、新增 `/work-dispatch` 与 `/apps` 与 `/messages` 三条路由及其 `features/` 落地页、顶栏按「搜索 → 部门 / 姓名 → 风格 → 系统状态 → 通知 → 头像」重排并移除「当前位置」、AI 面板改为不挤压正文的浮动面板并可拖动放大复位。顶栏部门 / 姓名与头像无数据源，只做 fail-closed 说明，不是已实现）
- pytest：`2595 passed, 108 warnings`（0 skipped，0 failed；未使用 `--ignore=`；2026-09-01 `P2-TASK-TENANT-COLUMN-001` 实测复核）
- 当前实现基线后端定向 pytest：`429 passed, 81 warnings, 0 failed`（`tests/contracts/`、`tests/runtime/`、`tests/api/`；2026-09-01 `P2-TASK-TENANT-COLUMN-001` 实测复核）
- 当前实现基线前端 `pnpm --dir web test`：`313 passed, 0 failed, 0 skipped`（26 个测试文件；2026-09-03 `P2-FE-NAV-SHELL-001` 实测复核）
- Golden Gate：`32/32 passed, 0 skipped, 0 failed`（negative 20/20，positive 12/12；2026-09-01 `P2-TASK-TENANT-COLUMN-001` 实测复核）
- `tests/architecture/`：`112 passed`（2026-09-03 `P2-FE-NAV-SHELL-001` 实测复核）
- 必达主链指针：`P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002`。`P2-LOW-RISK-WRITE-001` 当前 **BLOCKED** 于 OA 审批提交协议结构，输入到位前不开棒；这是 P2 收口的唯一真实卡点。
- 组织目录集成链的后继指针：`P2-TENANT-IDENTITY-001`；`P2-TASK-TENANT-COLUMN-001` 只完成 `tasks` 切片，真实组织身份来源、sessions、identity binding、组织目录镜像等剩余 scope 仍须独立授权。当前连接库开工实测 `tasks=0`、distinct `task_id=0`；此前 115/115 只保留为历史快照，不冒充当前实测或回填来源。方案 A 未猜值、未回填、未删旧记录，升级前 Task 保持 `tenant_id=NULL` 并继续对 Admin fail-closed 不可见。`P2-INTERNAL-WO-SCOPE-001` 仍 BLOCKED 于唯一主负责人可信来源缺失。必达主链仍 BLOCKED，见上一行；其他独立机会层任务不因本棒重排。
- 当前实现基线摘要：新 Task 从既有 `Principal` / `PrincipalOrgContext` 固化非空 `tenant_id`，缺失或空白租户在写入 SQL 前显式失败；Task Store 与 Admin Task / event / 对应 Binding 读取直接按可信 Task 租户列收窄，不再以逐 Task Trace 查询证明租户，列表查询放大已关闭。migration 对三条合成升级前 Task 完成 upgrade → downgrade → upgrade 往返，行数与 task_id 集合不变、升级后租户均为 NULL、downgrade 后行仍在；空合成集被测试主动拒绝，不能平凡通过。Trace reader 与孤立动作 Trace 的既有租户合同不变。
- P2 必达五项进度：①OA 只读纵切 ✅ ②Work Object + 最小工作台 ✅ ③后台轮询 ✅ ④低风险写入 ⬜ ⑤Golden ◐（`P2-GOLDEN-001` 已完成，仍需 `P2-GOLDEN-002`）
- 剩余必达链只含 `P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002`；Golden 只覆盖 Runtime 观察边界，工作台/隔离/审计归 API 与单元层，见 `docs/phase2/DECISIONS.md`。
- 前端界面链指针：`P2-FE-VISUAL-REFACTOR-001` → 交办页样式落地。`P2-FE-NAV-SHELL-001` 已完成导航骨架、顶栏元素、浮动面板与三个落地页；视觉（玻璃拟态、`theme.ts` 令牌、`@ant-design/x`）整体归 `P2-FE-VISUAL-REFACTOR-001`，本链无棒间前置阻塞。顶栏「部门 / 姓名」与头像仍无数据源，只有 fail-closed 说明，真正实现依赖后端身份读取接口，见 `docs/phase2/PHASE2_PLAN.md` 活欠债。该链属机会层，不改变必达主链的 BLOCKED 状态。
- 机会层任务、依赖与 BLOCKED 条件只见 `docs/phase2/PHASE2_PLAN.md` 的现役 DAG；分配 task_id 不等于排期。
