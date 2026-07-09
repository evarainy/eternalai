# P1-WORKFLOW-002 — 治理文档收敛与流程分级落地

```yaml
task_id: "P1-WORKFLOW-002"
task_type: "documentation"
goal: "把 role/review/risk 政策收敛为单一事实源，按 risk_tier 分级仪式，清除废弃枚举与死引用，使仓库文档只承载结果契约、过程编排全部归 codex-claude skill"
non_goals:
  - "不修改任何 app/、tests/、web/、scripts/ 代码"
  - "不修改 codex-claude skill 本身（skill 移植走 _scratch/V8_PORT_RUNBOOK_20260708.md，不在本任务内）"
  - "不批量回填历史 task prompts / task logs 的旧枚举（grandfather 原则）"
  - "不做 PHASE1_PLAN.md 修订史归档（记入 backlog，批次间隙处理）"
method_profile:
  execution_role: "documentation"
  execution_owner: "codex"
  review_owner: "claude_code"
  review_mode: "independent_review"
  risk_tier: "high"          # 触及 docs/dev/task_record_schema.yaml（schema 面 = high）
  method: "not_applicable"
  not_applicable_reason: "纯治理文档修改，无生产代码；证据形态为 grep/YAML/行数断言"
  model_note: "executor: Codex GPT-5.5 high; reviewer: Claude claude-opus-4-8 只读"
  reason_for_owner_choice: "Phase 1 默认分工"
touched_paths:
  - "docs/phase1/ROLE_POLICY.md"
  - "docs/phase1/TASK_PROMPT_TEMPLATE.md"
  - "docs/dev/task_record_schema.yaml"
  - "docs/phase1/tasks/P1-PARAM-001.md"
  - "docs/phase1/TASK_INDEX.md"
  - "CLAUDE.md"
  - "AGENTS.md"
forbidden_paths:
  - "app/**"
  - "tests/**"
  - "web/**"
  - "scripts/**"
  - "docs/blueprint/**"
  - "docs/phase0/**"
  - "docs/phase1/PHASE1_SPEC.md"
  - "docs/phase1/tasks/P1-SPEC-001.md"
  - ".github/**"
depends_on: []
branch: "phase1/P1-WORKFLOW-002"
```

## 决策背景（本任务书编码了 2026-07-08 的人工决策，Gate 1 批准本任务 Plan 即视为决策生效）

- D1 唯一编排：全局 `codex-claude` skill（Codex-first v8 Windows 移植版）是唯一 workflow SOP。仓库文档只写**结果契约**（关闭任务时什么必须为真），不写**过程编排**（谁在哪一步做什么）。项目内 `phase-task` skill 及其 hooks 已于 2026-07-08 人工批准后本地删除。
- D2 通用评审底线：**所有改动仓库的任务，不论 risk_tier，一律要求 `independent_review`**。原因：人工只读方案与报告、不读代码，独立评审是唯一缺陷网。降级必须在 task prompt 显式写明理由并经人工批准。
- D3 模型约束：评审模型 pin `claude-opus-4-8`。**Fable 5 不进入工作流**（2026-07-07 起在订阅内烧 usage credits）。
- D4 本地 commit 放宽：low/medium 任务在 independent review PASS 后允许本地 commit；high 任务 commit 前需人工确认。**push / merge 永远需要人工批准（Gate 2），不放宽**。
- D5 分级仪式：Plan gate、Task Record 详略按 risk_tier 分级（见下方 ROLE_POLICY 替换文本中的表格）。
- D6 锚点核对脚本化：blueprint/spec 行号锚点核对不依赖任何 LLM 流水线（Gemini CLI 已停服），由脚本机械核对；Antigravity CLI 仅作为 high 任务的人工触发第三票，不进流水线。

## 执行步骤（逐文件，含精确替换内容）

### Step 1 — 重写 `docs/phase1/ROLE_POLICY.md`（全文替换）

用下面内容**整体替换**该文件（目标 ≤ 70 行）：

````markdown
# Phase 1 Role Policy

This file is the Phase 1 source of truth for role assignment, review shape, and risk-tier ceremony.

Layering rule: repository docs define **result contracts** (what must be true when a task closes). **Process choreography** (who runs which step, packets, gate mechanics) lives in the `codex-claude` workflow skill and is not duplicated here.

## Roles & canonical agent ids

- `codex` — default executor: implementation, self-review, evidence/packet building, staging.
- `claude_code` — default reviewer: read-only Plan drafting/sanity-check and diff review.
- `human` — approves Plans (Gate 1), approves push/merge (Gate 2), owns red-line decisions.

