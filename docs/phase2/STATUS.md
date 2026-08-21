# Phase 2 当前状态

- 当前基线 task_id：`P2-GOV-SYNC-021`
- pytest：`2124 passed, 84 warnings`（0 skipped，0 failed；`P2-CONFIRM-BINDING-001` 收口实跑）
- 前端 `pnpm --dir web test`：`105 passed, 0 failed, 0 skipped`（Work Object 10/10、Bindings 12/12、OpenAPI 字节一致性 1/1）
- Golden Gate：`27/27 passed, 0 skipped, 0 failed`（negative 16/16，positive 11/11）
- `tests/architecture/`：`38 passed`
- 下一棒（串行，单 lane）：`P2-OA-CREDENTIAL-POLL-001`（A 档，必达项 3）—— 用户 OA 密码绑定入口 + 加密存储 + 后台定时轮询，并一并完成凭证存储主键改造。**本棒新增 migration，使用共享测试库。**
  - **开工前须确认或以保守设计规避**两个 OA 侧未知项：登录失败锁定阈值（错几次锁、锁多久、如何解锁）、程序登录在 OA 侧的审计可见性与在线状态影响。保守设计 = 一错即持久失效 + 按会产生审计记录处理。
- P2 必达五项进度：①OA 只读纵切 ✅ ②Work Object + 最小工作台 ✅ ③后台轮询（`P2-OA-CREDENTIAL-POLL-001`）⬜ ④低风险写入 ⬜ ⑤Golden ⬜
- Golden 已拆两棒（2026-08-21 裁决，解 DAG 循环）：`P2-GOLDEN-001` 先冻当前已实现路径（只读、Work Object、确认绑定），解锁 `P2-LOW-RISK-WRITE-001`；写入落地后由 `P2-GOLDEN-002` 增量冻写入路径。必达项 5 需两棒都完成。
- 机会层：`P2-PORT-SEAM-001`（A 档，与 `P2-CONFIRM-BINDING-001` 的撞车已随后者合入解除）、`P2-OA-ORGANIZATION-DIRECTORY-001` → `P2-INTERNAL-WORK-OBJECT-001`（均 A 档，2026-08-20 任务派发方案）、`P2-SKILL-CANDIDATE-001`、`P2-MEMORY-001`、`P2-HIKVISION-ADAPTER-001`。
- 未决、不得推定：**整体界面布局（导航结构、AI 与工作台的融合方式）仍待雨爷裁定**，前端棒在裁定前不得自行确定导航形态。
- 遗留 worktree 待清理：`git worktree list` 中多数 Phase 1 / 已合并 Phase 2 任务的 worktree 未移除，其中 `.worktrees/P2-PORT-SEAM-001` 是撞库空转留下的空壳。清理需专项授权，尚未执行。
