"""Pytest-only observation of Runtime output schemas at the envelope choke point.

The observer stores only structural metadata: test node id, surface, canonical
digest, a fixed reason code, and whether projection consumed the contract.
Schema contents, values, and exception text never enter its records or worker
payloads.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from functools import wraps
from threading import Lock
from typing import Any, Literal, cast, get_args

import pytest

from app.ports.capability_gateway import ExecutionStatus
from app.runtime.response_projection import (
    canonical_schema_digest,
    schema_has_credential_property,
)

ObservationSurface = Literal["capability", "snapshot"]
ObservationReason = Literal["ok", "empty", "unreadable", "marker"]

INTEGRATION_SENTINEL_NODEID = (
    "tests/architecture/test_response_output_contracts.py::"
    "test_runtime_schema_observer_integration_sentinel"
)
WORKER_PAYLOAD_KEY = "p4_runtime_schema_observer"

_PAYLOAD_VERSION = 2
_EXECUTION_STATUSES = frozenset(get_args(ExecutionStatus))
_OUTSIDE_TEST_NODEID = "<outside-test>"
_CURRENT_NODEID: ContextVar[str] = ContextVar(
    "p4_runtime_schema_observer_nodeid",
    default=_OUTSIDE_TEST_NODEID,
)
_STATE_ATTR = "_p4_runtime_schema_observer_state"
_RUNTIME_ATTR = "_p4_runtime_schema_observer_runtime"
_ORIGINAL_ATTR = "_p4_runtime_schema_observer_original"
_WRAPPER_ATTR = "_p4_runtime_schema_observer_wrapper"
_WORKER_PAYLOADS_ATTR = "_p4_runtime_schema_observer_worker_payloads"
_MISSING_WORKERS_ATTR = "_p4_runtime_schema_observer_missing_workers"


@dataclass(frozen=True, slots=True)
class SchemaObservation:
    nodeid: str
    surface: ObservationSurface
    digest: str
    reason: ObservationReason
    projection_consumed: bool


@dataclass(frozen=True, slots=True)
class ObserverSummary:
    wrapper_hits: int
    observations: tuple[SchemaObservation, ...]
    sentinel_collected: bool
    wrapper_intact: bool
    unreadable_statuses: int = 0
    missing_worker_payloads: tuple[str, ...] = ()
    worker_payloads: int = 0

    @property
    def sentinel_observed(self) -> bool:
        return any(
            observation.nodeid == INTEGRATION_SENTINEL_NODEID
            for observation in self.observations
        )

    def reason_count(self, reason: ObservationReason) -> int:
        return sum(observation.reason == reason for observation in self.observations)

    @property
    def empty_projected(self) -> int:
        return sum(
            observation.reason == "empty" and observation.projection_consumed
            for observation in self.observations
        )

    @property
    def empty_nonprojected(self) -> int:
        return sum(
            observation.reason == "empty" and not observation.projection_consumed
            for observation in self.observations
        )

    @property
    def unreadable(self) -> int:
        return self.reason_count("unreadable") + self.unreadable_statuses


@dataclass(frozen=True, slots=True)
class ObserverVerdict:
    authoritative: bool
    passed: bool
    failures: tuple[str, ...]


@dataclass(slots=True)
class ObserverState:
    wrapper_hits: int = 0
    observations: list[SchemaObservation] = field(default_factory=list)
    sentinel_collected: bool = False
    wrapper_intact: bool = True
    unreadable_statuses: int = 0
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def note_wrapper_hit(self) -> None:
        with self._lock:
            self.wrapper_hits += 1

    def note_schema(
        self,
        nodeid: str,
        surface: ObservationSurface,
        schema: object,
        *,
        projection_consumed: bool,
    ) -> None:
        observation = observe_schema(
            nodeid,
            surface,
            schema,
            projection_consumed=projection_consumed,
        )
        with self._lock:
            self.observations.append(observation)

    def note_unreadable(
        self,
        nodeid: str,
        surface: ObservationSurface,
        *,
        projection_consumed: bool,
    ) -> None:
        with self._lock:
            self.observations.append(
                _unreadable_observation(
                    nodeid,
                    surface,
                    projection_consumed=projection_consumed,
                )
            )

    def note_unreadable_status(self) -> None:
        with self._lock:
            self.unreadable_statuses += 1

    def note_sentinel_collected(self) -> None:
        with self._lock:
            self.sentinel_collected = True

    def note_wrapper_intact(self, intact: bool) -> None:
        with self._lock:
            self.wrapper_intact = intact

    def summary(self) -> ObserverSummary:
        with self._lock:
            return ObserverSummary(
                wrapper_hits=self.wrapper_hits,
                observations=tuple(self.observations),
                sentinel_collected=self.sentinel_collected,
                wrapper_intact=self.wrapper_intact,
                unreadable_statuses=self.unreadable_statuses,
            )


def _unreadable_observation(
    nodeid: str,
    surface: ObservationSurface,
    *,
    projection_consumed: bool,
) -> SchemaObservation:
    return SchemaObservation(
        nodeid=nodeid,
        surface=surface,
        digest="unavailable",
        reason="unreadable",
        projection_consumed=projection_consumed,
    )


def observe_schema(
    nodeid: str,
    surface: ObservationSurface,
    schema: object,
    *,
    projection_consumed: bool,
) -> SchemaObservation:
    """Reduce a schema to a value-free observation without leaking failures."""

    if not isinstance(schema, Mapping):
        return _unreadable_observation(
            nodeid,
            surface,
            projection_consumed=projection_consumed,
        )
    try:
        digest = canonical_schema_digest(schema)
        if not schema:
            reason: ObservationReason = "empty"
        elif schema_has_credential_property(schema):
            reason = "marker"
        else:
            reason = "ok"
    except Exception:
        return _unreadable_observation(
            nodeid,
            surface,
            projection_consumed=projection_consumed,
        )
    return SchemaObservation(
        nodeid=nodeid,
        surface=surface,
        digest=digest,
        reason=reason,
        projection_consumed=projection_consumed,
    )


def judge_summary(summary: ObserverSummary) -> ObserverVerdict:
    failures: list[str] = []
    if not summary.wrapper_intact:
        failures.append("wrapper_replaced")
    if summary.missing_worker_payloads:
        failures.append("missing_worker_payload")
    if summary.reason_count("marker"):
        failures.append("marker")
    if summary.empty_projected:
        failures.append("empty_projected")
    if summary.unreadable:
        failures.append("unreadable")
    if not summary.observations:
        failures.append("zero_observations")
    if not summary.sentinel_collected:
        failures.append("sentinel_not_collected")
    elif not summary.sentinel_observed:
        failures.append("sentinel_not_observed")

    authoritative = summary.sentinel_collected
    return ObserverVerdict(
        authoritative=authoritative,
        passed=authoritative and not failures,
        failures=tuple(failures),
    )


def session_should_fail(verdict: ObserverVerdict) -> bool:
    tolerated_without_sentinel = {"sentinel_not_collected", "zero_observations"}
    return any(failure not in tolerated_without_sentinel for failure in verdict.failures)


def summary_to_worker_payload(summary: ObserverSummary) -> dict[str, object]:
    return {
        "version": _PAYLOAD_VERSION,
        "wrapper_hits": summary.wrapper_hits,
        "observations": [
            {
                "nodeid": observation.nodeid,
                "surface": observation.surface,
                "digest": observation.digest,
                "reason": observation.reason,
                "projection_consumed": observation.projection_consumed,
            }
            for observation in summary.observations
        ],
        "sentinel_collected": summary.sentinel_collected,
        "wrapper_intact": summary.wrapper_intact,
        "unreadable_statuses": summary.unreadable_statuses,
    }


def _summary_from_worker_payload(payload: Mapping[str, object]) -> ObserverSummary:
    if payload.get("version") != _PAYLOAD_VERSION:
        raise ValueError("unsupported observer payload")
    wrapper_hits = payload.get("wrapper_hits")
    sentinel_collected = payload.get("sentinel_collected")
    wrapper_intact = payload.get("wrapper_intact")
    unreadable_statuses = payload.get("unreadable_statuses")
    raw_observations = payload.get("observations")
    if (
        not isinstance(wrapper_hits, int)
        or isinstance(wrapper_hits, bool)
        or wrapper_hits < 0
        or not isinstance(sentinel_collected, bool)
        or not isinstance(wrapper_intact, bool)
        or not isinstance(unreadable_statuses, int)
        or isinstance(unreadable_statuses, bool)
        or unreadable_statuses < 0
        or not isinstance(raw_observations, list)
    ):
        raise ValueError("invalid observer payload")

    observations: list[SchemaObservation] = []
    for raw in raw_observations:
        if not isinstance(raw, Mapping):
            raise ValueError("invalid observer observation")
        nodeid = raw.get("nodeid")
        surface = raw.get("surface")
        digest = raw.get("digest")
        reason = raw.get("reason")
        projection_consumed = raw.get("projection_consumed")
        if (
            not isinstance(nodeid, str)
            or surface not in {"capability", "snapshot"}
            or not isinstance(digest, str)
            or reason not in {"ok", "empty", "unreadable", "marker"}
            or not isinstance(projection_consumed, bool)
        ):
            raise ValueError("invalid observer observation")
        observations.append(
            SchemaObservation(
                nodeid=nodeid,
                surface=cast(ObservationSurface, surface),
                digest=digest,
                reason=cast(ObservationReason, reason),
                projection_consumed=projection_consumed,
            )
        )
    return ObserverSummary(
        wrapper_hits=wrapper_hits,
        observations=tuple(observations),
        sentinel_collected=sentinel_collected,
        wrapper_intact=wrapper_intact,
        unreadable_statuses=unreadable_statuses,
        worker_payloads=1,
    )


def merge_worker_payloads(
    payloads: Sequence[Mapping[str, object]],
    *,
    missing_worker_payloads: Sequence[str] = (),
) -> ObserverSummary:
    """Merge JSON-safe xdist summaries without sharing process-global state."""

    summaries: list[ObserverSummary] = []
    missing = list(missing_worker_payloads)
    for payload in payloads:
        try:
            summaries.append(_summary_from_worker_payload(payload))
        except (TypeError, ValueError):
            missing.append("malformed_worker_payload")

    return ObserverSummary(
        wrapper_hits=sum(summary.wrapper_hits for summary in summaries),
        observations=tuple(
            observation
            for summary in summaries
            for observation in summary.observations
        ),
        sentinel_collected=any(summary.sentinel_collected for summary in summaries),
        wrapper_intact=all(summary.wrapper_intact for summary in summaries),
        unreadable_statuses=sum(summary.unreadable_statuses for summary in summaries),
        missing_worker_payloads=tuple(missing),
        worker_payloads=len(summaries),
    )


def make_observing_wrapper(
    original: Callable[..., Any],
    state: ObserverState,
    nodeid_provider: Callable[[], str] = _CURRENT_NODEID.get,
) -> Callable[..., Any]:
    """Build a transparent Runtime wrapper that calls the original exactly once."""

    @wraps(original)
    def observing_wrapper(self: object, *args: object, **kwargs: object) -> Any:
        state.note_wrapper_hit()
        nodeid = nodeid_provider()

        exec_result = kwargs.get("exec_result")
        if exec_result is None and len(args) > 3:
            exec_result = args[3]
        try:
            status = getattr(exec_result, "status")
        except Exception:
            state.note_unreadable_status()
            return original(self, *args, **kwargs)
        if not isinstance(status, str) or status not in _EXECUTION_STATUSES:
            state.note_unreadable_status()
            return original(self, *args, **kwargs)
        projection_consumed = status == "completed"

        capability = kwargs.get("capability")
        if capability is not None:
            try:
                output_schema = getattr(capability, "output_schema")
            except Exception:
                state.note_unreadable(
                    nodeid,
                    "capability",
                    projection_consumed=projection_consumed,
                )
            else:
                state.note_schema(
                    nodeid,
                    "capability",
                    output_schema,
                    projection_consumed=projection_consumed,
                )

        projection_snapshot = kwargs.get("projection_snapshot")
        if projection_snapshot is not None:
            try:
                snapshot_schema = getattr(
                    projection_snapshot,
                    "load_output_schema",
                )()
            except Exception:
                state.note_unreadable(
                    nodeid,
                    "snapshot",
                    projection_consumed=projection_consumed,
                )
            else:
                state.note_schema(
                    nodeid,
                    "snapshot",
                    snapshot_schema,
                    projection_consumed=projection_consumed,
                )

        return original(self, *args, **kwargs)

    return observing_wrapper


def _is_worker(config: Any) -> bool:
    return hasattr(config, "workerinput")


def _is_xdist_controller(config: Any) -> bool:
    return not _is_worker(config) and config.pluginmanager.hasplugin("xdist")


def _restore_original(config: Any) -> None:
    runtime = getattr(config, _RUNTIME_ATTR, None)
    original = getattr(config, _ORIGINAL_ATTR, None)
    if runtime is not None and original is not None:
        runtime._build_envelope = original


def _safe_summary_line(summary: ObserverSummary, verdict: ObserverVerdict) -> str:
    if verdict.authoritative and verdict.passed:
        status = "PASS"
    elif session_should_fail(verdict):
        status = "FAIL"
    else:
        status = "NOT-RUN"
    mode = "authoritative" if verdict.authoritative else "not-authoritative"
    return (
        "P4_RUNTIME_SCHEMA_OBSERVER "
        f"mode={mode} status={status} wrapper_hits={summary.wrapper_hits} "
        f"observations={len(summary.observations)} "
        f"offenders={summary.reason_count('marker')} "
        f"empty={summary.reason_count('empty')} "
        f"empty_projected={summary.empty_projected} "
        f"empty_nonprojected={summary.empty_nonprojected} "
        f"unreadable={summary.unreadable} "
        f"sentinel_collected={int(summary.sentinel_collected)} "
        f"sentinel_observed={int(summary.sentinel_observed)} "
        f"missing_worker_payload={len(summary.missing_worker_payloads)}"
    )


# The functions below are the pytest adapter.  Core state, judgement, wrapper,
# and worker merge above do not require pytest objects and are directly tested.
def pytest_configure(config: Any) -> None:
    if hasattr(config, _STATE_ATTR):
        return
    from app.runtime.runtime import RuntimeImpl

    state = ObserverState()
    original = RuntimeImpl._build_envelope
    wrapper = make_observing_wrapper(original, state)
    RuntimeImpl._build_envelope = wrapper
    setattr(config, _STATE_ATTR, state)
    setattr(config, _RUNTIME_ATTR, RuntimeImpl)
    setattr(config, _ORIGINAL_ATTR, original)
    setattr(config, _WRAPPER_ATTR, wrapper)
    setattr(config, _WORKER_PAYLOADS_ATTR, [])
    setattr(config, _MISSING_WORKERS_ATTR, [])


def pytest_collection_modifyitems(config: Any, items: Sequence[Any]) -> None:
    state = cast(ObserverState, getattr(config, _STATE_ATTR))
    if any(item.nodeid == INTEGRATION_SENTINEL_NODEID for item in items):
        state.note_sentinel_collected()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: Any, nextitem: Any) -> Any:
    del nextitem
    token = _CURRENT_NODEID.set(item.nodeid)
    try:
        yield
    finally:
        _CURRENT_NODEID.reset(token)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: object) -> None:
    config = node.config
    if not _is_xdist_controller(config):
        return
    payloads = cast(list[Mapping[str, object]], getattr(config, _WORKER_PAYLOADS_ATTR))
    missing = cast(list[str], getattr(config, _MISSING_WORKERS_ATTR))
    workeroutput = getattr(node, "workeroutput", None)
    payload = workeroutput.get(WORKER_PAYLOAD_KEY) if isinstance(workeroutput, Mapping) else None
    if error is not None or not isinstance(payload, Mapping):
        missing.append("missing_worker_payload")
        return
    payloads.append(cast(Mapping[str, object], payload))


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    del exitstatus
    config = session.config
    state = cast(ObserverState, getattr(config, _STATE_ATTR))
    runtime = getattr(config, _RUNTIME_ATTR)
    wrapper = getattr(config, _WRAPPER_ATTR)
    state.note_wrapper_intact(runtime._build_envelope is wrapper)

    try:
        if _is_worker(config):
            config.workeroutput[WORKER_PAYLOAD_KEY] = summary_to_worker_payload(
                state.summary()
            )
            return

        if _is_xdist_controller(config):
            payloads = cast(
                list[Mapping[str, object]],
                getattr(config, _WORKER_PAYLOADS_ATTR),
            )
            missing = cast(list[str], getattr(config, _MISSING_WORKERS_ATTR))
            summary = merge_worker_payloads(
                payloads,
                missing_worker_payloads=missing,
            )
            summary = replace(
                summary,
                wrapper_intact=summary.wrapper_intact and state.summary().wrapper_intact,
            )
        else:
            summary = state.summary()

        verdict = judge_summary(summary)
        print(_safe_summary_line(summary, verdict))
        if session_should_fail(verdict):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
    finally:
        _restore_original(config)


def pytest_unconfigure(config: Any) -> None:
    _restore_original(config)


__all__ = (
    "INTEGRATION_SENTINEL_NODEID",
    "ObserverState",
    "ObserverSummary",
    "ObserverVerdict",
    "SchemaObservation",
    "judge_summary",
    "make_observing_wrapper",
    "merge_worker_payloads",
    "observe_schema",
    "session_should_fail",
    "summary_to_worker_payload",
)
