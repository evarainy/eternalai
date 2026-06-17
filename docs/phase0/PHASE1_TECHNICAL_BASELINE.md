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

### 3.1 Internal vLLM re-test (2026-06)

Closing the deferred internal-endpoint validation. Re-run against an internal vLLM (OpenAI-compatible) endpoint with a 120s timeout and thinking-off requested; sample sets and scoring unchanged from the public runs. Task: P0-SPIKE-INTERNAL-REVAL (2026-06-17).

| Approach | Spike | Model | Structured Output (internal) | Tool Calling (internal) | Conclusion |
|---|---|---|---|---|---|
| Raw OpenAI SDK + Pydantic `Literal[...]` | P0-SPIKE-001 | qwen3.5-27b / glm-4.7 | **98.1% / 90.7%** | N/A | baseline confirmed on internal infra |
| instructor Mode.JSON (max_retries=3) | P0-SPIKE-002 | qwen3.5-27b / glm-4.7 | **82.0% / 94.0%** | 75.0% (qwen and glm) | structured now PASS; tool calling < 80% (prompt issue) |
| PydanticAI PromptedOutput (retries={"output":3}) | P0-SPIKE-007 | qwen3.5-27b | **82.0%** | not re-measured (agent path) | structured now PASS |

Notes:
- Public-run failures were infrastructure noise — 30s timeouts (instructor), provider `ModelAPIError` (pydanticai), and a 1024-token truncation bug — not framework capability. With a 120s internal timeout, a real output-retry knob, and a 2048-token budget, instructor and pydanticai both clear the 80% structured threshold.
- Tool calling is 75% on the OpenAI-SDK path for both qwen and glm, failing the same two `query_oa_leave_balance` samples (TC-001/TC-004) across both endpoints and both model families — a reproducible prompt/tool-description issue, not an endpoint or framework limit. The pydanticai agent-loop tool path was not separately re-measured.
- raw OpenAI SDK reaches 98.1% in a single pass with no retry loop; instructor/pydanticai need max_retries to clear the threshold. **The baseline decision (section 2) is unchanged.**

## 4. Scope Limitations

- The original three spikes used **provider API mode** (DashScope public endpoint); they were **re-validated against an internal vLLM endpoint in 2026-06** (see section 3.1), so the public-mode caveats below now carry internal evidence.
- Structured output is **internal-validated**: raw-SDK 98.1% / 90.7%, instructor 82% / 94% (Run B), pydanticai 82% (Run B) — all >= 80% on internal infrastructure.
- Internal vLLM/Qwen validation is **executed (P0-SPIKE-INTERNAL-REVAL, 2026-06)** for structured output. Open items: tool calling is 75% on the OpenAI-SDK path (a reproducible prompt issue), and pydanticai agent-loop tool calling was not separately re-measured.

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
- `docs/phase0/task_logs/P0-SPIKE-INTERNAL-REVAL_20260617_passed.yaml` (internal vLLM re-test)
