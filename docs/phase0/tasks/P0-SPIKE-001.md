# P0-SPIKE-001 — Single-task Prompt

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
task_id: P0-SPIKE-001
title: Qwen Local Model Structured Output Spike
type: spike
depends_on:
  - P0-PREP-003
objective: 验证 Qwen 系列模型在目标 GPU 环境下的量化方案、可用 max_model_len、结构化输出成功率和推理成本。
deliverable:
  - docs/adr/phase0/ADR-P0-SPIKE-001-qwen-structured-output.md
constraints:
  - 不产生生产 Runtime 代码
  - 不修改主蓝图
  - 不引入新的模型服务架构
  - Spike 代码不得进入 app/ 任何正式模块
acceptance_criteria:
  - 记录模型版本、量化方案、GPU、显存、推理服务、max_model_len
  - 使用不少于 50 条固定样例
  - 至少覆盖 Intent、CapabilityRef、PlanDraft、ResponseEnvelope 四类结构化输出样例
  - 成功定义为：可解析、字段完整、枚举合法、业务关键字段不为空
  - 统计结构化输出成功率，并明确是否达到 >= 80%
  - 给出 Phase 1 是否可用 OpenAI-compatible 本地模型网关的建议
spike_code_disposition: 废弃或沉淀为 tests/utils/
touched_paths:
  - docs/adr/phase0/
  - experiments/phase0/qwen_structured_output/
  - tests/utils/
forbidden_paths:
  - app/
```

## Execution instruction

Apply `docs/phase0/CODEX_SINGLE_TASK_PROMPT_TEMPLATE.md` to this task. First output the Plan and wait for human confirmation before modifying files.


## v1.0.11 ADR evidence fields

The ADR must record: vLLM version, model name, model digest/model path, structured output mode, tool choice mode, schema used, success sample count, failure sample count, malformed output sample, retry strategy, fallback strategy, whether result is suitable for Phase 1, whether result only supports mock usage, and known limitations. `named` / `required` tool choice can support Phase 1 recommendation; `auto` tool calling alone cannot release Phase 1.
