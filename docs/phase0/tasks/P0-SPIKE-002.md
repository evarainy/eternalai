# P0-SPIKE-002 — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- Your tool's boot file: AGENTS.md (Codex) or CLAUDE.md (Claude Code)
- docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md
- docs/phase0/BOUNDARY_CHECKLIST.md
- docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md only as source of truth; do not paste it in full.

## Global hard rules

- Execute only this task_id.
- Do not modify frozen blueprint files.
- Do not implement Phase 1 features.
- Do not add unapproved dependencies.
- Do not weaken tests to pass.
- Stop after Unified Task Record and wait for human confirmation.

## Task YAML

```yaml
task_id: P0-SPIKE-002
title: instructor + vLLM OpenAI-compatible API Stability Spike
type: spike
depends_on:
  - P0-PREP-003
objective: 验证 instructor 在 vLLM / OpenAI-compatible API 下的结构化输出、重试、异常解析和工具调用响应解析稳定性。
deliverable:
  - docs/adr/phase0/ADR-P0-SPIKE-002-instructor-vllm-stability.md
constraints:
  - 不产生生产 Runtime 代码
  - 不直接定义正式 Tool 调用协议
  - Spike 代码不得进入 app/
acceptance_criteria:
  - 覆盖成功、JSON 缺字段、字段类型错误、模型拒答、超时、非 JSON 输出等场景
  - 记录重试成功率、失败类型和建议参数
  - 给出 Phase 1 是否采用 instructor 作为默认结构化输出实现的建议
spike_code_disposition: 废弃或沉淀为 tests/utils/
touched_paths:
  - docs/adr/phase0/
  - experiments/phase0/instructor_vllm/
  - tests/utils/
forbidden_paths:
  - app/
```

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.


## v1.0.11 ADR evidence fields

The ADR must record: vLLM version, model name, model digest/model path, structured output mode, tool choice mode, schema used, success sample count, failure sample count, malformed output sample, retry strategy, fallback strategy, whether result is suitable for Phase 1, whether result only supports mock usage, and known limitations. `named` / `required` tool choice can support Phase 1 recommendation; `auto` tool calling alone cannot release Phase 1.
