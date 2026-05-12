# ADR-P0-SPIKE-002 — instructor + vLLM OpenAI-compatible API Stability Spike (Provider API Mode)

- status: accepted (failed)
- date: 2026-05-12
- task_id: P0-SPIKE-002
- owner: Claude Code
- related_spec: docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md

## 1. Context

P0-SPIKE-002 validates whether the **instructor** library (jxnl/instructor) can produce stable structured output, retry, exception parsing, and tool calling response parsing via an OpenAI-compatible API endpoint. The original task prompt implies validation against an internal vLLM/GPU/Qwen deployment. This execution uses **provider API mode**: a public OpenAI-compatible API endpoint for client-side compatibility validation.

This ADR does NOT validate internal vLLM, GPU, quantization, max_model_len, or local Qwen deployment.

## 2. Provider API Boundary

```yaml
execution_environment:        public_vendor_api
internal_endpoint_validation: deferred / not_executed
activation_condition:         rerun against internal vLLM/Qwen server when intranet access is available
recommendation_scope:         client-side compatibility only
```

## 3. Question to Answer

- Can instructor (via public OpenAI-compatible API) produce structured output with Pydantic schema validation?
- What is the first-pass success rate (max_retries=0) and retry success rate (max_retries=3)?
- Does instructor's retry mechanism improve success rate?
- Does instructor support tool calling via this provider?
- Should instructor be adopted as the Phase 1 structured output implementation?

## 4. Validation Setup

- environment: public vendor API (OpenAI-compatible endpoint)
- hardware: not applicable (provider API mode; no local GPU)
- software_versions:
  - Python: system Python
  - instructor: 1.15.1
  - openai SDK: 2.36.0
  - pydantic: 2.13.4
  - httpx: 0.28.1
  - vLLM version: not applicable_provider_mode
  - model name: qwen3.6-27b
  - model digest / model path: not applicable_provider_mode
- dependency_source: PyPI (internal mirror availability not confirmed)
- test_dataset: 50 structured output samples (success: 15, missing_fields: 9, type_error: 8, refusal: 6, non_json: 6, timeout: 6) + 8 tool calling samples
- validation_method: instructor `response_model` backed by Pydantic v2 BaseModel with `Literal[...]` enum enforcement
- request_timeout: 30s per request (3s for timeout adversarial samples)
- request_delay: 2s between logical sample requests
- rate_limit_abort_threshold: 3 consecutive provider permission/rate-limit errors
- logical_sample_count: 108 (Run A: 50 + Run B: 50 + tool calling: 8)
- actual_provider_request_count: not_measured (instructor retries issue additional API calls per logical sample)
- commands_run: see section 11

### Validation Path

Structured output validation is performed by **instructor** using `response_model` backed by Pydantic BaseModel subclasses. Instructor sends the request, receives the response, and internally validates it against the `response_model`. If validation fails, instructor raises `InstructorRetryException` (or retries if `max_retries > 0`). The spike code does NOT call `model_validate()` directly for structured output validation; that is handled internally by instructor.

Tool argument validation uses explicit `Pydantic.model_validate()` on the tool call arguments returned by the provider.

### instructor Mode Discovery

The initial spike attempt used `instructor.from_openai(client)` (default mode), which internally uses `tool_choice="required"`. The provider (DashScope with qwen3.6-27b) returned:

```
Error code: 400 - The tool_choice parameter does not support being set to required or object in thinking mode
```

This is because qwen3.6-27b defaults to "thinking mode" on DashScope, and thinking mode does not support `tool_choice=required`. The fix was to explicitly use `mode=instructor.Mode.JSON`.

## 5. Acceptance Criteria Result

| criterion | result | evidence |
|---|---|---|
| Environment variables present | **passed** | check_env output: LLM_BASE_URL: set, LLM_API_KEY: set, LLM_MODEL: qwen3.6-27b |
| API reachable | **passed** | check_env output: api_reachable: true |
| instructor importable and version recorded | **passed** | instructor_version: 1.15.1 |
| json_schema mode support | **passed** | json_schema_supported: true |
| json_object mode support | **passed** | json_object_supported: true |
| tool calling probe | **passed** | check_env: tool_calling_supported: true |
| >= 50 structured output samples executed | **passed** | 50 samples in Run A (complete); 50 samples in Run B (complete) |
| All 6 failure categories covered | **passed** | timeout, parse_fail, schema_fail, refusal, provider_permission_or_rate_limit, api_error |
| Structured output success_rate >= 80% (max_retries=0) | **FAILED** | 18/50 = 36.0% |
| Structured output success_rate >= 80% (max_retries=3) | **FAILED** | 28/50 = 56.0% |
| Retry mechanism improves success rate | **passed** | Improvement: 36.0% (no retry) -> 56.0% (with retry); 10 samples recovered |
| Tool calling success_rate >= 80% | **FAILED** | 6/8 = 75.0% (2 OA leave balance queries: model did not call tool) |
| Phase 1 recommendation | **not recommended** | See section 12 |

