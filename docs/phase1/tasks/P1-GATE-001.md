# P1-GATE-001 — Single-task Prompt (v2, hardened)

> **适用范围声明**: 本文件替代旧版 P1-GATE-001.md, 与 `docs/phase1/TASK_PROMPT_TEMPLATE.md` 配套使用。模板中的 Plan-first 流程、执行中工作流规则(no commit / no push / no merge、仅 stage for review、禁用 `--no-verify`)、证据规则**全量适用**。本文件只补充任务专属内容; 两者冲突时停手并请求 task-prompt 补丁(`task_prompt_incomplete`)。
> **权威声明**: Phase 1 规则以 repo root `CLAUDE.md` 摘要与 `docs/phase1/*` 为准; `AGENTS.md` 是旧 Phase 0 boot 文件, **不作为本任务权威**。

## Background / 任务由来

当前 golden 链路**不是真回归门**: `scripts/run_golden_tasks.py` 仅 `json.dump(summary)` 后 `return 0`(仅 infra 异常返回 2); `tests/golden_tasks/test_golden_tasks.py` 的 happy 用例只断言 `positive_passed >= 1` 且 `negative_passed >= 1`, 无 80%/100% 阈值; `.github/workflows/ci.yml` 的 "Golden tasks" step 只跑 `--summary`, 不按阈值 fail。

B2-B5 的"自动验收"全靠这道门。门是空的时候, 实现型任务"通过"无保障。**P1-GATE-001 是 B2 前必须完成的前置任务**(B2 前置 = P1-GATE-001 passed + P1-SPEC-001 approved/landed)。

## Required context(按需加载)

- `CLAUDE.md`(repo root) — Phase 1 governance
- `docs/phase1/TASK_PROMPT_TEMPLATE.md` — Plan-first / 工作流 / 证据规则(全量适用)
- `docs/phase1/PHASE1_PLAN.md` §C.3 GATE / §D 验证策略(若与本 prompt 冲突 → 停手要补丁, 见 stop_conditions)
- `scripts/run_golden_tasks.py` — 当前 runner(`--summary` 输出 JSON 后 `return 0`; infra 异常 `return 2`)
- `tests/golden_tasks/test_golden_tasks.py` — `build_summary`、`FROZEN_GT_IDS`、happy 用例
- `.github/workflows/ci.yml` — Golden tasks step
- `docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md` §20.1 / §14.5 / §12.5(阈值权威, 行号仅作定位锚点; 阈值语义已内嵌于本文件 thresholds 段)
- `docs/dev/task_record_schema.yaml` — Task Record schema

## method_profile

```yaml
method_profile:
  execution_role: "execution"
  execution_owner: "codex"
  review_owner: "separate_session"
  review_mode: "codex_review"
  method: "TDD"
  reason_for_owner_choice: >
    CI 行为变更(runner 非零退出 + CI step fail) + 可证伪阈值断言,
    属 test-heavy / regression-sensitive 改动; Codex 执行并做 first-pass self-review,
    Claude Code / Opus 作为 separate_session 做只读 plan/review 辅助。
```

## Task YAML

```yaml
task_id: P1-GATE-001
title: "Golden-task 真回归门: runner 阈值判定 + CI 非零退出"
type: test
method: TDD
source_plan: docs/phase1/PHASE1_PLAN.md   # §C.3 GATE
objective: >
  把 golden task 链路变成真正的阈值门:
  当 failed > 0, 或正向通过率 < 80%, 或负向(含边界/安全, 统一并入 negative)通过率 < 100%,
  或存在未豁免 skipped, 或任一类别有效分母为 0 时,
  runner 以退出码 1 失败、CI step 转红。
  实现路径按本文件 Pinned design(D1-D8)执行, 不留二选一。
thresholds:
  authority: "MVP spec §20.1(L4495 声明本节与前文不一致时以本节为准; 定位锚点 L4501-4503)"
  cross_ref: "§14.5(定位锚点 L3951-3954, 交叉印证)"
  negative_boundary_list_only: "§12.5(仅负向/边界场景清单, 不含正向 >=80% 表述)"
  positive_pass_rate: ">= 80%(按有效分母计, 见 D5)"
  negative_pass_rate: "== 100%(负向含边界/安全语义, 统一并入 negative; fixture category 硬约束 positive/negative 二选一, summary 中不存在也不得引用 boundary/security 字段)"
touched_paths:
  - scripts/run_golden_tasks.py                 # 新增 --gate 分支与退出码逻辑
  - tests/golden_tasks/test_golden_tasks.py     # build_summary 附加字段; 不放 gate 判定逻辑
  - tests/golden_tasks/test_golden_gate.py      # 新增: gate 可证伪单测(合成 summary, 不触碰 fixtures)
  - .github/workflows/ci.yml                    # 仅 "Golden tasks" step 一行命令
  - docs/phase1/task_logs/                      # Task Record YAML + INDEX.md 行
forbidden_paths:
  - app/                             # 含 app/ports/(FROZEN); 本任务不需要触碰任何生产代码
  - web/
  - docs/blueprint/
  - docs/phase0/
  - docs/phase1/tasks/               # 本任务执行期间不改任何 task prompt(含本文件)
  - tests/golden_tasks/fixtures/     # 不改 fixture 业务语义; 不得为让 gate 变绿而增删改 fixture
  - tests/architecture/              # 只运行, 不修改
  - AGENTS.md
constraints:
  - 不改 FROZEN_GT_IDS
  - 不引入任何新外部依赖; 不引入 instructor / PydanticAI
  - build_summary 只做加法(见 D3), 既有 key 与既有 happy 用例断言语义不变
  - .github/workflows/ci.yml 中除 "Golden tasks" step 外一律不动
```

