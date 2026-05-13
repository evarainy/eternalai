# CODEX_SINGLE_TASK_PROMPT_TEMPLATE — Phase 0 v2.0.0

将下面模板复制给 Codex / Claude Code。每次只替换一个 `task_id` 和对应任务内容。不要一次性执行整个 Batch。

````markdown
你现在只允许执行一个任务：

- task_id: <填写一个 task_id>
- source_spec: docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md
- task_index: docs/phase0/TASK_INDEX.md
- boundary_checklist: docs/phase0/BOUNDARY_CHECKLIST.md
- context_strategy: docs/phase0/CONTEXT_LOADING_STRATEGY.md
- current_task_prompt: docs/phase0/tasks/<task_id>.md

## 全局硬约束

- Codex / generic coding agent: read `AGENTS.md`
- Claude Code / MiMo: read `CLAUDE.md`

按 `docs/phase0/CONTEXT_LOADING_STRATEGY.md` 只加载当前工具需要的文件；不要把整份 Phase 0 spec 粘贴进本任务上下文。本模板仍是每次单任务执行的直接约束。

### Context hygiene

按 `docs/phase0/CODING_STYLE_BASELINE.md` 仅加载当前任务相关的 section；参见其 "Section applicability guide"。
按 `docs/phase0/REPOSITORY_CONTEXT_MAP.md` Section 5 的 "Progressive loading guide by task type" 决定加载内容。
不要把整份 BOUNDARY_CHECKLIST.md / CODING_STYLE_BASELINE.md / ROLE_AND_METHOD_GUARDRAILS.md / frozen blueprint 粘贴进 session。
不要默认加载全部 ADR / 全部 Task Log；只加载当前任务需要的。
每 3 个 task 执行一次 `BOUNDARY_CHECKLIST.md`。
一个 session 优先只执行一个 task_id。
不要把 cleanup 和 implementation 混在一个任务里，除非 per-task prompt 明确允许。

## 角色与工程方法

Batch 2+ per-task prompt 的 Task YAML 必须包含 `method_profile` 字段：

```yaml
method_profile:
  execution_role: "execution | review | mixed | documentation"
  execution_owner: "claude_code_mimo | codex | human | mixed"
  review_owner: "codex | claude_code_mimo | human | separate_session | none"
  review_mode: "none | codex_review | human_optional"
  method: "PDR | BDD | TDD | mixed | not_applicable"
  reason_for_owner_choice: "<why this owner/reviewer/method is appropriate>"
```

### Engineering Method Selection（速查）

| 任务类型 | 推荐 method | 证据要求 |
|---|---|---|
| ADR / Spike / 架构决策 / 依赖选型 / 技术路线选择 / 设计评审 | PDR | plan, alternatives, risks, blocking conditions, recommendation, verification |
| 业务流程 / Golden Task / API behavior / 用户行为闭环 / 权限审批流程 | BDD | Given/When/Then 或 input-action-expected-output；不要求 .feature 文件 |
| 生产代码 / Runtime / Gateway / Policy / Identity / Trace / parser / validator / schema / adapter / bugfix / regression-sensitive | TDD | minimal failing assertion first, then implementation, then regression |
| 一个任务同时含代码实现和行为闭环 | mixed | 必须说明哪部分 TDD、哪部分 BDD/PDR；不允许用 mixed 逃避证据要求 |
| 文档同步 / cleanup / research-only / 索引同步 | not_applicable | 必须写明 reason / scope / evidence；不能留空 |

### execution_owner 选择

- **默认**: Claude Code / MiMo 执行，Codex 独立审查。适用于 documentation、cleanup、repo-navigation、prompt/template maintenance、environment-heavy spike。
- **Codex 执行覆盖**: 复杂/核心 implementation、架构敏感 production code、API contract、parser/validator/schema/runtime logic、test-heavy/regression-sensitive、安全敏感逻辑。
- **审查独立性**: executor 不能作为唯一 approver。Claude Code 执行 → Codex 默认审查。Codex 执行 → Claude Code / human / separate review session 审查。Self-review 可作为 first pass，但不替代独立审查。

### review_mode 规则

- repository-changing Phase 0 / Phase 1 tasks default to `codex_review`（包括 implementation / test / infrastructure / documentation / template / cleanup / baseline summary / navigation sync）。
- `none` 或 `human_optional` 仅用于：纯讨论、纯草稿、不修改文件、不产生 staged diff、不生成 Task Record。
- Review session 必须只读，不修改代码。

详细 guardrails 见 `docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md` "Engineering Method Selection" 章节。

## v2.0.0 执行规则

- `P0-PREP-*` 是 execution-pack-only preparation tasks，不属于业务实现任务。
- `not_applicable` 必须写明 reason、scope、blocked_by_task_id、activation_task_id、expiry_condition 和 evidence；不得掩盖失败。
- Golden Task 正向通过率 >= 80%；负向/边界/安全拒绝路径 100% 通过。
- Package confirmation 不绑定强制人工 diff review。Human review 是 optional。
- Hooks / subagent 是 optional enhancement，不替代 Task Record、CI/self-check、ADR。
- Spike ADR 必须保留足够证据。Superpowers 只能作为 advisory aid。

