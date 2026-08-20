# Phase 2 当前状态

- 当前基线 task_id：`P2-GOV-SYNC-020`
- pytest：`2091 passed, 81 warnings`（0 skipped，0 failed；`P2-WORK-OBJECT-001` 收口实跑）
- 前端 `pnpm --dir web test`：`105 passed, 0 failed, 0 skipped`（Work Object 10/10、Bindings 12/12、OpenAPI 字节一致性 1/1）
- Golden Gate：`27/27 passed, 0 skipped, 0 failed`（negative 16/16，positive 11/11）
- `tests/architecture/`：`38 passed`
- 下一棒（两条均无前置，可并行；**注意 migration 归属**）：
  - `P2-CONFIRM-BINDING-001`（A 档，必达项 4 的硬前置）—— `HumanGatePort` + Task 版本绑定清单。**本棒新增 migration，使用共享测试库。**
  - `P2-PORT-SEAM-001`（A 档，机会层）—— 两个执行内核接缝，纯后端、不建表。**与上一条并行时须配独立测试库。**
- P2 必达五项进度：①OA 只读纵切 ✅ ②Work Object + 最小工作台 ✅ ③后台轮询（`P2-OA-CREDENTIAL-POLL-001`）⬜ ④低风险写入 ⬜ ⑤Golden ⬜
- 机会层新增（2026-08-20 任务派发方案）：`P2-OA-ORGANIZATION-DIRECTORY-001` → `P2-INTERNAL-WORK-OBJECT-001`，均 A 档。方案 §2.3 要求等 `P2-PORT-SEAM-001` 合入后再开始，该等待是排期安排、非技术依赖。
- `P2-OA-CREDENTIAL-POLL-001` 的 Scope 已扩充（2026-08-20）：除密码绑定与后台轮询外，**须一并把凭证存储主键改为 `(用户, 目标系统)`**——现行 `oa_session_credentials` 以 `ai_user_id` 单主键，「每用户×每系统一套凭证」的决定在当前 schema 下落不了地。共用一次 migration。
- 未决、不得推定：**整体界面布局（导航结构、AI 与工作台的融合方式）仍待雨爷裁定**，前端棒在裁定前不得自行确定导航形态。
