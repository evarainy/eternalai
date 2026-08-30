"""Static and observed credential-property guards for Runtime output contracts."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.infra.adapters.oa.contracts import (
    OAPendingWorkflowCollection,
    OASystemMessageCollection,
)
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.capability_gateway import ExecutionResult
from app.ports.capability_registry import CapabilitySpec
from app.runtime.models import CapabilityRef
from app.runtime.response_projection import (
    ProjectionContractSnapshot,
    schema_has_credential_property,
)
from app.runtime.runtime import RuntimeImpl
from tests.architecture.runtime_schema_observer import (
    INTEGRATION_SENTINEL_NODEID,
    ObservationSurface,
    ObserverState,
    ObserverSummary,
    SchemaObservation,
    judge_summary,
    make_observing_wrapper,
    merge_worker_payloads,
    observe_schema,
    summary_to_worker_payload,
)
from tests.runtime.registry_fakes import (
    VALID_RUNTIME_OUTPUT_SCHEMAS,
    active_capability,
    runtime_output_schema,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _golden_output_contracts() -> list[tuple[str, dict[str, Any]]]:
    contracts: list[tuple[str, dict[str, Any]]] = []
    fixture_root = _REPO_ROOT / "tests" / "golden_tasks" / "fixtures"
    for path in sorted(fixture_root.glob("GT-*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        for capability in fixture["given"]["registered_capabilities"]:
            schema = capability.get("output_schema")
            if isinstance(schema, dict) and schema:
                contracts.append((f"{path.stem}:{capability['capability_id']}", schema))
    return contracts


def _static_output_contracts() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "production:oa.list_pending_workflows",
            OAPendingWorkflowCollection.model_json_schema(),
        ),
        (
            "production:oa.list_system_messages",
            OASystemMessageCollection.model_json_schema(),
        ),
        *((f"golden:{name}", schema) for name, schema in _golden_output_contracts()),
        *(
            (f"runtime-registry:{name}", schema)
            for name, schema in sorted(VALID_RUNTIME_OUTPUT_SCHEMAS.items())
        ),
    ]


def _safe_schema() -> dict[str, Any]:
    return runtime_output_schema("registry_fakes.default")


def _marker_schema() -> dict[str, Any]:
    schema = _safe_schema()
    schema["properties"]["SYNTHETIC_password_property"] = {"type": "string"}
    return schema


def _summary(
    *observations: SchemaObservation,
    sentinel_collected: bool = True,
) -> ObserverSummary:
    return ObserverSummary(
        wrapper_hits=1,
        observations=tuple(observations),
        sentinel_collected=sentinel_collected,
        wrapper_intact=True,
    )


def _safe_sentinel_observation(
    surface: ObservationSurface,
    *,
    projection_consumed: bool = True,
) -> SchemaObservation:
    return observe_schema(
        INTEGRATION_SENTINEL_NODEID,
        surface,
        _safe_schema(),
        projection_consumed=projection_consumed,
    )


def _empty_observation(*, projection_consumed: bool) -> SchemaObservation:
    return observe_schema(
        "SYNTHETIC_EMPTY_SCHEMA_NODEID",
        "capability",
        {},
        projection_consumed=projection_consumed,
    )


def _sentinel_owner() -> CapabilitySpec:
    """Return the owner through a function so construction shape is irrelevant."""

    return active_capability(
        "SYNTHETIC.p4.runtime-schema-observer",
        output_schema=_safe_schema(),
    )


def _build_completed_envelope(capability: CapabilitySpec) -> Any:
    runtime = RuntimeImpl.__new__(RuntimeImpl)
    runtime._response_builder = ResponseEnvelopeBuilder()
    return runtime._build_envelope(
        "SYNTHETIC_RESPONSE_ID",
        "SYNTHETIC_TASK_ID",
        "SYNTHETIC_SESSION_ID",
        ExecutionResult(
            status="completed",
            data={"result": "SYNTHETIC_SAFE_RESULT"},
            trace_id="SYNTHETIC_EXECUTION_TRACE_ID",
        ),
        "SYNTHETIC_TRACE_ID",
        CapabilityRef(capability_id=capability.capability_id),
        capability=capability,
        projection_snapshot=ProjectionContractSnapshot.from_capability(capability),
    )


def test_valid_output_contract_inventory_has_no_credential_properties() -> None:
    contracts = _static_output_contracts()
    offenders = [
        name for name, schema in contracts if schema_has_credential_property(schema)
    ]

    assert offenders == []
    assert len(_golden_output_contracts()) > 0
    assert len(VALID_RUNTIME_OUTPUT_SCHEMAS) > 0
    assert {name for name, _schema in contracts if name.startswith("production:")} == {
        "production:oa.list_pending_workflows",
        "production:oa.list_system_messages",
    }


def test_deliberately_invalid_output_contract_is_detected() -> None:
    invalid = {
        "type": "object",
        "properties": {
            "safe": {"type": "string"},
            "nested": {
                "type": "object",
                "properties": {"SYNTHETIC_refresh_token": {"type": "string"}},
            },
        },
    }

    assert schema_has_credential_property(invalid) is True


def test_every_valid_runtime_fake_schema_detects_an_injected_credential_property() -> (
    None
):
    undetected: list[str] = []
    for name, schema in sorted(VALID_RUNTIME_OUTPUT_SCHEMAS.items()):
        mutated = deepcopy(schema)
        properties = mutated.setdefault("properties", {})
        properties["SYNTHETIC_password_property"] = {"type": "string"}
        if not schema_has_credential_property(mutated):
            undetected.append(name)

    assert undetected == []


def test_observer_accepts_safe_observations() -> None:
    observations = (
        _safe_sentinel_observation("capability"),
        _safe_sentinel_observation("snapshot"),
    )

    verdict = judge_summary(_summary(*observations))

    assert verdict.authoritative is True
    assert verdict.passed is True
    assert verdict.failures == ()


def test_observer_accepts_empty_nonprojected_with_safe_sentinel() -> None:
    summary = _summary(
        _safe_sentinel_observation("snapshot"),
        _empty_observation(projection_consumed=False),
    )

    verdict = judge_summary(summary)

    assert summary.reason_count("marker") == 0
    assert summary.empty_projected == 0
    assert summary.empty_nonprojected == 1
    assert verdict.passed is True
    assert verdict.failures == ()


def test_observer_rejects_empty_projected_with_same_safe_sentinel() -> None:
    summary = _summary(
        _safe_sentinel_observation("snapshot"),
        _empty_observation(projection_consumed=True),
    )

    verdict = judge_summary(summary)

    assert summary.reason_count("marker") == 0
    assert summary.empty_projected == 1
    assert summary.empty_nonprojected == 0
    assert verdict.passed is False
    assert verdict.failures == ("empty_projected",)


def test_observer_rejects_zero_observations() -> None:
    verdict = judge_summary(_summary())

    assert verdict.passed is False
    assert "zero_observations" in verdict.failures
    assert "sentinel_not_observed" in verdict.failures


def test_observer_rejects_capability_surface_marker() -> None:
    marker = observe_schema(
        INTEGRATION_SENTINEL_NODEID,
        "capability",
        _marker_schema(),
        projection_consumed=True,
    )
    safe_snapshot = _safe_sentinel_observation("snapshot")

    verdict = judge_summary(_summary(marker, safe_snapshot))

    assert marker.reason == "marker"
    assert verdict.passed is False
    assert "marker" in verdict.failures


def test_observer_rejects_snapshot_surface_marker() -> None:
    safe_capability = _safe_sentinel_observation("capability")
    marker = observe_schema(
        INTEGRATION_SENTINEL_NODEID,
        "snapshot",
        _marker_schema(),
        projection_consumed=True,
    )

    verdict = judge_summary(_summary(safe_capability, marker))

    assert marker.reason == "marker"
    assert verdict.passed is False
    assert "marker" in verdict.failures


def test_observing_wrapper_calls_original_once_and_preserves_return() -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    state = ObserverState()
    capability = _sentinel_owner()
    snapshot = ProjectionContractSnapshot.from_capability(capability)

    def original(
        instance: object,
        *args: object,
        **kwargs: object,
    ) -> str:
        del instance
        calls.append((args, kwargs))
        return "SYNTHETIC_ORIGINAL_RETURN"

    wrapper = make_observing_wrapper(
        original,
        state,
        nodeid_provider=lambda: "SYNTHETIC_NODEID",
    )

    result = wrapper(
        object(),
        "SYNTHETIC_ARGUMENT",
        exec_result=ExecutionResult(
            status="completed",
            trace_id="SYNTHETIC_EXECUTION_TRACE_ID",
        ),
        capability=capability,
        projection_snapshot=snapshot,
    )

    assert result == "SYNTHETIC_ORIGINAL_RETURN"
    assert len(calls) == 1
    assert state.summary().wrapper_hits == 1
    assert [item.surface for item in state.summary().observations] == [
        "capability",
        "snapshot",
    ]
    assert all(
        item.projection_consumed for item in state.summary().observations
    )


def test_observing_wrapper_calls_original_once_and_preserves_exception() -> None:
    calls = 0
    state = ObserverState()
    original_error = RuntimeError("SYNTHETIC_ORIGINAL_ERROR")

    def original(instance: object) -> None:
        nonlocal calls
        del instance
        calls += 1
        raise original_error

    wrapper = make_observing_wrapper(
        original,
        state,
        nodeid_provider=lambda: "SYNTHETIC_NODEID",
    )

    with pytest.raises(RuntimeError) as caught:
        wrapper(object())

    assert caught.value is original_error
    assert calls == 1
    assert state.summary().wrapper_hits == 1


def test_observing_wrapper_derives_projection_only_from_execution_status() -> None:
    state = ObserverState()
    capability = _sentinel_owner()
    snapshot = ProjectionContractSnapshot.from_capability(capability)

    def original(instance: object, *args: object, **kwargs: object) -> None:
        del instance, args, kwargs

    wrapper = make_observing_wrapper(
        original,
        state,
        nodeid_provider=lambda: "SYNTHETIC_completed_Golden_snapshot_ENV",
    )
    for status in ("completed", "waiting_user"):
        wrapper(
            object(),
            "SYNTHETIC_RESPONSE_ID",
            "SYNTHETIC_TASK_ID",
            "SYNTHETIC_SESSION_ID",
            ExecutionResult(
                status=status,
                trace_id="SYNTHETIC_EXECUTION_TRACE_ID",
            ),
            capability=capability,
            projection_snapshot=snapshot,
        )

    assert [
        observation.projection_consumed
        for observation in state.summary().observations
    ] == [True, True, False, False]


def test_observing_wrapper_rejects_status_outside_execution_status() -> None:
    state = ObserverState()
    capability = _sentinel_owner()

    def original(instance: object, *args: object, **kwargs: object) -> None:
        del instance, args, kwargs

    wrapper = make_observing_wrapper(original, state)
    invalid_result = ExecutionResult.model_construct(
        status="SYNTHETIC_INVALID_STATUS",
        trace_id="SYNTHETIC_EXECUTION_TRACE_ID",
    )

    wrapper(
        object(),
        "SYNTHETIC_RESPONSE_ID",
        "SYNTHETIC_TASK_ID",
        "SYNTHETIC_SESSION_ID",
        invalid_result,
        capability=capability,
    )
    summary = state.summary()
    verdict = judge_summary(summary)

    assert summary.unreadable_statuses == 1
    assert summary.observations == ()
    assert "unreadable" in verdict.failures


def test_runtime_schema_observer_integration_sentinel() -> None:
    envelope = _build_completed_envelope(_sentinel_owner())

    assert envelope.data == {"result": "SYNTHETIC_SAFE_RESULT"}


def test_worker_merge_accepts_two_safe_workers() -> None:
    first = _summary(_safe_sentinel_observation("capability"))
    second = _summary(
        observe_schema(
            "SYNTHETIC_WORKER_NODEID",
            "snapshot",
            _safe_schema(),
            projection_consumed=False,
        ),
        sentinel_collected=False,
    )

    merged = merge_worker_payloads(
        [summary_to_worker_payload(first), summary_to_worker_payload(second)]
    )
    verdict = judge_summary(merged)

    assert merged.worker_payloads == 2
    assert merged.wrapper_hits == 2
    assert verdict.passed is True


def test_worker_merge_rejects_one_offender_worker() -> None:
    safe = _summary(_safe_sentinel_observation("capability"))
    offender = _summary(
        observe_schema(
            "SYNTHETIC_WORKER_NODEID",
            "snapshot",
            _marker_schema(),
            projection_consumed=False,
        ),
        sentinel_collected=False,
    )

    merged = merge_worker_payloads(
        [summary_to_worker_payload(safe), summary_to_worker_payload(offender)]
    )
    verdict = judge_summary(merged)

    assert verdict.passed is False
    assert "marker" in verdict.failures


def test_worker_merge_rejects_all_empty_workers() -> None:
    first = _summary(sentinel_collected=False)
    second = _summary(sentinel_collected=False)

    merged = merge_worker_payloads(
        [summary_to_worker_payload(first), summary_to_worker_payload(second)]
    )
    verdict = judge_summary(merged)

    assert verdict.passed is False
    assert "zero_observations" in verdict.failures
    assert "sentinel_not_collected" in verdict.failures


def test_worker_merge_rejects_collected_but_unobserved_sentinel() -> None:
    collected_only = _summary()
    other = _summary(
        observe_schema(
            "SYNTHETIC_WORKER_NODEID",
            "capability",
            _safe_schema(),
            projection_consumed=False,
        ),
        sentinel_collected=False,
    )

    merged = merge_worker_payloads(
        [summary_to_worker_payload(collected_only), summary_to_worker_payload(other)]
    )
    verdict = judge_summary(merged)

    assert merged.sentinel_collected is True
    assert merged.sentinel_observed is False
    assert verdict.passed is False
    assert "sentinel_not_observed" in verdict.failures


def test_worker_merge_rejects_missing_worker_payload() -> None:
    safe = _summary(_safe_sentinel_observation("capability"))

    merged = merge_worker_payloads(
        [summary_to_worker_payload(safe)],
        missing_worker_payloads=("missing_worker_payload",),
    )
    verdict = judge_summary(merged)

    assert verdict.passed is False
    assert "missing_worker_payload" in verdict.failures


@pytest.mark.parametrize(
    "malformation",
    ("missing_projection_consumed", "wrong_type", "old_version"),
)
def test_worker_merge_rejects_malformed_projection_payload(
    malformation: str,
) -> None:
    payload = summary_to_worker_payload(
        _summary(
            _safe_sentinel_observation("snapshot"),
            _empty_observation(projection_consumed=False),
        )
    )
    raw_observations = payload["observations"]
    assert isinstance(raw_observations, list)
    first = raw_observations[0]
    assert isinstance(first, dict)
    if malformation == "missing_projection_consumed":
        first.pop("projection_consumed")
    elif malformation == "wrong_type":
        first["projection_consumed"] = 0
    else:
        payload["version"] = 1

    merged = merge_worker_payloads([payload])
    verdict = judge_summary(merged)

    assert merged.missing_worker_payloads == ("malformed_worker_payload",)
    assert merged.empty_nonprojected == 0
    assert verdict.passed is False
    assert "missing_worker_payload" in verdict.failures