## 敏感词场景说明

以下场景允许出现 token/password/cookie/sessionid/access_token/refresh_token/api_key/private_key 等字样：

- Python 类型定义、接口字段名、配置键名。
- sanitizer 测试的输入样例，用于验证拦截。
- 代码注释、ADR 和安全规则文档。

以下场景禁止出现敏感值：

- Trace 持久化输出。
- ResponseEnvelope JSON。
- Mock Adapter 正向返回。
- task log / self-check log 主体。
- fixture 的 expected persisted output。

## 执行中工作流规则

- **No commit, no push, no merge** 除非 per-task prompt 在 review 完成后明确允许。
- 仅 stage for review。
- 不得使用 `--no-verify` 或绕过 git hooks。
- Task Record 输出且人工确认前，不得创建 commit。
- 需要 commit 时，使用 `phase0(<task_id>): <one-line summary>` 格式。
- Task Record 中 `git_commit_sha` 使用 deferred convention（见证据规则）。
- `changed_files` 必须在最终 stage 之后、commit 之前更新，确保与 `git diff --cached --name-only` 完全匹配。
- 不得在 stage 前生成 Task Record 导致 stale evidence。
- blocked / failed Task Record 后续重跑通过时，不得遗留 stale untracked blocked/failed YAML。
- untracked 文件必须在 review 前 stage 或清理。

## 行动前必须先输出 Plan，等待人工确认后再执行

请先不要修改文件。先输出以下内容：

### 1. Task Scope
- 当前 task_id：
- 本任务目标：
- 本任务类型：spike / preparation / infrastructure / interface_contract / implementation / test / documentation / review
- 本任务不会做什么：

### 2. Acceptance Criteria
逐条复制并解释当前任务的 `acceptance_criteria`。不得简化成"跑通即可"。

### 3. Failure Examples / Blocking Examples
- implementation / test 任务：逐条引用 `failure_examples`；缺失时停止输出 `task_definition_incomplete`。
- interface_contract 任务：逐条引用 `contract_violation_examples` 或声明继承 Phase 0 spec 第 8.4.1 节全局样例。
- Spike / preparation / infrastructure 任务：可替换为 blocking examples（GPU 不可用、依赖不可用等）；不得因缺少 implementation 失败样例而停止，但必须列出阻塞样例和验证证据。
- 不得自行编造生产逻辑。

### 4. Step-by-step Plan
每一步必须包含：
- step name
- files_to_touch
- verification command / evidence
- expected result

### 5. Step Verification Points
逐条引用任务定义中的 `step_verification_points` 或 ADR 验证点。

### 6. Files to Touch
只列出本任务允许修改的 `touched_paths`。

### 7. Forbidden Paths
逐条列出本任务的 `forbidden_paths`，并承诺不修改。

### 8. Test Commands
列出每一步和最终要运行的测试命令。若某命令因环境缺失不能运行，必须说明原因并记录为阻塞，不得伪造通过。

### 9. Stop Conditions
出现以下情况必须停止并报告：修改 forbidden_paths、新增未授权依赖、prompt 不完整 (`task_prompt_incomplete`)、缺少关键失败样例、依赖未完成、需访问真实生产系统、测试失败且无法在 touched_paths 内修复。

等待我确认 Plan 后再开始执行。

## 失败后处理

出现阻塞或测试失败且无法在当前 `touched_paths` 内修复时：

1. 停止修改文件，不要提交失败状态。
2. 保留现场前先询问人工；如需保留，使用 `git stash push -u -m "FAILED <task_id> <timestamp>"`。
3. 生成 Task Failure Record：`docs/phase0/task_logs/<task_id>_<YYYYMMDD_HHMMSS>_failed.yaml`。
4. Task Failure Record 必须列出失败的 acceptance criteria、失败命令、日志片段、已修改文件、建议处理方式。
5. 等待人工决定：patch 补修、重开 task branch、还是新增 ADR / task patch。
6. 不得跳过失败任务继续后续任务；不得用 `not_applicable` 掩盖失败；每个 `not_applicable` 必须填写完整解释字段。

## 执行中要求

每完成一个 step，输出：

- step name
- changed files
- command run
- result
- evidence path / log path
- next step

不得跳步，不得执行下一个 task_id。

## 证据规则

Task Record 中所有证据必须真实，不得编造：

