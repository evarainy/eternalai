# P1-SPEC-001 — Single-task Prompt (hardened)

> **适用范围声明**: 本文件与 `docs/phase1/TASK_PROMPT_TEMPLATE.md` 配套使用。模板中的 Plan-first 流程、执行中工作流规则(no commit / no push / no merge、仅 stage for review、禁用 `--no-verify`)、Task Record 与证据规则**全量适用**。本文件只补充任务专属内容; 两者冲突时停手并请求 task-prompt 补丁(`task_prompt_incomplete`)。
> **权威声明**: Phase 1 规则以当前任务 prompt、repo root `CLAUDE.md`、`docs/phase1/*` 和明确标注跨阶段沿用的文档为准; `AGENTS.md` 只是 generic coding agent compact boot file, **不是 Phase 1 任务权威**。

## Background / 任务由来

`docs/phase1/PHASE1_SPEC.md` 当前为 PLACEHOLDER（由 P1-SKEL-001 建立）。B2-B5 实现型纵切的 acceptance criteria 必须有明确来源。现仅有 blueprint（方向）+ MVP spec v1.0.11（多处锚的是 Phase 0 范围）+ Phase 1 Plan（切分策略），尚无 Phase 1 专属 spec 文档。

**P1-SPEC-001 是 B2 的硬前置。`docs/phase1/PHASE1_SPEC.md` 未落盘并经人类批准前，不得进入 B2。**

本任务只生成 `docs/phase1/PHASE1_SPEC.md` 的正式内容与 Task Record; 不执行 B2-B5, 不修改 frozen ports, 不修改 blueprint, 不生成 B2-B5 task prompts。

## Required context

执行者必须按需读取以下上下文; 不要把整份长 spec 粘贴进 session:

- `CLAUDE.md` — Phase 1 governance, mainline order, no-commit/no-push/no-merge discipline
- `docs/phase1/ROLE_POLICY.md` — Phase 1 role / review / risk policy
- `docs/phase1/TASK_PROMPT_TEMPLATE.md` — Plan-first, evidence, Task Record, staged-diff rules
- `docs/phase1/PHASE1_PLAN.md` — C.1/C.2 scope and vertical-slice plan; subordinate to this spec after approval
- `docs/phase1/TASK_INDEX.md` — B2 hard gate and dependency DAG
- `docs/phase1/BLUEPRINT_ERRATA.md` — errata and clarifications that override frozen source documents
- `docs/phase0/PHASE1_TECHNICAL_BASELINE.md` — raw OpenAI SDK + vLLM raw JSON baseline
- `docs/dev/task_record_schema.yaml` — unified Task Record schema
- `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md` §13 L2680-L2699
- `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md` §4.3.2 L438-L446
- `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md` §4.3.3 L468
- `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md` §10.1 L2166-L2168
- `docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md` §20.1 L4501-L4503
- `docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md` §12.5

## Source authority order / 来源权威序

当来源之间冲突时, 按以下顺序解释 Phase 1 spec:

```text
BLUEPRINT_ERRATA.md > PHASE1_TECHNICAL_BASELINE.md > MVP spec v1.0.11（阈值/验收语言） > blueprint（方向）
```

- blueprint 中已被 `BLUEPRINT_ERRATA.md` 勘误或澄清的表述不得进入 `PHASE1_SPEC.md` 正文, 除非以"禁止 / 已勘误 / 非基线"语境出现。
- `instructor` / `PydanticAI` 不得被写成 Phase 1 基线。Phase 1 baseline = raw OpenAI SDK + vLLM raw JSON mode + Pydantic v2 validation.
- ARQ 只能作为 L1 候选 / 澄清项出现, 不得被写成 Phase 1 L0 必装主线。
- MVP spec v1.0.11 §20.1 是 golden threshold / acceptance language 的主要来源; §12.5 只作为负向 / 边界场景清单来源。

## method_profile

```yaml
method_profile:
  execution_role: "documentation"
  execution_owner: "codex"
  review_owner: "claude_code"
  review_mode: "independent_review"
  risk_tier: "high"
  model_note: "Record actual executor/reviewer model in Task Record notes until schema has model fields."
  method: "PDR"
  reason_for_owner_choice: >
    PHASE1_SPEC.md 是 B2-B5 全部验收面的来源。虽然本任务是 docs-only，
    但其错误会向下游所有实现型任务扩散，因此按 ROLE_POLICY 手工升为 high，
    需要 independent_review + human approval。
```

## Task YAML

