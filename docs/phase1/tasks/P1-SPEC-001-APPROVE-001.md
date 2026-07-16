# P1-SPEC-001-APPROVE-001 — 批准 P1-SPEC-001 并解锁 B2

```yaml
task_id: "P1-SPEC-001-APPROVE-001"
task_type: "preparation"
goal: "在 P1-SPEC-001 已合并并经 Gate 2 验收后，完成其正式批准与 B2 硬门释放：修正 descriptor 两处错误的 out_of_scope 锚点、把 PHASE1_SPEC.md status 由 draft 改为 approved、把 SPEC-001 Task Record/INDEX 同步到 accepted+approved、在 TASK_INDEX 标注 B2 硬门已满足；三方（spec status / Task Record / INDEX）一致"
non_goals:
  - "不改动 PHASE1_SPEC.md 正文语义：5 in/4 out 映射、20 个稳定 ID 小节、B2-B5 四章内容字节不变，仅改 status 字段与批准门注释"
  - "不生成 B2 per-task prompt、不启动 B2、不动 B2-B5 任何实现"
  - "不修改 ROLE_POLICY、TASK_PROMPT_TEMPLATE、Task Record schema、PHASE1_PLAN、blueprint、frozen ports、app/tests/scripts/web/CI"
method_profile:
  execution_role: "documentation"
  execution_owner: "codex"
  review_owner: "claude_code"
  review_mode: "independent_review"
  risk_tier: "high"
  method: "PDR"
  model_note: "Opus read-only Plan and final-diff reviews; Codex owns execution"
  reason_for_owner_choice: "这一步把 Phase 1 官方 spec 置为 approved 并释放整个 B2-B5，是 Phase 1 最高杠杆的治理翻转"
controller_risk_tier: "R2"
risk_classification_reason: "官方结果契约批准 + B2 释放门；docs-only 但影响全部下游"
plan_review_required: true
automation_class: "auto"
authorization_mode: "standard"
required_stops:
  - "human_result_acceptance: 批准三方一致的批准结果，再收官"
r3_authorization: []
authorized_descriptor_edits:
  - "本任务显式授权编辑 docs/phase1/tasks/P1-SPEC-001.md 仅限修正两处 out_of_scope 锚点，不改其它任何 SPEC-001 descriptor 语义"
anchor_corrections:
  - 'out_of_scope: "生产级 Controlled Exploration（§4.3.3 L468）" → "生产级 Controlled Exploration（§13 L2697）"'
  - 'out_of_scope: "动态 Tool Composition（§13 L2697）" → "动态 Tool Composition（§2.3 L103、§6.5 L1138）"'
  - "另外两条（真实业务系统写操作 §13 L2697-L2698、复杂 DAG/长事务 §4.3.3 L468）保持不变"
  - "执行前必须重新对 docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md 核验 L2697=生产级 CE 默认关闭、L103=Dynamic Tool Composition、L1138=动态 Tool Composition Phase 3+、L468=复杂 DAG/长事务，核不上则停并报告"
touched_paths:
  - "docs/phase1/tasks/P1-SPEC-001-APPROVE-001.md"
  - "docs/phase1/tasks/P1-SPEC-001.md"
  - "docs/phase1/PHASE1_SPEC.md"
  - "docs/phase1/TASK_INDEX.md"
  - "docs/phase1/task_logs/P1-SPEC-001-APPROVE-001_*.yaml"
  - "docs/phase1/task_logs/INDEX.md"
  - "docs/phase1/task_logs/P1-SPEC-001_20260715_233504_passed.yaml"
forbidden_paths:
  - "app/**"
  - "tests/**"
  - "scripts/**"
  - "web/**"
  - ".github/**"
  - "docs/phase0/**"
  - "docs/blueprint/**"
  - "docs/phase1/PHASE1_PLAN.md"
  - "docs/phase1/ROLE_POLICY.md"
  - "docs/phase1/TASK_PROMPT_TEMPLATE.md"
  - "docs/dev/task_record_schema.yaml"
acceptance_criteria:
  - "APPROVE-AC-01: docs/phase1/tasks/P1-SPEC-001.md 两处错锚点修正为经蓝图核对的正确值（CE→§13 L2697；DTC→§2.3 L103、§6.5 L1138），其余 descriptor 语义字节不变"
  - "APPROVE-AC-02: docs/phase1/PHASE1_SPEC.md status 由 draft 改为 approved，批准门注释更新为『三方一致、B2 可解锁』；spec 正文（5in/4out、20 小节、B2-B5 四章）字节级不变，仅 status/门注释变化"
  - "APPROVE-AC-03: 三方一致——PHASE1_SPEC status=approved、SPEC-001 Task Record human_result_acceptance=accepted 且标注 spec 已 approved、INDEX 的 SPEC-001 行=approved + Gate2 accepted + B2 unlocked"
  - "APPROVE-AC-04: TASK_INDEX 标注 B2 硬门（P1-GATE-001 passed + PHASE1_SPEC approved/landed）已满足；不生成 B2 per-task prompt、不启动 B2"
  - "APPROVE-AC-05: 本任务 descriptor 与 INDEX 行使任务可复现、不扩域"
  - "APPROVE-AC-06: staged candidate 无 forbidden path、无 R3 变更；对 SPEC-001 descriptor 的编辑严格限于两处锚点"
failure_examples:
  - "改动 spec 正文（缩/扩 5in/4out、增删小节、改 B2-B5 内容）"
  - "锚点改成未经蓝图逐行核对的值，或顺手改动另外两条正确锚点"
  - "提前生成 B2 per-task prompt 或启动 B2"
  - "三方未全部一致就把 spec 标为 approved"
  - "触碰实现代码/测试/模板/policy/schema/blueprint"
step_verification_points:
  - "Baseline: origin/phase0/main 干净、SPEC-001 已合并（10c5993d）、Gate 2 已 out-of-repo 验收（run_state complete@rev16）"
  - "Plan: 编辑仓库内容前先绑定 Opus Plan Review；锚点已对蓝图重核"
  - "Apply: 只改声明的七个 docs/log 面；spec 正文 diff 仅 status/门注释行"
  - "Validate: spec 正文字节不变证明、锚点 before/after、三方一致 grep、精确 staged 路径"
  - "Review/integrate: 最终 Opus meta、精确 commit tree、非强制集成、post-merge CI"
validation_commands:
  - "uv run python scripts/check_dependencies.py"
  - "uv run pytest tests/architecture/ -q -p no:cacheprovider"
  - "uv run pytest"
  - "git diff --cached --name-only"
  - "git diff --cached --stat"
  - "git diff --cached --check"
  - "git ls-files --others --exclude-standard"
evidence_requirements:
  - "spec 正文字节不变证明（仅 status/门注释行变化）"
  - "两处锚点 before/after 及对应蓝图行重核结果"
  - "三方一致（spec status / SPEC-001 Task Record / INDEX）扫描"
  - "Plan 与 final claude_*_meta.json 绑定真实 artifact"
  - "精确 staged 路径、candidate tree/diff、commit parent/tree、push/PR/merge、post-merge CI"
stop_conditions:
  - "需要任何 forbidden path、删除、schema/模板/policy 变更、secret、DB/数据变更或危险 Git 操作"
  - "锚点无法对蓝图重核确认"
  - "spec 正文语义无法在只改 status/门注释的前提下保持不变"
  - "Plan/final Review、验证、范围、分支保护或 CI 证据缺失或陈旧"
local_commit_policy: "after_review_pass"
integration_policy:
  mode: "git"
  remote_strategy: "task_branch_pr_merge"
  task_branch_ci: "if_triggered"
  post_merge_ci: "required"
auto_next_policy: "blocked"
depends_on:
  - "P1-SPEC-001 integrated at 10c5993d with merge-SHA CI passing; Gate 2 accepted out-of-repo (controller complete@rev16)"
  - "LOCAL-WF-V4-PARTA-20260716-104543 canonical→mirror sync complete (控制面当前、doctor ok)"
branch: "phase1/P1-SPEC-001-APPROVE-001"
references:
  - "AGENTS.md"
  - "CLAUDE.md"
  - "docs/phase1/ROLE_POLICY.md"
  - "docs/phase1/TASK_PROMPT_TEMPLATE.md"
  - "docs/dev/task_record_schema.yaml"
  - "docs/phase1/tasks/P1-SPEC-001.md"
  - "docs/phase1/PHASE1_SPEC.md"
  - "docs/phase1/TASK_INDEX.md"
  - "docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md"
```

本任务只做批准与解锁；不启动 B2。B2 的 per-task prompt 必须在本任务合并并经人工结果验收之后另行生成。