Legacy migration table — legacy values are valid only when interpreting historical artifacts (do not backfill old prompts or records; new prompts must use canonical ids only):

| Legacy value | Maps to |
|---|---|
| `claude_code_mimo` (owner id) | `claude_code` |
| `codex_review` (review mode) | `independent_review`, or `self_review` by artifact context |
| `self_check` | `self_review` |
| `human_optional` | `human_review` if a review actually happened, else `none` |

## Defaults

- `execution_owner: codex`, `review_owner: claude_code`.
- The executor can never be the sole approver of its own work.
- A Plan may be drafted by either the executor or the reviewer; the non-author side must record a written sanity-check before human approval. Diff review remains the final defense regardless of who drafted the Plan.

## review_mode enum

- `self_review` — executor first pass; never sufficient on its own for a repo-changing task.
- `independent_review` — a reviewer independent from the executor reviews the Plan, diff, or artifact.
- `human_review` — a human performs the review or approval step.
- `none` — only for tasks producing no repo-changing artifact; requires an explicit statement in the task prompt.

**Universal review floor: every repo-changing task requires `independent_review`, regardless of risk tier.** A task prompt may downgrade this only with an explicit written reason plus human approval, and the downgrade must be recorded in the Task Record.

## risk_tier

| Task surface | Default tier |
|---|---|
| CI, gate, thresholds, frozen ids, fixtures, schema, migration | `high` |
| `app/` code, `tests/`, `web/` | `medium` |
| Docs only | `low` |
| Unspecified | `medium` |

Humans may raise a tier. Humans must not lower a tier that these rules classify as `high`.

## Ceremony by tier

| tier | Plan gate | Review | Local commit | Push / merge | Task Record |
|---|---|---|---|---|---|
| `low` | none (the task prompt is the plan) | `independent_review` of the diff | allowed after review PASS | Gate 2 human approval | one line in `TASK_INDEX.md` + pointer to review verdict |
| `medium` | one-screen Plan → human Gate 1 | `independent_review` | allowed after review PASS | Gate 2 human approval | slim YAML (see schema) |
| `high` | full Plan → human Gate 1 (outline-first for large scopes) | `independent_review` (+ optional human-triggered third vote) | only after explicit human ack | Gate 2 human approval | full YAML |
| spike (`experiments/` only) | none | none | n/a (never merged as-is) | n/a | one-page PDR/ADR only if the result is worth keeping |

Slim vs full Task Record field sets are defined in `docs/dev/task_record_schema.yaml`.

## AGENTS.md status

`AGENTS.md` is the compact boot file for generic coding agents. On conflict, the current task prompt plus this file win.
````

### Step 2 — 重写 `docs/phase1/TASK_PROMPT_TEMPLATE.md`（全文替换）

用下面内容**整体替换**该文件（目标 ≤ 140 行）。注意：只保留任务契约；所有执行 SOP（Plan 输出格式、执行中逐步汇报、失败后处理、审查流程步骤、Superpowers 条款）一律不再出现：