```yaml
task_id: P1-SPEC-001
title: "Phase 1 详细 spec 产出（B2 硬前置）"
type: documentation
method: PDR
depends_on:
  - "human approval of this prompt patch"
  - "P1-ERRATA-001 landed"
  - "P1-GATE-001 passed"
  - "P1-WORKFLOW-001 landed"
depends_on_note: >
  可并行起草 PHASE1_SPEC.md, 但 PHASE1_SPEC.md 被批准前,
  P1-GATE-001 必须 passed, P1-ERRATA-001 / P1-WORKFLOW-001 必须 landed。
objective: >
  承接 blueprint §13（Phase 1：MVP 主链 L2680-L2699）+
  §4.3 Workflow（§4.3.2 L438-L446、§4.3.3 L468）+
  Phase 1 Errata / Technical Baseline / MVP spec 验收语言,
  裁剪为可执行的 Phase 1 详细 spec，整体替换 docs/phase1/PHASE1_SPEC.md。
  B2-B5 每个纵切的 acceptance criteria 必须可追溯到本 spec 的稳定小节 ID。
deliverable: "docs/phase1/PHASE1_SPEC.md"
b2_gate: >
  B2 不得在本任务落盘并人类批准前启动。
  B2 前置条件 = P1-GATE-001 passed + PHASE1_SPEC (P1-SPEC-001) approved/landed。
spec_scope:
  in_scope:
    - "Web/CLI 入口 → Intent Router → Workflow/Tool 执行 → Policy Precheck → Trace → Evaluator（§13 L2685-L2690）"
    - "Admin Lite：Registry / Policy / Trace / 基础用户角色 / Binding 状态（§13 L2691）"
    - "Session Memory + 基础 Semantic/System Knowledge（§10.1 L2166-L2168：Phase 1 只实现最小层）"
    - "IdentityMapping Mock 表；Policy Guard 绑定状态预检；未绑定返回 SDUI operator_handback_card；无能力返回 no_capability_found（§13 L2693-L2696）"
    - "Workflow Engine 轻量版：线性步骤、简单分支、step IO 映射、step 级 Policy、有限重试、human_gate、版本锁定、全链路 Trace（§4.3.2 L438-L446）"
  out_of_scope:
    - "真实业务系统写操作（§13 L2697-L2698）"
    - "生产级 Controlled Exploration（§4.3.3 L468）"
    - "动态 Tool Composition（§13 L2697）"
    - "复杂 DAG/长事务（§4.3.3 L468）"
touched_paths:
  - docs/phase1/PHASE1_SPEC.md
  - docs/phase1/task_logs/P1-SPEC-001_*.yaml
  - docs/phase1/task_logs/INDEX.md
forbidden_paths:
  - app/
  - app/ports/
  - docs/phase0/
  - docs/blueprint/
  - docs/phase1/PHASE1_PLAN.md
  - docs/phase1/TASK_INDEX.md
  - docs/phase1/TASK_PROMPT_TEMPLATE.md
  - docs/phase1/tasks/
  - tests/
  - scripts/
  - .github/workflows/
  - web/
  - AGENTS.md
```

## Spec structure contract

`PHASE1_SPEC.md` 必须整体替换 placeholder, 并满足以下结构契约。执行者不得用自由散文替代本契约。

### Header contract

文件头部必须包含:

- `status: draft`
- 人类批准后改为 `status: approved by <human> on <YYYY-MM-DD>`
- 权威声明: B2-B5 范围 / 验收以 `PHASE1_SPEC.md` 为准; 与 `docs/phase1/PHASE1_PLAN.md` C.1/C.2 冲突时, 以 `PHASE1_SPEC.md` 为准。

### Stable section ID contract

- 每个可引用小节必须有稳定 ID, 形如 `S-B2.1`, `S-B2.2`, `S-B3.1`。
- 下游 task prompt / Task Record / review 引用以稳定 ID 为准; 行号只作定位辅助。
- 修订文字不得随意重排或复用 ID; 若删除小节, 在 revision note 中记录 ID tombstone。

### Batch chapter contract

B2-B5 每个批次各一章。每章强制包含 5 个小节, 且小节标题必须可 grep / 枚举核对:

1. `范围与非目标`: 锚回 blueprint / errata / baseline 行号, 明确本批次做什么和不做什么。
2. `涉及的 frozen port 清单`: 只引用 13 个 frozen port 中本批次涉及哪些; 零修改提议, 不得建议改 `app/ports/`。
3. `验收来源`: 分别说明 golden / pytest / `tests/architecture` 各测什么; 不得只写"跑测试"。
4. `golden 覆盖增量`: 列出该批次必须新增哪些 golden 场景, 至少覆盖负向路径。扩充流程必须写成"专门任务 + 显式授权修改 FROZEN_GT_IDS / fixtures + 人批"; spec 内不得直接写 fixture JSON。
5. `裁剪决策记录`: PDR 落点, 说明裁掉 blueprint 哪些内容及原因。

B4 章必须额外包含子任务切分建议, 至少覆盖:

- 引擎骨架
- step Policy
- human_gate
- 其他必要切分由执行者按 spec 合理提出, 但不得写代码级设计或函数签名。

## Acceptance criteria