## 6. Decision

decision: **failed**

The spike did not meet the acceptance criteria. Run A (36%), Run B (56%), and tool calling (75%) are all below 80%. The retry mechanism improves success rate from 36% to 56% but does not reach the 80% threshold. Instructor is not recommended as the default Phase 1 structured output implementation.

### Key findings

1. **instructor Mode.JSON works** after discovering thinking mode incompatibility with the default TOOLS mode.
2. **Retry mechanism improves success rate**: 36% (max_retries=0) -> 56% (max_retries=3). 10 additional samples recovered.
3. **Timeout is the primary failure mode** in both runs. Complex schemas (PlanDraft, ResponseEnvelope) and adversarial prompts generate long responses that exceed 30s.
4. **Provider permission/rate-limit errors** were effectively mitigated with 2s request pacing: 2 errors across 108 logical samples.
5. **Tool calling partially works**: 6/8 passed. OA leave balance queries failed because model did not invoke the tool.
6. **Thinking mode blocks instructor TOOLS mode**: `tool_choice=required` not supported in thinking mode.

### Instructor Mode Evidence

| field | value |
|---|---|
| instructor_version | 1.15.1 |
| instructor_mode_attempted | JSON (explicit mode=instructor.Mode.JSON); TOOLS (default, failed: thinking mode blocks tool_choice=required) |
| effective_instructor_mode | JSON |
| underlying_response_format_if_observable | json_object (instructor Mode.JSON sends response_format json_object) |
| json_schema_supported_by_provider | true |
| json_object_supported_by_provider | true |
| tool_calling_supported_by_provider | true |

### Structured Output Mode Tracking

| field | value |
|---|---|
| structured_output_mode_attempted | json_schema (probe) + json_object (instructor Mode.JSON) |
| effective_mode_used_for_acceptance | json_object (via instructor Mode.JSON) |
| success_rate_by_mode | json_object via instructor: max_retries=0: 18/50=36.0%; max_retries=3: 28/50=56.0% |
| json_schema_supported_by_provider | true |
| json_object_supported_by_provider | true |

### Enum Validation Method

Enum-bearing fields use `Literal[...]` types in Pydantic BaseModel:
- `IntentResult.intent`: `Literal["ask_question", "request_action", "provide_info", "chitchat", "complaint", "feedback"]`
- `PlanDraftResult.estimated_cost`: `Literal["low", "medium", "high"]`
- `PlanDraftResult.risk_level`: `Literal["low", "medium", "high"]`
- `ResponseEnvelopeResult.status`: `Literal["success", "error"]`

### Failure Category Breakdown — Run A (max_retries=0, 50 samples)

Per-sample final failure categories (mutually exclusive, sums to 32 failed samples):

| category | count | representative samples |
|---|---|---|
| timeout | 15 | SUC-010, TYP-004, TYP-005, TYP-007, REF-003, REF-006, NJS-004, TMO-001 through TMO-006 |
| parse_fail | 10 | SUC-006, MIS-004 through MIS-008, TYP-002, TYP-003, TYP-006, NJS-003 |
| schema_fail | 5 | MIS-002, MIS-003, TYP-001, TYP-008, NJS-005 |
| provider_permission_or_rate_limit | 1 | NJS-001 |
| refusal | 1 | NJS-002 |
| **total failed** | **32** | |
| **passed** | **18** | |

Note: Run A uses `max_retries=0`. There is no retry path; each sample produces exactly one attempt.

### Failure Category Breakdown — Run B (max_retries=3, 50 samples)

Per-sample final failure categories (mutually exclusive, sums to 22 failed samples):

| category | count | representative samples |
|---|---|---|
| timeout | 13 | MIS-002, TYP-002, TYP-004, TYP-007, REF-003, NJS-004, TMO-001 through TMO-006 |
| schema_fail | 4 | TYP-005, NJS-005, and others |
| parse_fail | 2 | TYP-008, and others |
| provider_permission_or_rate_limit | 1 | NJS-003 |
| other (retry exhausted) | 2 | remaining failures |
| **total failed** | **22** | |
| **passed** | **28** | |