````markdown
# TASK_PROMPT_TEMPLATE — Phase 1 v2.0.0

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
touched_paths: []
forbidden_paths: []
acceptance_criteria: []       # 每条必须机器可验证，或声明明确的证据形态
failure_examples: []          # implementation/test 必填；spike/preparation/infrastructure 可用 blocking examples 替代
step_verification_points: []  # 可选；high 任务建议提供
validation_commands: []       # 本任务要实际运行的验证命令
evidence_requirements: []     # 每条 acceptance criterion 对应的证据形态
stop_conditions: []           # 任务级停手条件（全局停手条件见 AGENTS.md）
local_commit_policy: "after_review_pass | human_ack_required"   # 默认按 ROLE_POLICY tier 表
depends_on: []
branch: "phase1/<task_id>"
references: []                # 只引用不复述；引用 spec 用稳定小节 ID + 行号锚点
```

- 缺 `method_profile` 或必备字段时，执行方必须停止并输出 `task_prompt_incomplete`。
- Task prompt 约束**结果契约**，不约束实现步骤；step-by-step SOP 只允许在 `risk_tier: high` 且步骤本身是验收对象时出现。
- 通用规则一律引用（ROLE_POLICY / AGENTS.md / CLAUDE.md / schema），不复述。增量式范例：`docs/phase1/tasks/P1-SPEC-001.md`。

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
6. Task Record 详略按 risk_tier 分级：low = TASK_INDEX 一行；medium = slim YAML；high = full YAML（字段集见 schema）。

## Golden Task 阈值（结果契约）

- 正向任务通过率 >= 80%；负向 / 边界 / 安全拒绝路径 100% 通过（含 GT-012 多 active 绑定未指定 scope 场景）。
- `P1-GATE-001` 之后的 implementation 任务必须运行 `uv run python scripts/run_golden_tasks.py --gate`。

## B2+ per-task prompt 生成规则

1. B2-B5 每个任务启动前必须先生成 `docs/phase1/tasks/<task_id>.md`，结构见上。
2. 每个 task prompt 必须声明 touched_paths / forbidden_paths、acceptance criteria、evidence requirements、method_profile。
3. 相关任务必须引用当前 Phase 1 技术基线（`docs/phase0/PHASE1_TECHNICAL_BASELINE.md`）。
4. prompt 缺陷在 Plan review 时当场以 task-prompt patch 修复并附在同一 task_id 内；不再开设独立的 prompt-hardening 任务。
5. 不从模糊的聊天指令开始实现；必须有正式 task prompt。
````

### Step 3 — 修订 `docs/dev/task_record_schema.yaml`

3.1 头部两行替换：

- 旧：
  ```
  # Unified Phase 0 Task Record schema v1.0.11.
  # Store task records as: docs/phase0/task_logs/<task_id>_<YYYYMMDD_HHMMSS>_<passed|failed|blocked>.yaml
  ```
- 新：
  ```
  # Unified Task Record schema v1.1.0 (Phase 0 + Phase 1).
  # Store task records as: docs/phase<N>/task_logs/<task_id>_<YYYYMMDD_HHMMSS>_<passed|failed|blocked>.yaml
  # Record detail is tiered by risk_tier (see docs/phase1/ROLE_POLICY.md):
  #   low    -> no YAML record; one line in TASK_INDEX.md + pointer to review verdict
  #   medium -> slim record: task_id, task_type, result, summary, acceptance_criteria_result,
  #             changed_files, tests_run, review, git_commit_sha (deferred convention)
  #   high   -> full record: all fields below
  # not_applicable six-field template is mandatory only for implementation/test tasks;
  # documentation tasks may give a one-line reason instead (never to mask a failure).
  ```

3.2 review.mode 枚举替换：

- 旧：`  mode: "none | self_check | human_optional | codex_review"`
- 新：
  ```
  mode: "none | self_review | independent_review | human_review"
  # legacy values (self_check / human_optional / codex_review) are historical-only; see ROLE_POLICY.md
  ```

### Step 4 — 补丁 `docs/phase1/tasks/P1-PARAM-001.md`（仅枚举替换，不改任务内容）

- L14 旧：`  execution_owner: "claude_code_mimo"` → 新：`  execution_owner: "claude_code"`（按 ROLE_POLICY legacy 映射表；**不得映射为 `codex`**——L15 `review_owner: "codex"` 保持不动，若 L14 也改成 codex 就成了执行者自审却标称 independent_review，违反 universal review floor）
- L16 旧：`  review_mode: "codex_review"` → 新：`  review_mode: "independent_review"`
- 若该文件其他处出现 `claude_code_mimo` / `codex_review` / `mimo`，一并替换为对应新值（先 `grep -n` 确认）。
- 豁免依据（与 non_goals 的 grandfather 原则不冲突）：P1-PARAM-001 是尚未执行的 blocked 任务（见 TASK_INDEX B1 表，无对应 task_log），本步属于前向修正待执行 prompt，不是回填历史证据。

### Step 5 — 修订 `CLAUDE.md`（逐行精确替换）

5.1 旧：
```
- LLM baseline = Qwen + vLLM raw JSON. Do NOT introduce instructor / PydanticAI (rejected by ADR, internal-validated; see `docs/phase0/PHASE1_TECHNICAL_BASELINE.md` §3.1).
```
保持不变（此行已准确）。

5.2 旧：
```
- Current mainline order: `P1-ERRATA-001 -> P1-WORKFLOW-001 -> patch P1-SPEC-001 -> execute P1-SPEC-001 -> B2`.
```
新：
```
- Current mainline order: `P1-WORKFLOW-002 -> patch P1-SPEC-001 -> execute P1-SPEC-001 -> B2` (P1-ERRATA-001 / P1-WORKFLOW-001 landed).
```

5.3 旧：
```
- One session turn = one `task_id`. Start with `/phase-task <task_id>`.
- Plan first; wait for human approval before edits.
- No commit, no push, no merge unless a human explicitly approves.
```
新：
```
- One session turn = one `task_id`, run through the global `codex-claude` workflow skill (sole process SOP; repo docs are result contracts only).
- Plan gate per ROLE_POLICY ceremony table: medium/high need a human-approved Plan before edits; low may proceed directly against the task prompt.
- Every repo-changing task requires `independent_review` (universal review floor, see ROLE_POLICY).
- No push, no merge unless a human explicitly approves (Gate 2). Local commit follows the ROLE_POLICY ceremony table (low/medium: after review PASS; high: human ack).
```

5.4 旧：
```
- Fable5 review backlog: `/Users/evarainy/Downloads/fable-review.md` (local reference only, not repo authority)
```
删除整行（macOS 死路径）。

5.5 Scratch 节，旧：
```
- Temp files go in `_scratch/` only. Not in `app/`, `tests/`, `docs/`, or repo root.
```
新：
```
- Workflow-skill runtime scratch lives outside the repo (`$CLAUDE_CODEX_SCRATCH_ROOT`). Manual / non-skill temp files go in `_scratch/` only. Neither goes in `app/`, `tests/`, `docs/`, or repo root.
```

### Step 6 — 修订 `AGENTS.md`（逐行精确替换）

6.1 旧：
```
- **LLM**: Qwen + vLLM raw JSON mode (instructor/PydanticAI both failed)
```
新：
```
- **LLM**: Qwen + vLLM raw JSON mode (baseline; instructor/PydanticAI rejected by ADR — do not introduce; see docs/phase0/PHASE1_TECHNICAL_BASELINE.md §3.1)
```

6.2 头部第 3 行（"This file is intentionally short..."）之后新增一行：
```
Process choreography (plan/review/gate mechanics) lives in the `codex-claude` workflow skill; this file is boot context plus fail-closed floor only.
```

6.3 Git workflow 节，旧：
```
- **No commit, no push, no merge** unless a human explicitly approves.
```
新：
```
- **No push, no merge** unless a human explicitly approves (Gate 2). Local commit only per `docs/phase1/ROLE_POLICY.md` ceremony table (low/medium: after independent review PASS; high: human ack).
```

6.4 Non-negotiable hard rules 节，旧：
```
1. Execute exactly one `task_id` per session turn; stop after Task Record and wait for human confirmation.
```
新：
```
1. Execute exactly one `task_id` per session turn; ceremony (plan gate, record detail, commit policy) follows `docs/phase1/ROLE_POLICY.md`.
```

6.5 旧：
```
3. Output a Plan first. Do not modify files until a human approves the Plan.
```
新：
```
3. Plan gate per ROLE_POLICY: medium/high tasks need a human-approved Plan before file edits; low tasks may proceed directly against the task prompt.
```

6.6 旧：
```
12. Current mainline order: `P1-ERRATA-001 -> P1-WORKFLOW-001 -> patch P1-SPEC-001 -> execute P1-SPEC-001 -> B2`.
```
新：
```
12. Current mainline order: `P1-WORKFLOW-002 -> patch P1-SPEC-001 -> execute P1-SPEC-001 -> B2`.
```

6.7 Scratch/temp 节首行前新增一行：
```
- Workflow-skill runtime scratch lives outside the repo (`$CLAUDE_CODEX_SCRATCH_ROOT`); repo-local `_scratch/` is for manual/non-skill temp files only.
```

### Step 7 — 注册 `docs/phase1/TASK_INDEX.md`

- 在 `## 1. B1 — 启动准备` 的表格末尾追加一行（列格式同表）：`| P1-WORKFLOW-002 | 治理文档收敛与流程分级落地 | none | ROLE_POLICY / TASK_PROMPT_TEMPLATE v2.0.0 / schema v1.1.0 收敛，ceremony 按 risk_tier 分级 |`
- 同文件旧行「关键要求（沿用 Phase 0 v1.0.11 规范）：」改为「关键要求（统一 schema v1.1.0；Phase 0 旧记录按其当时版本解释）：」（与 Step 3 的版本号保持一致）。
- `grep -n "patch P1-SPEC-001" docs/phase1/TASK_INDEX.md`：若命中 mainline order 表述，同步改为含 `P1-WORKFLOW-002` 的新顺序。
- 不重排、不改动其他任务行。

