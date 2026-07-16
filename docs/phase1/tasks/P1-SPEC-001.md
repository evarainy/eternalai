# P1-SPEC-001 — Phase 1 详细 spec 产出（B2 硬前置）

```yaml
task_id: "P1-SPEC-001"
task_type: "documentation"
goal: "基于已落地的 Phase 1 勘误、技术基线和上游冻结文档，整体替换 PHASE1_SPEC.md placeholder，形成 B2-B5 可追溯、可验收的正式结果契约"
non_goals:
  - "不执行 B2-B5，不生成 B2-B5 task prompts，不修改 frozen ports、blueprint、PHASE1_PLAN、TASK_INDEX、模板、应用代码、测试或 CI"
  - "不引入新依赖决策，不直接编写或修改 golden fixture/FROZEN_GT_IDS"
method_profile:
  execution_role: "documentation"
  execution_owner: "codex"
  review_owner: "claude_code"
  review_mode: "independent_review"
  risk_tier: "high"
  method: "PDR"
  model_note: "Opus read-only Plan and final-diff reviews; Codex owns execution"
  reason_for_owner_choice: "PHASE1_SPEC.md is the public acceptance source for all B2-B5 implementation tasks"
controller_risk_tier: "R2"
risk_classification_reason: "High-impact public result contract and B2 release gate"
plan_review_required: true
automation_class: "human_pre_apply"
authorization_mode: "standard"
required_stops:
  - "human_pre_apply: approve the chapter outline before body fill"
  - "human_result_acceptance: approve the integrated spec before B2 unlock"
r3_authorization: []
spec_scope:
  in_scope:
    - "Web/CLI 入口 → Intent Router → Workflow/Tool 执行 → Policy Precheck → Trace → Evaluator（§13 L2685-L2690）"
    - "Admin Lite：Registry / Policy / Trace / 基础用户角色 / Binding 状态（§13 L2691）"
    - "Session Memory + 基础 Semantic/System Knowledge（§10.1 L2166-L2168：Phase 1 只实现最小层）"
    - "IdentityMapping Mock 表；Policy Guard 绑定状态预检；未绑定返回 SDUI operator_handback_card；无能力返回 no_capability_found（§13 L2693-L2696）"
    - "Workflow Engine 轻量版：线性步骤、简单分支、step IO 映射、step 级 Policy、有限重试、human_gate、版本锁定、全链路 Trace（§4.3.2 L438-L446）"
  out_of_scope:
    - "真实业务系统写操作（§13 L2697-L2698）"
    - "生产级 Controlled Exploration（§13 L2697）"
    - "动态 Tool Composition（§2.3 L103、§6.5 L1138）"
    - "复杂 DAG/长事务（§4.3.3 L468）"
touched_paths:
  - "docs/phase1/PHASE1_SPEC.md"
  - "docs/phase1/task_logs/P1-SPEC-001_*.yaml"
  - "docs/phase1/task_logs/INDEX.md"
forbidden_paths:
  - "app/**"
  - "docs/phase0/**"
  - "docs/blueprint/**"
  - "docs/phase1/PHASE1_PLAN.md"
  - "docs/phase1/TASK_INDEX.md"
  - "docs/phase1/TASK_PROMPT_TEMPLATE.md"
  - "docs/phase1/tasks/**"
  - "tests/**"
  - "scripts/**"
  - ".github/workflows/**"
  - "web/**"
  - "AGENTS.md"
acceptance_criteria:
  - "AC-1: docs/phase1/PHASE1_SPEC.md is an overall replacement of PLACEHOLDER, not an append"
  - "AC-2: B2-B5 each contain the five required subsections, for 20 grep-enumerable subsection headings"
  - "AC-3: a complete mapping table links every PHASE1_PLAN C.1 deliverable to a stable spec section ID"
  - "AC-4: app/ports is unchanged"
  - "AC-5: docs/blueprint is unchanged"
  - "AC-6: B2 unlocks only after human approval and consistent spec status, Task Record, and task_logs INDEX evidence"
  - "AC-7: instructor appears only in forbidden/errata/non-baseline context, or not at all"
  - "AC-8: the placeholder declaration has zero matches after replacement"
  - "AC-9: every batch chapter has a golden-extension authorization statement in its fourth subsection"
  - "AC-10: staged paths contain only touched_paths"
failure_examples:
  - "Copy instructor, PydanticAI, or ARQ from the blueprint without applying BLUEPRINT_ERRATA"
  - "Describe a batch too coarsely to generate downstream task contracts"
  - "Write function signatures or code-level design in place of an implementation Plan"
  - "Modify PHASE1_PLAN, TASK_INDEX, blueprint, fixtures, or FROZEN_GT_IDS"
  - "Introduce a dependency decision without an ADR and human approval"
  - "Respecify Phase 0 deliverables and pull them back into Phase 1 scope"
step_verification_points:
  - "Outline-first: present headings plus one scope sentence per chapter and wait for human_pre_apply approval before body fill"
  - "After fill: verify chapter/subsection counts, instructor context, and zero placeholder matches"
  - "Verify the complete C.1-to-stable-section-ID mapping"
  - "Verify exact staged names/stat/check and untracked files before final Review"
validation_commands:
  - "grep -R '不得将本文件的任何内容视为正式 Phase 1 spec' docs/phase1/PHASE1_SPEC.md || true"
  - "grep -i instructor docs/phase1/PHASE1_SPEC.md || true"
  - "git diff --cached --name-only"
  - "git diff --cached --stat"
  - "git diff --cached --check"
  - "git ls-files --others --exclude-standard"
evidence_requirements:
  - "Human outline approval note bound to the resumed task history"
  - "Stable-section and C.1 mapping enumeration"
  - "Context review for every instructor match and zero placeholder matches"
  - "Candidate-bound Plan/final Review meta and exact staged-path evidence"
  - "Post-integration spec status, Task Record, task_logs INDEX, merge SHA, CI, and human result acceptance"
stop_conditions:
  - "Current descriptor conflicts with a higher-authority repository rule or lacks information: task_prompt_incomplete"
  - "BLUEPRINT_ERRATA is absent/unlanded, required source anchors cannot be verified, or P1-GATE is not passed before approval/landing"
  - "A frozen port appears insufficient, a new dependency needs decision, or any forbidden path is required"
  - "Fixture JSON/FROZEN_GT_IDS changes or a stable B2-B5 traceability map would be required but cannot be authorized"
  - "PHASE1_SPEC.md cannot replace the placeholder without Phase 0 scope backflow"
local_commit_policy: "after_review_pass"
integration_policy:
  mode: "git"
  remote_strategy: "task_branch_pr_merge"
  task_branch_ci: "if_triggered"
  post_merge_ci: "required"
auto_next_policy: "blocked"
depends_on:
  - "P1-SPEC-CONTRACT-ALIGN integrated and human_result_acceptance satisfied"
  - "P1-GATE-001 passed"
  - "P1-ERRATA-001 landed"
  - "P1-WORKFLOW-001 landed"
branch: "phase1/P1-SPEC-001"
references:
  - "AGENTS.md"
  - "CLAUDE.md"
  - "docs/phase1/ROLE_POLICY.md"
  - "docs/phase1/TASK_PROMPT_TEMPLATE.md"
  - "docs/phase1/PHASE1_PLAN.md"
  - "docs/phase1/TASK_INDEX.md"
  - "docs/phase1/BLUEPRINT_ERRATA.md"
  - "docs/phase0/PHASE1_TECHNICAL_BASELINE.md"
  - "docs/dev/task_record_schema.yaml"
```