## Pinned design(执行者不得自行偏离; 确需偏离, 在 Plan 中提出并等待人工批准)

- **D1 — CLI 契约**: `--summary` 退出码语义与既有 key 语义不变(除 infra 异常外返回 0); D3 新增字段属于允许的向后兼容加法。新增 `--gate`: 先向 stdout 输出与 `--summary` 同源的 JSON summary(保留 CI 日志可读性), 再执行 gate 判定; gate fail 时向 stderr 逐条打印失败 reason。退出码: `0` = gate passed, `1` = gate failed by threshold, `2` = infrastructure failure(沿用现状)。gate 判定必须发生在 summary 成功构建之后, **不得**被现有 `try/except`(infra 异常 → 2)吞掉而使 exit 1 永远不可达。
- **D2 — CI 接线**: `.github/workflows/ci.yml` 的 "Golden tasks" step 命令替换为 `uv run python scripts/run_golden_tasks.py --gate`(bash 语法, ubuntu runner; 不新增独立 `--summary` step)。
- **D3 — summary 附加字段**: 当前 `build_summary` 已包含 `results[]`; 保留该既有 item 级数据, 不得删除或改名。在 `build_summary` 中**仅新增**四个 key: `positive_total`、`negative_total`、`positive_not_applicable`、`negative_not_applicable`(均由既有 `results[].category` 与 `results[].status` 派生)。不修改、不删除任何既有 key, 保证 `test_happy_minimum_positive_and_negative_goldens_pass` 无需改动断言语义。
- **D4 — gate 纯函数**: 在 `scripts/run_golden_tasks.py` 中实现 `evaluate_gate(summary: dict[str, Any]) -> GateDecision`; `GateDecision` 为不可变结构, 至少包含 `passed: bool` 与 `reasons: list[str]`。runner 仍沿用现有 `_load_runner` 的 importlib 模式只加载 `evaluate_all_golden_tasks` 与 `build_summary`; gate 判定逻辑不得放入 `tests/golden_tasks/test_golden_tasks.py`, 避免 runner 依赖测试模块中的 gate 规则。单一事实来源: pytest 用含 `results[]` 的合成 summary 直接测 `evaluate_gate`, runner 复用同一函数。
- **D5 — 有效分母**: `positive_effective = positive_total - positive_not_applicable`; negative 同理。`skipped` 与 `failed` **留在有效分母内**(即计为未通过); 仅 `not_applicable` 从分母排除。
- **D6 — 判定规则**(任一命中即 gate fail; 所有命中的规则逐条进入 `reasons`):
  - R1: `failed > 0`。
  - R2: `results[]` 中存在 `status == "skipped"` 且其 `golden_task_id` 不在 `GATE_SKIP_EXEMPT_GT_IDS` 中。该常量定义于 gate 函数旁: `GATE_SKIP_EXEMPT_GT_IDS: frozenset[str] = frozenset()`, **初始为空**(即 skipped 默认失败), 并附注释"修改本集合需未来任务显式授权 + 人工批准"。豁免仅免除 R2, 不从有效分母移除。
  - R3(fail-closed): `positive_effective == 0` 或 `negative_effective == 0`。
  - R4: 正向通过率 <80% 时失败; 必须用整数交叉相乘等价判定(例如 `positive_passed * 5 < positive_effective * 4`), 禁止浮点除法比较。
  - R5: `negative_passed != negative_effective`(等价于 <100%)。
  - R6(fail-closed): `results[]` 中出现 `category` 不属于 `{"positive", "negative"}` 的条目。
