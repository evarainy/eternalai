# TASK_INDEX — Phase 1 Dependency DAG v1.1.0

本文件是 Phase 1 的任务依赖 DAG。Codex / Claude Code 必须按批次和 `depends_on` 执行，不得跳过前置任务。

**强制单任务执行：每个 lane/state 只能执行一个 `task_id`。下一任务必须同时满足 DAG、当前 task contract 的 `auto_next_policy` 与全部 required stops；R2 的 post-integration result acceptance 未闭合时不得 auto-next。**

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

## B2+ per-task prompt gate

B1 的 per-task prompt 已内置（`docs/phase1/tasks/`）。B2-B5 启动前必须生成对应 `docs/phase1/tasks/<task_id>.md`。不得在缺少 per-task prompt 的情况下执行 B2-B5。

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

本链只登记 W002 治理修复及其后续任务依赖，不改写已完成的 `P1-WORKFLOW-002` 历史状态，也不表示 03—05 已具备执行条件。

| task_id | title | depends_on | deliverable / start gate |
|---|---|---|---|
| P1-WORKFLOW-002-REPAIR-001 | W002 完整范围修复与活动控制面收敛 | P1-WORKFLOW-002 completed；P1-WORKFLOW-002-REPAIR-BOOTSTRAP-001 landed | 正式 03—05 task contracts、活动治理规则核对、repo-local phase-task companion 边界证据；完成集成并通过 post-integration Gate 2 后才释放下一项 |
| P1-CI-ALIGN-001 | Phase 1 预合并 CI 对齐 | P1-WORKFLOW-002-REPAIR-001 completed + Gate 2 accepted | `docs/phase1/tasks/P1-CI-ALIGN-001.md`；本 lane 对齐 CI 事件矩阵与 frontend test；完成与结果验收后，P1-OBS-001 仅可作为新 lane 调度，不自动启动 |
| P1-OBS-001 | Trace 生命周期与敏感字段契约修复 | P1-CI-ALIGN-001 completed | `docs/phase1/tasks/P1-OBS-001.md`；contract 已登记，但依赖满足前不得启动 |
| P1-RUNTIME-ENTRY-001 | Runtime Composition Root 与 Golden Harness 解耦 | P1-OBS-001 completed | `docs/phase1/tasks/P1-RUNTIME-ENTRY-001.md`；contract 已登记，但依赖满足前不得启动 |

```text
P1-WORKFLOW-002-REPAIR-001
→ P1-CI-ALIGN-001
→ P1-OBS-001
→ P1-RUNTIME-ENTRY-001
```

**硬门**：`P1-WORKFLOW-002-REPAIR-001` 未完成集成且其 post-integration Gate 2 未通过前，`P1-CI-ALIGN-001`、`P1-OBS-001`、`P1-RUNTIME-ENTRY-001` 均不可启动；每次仍只执行一个 `task_id`。

正式 descriptor 存在只表示结果契约已登记，不表示 task prompt-ready 等于 dependency-ready。普通 commit/push/PR/merge/CI 是否自动由各 task contract 的 controller risk、automation、Review/validation/freshness 与 repo policy 决定；Gate 2 只验收集成结果。

### B1 可选任务

| task_id | title | depends_on | 备注 |
|---|---|---|---|
| P1-TOOLCALL-002 | 工具调用 prompt 第二轮复测 | 内网 vLLM endpoint 可用 | **optional / 不阻塞 Phase 1**；spike PDR；产出 experiments/phase1/ |

## 2. B2 — Intent → Capability 选择闭环

**前置（硬门，已满足）：`P1-GATE-001 passed + PHASE1_SPEC (P1-SPEC-001) approved/landed`**

| task_id | title | depends_on | 纵切内容 |
|---|---|---|---|
| （B2 任务 TBD，由 P1-SPEC-001 产出后生成 per-task prompt） | Intent → Capability 选择闭环 | P1-GATE-001, P1-SPEC-001 | Intent Router 实现 + Capability Preselector 轻量版 + `no_capability_found` 路径 |

> P1-SPEC-001 产品硬门已满足并标记 B2 unlocked；本任务不生成 per-task prompt、不启动 B2。`P1-SPEC-001-APPROVE-001` 集成并通过其自身 post-integration result acceptance 后，才可在新的 task lane 生成 B2 per-task prompt；缺少 prompt 仍不得执行。

## 3. B3 — Identity / Policy 预检闭环

**前置：B2 完成**

| task_id | title | depends_on | 纵切内容 |
|---|---|---|---|
| （B3 任务 TBD） | Identity / Policy 预检闭环 | B2 | IdentityMapping Mock 表 + Policy Guard 绑定预检 + 未绑定 `operator_handback_card` + confirm 路径 |

## 4. B4 — Workflow 轻量引擎 + 执行

**前置：B3 完成**

| task_id | title | depends_on | 纵切内容 |
|---|---|---|---|
| （B4 任务 TBD） | Workflow 轻量引擎 + 执行 | B3 | 线性 Workflow + step Policy + 有限重试 + human_gate，经 Gateway 执行 Mock Adapter |

## 5. B5 — Session Memory + Evaluator + Admin Lite

**前置：B4 完成**

| task_id | title | depends_on | 纵切内容 |
|---|---|---|---|
| （B5 任务 TBD） | Session Memory + Evaluator + Admin Lite | B4 | 最小记忆层 + Evaluator + Registry/Policy/Trace/Binding 管理页 |

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
→ B2 Intent → Capability 选择闭环
→ B3 Identity / Policy 预检闭环
→ B4 Workflow 轻量引擎 + 执行
→ B5 Session Memory + Evaluator + Admin Lite
```

## 7. Unified Task Record 要求

每个任务完成、失败或阻塞时，必须生成机器可读 YAML Task Record：

```text
docs/phase1/task_logs/<task_id>_<YYYYMMDD_HHMMSS>_<passed|failed|blocked>.yaml
```

统一 schema 见：

```text
docs/dev/task_record_schema.yaml
```

关键要求（统一 schema v1.2.0；Phase 0 与既有 Phase 1 记录按其原版本解释）：
- `not_applicable` 必须包含 reason、scope、blocked_by_task_id、activation_task_id、expiry_condition 和 evidence。
- `review.mode` 可为 `none | self_review | independent_review | human_review`。
- 旧 `codex_review`、`self_check`、`human_optional` 只用于历史记录解释，按 `docs/phase1/ROLE_POLICY.md` 的 migration table 处理。
- `package_confirmation_status` 可为 `created | not_created | not_applicable`。
- 新 high-tier records 分开记录 controller risk/automation/required stops、Plan/final Review、task-branch/PR checks、merge SHA、post-merge CI、R3 authorization、auto-next 与 filesystem companion（如适用）；未触发检查不得写成 passed。
