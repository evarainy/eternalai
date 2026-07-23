# TASK_INDEX — Phase 1 Dependency DAG v1.1.2

本文件是 Phase 1 的任务依赖 DAG。Codex / Claude Code 必须按批次和 `depends_on` 执行，不得跳过前置任务。

**执行模型：一个 native Goal 可顺序执行多个 `task_id`；每个 write lane 仍对应一份 Scope、独立 worktree 与 branch。auto-next 必须满足依赖、所需证据与 Review。**

## 0. 批次总览

```text
B1（首批）：启动准备
  ↓
B2：Intent → Capability 选择闭环     ← 产品硬门已满足：P1-GATE-001 passed + PHASE1_SPEC (P1-SPEC-001) approved/landed
  ↓
B3：Identity / Policy 预检闭环
  ↓
B4：Workflow 轻量引擎 + 执行
  ↓
B5：Session Memory + Evaluator + Admin Lite
```

## B2+ contract（legacy prompt 说明）

历史 V4 任务继续按原 per-task prompt 执行；新的 native Goal 以当前 Goal 与 `AGENTS.md` 为合同，不强制新增 descriptor。

## 1. B1 — 启动准备

| task_id | title | depends_on | deliverable |
|---|---|---|---|
| P1-GATE-001 | Golden-task 真回归门 | 人类批准 Phase 1 Plan | 阈值门：runner 非零退出 / CI 红；阈值来源 MVP spec §20.1 L4501-4503 / §14.5 L3951-3954 |
| P1-SKEL-001 | docs/phase1/ 目录 + 派生模板 + INDEX 骨架 + B1 per-task prompts | 人类批准 Phase 1 Plan | 本文件所在目录结构、PHASE1_PLAN.md、TASK_PROMPT_TEMPLATE.md、各 B1 per-task prompt |
| P1-ERRATA-001 | BLUEPRINT_ERRATA.md 勘误 + 澄清登记 | none | docs/phase1/BLUEPRINT_ERRATA.md（3 条正式条目 + 1 legacy note） |
| P1-PARAM-001 | Context Budget / vLLM 部署参数登记 | infra 提供实际部署参数（**blocked until infra values arrive**） | 部署参数基线表（等 infra 回值前天然 blocked） |
| P1-SPEC-CONTRACT-ALIGN | P1-SPEC-001 现行契约对齐 | P1-RUNTIME-ENTRY-001 integrated + merge-SHA CI passed；LOCAL-WF-V4-001 complete | 对齐后的 `P1-SPEC-001.md`、本任务 descriptor 与审查/集成证据；结果验收后才可启动 P1-SPEC-001 |
| P1-SPEC-001 | Phase 1 详细 spec 产出（**B2 硬前置**） | P1-SPEC-CONTRACT-ALIGN integrated + result accepted；P1-GATE-001 passed | `PHASE1_SPEC.md` 已 approved/landed，P1-SPEC-001 Gate 2 accepted；B2 产品硬门已满足 |
| P1-SPEC-001-APPROVE-001 | 批准 P1-SPEC-001 并解锁 B2 | P1-SPEC-001 integrated at `10c5993d` + merge-SHA CI passed + Gate 2 accepted | `docs/phase1/tasks/P1-SPEC-001-APPROVE-001.md`；同步 spec/Task Record/INDEX；不生成或启动 B2 |
| P1-WORKFLOW-002 | 治理文档收敛与流程分级落地 | none | ROLE_POLICY / TASK_PROMPT_TEMPLATE v2.0.0 / schema v1.1.0 收敛，ceremony 按 risk_tier 分级 |

> **P1-PARAM-001 说明**：此任务依赖 infra 提供生产/内网 vLLM 实际部署参数（`max_model_len`、量化方式、`request_timeout`、`max_tokens`、`enable_thinking` 行为）。infra 未回值前该任务天然 blocked，不与其他 B1 任务并列为"无条件可做"。

> **P1-SPEC-001（pre-B2 硬门）**：`docs/phase1/PHASE1_SPEC.md` 未落盘并经人类批准前，**不得进入 B2**。B2 前置条件 = `P1-GATE-001 passed + PHASE1_SPEC (P1-SPEC-001) approved/landed`。

### B1 治理修复控制链

本链只登记 W002 治理修复及其后续任务依赖，不改写已完成的 `P1-WORKFLOW-002` 历史状态。下列四项已按历史顺序完成 merge，且各自 merge-SHA CI success。