- **D7 — not_applicable**: 不单独触发 gate 失败, 但数量必须通过 D3 字段显式出现在 JSON 输出中。
- **D8 — 禁止幽灵字段**: 当前 fixture category 只有 positive/negative, 边界/安全语义并入 negative。代码与测试中**不得**新增或读取 `boundary_*`、`security_*` 等不存在的 summary key。

## failure_examples(执行者在 Plan §3 中逐条引用)

- F1: runner 在 `failed > 0` 时仍返回 0(masking) → 违反 R1/AC-4。
- F2: 把 `not_applicable` 计入分母, 使 80% 阈值虚高或虚低 → 违反 D5/AC-7。
- F3: 引用 `boundary_passed` 等不存在的 summary key, 导致 KeyError 或恒真判定 → 违反 D8/AC-7。
- F4: `skipped` 被静默计为通过或被忽略 → 违反 R2/AC-6。
- F5: 为让 gate 变绿而修改 `tests/golden_tasks/fixtures/` 或 `FROZEN_GT_IDS` → 触碰 forbidden_paths/冻结面, 立即停手。
- F6: gate 判定写在 `main()` 的 `try/except` 之内, 任何失败都走 infra 路径 `return 2`, exit 1 永不可达 → 违反 D1。
- F7: 只改 ci.yml 不改 runner(或反之), AC-8 表面满足实则无判定 → 违反 D1+D2 联合验收。
- F8: 伪造测试输出或 CI 结论写进 Task Record → 违反证据规则, Task Record 无效。
- F9: 通过 commit/push 触发真实 CI 来"验证转红", 或使用 `--no-verify` → 违反工作流规则; 真实 CI 观察属 merge 后人工验证(deferred evidence)。
- F10: 浮点比较 `passed / total >= 0.8` 引入精度边界问题 → 违反 R4 的整数交叉相乘要求。

## step_verification_points

- SVP-1(TDD red): 先写 `tests/golden_tasks/test_golden_gate.py` 合成 summary(含 `results[]`)用例, `uv run pytest tests/golden_tasks/test_golden_gate.py` 出现**预期失败**(gate 函数尚不存在/未实现)。
- SVP-2(TDD green): 在 `scripts/run_golden_tasks.py` 实现 `evaluate_gate`, 并在 `tests/golden_tasks/test_golden_tasks.py` 补 D3 字段后, `uv run pytest tests/golden_tasks/` 全绿, 且既有 happy 用例未修改断言语义。
- SVP-3(runner 层验证): monkeypatch `_load_runner` 返回假的 `evaluate_all_golden_tasks` / `build_summary`, 分别构造含 failed 的 summary 与全 passed summary, 断言 `main(["--gate"])` 返回 1 / 0; 加载异常路径返回 2。
- SVP-4(现状演练): 对真实 fixtures 运行 `uv run python scripts/run_golden_tasks.py --gate`。若在未注入任何变更的现状下即 fail(包括因 R6 发现非 positive/negative category) → **停手报告**(见 stop_conditions), 不得为转绿调整阈值、豁免集或 fixtures。
- SVP-5: `uv run pytest tests/architecture/`、`uv run ruff check app/ tests/ scripts/run_golden_tasks.py scripts/check_weak_tests.py`、`uv run mypy app/`、`uv run python scripts/check_weak_tests.py tests/golden_tasks/test_golden_gate.py` 全绿。
- SVP-6: `git diff --cached --name-only` 输出仅含 touched_paths 内文件; `git ls-files --others --exclude-standard` 无遗留 untracked, 或仅有进入任务前已存在且已在 Task Record 中明确说明的本机 OS 噪声文件。

## acceptance_criteria

