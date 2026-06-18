# P1-SPEC-001 — Single-task Prompt

## Background / 任务由来

`docs/phase1/PHASE1_SPEC.md` 当前为 PLACEHOLDER（由 P1-SKEL-001 建立）。B2-B5 实现型纵切的 acceptance criteria 必须有明确来源。现仅有 blueprint（方向）+ MVP spec v1.0.11（锚的是 Phase 0 范围），尚无 Phase 1 专属 spec 文档。

**P1-SPEC-001 是 B2 的硬前置。`docs/phase1/PHASE1_SPEC.md` 未落盘并经人类批准前，不得进入 B2。**

## method_profile

```yaml
method_profile:
  execution_role: "documentation"
  execution_owner: "claude_code_mimo"
  review_owner: "codex"
  review_mode: "codex_review"
  method: "PDR"
  reason_for_owner_choice: >
    Phase 1 spec 是架构决策文档（承接 blueprint §13 + §4.3 裁剪 + P1-GATE-001 产出的阈值约束），
    需要 PDR 方法：列选项（scope 裁剪边界）、决策（纵切粒度 / AC 来源 / 验收门），记录结果。
    无生产代码变更。
```

## Task YAML

```yaml
task_id: P1-SPEC-001
title: "Phase 1 详细 spec 产出（B2 硬前置）"
type: documentation
method: PDR
objective: >
  承接 blueprint §13（Phase 1：MVP 主链 L2680-2699）+
  §4.3 Workflow（§4.3.2 L438-455、§4.3.3 L468），
  裁剪为可执行的 Phase 1 详细 spec，产出 docs/phase1/PHASE1_SPEC.md。
  B2-B5 每个纵切的 acceptance criteria 必须可追溯到本 spec 章节。
deliverable: "docs/phase1/PHASE1_SPEC.md"
b2_gate: >
  B2 不得在本任务落盘并人类批准前启动。
  B2 前置条件 = P1-GATE-001 passed + PHASE1_SPEC (P1-SPEC-001) approved/landed。
spec_scope:
  in_scope:
    - "Web/CLI 入口 → Intent Router → Workflow/Tool 执行 → Policy Precheck → Trace → Evaluator（§13 L2685-2690）"
    - "Admin Lite：Registry / Policy / Trace / 基础用户角色 / Binding 状态（§13 L2691）"
    - "Session Memory + 基础 Semantic/System Knowledge（§10.1 L2166-2168：Phase 1 只实现最小层）"
    - "IdentityMapping Mock 表；Policy Guard 绑定状态预检；未绑定返回 SDUI operator_handback_card；无能力返回 no_capability_found（§13 L2693-2696）"
    - "Workflow Engine 轻量版：线性步骤、简单分支、step IO 映射、step 级 Policy、有限重试、human_gate、版本锁定、全链路 Trace（§4.3.2 L438-446）"
  out_of_scope:
    - "真实业务系统写操作（§13 L2697-2698）"
    - "生产级 Controlled Exploration（§4.3.3 L468）"
    - "动态 Tool Composition（§13 L2697）"
    - "复杂 DAG/长事务（§4.3.3 L468）"
acceptance_criteria:
  - AC-1: docs/phase1/PHASE1_SPEC.md 落盘，替换 PLACEHOLDER
  - AC-2: spec 承接 blueprint §13 + §4.3，in_scope / out_of_scope 明确
  - AC-3: B2-B5 每个纵切有对应 spec 章节和可验收 AC 来源
  - AC-4: 不改 app/ports/（FROZEN）
  - AC-5: 不改 docs/blueprint/
  - AC-6: 人类批准后方可解锁 B2（本任务 Task Record passed + 人类签字）
touched_paths:
  - docs/phase1/PHASE1_SPEC.md   # 替换 PLACEHOLDER，写入正式内容
  - docs/phase1/task_logs/P1-SPEC-001_*.yaml
forbidden_paths:
  - app/
  - app/ports/
  - docs/phase0/
  - docs/blueprint/
  - .github/workflows/
```

## Stop Conditions

- 需修改 `docs/blueprint/` → 停手（蓝图冻结）
- 需修改 `app/ports/` → 停手
- blueprint §13 / §4.3 行号锚点无法核到 → 停手报告

## Test Commands（文档型任务）

```powershell
git diff --cached --name-only   # 仅含 docs/phase1/PHASE1_SPEC.md + Task Record
git diff --cached --check
```
