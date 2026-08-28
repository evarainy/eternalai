# Phase 2 当前状态

- 当前基线 task_id：`P2-GOV-SYNC-033`
- pytest：`2262 passed, 84 warnings`（0 skipped，0 failed；后台全量 run `20260827T143721Z-14868`，未使用 `--ignore`）
- 前端 `pnpm --dir web test`：`110 passed, 0 failed, 0 skipped`（本棒不改前端且未单跑，沿用上一棒实测数字）
- Golden Gate：`32/32 passed, 0 skipped, 0 failed`（negative 20/20，positive 12/12；GT-029～GT-033 通过完整 Runtime 与真实 envelope builder 冻结 `confirm_card` 参数值 allowlist，GT-012 多绑定 scope clarification 继续通过）
- `tests/architecture/`：`42 passed`（本棒不触及架构边界且未单跑；沿用上一棒定向数字，本棒全量回归覆盖该目录）
- 下一棒（串行，单 lane）：`P2-USER-ACTION-SEAM-001`（A 档）—— 2026-08-28 裁决插棒。串行链为 `USER-ACTION-SEAM-001 → SDUI-RENDERER-001 → LOW-RISK-WRITE-001 → GOLDEN-002`。插棒原因：冻结蓝图 SDUI 安全边界要求「按钮点击只产生结构化 user_action」，而 `UserAction` 全仓无生产接线，Renderer 的「可操作确认面」在当前接线下无法合规实现。
- `P2-GOLDEN-001` 已于 2026-08-27 完成本棒获批增量：新增并冻结 GT-029～GT-033，既有 fixture 未改、既有冻结条目未删；参数值 allowlist 从既有单元覆盖进入 Golden 冻结 regime。
- P2 必达五项进度：①OA 只读纵切 ✅ ②Work Object + 最小工作台 ✅ ③后台轮询 ✅ ④低风险写入 ⬜ ⑤Golden ◐（`P2-GOLDEN-001` 已完成，仍需 `P2-GOLDEN-002`）
- Golden 已拆两棒（2026-08-21 裁决，解 DAG 循环）：`P2-GOLDEN-001` 已完成 `confirm_card` 参数值 allowlist 的 Runtime 观察边界冻结；低风险写入的可操作确认面另依赖 `P2-CAPABILITY-AUTOMATION-LEVEL-001` → `P2-USER-ACTION-SEAM-001` → `P2-SDUI-RENDERER-001`，写入落地后由 `P2-GOLDEN-002` 增量冻写入路径。必达项 5 需两棒都完成；其完成判据口径已于 2026-08-28 裁定——Golden 只覆盖 Runtime 观察边界，工作台/隔离/审计归 API 与单元层且已有覆盖，不构成必达项 5 缺口。
- 机会层：`P2-INTERNAL-WO-MODEL-001` **已完成**；`P2-OA-ORGANIZATION-DIRECTORY-001` → `P2-INTERNAL-WO-SCOPE-001` → `P2-INTERNAL-WO-DISPATCH-001` → `P2-INTERNAL-WO-ATTACHMENT-001`（均 A 档，**组织目录 HAR 尚未采集，此链阻塞中**）；`P2-CAPABILITY-AUTOMATION-LEVEL-001` **已完成**，`P2-USER-ACTION-SEAM-001`（A 档，结构化 `UserAction` 端到端接线）→ `P2-SDUI-RENDERER-001`（A 档，记录列表、受限 `envelope.data` 与既有 `confirm_card` 可操作确认面；**前置由自动化程度棒改为 SEAM 棒，尚未满足**）→ `P2-LOW-RISK-WRITE-001`；`P2-FE-WORKBENCH-001`（B 档，自动化程度前置已满足；首版 Dock 只用 antd 6 原生组件，不被 `@ant-design/x` 阻塞）；`P2-PAGE-CONTEXT-CONTRACT-001`（A 档，无前置，须先于 DISPATCH 与搜索上下文注册）；`P2-WO-SEARCH-001`（A 档，依赖 MODEL-001 与页面上下文合同）；`P2-PORT-SEAM-001`、`P2-SKILL-CANDIDATE-001`、`P2-MEMORY-001`、`P2-HIKVISION-ADAPTER-001`。
- 前端信息架构与终态导航**已于 2026-08-27 同日修订收口**（`DECISIONS.md` 同日「前端信息架构与终态导航」）：四项一级平铺（工作事项 / 任务交办 / 软件中心 / 消息）+ `ChatPage` 落地页 `/`。「会话」不再是一级项；`我问过的` 是工作事项内的 P3 筛选，P2 只有 `今日` / `全部`。会话持久化、`最近问过的` 与 `/ai/sessions/:id` 均归 P3；P2 Dock 为 AppShell 单例并以非持久化 Zustand 保住普通路由切换中的当前会话。前端棒以该条为导航、命名、布局与安全边界来源；低数字素养用户硬约束见同日另一条。
- 首页目标态（P3）：落地页 `/` 对标主流 AI 对话产品，支持历史会话切换、续接与对话附件；不改变 P2 单输入框边界。
- 附件边界：统一复用与授权主体无关的存储层，下载授权主体参数化；`P2-INTERNAL-WO-ATTACHMENT-001` 首版仍只交付 Work Object 附件。
- 外部系统接入 §10 全组已落盘：OA iframe 实测排除、录制走网络请求、任务交办沿用插槽边界；浏览器扩展保留为待议欠债。
- 遗留 worktree 待清理：`git worktree list` 中多数 Phase 1 / 已合并 Phase 2 任务的 worktree 未移除，其中 `.worktrees/P2-PORT-SEAM-001` 是撞库空转留下的空壳。清理需专项授权，尚未执行。
- 外部输入阻塞：**组织目录 HAR 尚未采集**（雨爷 2026-08-27 确认回来再采）。它是 `P2-OA-ORGANIZATION-DIRECTORY-001` → `SCOPE` → `DISPATCH` 后端 → `ATTACHMENT` 四棒的唯一剩余前置；部门层级授权规则已于同日裁定，不再是阻塞项。