- AC-1(本地可证伪): 合成 summary(含 `results[]` 且含 1 条 failed)驱动 `evaluate_gate` → `passed == False` 且 reasons 命中 R1; monkeypatch `_load_runner` 后 `main(["--gate"])` 返回 1。真实 CI 转红观察**不在本任务内完成**, 作为 merge 后人工验证项以 deferred evidence 写入 Task Record。
- AC-2(可证伪): 正向 7/10(70%) → fail(R4); 正向 8/10(恰 80%) → 不因 R4 失败。
- AC-3(可证伪): 负向有效分母内任一条目非 passed → fail(R5)。
- AC-4: `failed > 0` → fail(与 AC-1 同源, 独立断言存在)。
- AC-5: not_applicable 条目不触发 gate 失败, 且 `positive_not_applicable` / `negative_not_applicable` 出现在 JSON 输出中。
- AC-6: 非豁免 skipped → fail(R2); `GATE_SKIP_EXEMPT_GT_IDS` 初始为空 frozenset, 带"修改需显式任务授权"注释。豁免仅免除 R2, 不从有效分母移除。本条与本 prompt D6/R2 既定策略一致即满足, **不要求**修改任何 docs/phase1/tasks/ 文件。
- AC-7(可证伪的分母排除): 构造"计入 NA 则 <80%、排除 NA 则恰 80%"的合成 summary(如 positive_total=6、positive_not_applicable=1、positive_passed=4: 4/5=80% pass, 若误算 4/6 则 fail), 断言 gate pass; 且代码中无 boundary/security summary 字段引用(D8)。
- AC-8: ci.yml "Golden tasks" step 命令精确为 `uv run python scripts/run_golden_tasks.py --gate`; 该 step 之外 ci.yml 无任何改动。
- AC-9: `tests/architecture/` 全绿且本任务未修改其内容; import boundary 未破坏。
- AC-10: 新增用例覆盖 R1-R6 + 豁免集语义 + fail-closed 空分母, 每条断言均存在明确反例输入(可证伪), 并通过 weak test checker。
- AC-11(向后兼容): `--summary` 行为与输出既有 key 完全不变; `test_happy_minimum_positive_and_negative_goldens_pass` 未被修改或削弱。

## stop_conditions(命中任一 → 停止并报告, 不得继续修改)

- 需修改 forbidden_paths 内任何文件(含 `app/ports/`、fixtures、`tests/architecture/`、本文件自身)。
- SVP-4 现状演练即 fail: 说明存在真实回归或需要人工政策决定; 不得调阈值、改 fixtures、扩豁免集来转绿。
- `scripts/run_golden_tasks.py` 或 `tests/golden_tasks/test_golden_tasks.py` 实际结构与本 prompt 引用明显不符(如 `build_summary` 不存在或签名不同) → 结构漂移, 停手报告。
- 需要新增任何外部依赖。
- 本 prompt 与 `PHASE1_PLAN.md` §C.3 或 TASK_PROMPT_TEMPLATE 冲突, 或本 prompt 信息不足以继续 → 输出 `task_prompt_incomplete` 并请求补丁, 不得猜测。
- 任一测试失败且无法在 touched_paths 内修复。
- 需要访问真实生产/内网系统(本任务全程 mock/本地)。

## test_commands(命令应避免 shell-specific 语法; ci.yml 内为 Ubuntu bash)

```bash
# gate 单测(TDD red -> green)
uv run pytest tests/golden_tasks/test_golden_gate.py
# golden 全量(含既有 happy 用例)
uv run pytest tests/golden_tasks/
# 现状演练(SVP-4; 若红 -> 停手报告)
uv run python scripts/run_golden_tasks.py --gate
# 向后兼容确认
uv run python scripts/run_golden_tasks.py --summary
# import boundary + weak test checker
uv run pytest tests/architecture/
uv run python scripts/check_weak_tests.py tests/golden_tasks/test_golden_gate.py
# lint / type
uv run ruff check app/ tests/ scripts/run_golden_tasks.py scripts/check_weak_tests.py
uv run mypy app/
# staged diff 检查(review 前必跑)
git diff --cached --name-only
git diff --cached --stat
git diff --cached --check
git ls-files --others --exclude-standard
```

## 工作流与 Task Record(重申模板规则, 全量适用)

- **Plan-first**: 先按 TASK_PROMPT_TEMPLATE 输出 Plan(§1 Task Scope / §2 Acceptance Criteria 逐条复制解释 / §3 逐条引用本文件 failure_examples / §4 Step-by-step Plan / §5 逐条引用本文件 step_verification_points / §6 touched_paths / §7 forbidden_paths / §8 test_commands / §9 stop_conditions), **等待人工确认后再修改任何文件**。
- **No commit / no push / no merge**; 仅 stage for review; 不得使用 `--no-verify` 或绕过 git hooks; Task Record 输出且人工确认前不得创建 commit。
- **Task Record**: 保存至 `docs/phase1/task_logs/<task_id>_<YYYYMMDD_HHMMSS>_<passed|failed>.yaml`, 符合 `docs/dev/task_record_schema.yaml`(通过 `yaml.safe_load` + 无重复 key 校验), 并补 `docs/phase1/task_logs/INDEX.md` 行。
- **证据规则**: 命令输出必须为真实运行片段, 不得伪造; `changed_files` 在最终 stage 之后、commit 之前更新, 与 `git diff --cached --name-only` 完全一致(含顺序); `git_commit_sha` 使用 deferred convention(`recorded_by_git_history_after_final_commit` + note); CI run id 记为 deferred, merge 后由人工补充"注入失败 → CI 红"的观察结论; blocked/failed 重跑通过后不得遗留 stale YAML。