1. **命令输出**: 测试命令、检查命令必须实际运行并记录真实输出片段。不得伪造通过结果。
2. **changed_files**: 必须与 `git diff --cached --name-only` 完全一致，包括顺序。
3. **git diff --cached --stat**: 必须与实际 staged diff 一致。
4. **YAML 合法性**: Task Record 必须通过 `yaml.safe_load` 验证。不得出现重复 key（必须使用 UniqueKeyLoader 或等价检查）。
5. **not_applicable 完整性**: 每个 `not_applicable` 必须填写 not_applicable_reason、not_applicable_scope、blocked_by_task_id、activation_task_id、expiry_condition、evidence；不得用空字符串或占位符。
6. **package / archive**: 未创建 package 时 `package_confirmation_status` 写 `not_applicable`；不得写 "fresh package created"。详见 `docs/dev/task_record_schema.yaml`。
7. **git_commit_sha deferred**: commit 前留空或写 deferred 说明：
   ```yaml
   git_commit_sha: "recorded_by_git_history_after_final_commit"
   git_commit_sha_note: "The final commit SHA is not embedded in this Task Record because committing this file changes HEAD. The authoritative commit SHA is the Git commit containing this Task Record and the remote branch history after push."
   ```

详细 schema 见 `docs/dev/task_record_schema.yaml`。

## 完成后输出

必须输出并保存统一 Task Record，格式见：

```text
docs/dev/task_record_schema.yaml
```

保存路径：

```text
docs/phase0/task_logs/<task_id>_<YYYYMMDD_HHMMSS>_<passed|failed>.yaml
```

Task Record 必须包含外部证据：Git commit SHA、CI run id、命令输出片段或日志路径。不要只写 `passed`。

Task Record 中 `changed_files` 必须在最终 stage 之后、commit 之前更新，确保与 `git diff --cached --name-only` 完全匹配。不得在 stage 前生成 Task Record 导致 stale evidence。

完成后可选执行 Human Review Checklist（非阻断条件）：

```text
docs/dev/human_review_checklist.md
```

若本任务需要创建 commit，commit 格式建议为：

```text
phase0(<task_id>): <one-line summary>
```

## 审查与角色边界

### 角色分工
- Roles are assigned per task, not hard-coded by tool.
- Claude Code / MiMo: 默认执行角色，适用于 documentation、cleanup、repo-navigation、prompt/template maintenance、environment-heavy spike。
- Codex: 默认独立审查角色（当 `review_mode: codex_review` 时）。
- Codex 可作为执行角色覆盖，适用于 complex/core implementation、architecture-sensitive code、API contract、parser/validator/schema/runtime、test-heavy、security-sensitive。
- 不管谁执行，executor 不能作为唯一 approver。
- repository-changing tasks 必须有独立审查，除非 task prompt 明确说明豁免理由。
- Review session 必须只读，不修改代码。
- Self-review 可作为 first pass，但不替代独立审查。

### Codex review 流程（当 review_mode: codex_review 时）
1. 执行者（Claude Code / MiMo 或 Codex）完成代码实现并生成 Task Record。
2. 审查者审查 staged diff、Task Record 和 acceptance criteria。
3. 审查使用 `docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md` 中 Review Guardrails。
4. 审查结果为 PASS 或 FAIL + blocking issues；审查不自动修改代码。

### Superpowers
- Superpowers 只能作为 advisory workflow aid。
- 不得覆盖 task prompt / forbidden paths / no commit / no push / Codex review requirements。
````

## 使用方式

0. 优先使用 `docs/phase0/tasks/<task_id>.md` 中的 per-task prompt，避免把整份 spec 反复塞入上下文。
1. 从 `FIRST_BATCH_TASKS.md` 或 `TASK_INDEX.md` 选择一个 task_id。
2. 把该 task 的完整 YAML 复制到本模板之后。
3. 要求 Codex 先输出 Plan。
4. 人工确认 Plan 后再允许执行。
5. 每完成 3 个 task，执行 `BOUNDARY_CHECKLIST.md`。

6. 任务完成后可选执行 Human Review Checklist（非阻断）。

## v2.0.0 Golden Task and Review Clarification

- Golden Task 正向任务通过率必须 >= 80%；负向路径 / 边界路径 / 安全拒绝路径必须 100% 通过；GT-012 多 active 绑定但未指定 scope 必须通过。
- Human Review Checklist 是 optional / recommended；任务完成不因缺少人工 diff review 阻断，但必须有 self-check、Task Record 和可引用证据。


## Batch 2+ prompt gate

Batch 0 / Batch 1 的 per-task prompt 已内置。Batch 2+ 启动前必须生成对应 `docs/phase0/tasks/<task_id>.md`。

### Batch 2+ per-task prompt 生成规则

1. Batch 2+ 启动前必须生成对应 `docs/phase0/tasks/<task_id>.md`。
2. 每个 per-task prompt 必须包含 method_profile 字段（格式见上方 "角色与工程方法" 章节）。
3. method 选择参考本文档 "Engineering Method Selection" 速查表。
4. review_mode 默认值：repository-changing tasks → `codex_review`；纯讨论/草稿/不修改文件 → `none` 或 `human_optional`。
5. 每个 task 必须声明 allowed_paths / forbidden_paths、evidence requirements、review_mode / execution_owner / review_owner。
6. 每个 task 必须引用当前 Phase 1 技术基线（如果相关）。
7. 缺失 per-task prompt 或 method_profile 时，停止并输出 `task_prompt_incomplete`。
8. 不要从模糊的聊天指令开始实现；必须有正式 task prompt。
```