## Background / 任务由来

`docs/phase1/PHASE1_SPEC.md` 当前为 PLACEHOLDER（由 P1-SKEL-001 建立）。B2-B5 实现型纵切的 acceptance criteria 必须有明确来源。现仅有 blueprint（方向）+ MVP spec v1.0.11（多处锚的是 Phase 0 范围）+ Phase 1 Plan（切分策略），尚无 Phase 1 专属 spec 文档。

**P1-SPEC-001 是 B2 的硬前置。`docs/phase1/PHASE1_SPEC.md` 未落盘并经人类批准前，不得进入 B2。**

本任务只生成 `docs/phase1/PHASE1_SPEC.md` 的正式内容与 Task Record; 不执行 B2-B5, 不修改 frozen ports, 不修改 blueprint, 不生成 B2-B5 task prompts。

## Required context

执行者必须按需读取以下上下文; 不要把整份长 spec 粘贴进 session:

- `AGENTS.md` — repository-wide authority, red lines, validation and integration rules
- `CLAUDE.md` — Phase 1 governance and mainline order
- `docs/phase1/ROLE_POLICY.md` — Phase 1 role / review / risk policy
- `docs/phase1/TASK_PROMPT_TEMPLATE.md` — current task result-contract fields and evidence rules
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

- 按契约先完成 Plan Review，再提交 `PHASE1_SPEC.md` 大纲（标题 + 每章一句范围）给人类批准；未批准不得填充正文。
- 完成验证和 candidate-bound final Review 后，按仓库 Git integration policy 执行非强制集成，不使用 rebase/reset-hard/force。
- Task Record 保存到 `docs/phase1/task_logs/P1-SPEC-001_<YYYYMMDD_HHMMSS>_<passed|failed|blocked>.yaml`。
- `changed_files` 必须与最终 candidate 的精确 changed paths 一致。
- 更新 `docs/phase1/task_logs/INDEX.md`；spec status、Task Record、INDEX、merge/CI 和 human result acceptance 一致后，B2 才能解锁。
