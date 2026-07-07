# TASK_INDEX — Phase 1 Dependency DAG v1.0.0

本文件是 Phase 1 的任务依赖 DAG。Codex / Claude Code 必须按批次和 `depends_on` 执行，不得跳过前置任务。

**强制单任务执行：每次只能执行一个 `task_id`。完成该 task 并输出统一 Task Record 后，等待人工确认，再进入下一个 task。**

## 0. 批次总览

```text
B1（首批）：启动准备
  ↓
B2：Intent → Capability 选择闭环     ← 前置：P1-GATE-001 passed + PHASE1_SPEC (P1-SPEC-001) approved/landed
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
| P1-SPEC-001 | Phase 1 详细 spec 产出（**B2 硬前置**） | P1-GATE-001 passed（建议）；人类批准 | docs/phase1/PHASE1_SPEC.md（B2-B5 实现型任务的必要前提） |

> **P1-PARAM-001 说明**：此任务依赖 infra 提供生产/内网 vLLM 实际部署参数（`max_model_len`、量化方式、`request_timeout`、`max_tokens`、`enable_thinking` 行为）。infra 未回值前该任务天然 blocked，不与其他 B1 任务并列为"无条件可做"。

> **P1-SPEC-001（pre-B2 硬门）**：`docs/phase1/PHASE1_SPEC.md` 未落盘并经人类批准前，**不得进入 B2**。B2 前置条件 = `P1-GATE-001 passed + PHASE1_SPEC (P1-SPEC-001) approved/landed`。

### B1 可选任务

| task_id | title | depends_on | 备注 |
|---|---|---|---|
| P1-TOOLCALL-002 | 工具调用 prompt 第二轮复测 | 内网 vLLM endpoint 可用 | **optional / 不阻塞 Phase 1**；spike PDR；产出 experiments/phase1/ |

## 2. B2 — Intent → Capability 选择闭环

**前置（硬门）：`P1-GATE-001 passed + PHASE1_SPEC (P1-SPEC-001) approved/landed`**

| task_id | title | depends_on | 纵切内容 |
|---|---|---|---|
| （B2 任务 TBD，由 P1-SPEC-001 产出后生成 per-task prompt） | Intent → Capability 选择闭环 | P1-GATE-001, P1-SPEC-001 | Intent Router 实现 + Capability Preselector 轻量版 + `no_capability_found` 路径 |

> B2 per-task prompt 必须在 P1-SPEC-001 落盘并批准后才能生成。缺少 per-task prompt 不得执行。

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
B1 启动准备（P1-GATE-001 先完成）
→ P1-SPEC-001 落盘并批准（B2 硬前置）
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

关键要求（沿用 Phase 0 v1.0.11 规范）：
- `not_applicable` 必须包含 reason、scope、blocked_by_task_id、activation_task_id、expiry_condition 和 evidence。
- `review.mode` 可为 `none | self_review | independent_review | human_review`。
- 旧 `codex_review`、`self_check`、`human_optional` 只用于历史记录解释，按 `docs/phase1/ROLE_POLICY.md` 的 migration table 处理。
- `package_confirmation_status` 可为 `created | not_created | not_applicable`。
