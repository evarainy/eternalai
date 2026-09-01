# Phase 2 当前状态

- 当前基线 task_id：`P2-GOV-SYNC-050`
- pytest：`2576 passed, 104 warnings`（0 skipped，0 failed；未使用 `--ignore=`；2026-09-01 `P2-GOV-SYNC-049` 实测复核）
- 当前实现基线后端定向 pytest：`424 passed, 77 warnings, 0 failed`（`tests/contracts/`、`tests/runtime/`、`tests/api/`；2026-09-01 `P2-GOV-SYNC-049` 实测复核）
- 当前实现基线前端 `pnpm --dir web test`：`266 passed, 0 failed, 0 skipped`（21 个测试文件；2026-09-01 `P2-GOV-SYNC-049` 实测复核）
- Golden Gate：`32/32 passed, 0 skipped, 0 failed`（negative 20/20，positive 12/12；2026-09-01 `P2-GOV-SYNC-049` 实测复核）
- `tests/architecture/`：`112 passed`（2026-09-01 `P2-GOV-SYNC-049` 实测复核）
- 必达主链指针：`P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002`。`P2-LOW-RISK-WRITE-001` 当前 **BLOCKED** 于 OA 审批提交协议结构，输入到位前不开棒；这是 P2 收口的唯一真实卡点。
- 组织目录集成链的后继指针：`P2-TENANT-IDENTITY-001`；两根 Audit 前置已完成。历史恢复欠债保持激活：开工快照 115/115 个 Task 因没有可信同租户 Trace 证明而对四个 Admin 证据入口不可见，当前 OA Binding 为 0 条、受影响 0 条；恢复须由该后继棒为 `tasks` 增加可信租户列，本棒未实施。`P2-INTERNAL-WO-SCOPE-001` 仍 BLOCKED 于唯一主负责人可信来源缺失。必达主链仍 BLOCKED，见上一行；其他独立机会层任务不因本棒重排。
- 当前实现基线摘要：`audit_reader` 与 `admin` 动作分离，四个 Admin 证据入口先鉴权再按可信租户 Trace 证明收窄；新 Trace 由既有 `Principal` / `PrincipalOrgContext` 经 Runtime、Workflow、Gateway 固化非空租户与用户，三类读取无条件带租户谓词。历史快照 134 行中当前 0 行可归属、134 行保持 NULL，未猜默认值、未删行；普通 Work Object 列表/搜索、Runtime 对话与读取路径不使用该 Admin 证明逻辑。
- P2 必达五项进度：①OA 只读纵切 ✅ ②Work Object + 最小工作台 ✅ ③后台轮询 ✅ ④低风险写入 ⬜ ⑤Golden ◐（`P2-GOLDEN-001` 已完成，仍需 `P2-GOLDEN-002`）
- 剩余必达链只含 `P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002`；Golden 只覆盖 Runtime 观察边界，工作台/隔离/审计归 API 与单元层，见 `docs/phase2/DECISIONS.md`。
- 机会层任务、依赖与 BLOCKED 条件只见 `docs/phase2/PHASE2_PLAN.md` 的现役 DAG；分配 task_id 不等于排期。