### Step 8 — 全库残留扫描（只报告，不越界修改）

```
grep -rn "phase-task" CLAUDE.md AGENTS.md docs/phase1/ --include="*.md"
grep -rn "claude_code_mimo\|codex_review\|self_check\|human_optional" docs/phase1/ROLE_POLICY.md docs/phase1/TASK_PROMPT_TEMPLATE.md docs/dev/task_record_schema.yaml docs/phase1/tasks/P1-PARAM-001.md
grep -rn "fable-review" CLAUDE.md
```
- 第 1 条在 touched_paths 内的命中须清除；`docs/phase1/task_logs/`、已执行历史任务 prompt 中的命中**保留不动**（历史证据）。已知预期历史命中：`docs/phase1/tasks/P1-SKEL-001.md`（已执行任务，约 L134/L154/L199 引用已删除的 phase-task skill）——保留不动，不触发 stop_conditions 第 3 条。
- 第 2 条允许的唯一命中：ROLE_POLICY.md 中 Legacy migration table 区块（解释句 + 4 行表格，合计 ≤ 9 行）与 schema 中 legacy 注释行（1 行）。

## acceptance_criteria（全部机器可验证）

1. `wc -l docs/phase1/ROLE_POLICY.md` ≤ 70；`wc -l docs/phase1/TASK_PROMPT_TEMPLATE.md` ≤ 140。
2. `grep -c "independent_review" docs/phase1/ROLE_POLICY.md` ≥ 3；文件含 "Universal review floor" 字样。
3. 在 TASK_PROMPT_TEMPLATE.md 中以下字符串 0 命中：`执行中工作流规则`、`失败后处理`、`审查与角色边界`、`Superpowers`、`行动前必须先输出 Plan`。
4. `grep -n "codex_review\|self_check\|human_optional\|claude_code_mimo"` 在 4 个 touched 文档中仅按 Step 8 白名单出现。
5. `grep -c "phase-task" CLAUDE.md` = 0；`grep -c "fable-review" CLAUDE.md` = 0。
6. `grep -c "P1-WORKFLOW-002" CLAUDE.md` ≥ 1、`AGENTS.md` ≥ 1、`docs/phase1/TASK_INDEX.md` ≥ 1。
7. `python -c "import yaml; yaml.safe_load(open('docs/dev/task_record_schema.yaml', encoding='utf-8')); print('OK')"` 输出 OK；schema 中 `independent_review` ≥ 1 次、`mode:` 行不再含 `codex_review`。
8. P1-PARAM-001.md 中 `claude_code_mimo` / `codex_review` 0 命中，其余内容与修改前逐字节一致（`git diff` 仅 2 行变更）。
9. 内容文件阶段（Task Record 生成前）：`git diff --cached --name-only` 与 touched_paths 完全一致（顺序不限），`git diff --cached --check` 无输出。此后生成并追加 stage Task Record（`docs/phase1/task_logs/P1-WORKFLOW-002_*.yaml`），最终 staged 清单 = touched_paths + 该 record 文件。（前提：本任务书自身已先行提交至 phase0/main，不在本任务 diff 内。）
10. `uv run pytest tests/architecture/` 全绿（回归确认，无代码面变化）。