| task_id | title | depends_on | deliverable / start gate |
|---|---|---|---|
| P1-WORKFLOW-V5-001 | Codex-Claude V5 治理迁移 | 已批准设计 `F464E36…` + 修订计划 `118AE10B…` + 2026-07-19 书面建设授权 | merged：task `8739a99`，merge `76586ce`；五个 V5 语义治理文件 + 本行 + descriptor + 最后一份 V4 Task Record 已落地 |
| P1-WORKFLOW-002-REPAIR-001 | W002 完整范围修复与活动控制面收敛 | P1-WORKFLOW-002 completed；P1-WORKFLOW-002-REPAIR-BOOTSTRAP-001 landed | merged：task `4f7d4f9`，merge `bce81d0`，merge-SHA CI `29205276520` success |
| P1-CI-ALIGN-001 | Phase 1 预合并 CI 对齐 | P1-WORKFLOW-002-REPAIR-001 completed | merged：task `3c35505`，merge `cdfd9eb`，merge-SHA CI `29210156919` success |
| P1-OBS-001 | Trace 生命周期与敏感字段契约修复 | P1-CI-ALIGN-001 completed | merged：task `a20650a`，merge `d8b729d`，merge-SHA CI `29215852960` success |
| P1-RUNTIME-ENTRY-001 | Runtime Composition Root 与 Golden Harness 解耦 | P1-OBS-001 completed | merged：task `e853b95`，merge `869647f`，merge-SHA CI `29222619275` success |

```text
P1-WORKFLOW-002-REPAIR-001
→ P1-CI-ALIGN-001
→ P1-OBS-001
→ P1-RUNTIME-ENTRY-001
```

**legacy 说明**：历史 Task Record 的 result acceptance 状态（含 `pending`）按原记录保留，本次只同步 merge/CI 事实，不闭合 legacy Gate 2，也不把它作为当前 Git/CI 授权。

正式 descriptor 存在只表示结果契约已登记，不表示 task prompt-ready 等于 dependency-ready。普通 commit/push/PR/merge/CI 是否自动由各 task contract 的 controller risk、automation、Review/validation/freshness 与 repo policy 决定；Gate 2 只验收集成结果。

### B1 可选任务

| task_id | title | depends_on | 备注 |
|---|---|---|---|
| P1-TOOLCALL-002 | 工具调用 prompt 第二轮复测 | 内网 vLLM endpoint 可用 | **optional / 不阻塞 Phase 1**；spike PDR；产出 experiments/phase1/ |

## 2. B2 — Intent → Capability 选择闭环

**前置（硬门，已满足）：`P1-GATE-001 passed + PHASE1_SPEC (P1-SPEC-001) approved/landed`**

| task_id | title | depends_on | 纵切内容 |
|---|---|---|---|
| P1-B2-001 | Registry 支撑的 Capability 选择段 | P1-RUNTIME-ENTRY-001, P1-GATE-001, P1-SPEC-001, P1-SPEC-001-APPROVE-001 | merged：selection 段；task `53cda6d`，merge `f99737c`，merge-SHA CI `29510375566` success |
| P1-INTENT-CAP-001 | Intent / structured-output 闭环补齐 | P1-B2-001 integrated + merge-SHA CI passed | merged：复用 P1-B2-001 selection behavior 并完成 B2 Runtime 闭环；task `e881905`，merge `d870ad8`，Task Record merge `046a2fe`，两次 merge-SHA CI success |
| P1-B2-LEDGER-ALIGN-001 | B2 与前置任务台账事实对齐 | P1-INTENT-CAP-001 Task Record merge `046a2fe` + CI `29710489147` success | merged：task `8a2ed5b`，merge `511ba7e`，merge-SHA CI `29715290480` success |
| P1-B2-002 | B2 frozen Golden 增量 | P1-B2-LEDGER-ALIGN-001 integrated + merge-SHA CI passed | merged：task `8569756`，merge `a5c2424`，merge-SHA CI `29716841081` success；新增 GT-013/GT-014，GT-008 保持 |

> `P1-B2-001` 与 `P1-INTENT-CAP-001` 均已落地：前者负责 Registry-backed selection 段，后者复用该段并补齐 Intent/structured-output，合起来完成 B2 Runtime 闭环。二者不是覆盖关系。`P1-B2-002` 只负责 S-B2.4 Golden 增量，不得借机重写已落地 Runtime。

## 3. B3 — Identity / Policy 预检闭环

**前置：B2 完成**

| task_id | title | depends_on | 纵切内容 |
|---|---|---|---|
| P1-B3-001 | Identity / Policy 预检闭环 | P1-B2-002 integrated + merge-SHA CI passed | IdentityMapping scope 无回退 + Binding 状态预检 + Policy allow/deny/confirm + SDUI/Trace/Task 闭环 |
| P1-B3-002 | B3 frozen Golden 增量 | P1-B3-001 integrated + merge-SHA CI passed | GT-015..GT-019：expired/revoked 与显式 account-set/device-domain/resource-scope 多绑定边界；B4 step Policy Frozen 已由 `P1-B4-002` 落地，B4 Golden 已由 `P1-B4-005` 落地（merge `878a7aa` / `e975858`），本任务不新增 B4 fixture/实现 |

