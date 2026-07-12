# TASK_PROMPT_TEMPLATE — Phase 1 v2.1.0

本文件定义 per-task prompt（`docs/phase1/tasks/<task_id>.md`）的**任务契约**形状。
执行编排（Plan/Review/gate 的操作步骤、packet 格式、汇报模板）由 `codex-claude` workflow skill 负责，不写在本文件。
Role / review / risk 政策唯一来源：`docs/phase1/ROLE_POLICY.md`。

## Per-task prompt 必备结构

```yaml
task_id: ""
task_type: "spike | preparation | infrastructure | interface_contract | implementation | test | documentation | review"
goal: ""                      # 一句话说清结果
non_goals: []                 # 本任务明确不做什么
method_profile:
  execution_role: "execution | review | mixed | documentation"
  execution_owner: "codex"
  review_owner: "claude_code"
  review_mode: "independent_review"    # 通用评审底线，降级须写明理由并经人工批准
  risk_tier: "low | medium | high"     # 判定规则见 ROLE_POLICY
  method: "PDR | BDD | TDD | mixed | not_applicable"
  model_note: ""
  reason_for_owner_choice: ""          # 使用默认分工时一行即可
controller_risk_tier: "R0 | R1 | R2 | R3"
risk_classification_reason: ""         # 按真实变更面分类；风险只能自动升级
automation_class: "auto | human_pre_apply | human_pre_action"
authorization_mode: "standard | bounded_goal_preapproval"
required_stops: []                     # 例：human_pre_apply / human_result_acceptance / human_pre_action
r3_authorization: []                    # 仅列人类明确授权的具体 R3 动作；默认空
touched_paths: []
forbidden_paths: []
acceptance_criteria: []       # 每条必须机器可验证，或声明明确的证据形态
failure_examples: []          # implementation/test 必填；spike/preparation/infrastructure 可用 blocking examples 替代
step_verification_points: []  # 可选；high 任务建议提供
validation_commands: []       # 本任务要实际运行的验证命令
evidence_requirements: []     # 每条 acceptance criterion 对应的证据形态
stop_conditions: []           # 任务级停手条件（全局停手条件见 AGENTS.md）
local_commit_policy: "after_review_pass | not_applicable"      # 无独立 local-commit 人工 Gate；仍受 Review/validation/freshness 约束
integration_policy:
  mode: "git | filesystem_only"
  remote_strategy: "task_branch_pr_merge | direct_none | not_applicable"
  task_branch_ci: "required | if_triggered | not_applicable"
  post_merge_ci: "required | if_triggered | not_applicable"
auto_next_policy: "allowed | blocked"
depends_on: []
branch: "phase1/<task_id>"
references: []                # 只引用不复述；引用 spec 用稳定小节 ID + 行号锚点
```

- 缺 `method_profile`、controller risk/automation、scope、AC、evidence 或 stop 字段时，执行方必须停止并输出 `task_prompt_incomplete`。
- Task prompt 约束**结果契约**，不约束实现步骤；step-by-step SOP 只允许在 `risk_tier: high` 且步骤本身是验收对象时出现。
- 通用规则一律引用（ROLE_POLICY / AGENTS.md / CLAUDE.md / schema），不复述。增量式范例：`docs/phase1/tasks/P1-SPEC-001.md`。
- `method_profile.risk_tier` 控制 Review/Task Record 详略；`controller_risk_tier` 决定技术风险审查；`automation_class`/`required_stops` 决定人工停点。三者不得混用。
- R2 默认 `human_pre_apply`；只有当前机器可读 task contract 明确保留 R2 Review/验证并移除人工停点时才可写 `automation_class: auto`。R3 必须列出具体授权动作，不能用宽泛类别代替。
- Gate 2 只表示 post-integration result acceptance；不得把它写成 commit/push/merge/CI 授权。普通非强制 Git/CI 是否自动由当前 task contract、repo policy、Review、validation、freshness 和 required checks 共同决定。

## Engineering Method Selection（速查）

| 任务类型 | 推荐 method | 证据要求 |
|---|---|---|
| ADR / Spike / 架构决策 / 依赖选型 / 技术路线选择 / 设计评审 | PDR | plan, alternatives, risks, blocking conditions, recommendation, verification |
| 业务流程 / Golden Task / API behavior / 用户行为闭环 / 权限审批流程 | BDD | Given/When/Then 或 input-action-expected-output；不要求 .feature 文件 |
| 生产代码 / Runtime / Gateway / Policy / Identity / Trace / parser / validator / schema / adapter / bugfix / regression-sensitive | TDD | minimal failing assertion first, then implementation, then regression |
| 一个任务同时含代码实现和行为闭环 | mixed | 必须说明哪部分 TDD、哪部分 BDD/PDR；不允许用 mixed 逃避证据要求 |
| 文档同步 / cleanup / research-only / 索引同步 | not_applicable | 必须写明 reason / scope / evidence；不能留空 |

## 敏感词场景说明

以下场景允许出现 token/password/cookie/sessionid/access_token/refresh_token/api_key/private_key 等**字样**：

- Python 类型定义、接口字段名、配置键名。
- sanitizer 测试的输入样例，用于验证拦截。
- 代码注释、ADR 和安全规则文档。

以下场景禁止出现敏感**值**：

- Trace 持久化输出。
- ResponseEnvelope JSON。
- Mock Adapter 正向返回。
- task log / self-check log 主体。
- fixture 的 expected persisted output。

## 证据规则（最小集，详见 `docs/dev/task_record_schema.yaml`）

1. 所有证据必须真实：验证命令必须实际运行，记录真实输出片段与退出码；不得伪造通过结果。
2. `changed_files` 必须与 `git diff --cached --name-only` 完全一致（含顺序），在最终 stage 之后、commit 之前更新。
3. Task Record 必须通过 `yaml.safe_load` 且无重复 key。
4. `not_applicable` 六件套字段只对 implementation / test 任务强制；documentation 任务写明 reason 一行即可，但不得用 `not_applicable` 掩盖失败。
5. `git_commit_sha` 使用 deferred convention（见 schema）。
6. Task Record 详略按 `method_profile.risk_tier` 分级：low = TASK_INDEX 指针或 task-required YAML；medium = slim YAML；high = full YAML（字段集见 schema）。
7. full record 必须分开记录 controller risk/automation/required stops、Plan 与 final Review、Task/PR checks、merge SHA、post-merge CI、R3 authorization、auto-next 和 filesystem companion（如适用）。未触发检查写 `not_triggered`，不得写绿色。

## Golden Task 阈值（结果契约）

- 正向任务通过率 >= 80%；负向 / 边界 / 安全拒绝路径 100% 通过（含 GT-012 多 active 绑定未指定 scope 场景）。
- `P1-GATE-001` 之后的 implementation 任务必须运行 `uv run python scripts/run_golden_tasks.py --gate`。

## B2+ per-task prompt 生成规则

1. B2-B5 每个任务启动前必须先生成 `docs/phase1/tasks/<task_id>.md`，结构见上。
2. 每个 task prompt 必须声明 touched_paths / forbidden_paths、acceptance criteria、evidence requirements、method_profile。
3. 相关任务必须引用当前 Phase 1 技术基线（`docs/phase0/PHASE1_TECHNICAL_BASELINE.md`）。
4. prompt 缺陷在 Plan review 时当场以 task-prompt patch 修复并附在同一 task_id 内；不再开设独立的 prompt-hardening 任务。
5. 不从模糊的聊天指令开始实现；必须有正式 task prompt。
