# P0-SPIKE-007 — Single-task Prompt

Use this instead of pasting the full Phase 0 spec.

## Required context

- AGENTS.md
- CLAUDE.md when using Claude Code
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
task_id: P0-SPIKE-007
title: PydanticAI + Qwen/vLLM Compatibility Spike
type: spike
depends_on:
  - P0-PREP-003
recommended_after:
  - P0-SPIKE-001
  - P0-SPIKE-002
objective: 验证 PydanticAI 与 vLLM OpenAI-compatible API 的兼容性，包括结构化输出格式、工具调用响应解析、异常处理、重试、可观测性和 Qwen 模型适配。
deliverable:
  - docs/adr/phase0/ADR-P0-SPIKE-007-pydanticai-qwen-vllm.md
constraints:
  - 不产生生产 Runtime 代码
  - 不将 PydanticAI 作为 Phase 1 强依赖
  - Spike 代码不得进入 app/ 任何正式模块
acceptance_criteria:
  - 记录兼容性结论：通过 / 失败 / 部分可用
  - 覆盖结构化输出、工具调用响应解析、异常处理、重试、观测埋点
  - 给出是否在 Phase 2 引入 PydanticAI 的明确建议
  - 若条件通过，必须写明限制条件和不适用场景
spike_code_disposition: 废弃或沉淀为 tests/utils/
touched_paths:
  - docs/adr/phase0/
  - experiments/phase0/pydanticai_qwen_vllm/
  - tests/utils/
forbidden_paths:
  - app/
```

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.


## v1.0.11 ADR evidence fields

The ADR must record: vLLM version, model name, model digest/model path, structured output mode, tool choice mode, schema used, success sample count, failure sample count, malformed output sample, retry strategy, fallback strategy, whether result is suitable for Phase 1, whether result only supports mock usage, and known limitations. `named` / `required` tool choice can support Phase 1 recommendation; `auto` tool calling alone cannot release Phase 1.