- AC-1: `docs/phase1/PHASE1_SPEC.md` 整体替换 PLACEHOLDER, 不是追加。
- AC-2: B2-B5 四章 × 五小节齐全, 20 个小节标题可 grep / 枚举核对。
- AC-3: 附 C.1 交付清单 → spec 小节 ID 的逐项映射表, 无遗漏。
- AC-4: 不改 `app/ports/`。
- AC-5: 不改 `docs/blueprint/`。
- AC-6: 人类批准后方可解锁 B2; 批准形态必须包含 spec status、Task Record、task_logs INDEX 三处一致。
- AC-7: `grep -i instructor docs/phase1/PHASE1_SPEC.md` 只允许命中"禁止 / 已勘误 / 非基线"语境, 或 0 命中。
- AC-8: 占位声明文本 grep 0 命中, 证明整体替换。
- AC-9: 每章第 4 小节存在且包含 golden 扩充授权流程语句。
- AC-10: `git diff --cached --name-only` 只包含 touched_paths。

## failure_examples

- F-a: 照抄 blueprint 的 `instructor` / `PydanticAI` / ARQ 表述, 未按 ERRATA 修正。
- F-b: 某批次只有一句话描述, 过粗, 无法生成后续 task prompt。
- F-c: 写函数签名 / 代码级设计, 越权替代实现任务的 Plan 阶段。
- F-d: 顺手修改 Plan / TASK_INDEX / blueprint。
- F-e: spec 内直接写 fixture JSON 或扩 FROZEN_GT_IDS, 绕过冻结授权。
- F-f: 引入新依赖决策, 例如 embedding 库, 但不标注"需 ADR + 人批"。
- F-g: 把 Phase 0 已交付物重新 spec 一遍, 造成范围倒灌。

## step_verification_points

- SVP-a: 大纲先行。执行 `P1-SPEC-001` 时先交章节骨架（标题 + 每章一句范围）给人批, 批准后才填充。
- SVP-b: 填充后跑结构 grep 自检: 章节数 / `instructor` / 占位声明。
- SVP-c: C.1 映射表核对。
- SVP-d: diff 四连:
  - `git diff --cached --name-only`
  - `git diff --cached --stat`
  - `git diff --cached --check`
  - `git ls-files --others --exclude-standard`

## stop_conditions

命中任一情况必须停止并报告; 不得继续修改:

- 与模板 / 本 prompt 冲突或信息不足 → `task_prompt_incomplete`。
- `BLUEPRINT_ERRATA.md` 不存在或未 landed → blocked。
- `P1-GATE-001` 未 passed, 且当前动作已从并行起草进入 approval / landing 阶段。
- 发现 port 契约不足以承载 Phase 1 范围 → 停手上报, 不在 spec 中写修改建议。
- 需要修改 forbidden_paths。
- 需要引入新依赖决策且无 ADR / 人批。
- blueprint / MVP spec / ERRATA 锚点无法核到。
- 需要写 fixture JSON 或扩 FROZEN_GT_IDS。
- 无法用稳定 ID 建立 B2-B5 追溯关系。
- `PHASE1_SPEC.md` 无法整体替换 placeholder 而不引入范围倒灌。

## test_commands

以下命令用于 `P1-SPEC-001` 执行完成后的文档型验证。代码块必须按 bash 执行或等价转换; 不得使用 PowerShell-only 语法。

```bash
# AC-8: 期望 0 命中; 若有命中, 说明 placeholder 没有被整体替换。
grep -R "不得将本文件的任何内容视为正式 Phase 1 spec" docs/phase1/PHASE1_SPEC.md || true

# AC-7: 允许 0 命中; 若有命中, 必须逐条人工核对只处于"禁止 / 已勘误 / 非基线"语境。
grep -i instructor docs/phase1/PHASE1_SPEC.md || true

# AC-10 / SVP-d: staged diff 必须只包含 touched_paths, 且 diff check 干净。
git diff --cached --name-only
git diff --cached --stat
git diff --cached --check
git ls-files --others --exclude-standard
```

判定方式:

- placeholder grep 有任何命中 = AC-8 failed。
- `instructor` grep 有命中但不是禁止 / 已勘误 / 非基线语境 = AC-7 failed。
- staged paths 超出 `touched_paths` = AC-10 failed。
- `git diff --cached --check` 有输出 = failed。

## Workflow and Task Record

- 先输出 Plan, 等人类批准后再执行。
- 按 SVP-a 先提交 `PHASE1_SPEC.md` 大纲（标题 + 每章一句范围）给人类批准; 未批准不得填充正文。
- 完成后仅 stage for review, 不 commit / push / merge。
- Task Record 保存到 `docs/phase1/task_logs/P1-SPEC-001_<YYYYMMDD_HHMMSS>_<passed|failed|blocked>.yaml`。
- `changed_files` 必须在最终 stage 之后、commit 之前更新, 并与 `git diff --cached --name-only` 完全一致。
- 更新 `docs/phase1/task_logs/INDEX.md`; spec status、Task Record、INDEX 三处批准状态必须一致后, B2 才能解锁。
