# P1-PARAM-001 — Single-task Prompt

## Background / 任务由来

内网复测（`PHASE1_TECHNICAL_BASELINE.md` §3.1）已完成：raw-SDK 结构化 98.1%/90.7% 达标，基线 = raw OpenAI SDK，无 wrapper。但部署参数（`max_model_len`、量化方式、`request_timeout`、`max_tokens`、`enable_thinking` 行为）尚未从 infra 确认登记为 Phase 1 基线。

**本任务 ready only after infra values arrive。infra 未给值前天然 blocked，不与 GATE/SKEL/ERRATA 并列为"无条件可做"。**

## method_profile

```yaml
method_profile:
  execution_role: "documentation"
  execution_owner: "claude_code"
  review_owner: "codex"
  review_mode: "independent_review"
  method: "PDR"
  reason_for_owner_choice: >
    scope=research-only；向 infra 确认部署参数后登记为基线表。
    PDR（Plan-Decide-Record）方法适用：需整理选项、向 infra 询问、记录决策依据。
    无生产代码变更、无测试断言。
```

## Task YAML

```yaml
task_id: P1-PARAM-001
title: "Context Budget / vLLM 部署参数登记（等 infra 回值）"
type: documentation
method: PDR
scope: research-only
readiness: "ready only after infra values arrive"
blocked_until: "infra 提供实际部署参数（外部依赖）"
objective: >
  消费已完成的内网复测结论，仅补齐部署参数：
  max_model_len、量化方式、request_timeout（复测用 120s）、
  max_tokens（复测用 2048）、enable_thinking 是否被 endpoint 尊重
  （REVAL remaining_risks 提示可能未被尊重）。
  向 infra 确认后登记成 Phase 1 部署基线表。
pdr_structure:
  plan: "列出待确认参数清单 + 依据（REVAL log 引用）"
  decide: "infra 回值后逐参数决策（选用 or 调整）"
  record: "登记到 docs/phase1/ 下部署参数文件（或并入 baseline 的 Phase 1 addendum）"
acceptance_criteria:
  - AC-1: 明确标注"参数登记，不重测结构化成功率"
  - AC-2: 不引入 instructor / PydanticAI
  - AC-3: 不改 app/ports/（FROZEN）
  - AC-4: 每个参数有来源（infra 确认 or REVAL log 引用）
  - AC-5: enable_thinking 行为注明"以 thinking-active 行为为准"（REVAL remaining_risks 明示观察到 reasoning trace + 高延迟）
  - AC-6: method=PDR / scope=research-only 明确登记在 Task Record
  - AC-9 method=PDR scope=research-only: true   # AC-9 从 P1-SKEL-001 任务 prompt 继承
blocked_until_infra: >
  本任务依赖 infra 提供以下参数确认值：
  - max_model_len（内网 vLLM endpoint 实际设定）
  - 量化方式（AWQ / GPTQ / none）
  - request_timeout（复测使用 120s，infra 生产值待确认）
  - max_tokens（复测使用 2048，infra 生产值待确认）
  - enable_thinking 是否被 endpoint 尊重
  在 infra 回值前，本任务 Task Record 状态为 blocked。
touched_paths:
  - docs/phase1/PHASE1_DEPLOY_PARAMS.md   # 或并入 baseline addendum，执行时确认路径
  - docs/phase1/task_logs/P1-PARAM-001_*.yaml
forbidden_paths:
  - app/
  - app/ports/
  - docs/phase0/
  - docs/blueprint/
  - .github/workflows/
  - pyproject.toml
  - uv.lock
```

## Stop Conditions

- infra 参数未到 → Task Record 填 `result: blocked`，停止，等回值
- 需修改 `app/ports/` → 停手
- 参数来源不可核（既无 infra 确认也无 REVAL log 引用）→ 停手报告，不猜测

## Test Commands（文档型任务）

```powershell
git diff --cached --name-only   # 仅含 docs/phase1/ 内文件 + Task Record
git diff --cached --check
```