## 4. B4 — Workflow 轻量引擎 + 执行

**前置：B3 完成**

| task_id | title | depends_on | 纵切内容 |
|---|---|---|---|
| `P1-B4-001` / `P1-B4-002` / `P1-B4-003` / `P1-B4-004` / `P1-B4-005` | Workflow 轻量引擎 + 执行 | B3 | 已落地：线性 Workflow + step Policy + 有限重试 + human_gate，经 Gateway 执行 Mock Adapter |

## 5. B5 — Session Memory + Evaluator + Admin Lite

**前置：B4 完成**

| task_id | title | depends_on | 纵切内容 |
|---|---|---|---|
| `P1-B5-001` / `P1-B5-002` / `P1-B5-003` / `P1-B5-004a` / `P1-B5-004b` / `P1-B5-005a` / `P1-B5-005b` / `P1-B5-006` | Session Memory + Evaluator + Admin Lite | B4 | 已落地：Session Memory + Semantic/System Knowledge + Evaluator + Admin Lite Registry/Task/Binding 管理面 + Golden；`P1-B5-005a` / `P1-B5-005b` / `P1-B5-006` 经 V5 Goal 落地，按 §7 无 Task Record |

## 5.1 Phase 1 明确欠下的任务（不排期，不属于 Phase 1 范围）

以下条目**不是 Phase 1 任务**，不得被当作待办领取。列在这里是因为「裁剪记录」容易被读成「决定不做」，而命名条目会被读成「欠着的」。

> **状态更新（2026-07-24）**：`P2-TRACE-PERSIST-001` 已在 Phase 2 落地（持久化 TracePort + Admin 审计查询端点，merge `f8eb8533`，CI run 30017941828 success），**已不是欠债**；下表仅 `P2-CONFIRM-RESUME-001` 仍在案。

| task_id | title | 欠债来源 | 触发条件 |
|---|---|---|---|
| `P2-CONFIRM-RESUME-001` | 非 Workflow 能力的 Policy `confirm` 恢复 | `PHASE1_SPEC.md` S-B5.5；spec 第 125 行把 confirm 恢复实现留给下游任务，B3 只做了确认响应、B4-003 只做了 Workflow `human_gate`，`action`/`query` 类型的确认卡目前是死路 | **自触发**：出现任何不经 Workflow 的高风险 `action`/`query` 能力时（例如 iVMS 单步开门禁）。确认前 Adapter 零调用的安全不变量已有常驻测试守卫，所以这是功能缺口而不是安全缺口 |
| `P2-TRACE-PERSIST-001` ✅ 已落地 | 持久化 TracePort 与生产审计可查（merge `f8eb8533`） | `PHASE1_SPEC.md` S-B5.2 / S-B5.5；Phase 1 只验证正确调用 `TracePort`，当前唯一实现 `NoopTraceWriter` 只写 DEBUG log，Policy、Identity、Gateway/Adapter、终局、Evaluator 与 Admin Lite 管理动作在重启后均不可追溯 | **deployment blocker**：任何生产部署前必须完成持久化实现及完整审计面验证；守卫只允许 `NoopTraceWriter` 在显式 testing/mock 环境构造，不得把守卫当成持久化替代品 |

## 6. 硬顺序摘要

```text
B1 治理修复链：
P1-WORKFLOW-002-REPAIR-001
→ P1-CI-ALIGN-001
→ P1-OBS-001
→ P1-RUNTIME-ENTRY-001

产品实现链（独立硬门）：
P1-SPEC-CONTRACT-ALIGN integrated/result accepted
→ P1-GATE-001 passed + P1-SPEC-001 approved/landed + Gate 2 accepted
→ P1-SPEC-001-APPROVE-001 integrated/result accepted
→ P1-B2-001 Registry-backed selection
→ P1-INTENT-CAP-001 Intent/structured-output 闭环
→ P1-B2-LEDGER-ALIGN-001 台账事实对齐
→ P1-B2-002 Golden 增量
→ B3 Identity / Policy 预检闭环
→ B4 Workflow 轻量引擎 + 执行
→ B5 Session Memory + Evaluator + Admin Lite
```

## 7. Legacy Task Record 说明

历史 Unified Task Record 按原 schema 与语义保留；新的 native Goal 按当前 `AGENTS.md` Completion 要求收口，不新增 Task Record。