Retry-attempt-level exception events (may exceed failed sample count because instructor retries issue additional API calls per logical sample):
- `retry_exception_events`: not_measured (instructor internal retry events are not individually logged by the spike harness)

### Failure Category Breakdown — Tool Calling (8 samples)

Per-sample final failure categories (mutually exclusive, sums to 2 failed samples):

| category | count | samples |
|---|---|---|
| no_tool_calls_in_response | 2 | TC-001, TC-004 |
| **total failed** | **2** | |
| **passed** | **6** | |

### Retry Analysis

| metric | max_retries=0 (Run A) | max_retries=3 (Run B) |
|---|---|---|
| samples | 50 | 50 |
| passed | 18 | 28 |
| failed | 32 | 22 |
| success_rate | 36.0% | 56.0% |
| retry_recovered_count | N/A | 10 |
| retry_exhausted_count | N/A | 22 |
| provider_permission_or_rate_limit_count | 1 | 1 |

Retry recovered 10 samples that failed in Run A but passed in Run B. Timeout remains the dominant failure mode in both runs.

### Tool Calling

| field | value |
|---|---|
| tool_calling_mode_attempted | OpenAI tools API with tool_choice="auto" |
| tool_calling_supported_by_provider | true |
| tool_calling_success_rate | 75.0% (6/8) |
| tool_selection_accuracy | 75.0% (6/8) |
| argument_validation_success_rate | 100.0% (all tool calls that were made had valid arguments) |
| failed_samples | TC-001 (query_oa_leave_balance): no tool calls; TC-004 (query_oa_leave_balance): no tool calls |
| provider_permission_or_rate_limit_count | 0 |

### Fallback Strategy

```
instructor Mode.JSON with max_retries;
TOOLS mode blocked by provider thinking mode restriction;
json_schema -> json_object not tested (json_schema is supported but instructor Mode.JSON uses json_object);
no semantic repair fallback;
no output auto-fix fallback;
no business fallback.
```

## 7. Impact on Later Design

- impact_on_phase1: instructor does not meet acceptance criteria. P0-SPIKE-001 raw OpenAI SDK + Pydantic + Literal[...] (92.6%) is the recommended Phase 1 baseline. Instructor requires further follow-up only if separately tuned against internal vLLM with lower latency and thinking mode disabled.
- impact_on_runtime: No Runtime code is changed by this spike.
- impact_on_gateway: No Gateway code is changed by this spike.
- impact_on_identity: No identity model is changed by this spike.
- impact_on_trace: No Trace persistence is changed by this spike.
- impact_on_sdui: No SDUI contract is changed by this spike.
- impact_on_domain_010a: P0-DOMAIN-010a remains unblocked by P0-SPIKE-001. P0-SPIKE-002 does not change the recommendation.

## 8. Risks and Open Questions

- **Timeout sensitivity**: Primary failure mode is 30s timeouts on complex schemas. Internal vLLM latency expected to be significantly lower.
- **instructor TOOLS mode blocked**: Cannot validate instructor's native tool calling mode due to thinking mode restriction.
- **Tool calling accuracy**: OA leave balance queries failed 2/3 times. May need better prompt engineering or tool descriptions.
- **Provider permission/rate-limit classification**: 403 errors from this provider may indicate quota exhaustion, rate limiting, or permission denial. The sanitized error messages do not distinguish between these cases. Classified as `provider_permission_or_rate_limit`.
- **Internal endpoint validation**: Must be re-validated against internal vLLM/Qwen when available.

## 9. spike_code_disposition

```yaml
spike_code_disposition:
  disposable:
    - experiments/phase0/instructor_vllm/
  promoted_to_test_utils: []
  prohibited:
    - app/
```

## 10. v1.0.11 Spike Evidence Fields

| field | value |
|---|---|
| vLLM version | not applicable_provider_mode |
| model name | qwen3.6-27b |
| model digest / model path | not applicable_provider_mode |
| structured output mode | json_object (via instructor Mode.JSON) |
| tool choice mode | auto (raw OpenAI tools API); required (instructor default, failed due to thinking mode) |
| schema used | IntentResult, CapabilityRefResult, PlanDraftResult, ResponseEnvelopeResult (Pydantic v2 BaseModel with Literal[...] enums) |
| success sample count | 18 (max_retries=0); 28 (max_retries=3) |
| failure sample count | 32 (max_retries=0); 22 (max_retries=3) |
| malformed output sample | MIS-002: schema_fail (missing fields); TYP-004: timeout (complex schema) |
| retry strategy | max_retries=0 (single attempt) and max_retries=3 (instructor auto-retry). Retry recovers 10 samples. |
| fallback strategy | TOOLS mode blocked by thinking mode restriction; json_object via Mode.JSON is the effective mode; no semantic repair; no output auto-fix; no business fallback |
| whether suitable for Phase 1 | no: 56% (max_retries=3) < 80%, 75% (tool calling) < 80%. Raw OpenAI SDK + Pydantic (92.6%) is the recommended approach. |
| whether only supports mock usage | no — 108 logical samples with real instructor + Pydantic validation |
| known limitations | see section 8 |

