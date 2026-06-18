# P1-GATE-001 — Single-task Prompt

Use this instead of pasting the full Phase 1 Plan or any blueprint into the session.

## Background / 任务由来

当前 golden 链路**不是真回归门**：`scripts/run_golden_tasks.py` 仅 `json.dump(summary)` 后 `return 0`（仅 infra 异常返回 2）；`tests/golden_tasks/test_golden_tasks.py` 的 happy 用例只断言 `positive_passed >= 1` 且 `negative_passed >= 1`，无 80%/100% 阈值；`.github/workflows/ci.yml` 的 "Golden tasks" step 只跑 `--summary`，不按阈值 fail。

B2-B5 的"自动验收"全靠这道门。门是空的时候，实现型任务"通过"无保障。**P1-GATE-001 是 B2 前必须完成的前置任务**。

## Required context（按需加载）

- `CLAUDE.md`（repo root）— Phase 1 governance
- `docs/phase1/PHASE1_PLAN.md` §C.3 GATE / §D 验证策略
- `scripts/run_golden_tasks.py` — 当前 runner（`return 0` 现状）
- `tests/golden_tasks/test_golden_tasks.py` — `build_summary`（L549-575）、`FROZEN_GT_IDS`（L66-78）
- `.github/workflows/ci.yml` — golden step（L85-86）
- `docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md` §20.1 L4501-4503 / §14.5 L3951-3954 / §12.5 L3494-3501

## method_profile

```yaml
method_profile:
  execution_role: "implementation"
  execution_owner: "codex"
  review_owner: "claude_code_mimo"
  review_mode: "codex_review"
  method: "TDD"
  reason_for_owner_choice: >
    CI 行为变更（runner 非零退出 + CI step fail）+ 可证伪阈值断言，
    属 test-heavy / regression-sensitive 改动；Codex 执行，Claude Code 独立审查。
```

## Task YAML

```yaml
task_id: P1-GATE-001
title: "Golden-task 真回归门：runner 阈值判定 + CI 非零退出"
type: infrastructure/test
method: TDD
source_plan: docs/phase1/PHASE1_PLAN.md   # §C.3 GATE
objective: >
  把 golden task 链路变成真正的阈值门：
  当 failed > 0 OR 正向通过率 < 80% OR 负向（含边界/安全）通过率 < 100% 时，
  runner 返回非零退出码、CI 失败。
  给 runner 加 --gate 模式（或让 --summary 同时判定），在 CI step 调用 gate 模式。
thresholds:
  authority: "MVP spec §20.1 L4501-4503（最高权威，L4495 以本节为准）"
  cross_ref: "§14.5 L3951-3954（交叉印证）"
  negative_boundary_list_only: "§12.5 L3496（仅负向/边界清单，不含正向≥80%）"
  positive_pass_rate: ">= 80%"
  negative_pass_rate: "== 100%（负向含边界/安全，并入 negative 统一计；fixture category 硬约束 positive/negative 二选一）"
pass_rate_denominator: >
  从 summary["results"] 按每条 item 的 category 派生 positive_total / negative_total，
  或在 build_summary 里新增 *_total 计数字段。
  分母排除 not_applicable 条目（positive_passed / (positive 总数 - positive 中 not_applicable 数)，负向同理）。
  不引用不存在的 boundary / security 字段（fixture 当前无此类 category）。
gate_boundary_conditions:
  not_applicable: "不算 gate 失败，但 runner 须显式记录数量"
  skipped: "默认失败，可通过白名单/原因码豁免特定 skip"
  failed_gt_0: "直接失败"
constraints:
  - 不动 app/ports/（FROZEN）
  - 不动 fixtures 业务语义
  - 不动 tests/architecture/
  - 不引入 instructor / PydanticAI
acceptance_criteria:
  - AC-1: 故意注入一条 failed 时 CI 必须红（可证伪验证）
  - AC-2: 正向通过率 < 80% 时 runner 非零退出
  - AC-3: 负向通过率 < 100% 时 runner 非零退出（负向含边界/安全，并入 negative 计）
  - AC-4: failed > 0 时 runner 非零退出
  - AC-5: not_applicable 记录不触发 gate 失败，但输出中显式记录数量
  - AC-6: skipped 默认失败策略已在 P1-GATE-001 spec 段落写死（可豁免）
  - AC-7: 分母排除 not_applicable；分类来源 = results[].category（positive/negative 二选一）；不引用不存在的 boundary/security 字段
  - AC-8: CI golden step 调用 gate 模式，不再仅 --summary
  - AC-9: tests/architecture/ 仍绿；import boundary 未破坏
  - AC-10: 补 tests/ 中对应阈值断言（positive<80%、negative<100%、failed>0、skipped 策略各一可证伪用例）
forbidden_paths:
  - app/ports/
  - docs/blueprint/
  - tests/golden_tasks/fixtures/   # 不改 fixture 业务语义
  - docs/phase0/
  - docs/phase1/tasks/              # 本任务执行期间不改其他 task prompt
```

## Stop Conditions

- 需修改 `app/ports/` 任何文件 → 停手报告
- `scripts/run_golden_tasks.py` 结构与 Plan 描述不符（`return 0` 不在预期位置）→ 停手报告
- 阈值引用字段在 summary 中不存在且无法在 touched_paths 内补 → 停手报告
- 需引入新外部依赖 → 停手报告

## Test Commands

```powershell
# 故意注入失败验证 CI 转红
uv run python scripts/run_golden_tasks.py --gate   # 或等价参数
# 单测阈值断言
uv run pytest tests/golden_tasks/
# import boundary
uv run pytest tests/architecture/
# staged diff 检查
git diff --cached --name-only
git diff --cached --check
```
