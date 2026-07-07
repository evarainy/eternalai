# Blueprint Errata & Clarifications

This document records Phase 1 errata and clarifications that must be applied
when deriving later Phase 1 specifications from the frozen blueprint and MVP
spec. It does not modify the frozen source documents.

Authority order for downstream Phase 1 specification work:

```text
BLUEPRINT_ERRATA.md > PHASE1_TECHNICAL_BASELINE.md > MVP spec v1.0.11 > blueprint
```

## Formal Entries

### E-001: instructor 非基线

| Field | Value |
|---|---|
| id | E-001 |
| type | Erratum |
| title | instructor 非基线 |
| source anchors | `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md` §6.11 L1332; §12.1.3 L2505 |
| original / problem | The frozen blueprint says Phase 1 defaults to `OpenAI SDK + instructor + Pydantic v2 Schema`. |
| correction / clarification | Phase 1 structured-output baseline is raw OpenAI SDK with raw JSON mode and Pydantic v2 validation. `instructor`, PydanticAI, and other wrapper libraries are not in the baseline. |
| authority / rationale | `docs/phase0/PHASE1_TECHNICAL_BASELINE.md` §2 L11-L22 records the accepted Phase 1 baseline: raw OpenAI SDK, `response_format: {"type": "json_object"}`, and no wrapper library. This erratum must be applied by `P1-SPEC-001` so `PHASE1_SPEC.md` does not inherit the obsolete instructor decision from the frozen blueprint. |

### E-002: ARQ 层级澄清

| Field | Value |
|---|---|
| id | E-002 |
| type | Clarification |
| title | ARQ 层级澄清 |
| source anchors | `docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md` §12.1.4 L2517-L2518; §12.1.4 L2525-L2526; upgrade route L2580-L2585 |
| original / problem | The blueprint mentions Redis + ARQ in the asynchronous task stack, which can be misread as a mandatory Phase 1 installation requirement. |
| correction / clarification | ARQ is an L1 candidate for department pilot or more reliable asynchronous execution needs. Phase 1 mainline L0 remains FastAPI BackgroundTasks / in-process executor for single-machine, small-scale pilot usage. |
| authority / rationale | The blueprint itself separates L0 and L1: L0 is BackgroundTasks / in-process executor, while Redis + ARQ is an L1 candidate. The upgrade route also says ARQ is introduced when a single-machine pilot enters department-pilot scale or stronger async reliability is needed. This clarification prevents `P1-SPEC-001` from treating ARQ as Phase 1 mandatory infrastructure. |

### E-003: adapter_error_mapped 错位

| Field | Value |
|---|---|
| id | E-003 |
| type | Erratum |
| title | adapter_error_mapped 错位 |
| source anchors | `docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md` §8.6.7 L878-L903; §12.4.1 trace matrix L3490; `app/ports/trace.py` L25 |
| original / problem | MVP spec §8.6.7 lists `TraceEvent.event_type` values but omits `adapter_error_mapped`; the trace matrix later references `adapter_error_mapped` for the `adapter_timeout` negative path. |
| correction / clarification | The frozen port is correct: `app/ports/trace.py` includes `adapter_error_mapped`. The erratum points to the MVP spec §8.6.7 omission and does not require any port change. |
| authority / rationale | `app/ports/trace.py` is the current frozen port contract and already contains the event type. `P1-SPEC-001` must inherit the port-backed event list and treat the omission as a spec-listing erratum, not an implementation defect. |
