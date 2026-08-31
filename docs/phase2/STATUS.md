# Phase 2 当前状态

- 当前基线 task_id：`P2-SDUI-RENDERER-002`
- pytest：`2536 passed, 87 warnings`（0 skipped，0 failed；本棒收口全量，未使用 `--ignore=`；开工基线为 `2529 passed, 87 warnings`）
- 当前实现基线后端定向 pytest：`409 passed, 60 warnings, 0 failed`（`tests/contracts/`、`tests/runtime/`、`tests/api/`；本棒实测）
- 当前实现基线前端 `pnpm --dir web test`：`226 passed, 0 failed, 0 skipped`（20 个测试文件；本棒实测）
- Golden Gate：`32/32 passed, 0 skipped, 0 failed`（negative 20/20，positive 12/12；本棒实测）
- `tests/architecture/`：`112 passed`；`tests/infra/organization_directory/`：`15 passed, 3 warnings`（本棒实测）
- 必达主链指针：`P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002`。`P2-LOW-RISK-WRITE-001` 当前 **BLOCKED** 于 OA 审批提交协议结构，输入到位前不开棒；这是 P2 收口的唯一真实卡点。
- 组织目录集成链的后继指针：`P2-AUDIT-READ-AUTHZ-001` 与 `P2-AUDIT-TRACE-SCOPE-001` 按既有裁决联合交付；组织目录 guard 冻结已解除，Trace schema 变更与历史行回填授权已到位。`P2-INTERNAL-WO-SCOPE-001` 仅解除本 guard 冻结，仍 BLOCKED 于唯一主负责人可信来源缺失。必达主链仍 BLOCKED，见上一行；其他独立机会层任务不因本棒重排。
- 当前实现基线摘要：`CapabilitySpec.output_schema` 是单一 `ResponseEnvelope` 外露合同；Runtime 在 formatter 前 fail-closed 投影，SDUI renderer 对确认卡目标、OA 导航与部分记录列表执行确定性 fail-closed 展示，action data 病态输入返回稳定失败信封。
- P2 必达五项进度：①OA 只读纵切 ✅ ②Work Object + 最小工作台 ✅ ③后台轮询 ✅ ④低风险写入 ⬜ ⑤Golden ◐（`P2-GOLDEN-001` 已完成，仍需 `P2-GOLDEN-002`）
- 剩余必达链只含 `P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002`；Golden 只覆盖 Runtime 观察边界，工作台/隔离/审计归 API 与单元层，见 `docs/phase2/DECISIONS.md`。
- 机会层任务、依赖与 BLOCKED 条件只见 `docs/phase2/PHASE2_PLAN.md` 的现役 DAG；分配 task_id 不等于排期。