## validation_commands

```
wc -l docs/phase1/ROLE_POLICY.md docs/phase1/TASK_PROMPT_TEMPLATE.md
python -c "import yaml; yaml.safe_load(open('docs/dev/task_record_schema.yaml', encoding='utf-8')); print('OK')"
uv run pytest tests/architecture/
git diff --cached --name-only && git diff --cached --stat && git diff --cached --check
git ls-files --others --exclude-standard
```

## stop_conditions

- 任一 touched 文件的当前内容与本任务书引用的"旧"文本不一致（说明文件已被其他改动漂移）→ 停止并报告差异，等待 task-prompt patch，不得自行推断。
- 需要修改 forbidden_paths 中任何文件才能满足验收 → 停止并报告。
- Step 8 扫描发现本任务书未预见的废弃枚举活引用（非历史证据）→ 停止并列出清单，等待人工决定是否扩大范围。

## local_commit_policy

`human_ack_required`（本任务 risk_tier: high）。commit message: `phase1(P1-WORKFLOW-002): converge governance docs and tier the ceremony`。

## Task Record

full YAML（high tier），存 `docs/phase1/task_logs/P1-WORKFLOW-002_<YYYYMMDD_HHMMSS>_<passed|failed>.yaml`，使用修订后的 schema v1.1.0 新枚举。
