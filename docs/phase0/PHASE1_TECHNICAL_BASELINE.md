# Phase 1 Structured-Output Technical Baseline v1.0.0

## 1. Purpose

This document records the structured-output technical baseline decision for Phase 1, synthesized from three Phase 0 spike tasks. It is an advisory reference; it does not add new constraints beyond what the source ADRs and task records already define.

Source spikes: P0-SPIKE-001, P0-SPIKE-002, P0-SPIKE-007.

## 2. Baseline Decision

**Phase 1 uses the P0-SPIKE-001 approach.** No wrapper library is in the baseline.

| Component | Choice |
|---|---|
| SDK | raw OpenAI SDK (`openai>=1.0.0`) |
| Response format | `response_format: {"type": "json_object"}` |
| Schema enforcement | Pydantic v2 `model_validate` |
| Enum enforcement | `Literal[...]` types on Pydantic BaseModel fields |
| Mode downgrade | `json_schema` -> `json_object` (provider does not support `json_schema`) |
| Retry | not yet validated; Phase 1 should implement retry with backoff |

**Not in baseline:** instructor, PydanticAI, or any other wrapper library.

## 3. Evidence Summary

Threshold: >= 80% for structured output; >= 80% for tool calling.

| Approach | Spike | Result | Structured Output | Tool Calling | Primary Failure | Notes |
|---|---|---|---|---|---|---|
| Raw OpenAI SDK + Pydantic `Literal[...]` | P0-SPIKE-001 | **PASSED** | **92.6% (50/54)** | N/A | timeout (2/4 failures) | **THIS IS THE BASELINE** |
| instructor 1.15.1 Mode.JSON | P0-SPIKE-002 | FAILED | 36.0% (no retry) / 56.0% (retry) | 75.0% (6/8) | timeout + parse_fail | thinking mode blocks TOOLS mode |
| PydanticAI 1.94.0 PromptedOutput | P0-SPIKE-007 | FAILED | 58.0% (default) / 66.0% (retry) | 62.5% (5/8) | DashScope ModelAPIError | ~97% excluding provider errors |

## 4. Scope Limitations

- All three spikes used **provider API mode** (DashScope public endpoint), not internal vLLM/GPU.
- Results prove **client-side SDK/schema compatibility only** — they do not prove internal vLLM/Qwen performance.
- Internal vLLM/Qwen validation is **deferred / not_executed**. Activation condition: rerun when intranet access is available.

## 5. What Changes This Baseline

Any change to the following requires an **independent validation task** — do not override this baseline without new evidence:

- **Model** (different model family or size)
- **Provider / API endpoint** (internal vLLM vs DashScope vs other)
- **Wrapper library** (adopting instructor, PydanticAI, or another wrapper)
- **Retry strategy** (retry count, backoff policy, timeout threshold)
- **Tool-calling strategy** (`tool_choice` mode, tool schema format)
- **Structured output mode** (`json_object` vs `json_schema` vs constrained decoding)

## 6. Evidence Links

### ADRs

- `docs/adr/phase0/ADR-P0-SPIKE-001-qwen-structured-output.md` (accepted)
- `docs/adr/phase0/ADR-P0-SPIKE-002-instructor-vllm-stability.md` (accepted — failed)
- `docs/adr/phase0/ADR-P0-SPIKE-007-pydanticai-qwen-vllm.md` (accepted — failed)

### Task Records

- `docs/phase0/task_logs/P0-SPIKE-001_20260512_passed.yaml`
- `docs/phase0/task_logs/P0-SPIKE-002_20260512_failed.yaml`
- `docs/phase0/task_logs/P0-SPIKE-007_20260513_failed.yaml`
