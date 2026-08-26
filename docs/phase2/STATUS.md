# Phase 2 当前状态

- 当前基线 task_id：`P2-OA-CREDENTIAL-POLL-001`
- pytest：`2189 passed, 84 warnings`（0 skipped，0 failed；包含 Alembic upgrade→downgrade→upgrade 可逆性循环）
- 前端 `pnpm --dir web test`：`108 passed, 0 failed, 0 skipped`（Work Object 13/13、OpenAPI 字节一致性 1/1；本棒收口实跑）
- Golden Gate：`27/27 passed, 0 skipped, 0 failed`（negative 16/16，positive 11/11；本棒 credential binding/polling 路径零直接覆盖，仅作既有路径回归证据）
- `tests/architecture/`：`42 passed`
- 下一棒（串行，单 lane）：`P2-GOLDEN-001`（A 档，必达项 5 的首棒）—— 冻结当前已实现的只读、Work Object、确认绑定与后台轮询路径；不提前实施低风险写入。
- P2 必达五项进度：①OA 只读纵切 ✅ ②Work Object + 最小工作台 ✅ ③后台轮询 ✅ ④低风险写入 ⬜ ⑤Golden ⬜
- Golden 已拆两棒（2026-08-21 裁决，解 DAG 循环）：`P2-GOLDEN-001` 先冻当前已实现路径（只读、Work Object、确认绑定），解锁 `P2-LOW-RISK-WRITE-001`；写入落地后由 `P2-GOLDEN-002` 增量冻写入路径。必达项 5 需两棒都完成。
- 机会层：`P2-INTERNAL-WO-MODEL-001`（A 档，**2026-08-27 解除对组织目录的依赖，现可开工**）；`P2-OA-ORGANIZATION-DIRECTORY-001` → `P2-INTERNAL-WO-SCOPE-001` → `P2-INTERNAL-WO-DISPATCH-001` → `P2-INTERNAL-WO-ATTACHMENT-001`（均 A 档，**组织目录 HAR 尚未采集，此链阻塞中**）；`P2-WO-SEARCH-001`（A 档，2026-08-27 新增，依赖 MODEL-001）；`P2-PORT-SEAM-001`、`P2-SKILL-CANDIDATE-001`、`P2-MEMORY-001`、`P2-HIKVISION-ADAPTER-001`。
- 前端信息架构与终态导航**已于 2026-08-27 裁定**（`DECISIONS.md` 同日「前端信息架构与终态导航（13 项）」）：五项一级平铺（工作事项 / 任务交办 / 会话 / 软件中心 / 消息）+ `ChatPage` 升格落地页 `/`。前端棒以该条为导航与命名来源。低数字素养用户的界面硬约束见同日另一条，写入每个前端棒 `done_when`。
- 遗留 worktree 待清理：`git worktree list` 中多数 Phase 1 / 已合并 Phase 2 任务的 worktree 未移除，其中 `.worktrees/P2-PORT-SEAM-001` 是撞库空转留下的空壳。清理需专项授权，尚未执行。
- 外部输入阻塞：**组织目录 HAR 尚未采集**（雨爷 2026-08-27 确认回来再采）。它是 `P2-OA-ORGANIZATION-DIRECTORY-001` → `SCOPE` → `DISPATCH` 后端 → `ATTACHMENT` 四棒的唯一剩余前置；部门层级授权规则已于同日裁定，不再是阻塞项。
