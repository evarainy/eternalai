# Phase 2 当前状态

- 当前基线 task_id：`P2-SDUI-SCHEMA-001`
- pytest：`2402 passed, 84 warnings`（0 skipped，0 failed；当前实现基线全量，未使用 `--ignore=`）
- 当前实现基线后端定向 pytest：`403 passed, 0 failed`（`tests/contracts/`、`tests/runtime/`、`tests/api/`）
- 当前实现基线前端 `pnpm --dir web test`：`158 passed, 0 failed, 0 skipped`（19 个测试文件，`P2-SDUI-SCHEMA-001` 实测）
- Golden Gate：`32/32 passed, 0 skipped, 0 failed`（negative 20/20，positive 12/12；当前实现基线实测）
- `tests/architecture/`：`64 passed`（`P2-SDUI-SCHEMA-001` 实测；含治理 SSOT 守卫）
- 必达主链指针：`P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002`。`P2-LOW-RISK-WRITE-001` 当前 **BLOCKED** 于 OA 审批提交协议结构，输入到位前不开棒；这是 P2 收口的唯一真实卡点。
- 机会层已解除阻塞的下一棒：`P2-SDUI-RENDERER-002`。2026-08-30 裁决固定串行顺序 `P2-SDUI-SCHEMA-001 → P2-SDUI-RENDERER-002`，见 `docs/phase2/DECISIONS.md`「裁决：SDUI 导航、跨语言合同与两棒排期」。必达主链仍 BLOCKED，见上一行。
- 当前实现基线摘要：`CapabilitySpec.output_schema` 是单一 `ResponseEnvelope` 外露合同；Runtime 在 formatter 前 fail-closed 投影，task-local capability、binding manifest 与 immutable projection snapshot 同源，两入口 completed 文案共用安全 formatter。
- P2 必达五项进度：①OA 只读纵切 ✅ ②Work Object + 最小工作台 ✅ ③后台轮询 ✅ ④低风险写入 ⬜ ⑤Golden ◐（`P2-GOLDEN-001` 已完成，仍需 `P2-GOLDEN-002`）
- 剩余必达链只含 `P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002`；Golden 只覆盖 Runtime 观察边界，工作台/隔离/审计归 API 与单元层，见 `docs/phase2/DECISIONS.md`。
- 机会层任务、依赖与 BLOCKED 条件只见 `docs/phase2/PHASE2_PLAN.md` 的现役 DAG；分配 task_id 不等于排期。
