# ADR-P0-XXX — <Decision Title>

- status: proposed | accepted | rejected | superseded
- date: YYYY-MM-DD
- task_id: EXAMPLE_TASK_ID
- owner: Codex / Claude Code / Human Reviewer
- related_spec: docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md

## 1. Context

说明本 ADR 对应的 Phase 0 验证任务、背景、约束和为什么需要现在做决定。

## 2. Question to Answer

本 ADR 必须回答的具体问题，例如：

- 该技术是否通过 Phase 0 验证？
- 是否允许进入 Phase 1？
- 需要什么限制条件？

## 3. Validation Setup

- environment:
- hardware:
- software_versions:
- dependency_source: internal mirror | offline cache | unknown
- test_dataset:
- commands_run:

## 4. Acceptance Criteria Result

| criterion | result | evidence |
|---|---|---|
| criterion 1 | passed / failed | log / command / artifact |
| criterion 2 | passed / failed | log / command / artifact |

## 5. Decision

decision: passed | failed | conditionally_passed

说明是否进入 Phase 1 或后续阶段，以及限制条件。

## 6. Impact on Later Design

- impact_on_phase1:
- impact_on_runtime:
- impact_on_gateway:
- impact_on_identity:
- impact_on_trace:
- impact_on_sdui:

## 7. Risks and Open Questions

- risk 1:
- risk 2:

## 8. spike_code_disposition

```yaml
spike_code_disposition:
  disposable:
    - experiments/phase0/<name>/
  promoted_to_test_utils:
    - tests/utils/<optional>
  prohibited:
    - app/
```

## 9. Evidence Links

- logs:
- screenshots:
- test output:
- commit / package:



## 10. v1.0.11 Spike Evidence Fields

For Qwen / vLLM / structured output / PydanticAI Spike ADRs, include:

- vLLM version
- model name
- model digest / model path
- structured output mode
- tool choice mode
- schema used
- success sample count
- failure sample count
- malformed output sample
- retry strategy
- fallback strategy
- whether result is suitable for Phase 1
- whether result only supports mock usage
- known limitations

For OA / U8 / Hik reconnaissance ADRs, include:

1. system_name
2. version_or_assumed_version
3. auth_mode
4. token/session lifecycle
5. user identity mapping method
6. permission source of truth
7. read API availability
8. write API availability
9. callback/webhook availability
10. rate limit / concurrency limit
11. audit log availability
12. sandbox/test environment availability
13. irreversible operation list
14. Phase 0 allowed operations
15. Phase 0 forbidden operations
16. open questions
17. blocking risks
18. recommendation: mock_only | can_build_adapter_later | needs_vendor_confirmation | not_suitable
