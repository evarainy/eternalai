# Phase 2 当前状态

- 当前基线 task_id：`P2-CAPABILITY-AUTOMATION-LEVEL-001`
- pytest：`2227 passed, 57 warnings`（0 skipped，0 failed；含 Capability 自动化程度、Work Object 办理映射、confirm 参数值 allowlist、migration 与 OA 审计指纹分层验证）
- 前端 `pnpm --dir web test`：`110 passed, 0 failed, 0 skipped`（办理按钮四动作、单行单按钮、OpenAPI 字节一致性 1/1；本棒收口实跑）
- Golden Gate：`27/27 passed, 0 skipped, 0 failed`（negative 16/16，positive 11/11；**Golden harness 不构造或消费 Work Object 办理投影，也不断言 confirm 第五键，对本棒改动零覆盖**，仅作既有路径回归证据）
- `tests/architecture/`：`42 passed`
- 下一棒（串行，单 lane）：`P2-GOLDEN-001`（A 档，必达项 5 的首棒）—— 冻结当前已实现的只读、Work Object、确认绑定与后台轮询路径；不提前实施低风险写入。`P2-WO-SEARCH-001` 已随本棒解锁，属机会层，可在 `P2-GOLDEN-001` 之后或另开 lane 时承接。
- `P2-GOLDEN-001` 的 fixture 人工批准停点已于 2026-08-27 解除：只批准新增题面及冻结新增题面；改删现有题面或移除现有冻结条目仍须单独申请。当前串行下一棒地位不变。
- P2 必达五项进度：①OA 只读纵切 ✅ ②Work Object + 最小工作台 ✅ ③后台轮询 ✅ ④低风险写入 ⬜ ⑤Golden ⬜
- Golden 已拆两棒（2026-08-21 裁决，解 DAG 循环）：`P2-GOLDEN-001` 先冻当前已实现路径（只读、Work Object、确认绑定），解锁 `P2-LOW-RISK-WRITE-001`；低风险写入的可操作确认面另依赖 `P2-CAPABILITY-AUTOMATION-LEVEL-001` → `P2-SDUI-RENDERER-001`，写入落地后由 `P2-GOLDEN-002` 增量冻写入路径。必达项 5 需两棒都完成。
- 机会层：`P2-INTERNAL-WO-MODEL-001` **已完成**；`P2-OA-ORGANIZATION-DIRECTORY-001` → `P2-INTERNAL-WO-SCOPE-001` → `P2-INTERNAL-WO-DISPATCH-001` → `P2-INTERNAL-WO-ATTACHMENT-001`（均 A 档，**组织目录 HAR 尚未采集，此链阻塞中**）；`P2-CAPABILITY-AUTOMATION-LEVEL-001` **已完成**，`P2-SDUI-RENDERER-001`（A 档，记录列表、受限 `envelope.data` 与既有 `confirm_card` 可操作确认面）已随本棒解锁 → `P2-LOW-RISK-WRITE-001`；`P2-FE-WORKBENCH-001`（B 档，自动化程度前置已满足；首版 Dock 只用 antd 6 原生组件，不被 `@ant-design/x` 阻塞）；`P2-PAGE-CONTEXT-CONTRACT-001`（A 档，无前置，须先于 DISPATCH 与搜索上下文注册）；`P2-WO-SEARCH-001`（A 档，依赖 MODEL-001 与页面上下文合同）；`P2-PORT-SEAM-001`、`P2-SKILL-CANDIDATE-001`、`P2-MEMORY-001`、`P2-HIKVISION-ADAPTER-001`。
- 前端信息架构与终态导航**已于 2026-08-27 同日修订收口**（`DECISIONS.md` 同日「前端信息架构与终态导航」）：四项一级平铺（工作事项 / 任务交办 / 软件中心 / 消息）+ `ChatPage` 落地页 `/`。「会话」不再是一级项；`我问过的` 是工作事项内的 P3 筛选，P2 只有 `今日` / `全部`。会话持久化、`最近问过的` 与 `/ai/sessions/:id` 均归 P3；P2 Dock 为 AppShell 单例并以非持久化 Zustand 保住普通路由切换中的当前会话。前端棒以该条为导航、命名、布局与安全边界来源；低数字素养用户硬约束见同日另一条。
- 首页目标态（P3）：落地页 `/` 对标主流 AI 对话产品，支持历史会话切换、续接与对话附件；不改变 P2 单输入框边界。
- 附件边界：统一复用与授权主体无关的存储层，下载授权主体参数化；`P2-INTERNAL-WO-ATTACHMENT-001` 首版仍只交付 Work Object 附件。
- 外部系统接入 §10 全组已落盘：OA iframe 实测排除、录制走网络请求、任务交办沿用插槽边界；浏览器扩展保留为待议欠债。
- 遗留 worktree 待清理：`git worktree list` 中多数 Phase 1 / 已合并 Phase 2 任务的 worktree 未移除，其中 `.worktrees/P2-PORT-SEAM-001` 是撞库空转留下的空壳。清理需专项授权，尚未执行。
- 外部输入阻塞：**组织目录 HAR 尚未采集**（雨爷 2026-08-27 确认回来再采）。它是 `P2-OA-ORGANIZATION-DIRECTORY-001` → `SCOPE` → `DISPATCH` 后端 → `ATTACHMENT` 四棒的唯一剩余前置；部门层级授权规则已于同日裁定，不再是阻塞项。