## 11. Validation Evidence

### Environment Check

```text
LLM_BASE_URL: set
LLM_API_KEY: set
LLM_MODEL: qwen3.6-27b
env_ok
instructor_importable: true
instructor_version: 1.15.1
probing api...
api_reachable: true
json_schema_supported: true
json_object_supported: true
tool_calling_supported: true
check_env_ok
```

### Self-Check

```text
Self-check: PASSED
- 50 samples >= 50 minimum
- All output_types have SCHEMA_MAP entries
- All ENUM_FIELD_DEFINITIONS fields exist in Pydantic models
- All enum-bearing model fields are Literal[...] (not str)
- All 6 required categories represented
- All SCHEMA_MAP types have CRITICAL_FIELDS entries
```

### Run A: max_retries=0 (18/50 passed, 36.0%)

```text
Run A complete: 18/50 passed (36.0%)
provider_permission_or_rate_limit_count: 1
Per-sample final failure categories (sums to 32):
  timeout: 15
  parse_fail: 10
  schema_fail: 5
  provider_permission_or_rate_limit: 1
  refusal: 1
Per-category success rates:
  success: 13/15 (86.7%)
  missing_fields: 1/9 (11.1%)
  type_error: 1/8 (12.5%)
  refusal: 4/6 (66.7%)
  non_json: 2/6 (33.3%)
  timeout: 0/6 (0.0%)
```

### Run B: max_retries=3 (28/50 passed, 56.0%)

```text
Run B complete: 28/50 passed (56.0%)
provider_permission_or_rate_limit_count: 1
Per-sample final failure categories (sums to 22):
  timeout: 13
  schema_fail: 4
  parse_fail: 2
  provider_permission_or_rate_limit: 1
  other: 2
Per-category success rates:
  success: 15/15 (100.0%)
  missing_fields: 3/4 (75.0%)
  type_error: 4/8 (50.0%)
  refusal: 5/6 (83.3%)
  non_json: 4/6 (66.7%)
  timeout: 0/6 (0.0%)
```

### Tool Calling (6/8 passed, 75.0%)

```text
TC-001 (query_oa_leave_balance): FAIL (no tool calls)
TC-002 (query_u8_invoice_status): OK
TC-003 (query_hik_access_log): OK
TC-004 (query_oa_leave_balance): FAIL (no tool calls)
TC-005 (query_u8_invoice_status): OK
TC-006 (query_hik_access_log): OK
TC-007 (query_oa_leave_balance): OK
TC-008 (query_u8_invoice_status): OK
Per-sample final failure categories (sums to 2):
  no_tool_calls_in_response: 2
provider_permission_or_rate_limit_count: 0
argument_validation_success_rate: 100.0%
```

## 12. Phase 1 Recommendation

**Instructor is NOT recommended as the Phase 1 structured output implementation.**

1. **Structured output**: 36% (no retry) / 56% (with retry). Both below 80% threshold. Timeout is the primary failure mode (30s limit on provider API).
2. **Tool calling**: 75% success rate. Below 80% threshold. OA queries need better prompting.
3. **Raw OpenAI SDK + Pydantic + Literal[...]** (P0-SPIKE-001, 92.6%) is the recommended Phase 1 baseline.
4. **instructor requires further follow-up** only if separately tuned against internal vLLM where latency is lower and thinking mode can be disabled.

**Activation condition**: Rerun against internal vLLM/Qwen server when intranet access is available, with longer timeout, thinking mode disabled, and improved tool calling prompts.

## 13. Evidence Links

- logs: Environment check and spike execution output recorded in section 11
- logical_sample_count: 108 (Run A: 50 + Run B: 50 + tool calling: 8)
- actual_provider_request_count: not_measured
- JSON report: $TEMP/p0_spike_002_report.json
- per-phase result files: $TEMP/p0_spike_002_run_a.json, $TEMP/p0_spike_002_run_b.json, $TEMP/p0_spike_002_tool_calling.json
- commit / package: No commit; staged for review only
