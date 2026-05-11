# CODEX_SINGLE_TASK_PROMPT_TEMPLATE — Phase 0 v1.0.11

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

## 角色与工程方法

当前 task prompt 应声明：
- role: execution / review / mixed / documentation
- method: PDR / BDD / TDD / mixed / not_applicable

执行或审查时按角色应用 `docs/phase0/ROLE_AND_METHOD_GUARDRAILS.md` 中对应 guardrails。

1. 不修改冻结主蓝图。
2. 不扩写主蓝图，不提前写 Phase 1 / Phase 2-5 spec。
3. 不接真实生产 OA / U8 / 海康 iVMS。
4. 不保存真实 password、token、cookie、sessionid、access_token、refresh_token、api_key、private_key。
5. 不新增未在内网 PyPI / npm 镜像或离线缓存中确认可用的依赖。
6. 不修改 forbidden_paths。若必须修改，停止并提出 task patch。
7. 每次只执行当前 task_id；不得顺手实现后续任务。
8. Spike 代码不得进入 app/ 正式模块。
9. Runtime 不得直接 import Adapter / execution_fabric。
10. 完成后必须输出统一 Task Record；固定 package confirmation 保留，但不绑定强制人工 diff review。


## v1.0.11 额外执行规则

- `P0-PREP-*` 是 execution-pack-only preparation tasks，不属于业务实现任务。
- FIRST_BATCH 只允许执行 `P0-PREP-*` 和 `P0-SPIKE-*`；不得提前创建工程骨架、Gateway、Runtime、Adapter 或 Golden Task runner。
- `not_applicable` 必须写明 reason、scope、blocked_by_task_id、activation_task_id、expiry_condition 和 evidence；不得掩盖失败。
- Golden Task 正向任务通过率 >= 80%；负向路径、边界路径、安全拒绝路径必须 100% 通过。
- Package confirmation 不再绑定强制人工 diff review。
- Human review / human diff review 是 optional，不是本期阻断条件。
- Hooks / subagent 是 optional enhancement，不替代 Task Record、CI/self-check 命令、ADR 或测试日志。
- Spike ADR 必须保留足够证据；模型类 Spike 需记录 vLLM/model/schema/tool choice/sample counts/retry/fallback/known limitations，业务系统 Spike 需记录版本、认证、权限、API、审计、沙箱、风险和 recommendation。

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

## 行动前必须先输出 Plan，等待人工确认后再执行

请先不要修改文件。先输出以下内容：

### 1. Task Scope
- 当前 task_id：
- 本任务目标：
- 本任务类型：spike / preparation / infrastructure / interface_contract / implementation / test / documentation / review
- 本任务不会做什么：

### 2. Acceptance Criteria
逐条复制并解释当前任务的 `acceptance_criteria`。不得简化成“跑通即可”。

### 3. Failure Examples / Blocking Examples
- 对 implementation / test 任务：必须逐条引用任务定义中的 `failure_examples`。
- 对 interface_contract 任务：必须逐条引用 `contract_violation_examples`，或声明继承 Phase 0 spec 第 8.4.1 节全局契约违规样例；不要求 implementation 级失败样例。
- 对 Spike / preparation 任务：`failure_examples` 可替换为 blocking examples，例如 GPU 不可用、依赖不可用、样本不足、版本不符合、无法访问目标系统文档等；`step_verification_points` 可替换为 ADR 验收项的通过/失败标准。
- 对 infrastructure 任务：不要求 implementation 级 `failure_examples`；必须引用 `blocking_examples` / `infrastructure_verification_points`，或在 Plan 中把每条 `acceptance_criteria` 转换为命令、文件检查或 evidence 检查。不得因为 Infra 任务缺少 implementation 失败样例而停止，除非验收标准本身不可测试或会要求提前实现后续模块。
- 对 Spike / preparation / infrastructure 任务，不得因为缺少 implementation 级失败样例而停止；但必须列出阻塞样例、验证证据和当前阶段可执行的检查。
- 对 implementation / test 任务，若任务定义缺少必要失败样例或步骤验证点，停止并输出：`task_definition_incomplete`。对 interface_contract 任务，若缺少 `contract_violation_examples` 且无法继承全局契约样例，也停止并输出 `task_definition_incomplete`。不得自行编造生产逻辑。

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
出现以下情况必须停止：
- 需要修改 forbidden_paths；
- 需要新增未授权依赖；
- 当前 per-task prompt 缺少必要任务内容，输出 `task_prompt_incomplete`；
- 任务定义缺少关键失败样例或步骤验证点；
- 发现当前任务依赖未完成；
- 需要访问真实生产系统；
- 测试失败且无法在当前 touched_paths 内修复。

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

完成后可选执行 Human Review Checklist（非阻断条件）：

```text
docs/dev/human_review_checklist.md
```

若本任务需要创建 commit，commit 格式建议为：

```text
phase0(<task_id>): <one-line summary>
```

Task Record 最后必须包含：

```text
confirmation that a fresh package was created from the current repository state
```
````

## 使用方式

0. 优先使用 `docs/phase0/tasks/<task_id>.md` 中的 per-task prompt，避免把整份 spec 反复塞入上下文。
1. 从 `FIRST_BATCH_TASKS.md` 或 `TASK_INDEX.md` 选择一个 task_id。
2. 把该 task 的完整 YAML 复制到本模板之后。
3. 要求 Codex 先输出 Plan。
4. 人工确认 Plan 后再允许执行。
5. 每完成 3 个 task，执行 `BOUNDARY_CHECKLIST.md`。

6. 任务完成后，可按 `docs/dev/human_review_checklist.md` 进行可选人工复核；它不是 v1.0.11 阻断条件。


## v1.0.11 Golden Task and Review Clarification

- Golden Task 正向任务通过率必须 >= 80%；负向路径 / 边界路径 / 安全拒绝路径必须 100% 通过；GT-012 多 active 绑定但未指定 scope 必须通过。
- Human Review Checklist 是 optional / recommended；任务完成不因缺少人工 diff review 阻断，但必须有 self-check、Task Record 和可引用证据。


## Batch 2-7 prompt gate

当前执行包仅内置 Batch 0 / Batch 1 的 per-task prompt。Batch 2-7 启动前必须生成对应 `docs/phase0/tasks/<task_id>.md`；缺失时停止并输出 `task_prompt_incomplete`。
