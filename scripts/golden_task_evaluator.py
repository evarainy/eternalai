"""End-to-end Golden Task runner judgments."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from app.composition import build_runtime
from app.execution_fabric.mock_adapters.error_injection import (
    InjectionAwareAdapter,
    MockInjectionDuration,
    clear_injection,
    set_injection,
)
from app.execution_fabric.mock_adapters.hikvision_ivms.mock_hikvision_ivms_adapter import (
    MockHikvisionIVMSAdapter,
)
from app.execution_fabric.mock_adapters.oa.mock_oa_adapter import MockOAAdapter
from app.execution_fabric.mock_adapters.u8.mock_u8_adapter import MockU8Adapter
from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.ports.adapter import AdapterPort, AdapterResult, MockErrorMode
from app.ports.capability_gateway import RequestChannel, RequestOrgContext
from app.ports.capability_registry import (
    CapabilityExecutionIdentity,
    CapabilityRiskLevel,
    CapabilitySpec,
    CapabilityStatus,
    CapabilityTargetSystem,
    CapabilityType,
)
from app.ports.identity_mapping import (
    ExecutionIdentity,
    IdentityBindStatus,
    IdentityCheckResult,
    TargetSystem,
)
from app.ports.policy_guard import PolicyDecision, PolicyDecisionValue
from app.ports.response_envelope import ResponseEnvelope
from app.ports.task_store import SessionRecord, TaskRecord
from app.runtime.models import CapabilityRef
from scripts.golden_task_assertions import judge_assertions
from scripts.golden_task_fixture_support import FIXTURES_DIR, apply_mock_state, load_fixture

FROZEN_GT_IDS = (
    "GT-001",
    "GT-002",
    "GT-003",
    "GT-004",
    "GT-005",
    "GT-006",
    "GT-007",
    "GT-008",
    "GT-009",
    "GT-010",
    "GT-012",
    "GT-013",
    "GT-014",
)
GT_IDS = tuple(path.stem for path in sorted(FIXTURES_DIR.glob("GT-*.json")))
GoldenTaskStatus = Literal["passed", "failed", "skipped", "not_applicable"]


@dataclass(frozen=True)
class RunnerAssertionJudgement:
    status: Literal["passed", "failed"]
    reasons: list[str]


@dataclass(frozen=True)
class GoldenTaskResult:
    golden_task_id: str
    category: str
    status: GoldenTaskStatus
    reasons: list[str]

    def to_summary_item(self) -> dict[str, Any]:
        return {
            "golden_task_id": self.golden_task_id,
            "category": self.category,
            "status": self.status,
            "reasons": self.reasons,
        }


class SpyTaskStore:
    def __init__(self) -> None:
        self.created: list[TaskRecord] = []
        self.status_updates: list[tuple[str, str, str | None]] = []

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        self.created.append(record)
        return record

    async def get_task(self, task_id: str) -> TaskRecord | None:
        return None

    async def update_status(
        self,
        task_id: str,
        status: str,
        error_code: str | None = None,
    ) -> TaskRecord:
        self.status_updates.append((task_id, status, error_code))
        created = self.created[0]
        return TaskRecord(
            task_id=task_id,
            session_id=created.session_id,
            ai_user_id=created.ai_user_id,
            status=cast(Any, status),
            trace_id=created.trace_id,
            error_code=error_code,
        )

    async def append_event(self, task_id: str, event: Any) -> None:
        return None


class ExistingSessionStore:
    async def create_session(self, record: SessionRecord) -> SessionRecord:
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return SessionRecord(session_id=session_id)


class SpyTracePort:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def set_sanitizer(self, hook: Any) -> None:
        return None

    async def record_event(self, event: Any) -> None:
        return None

    async def start_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
    ) -> None:
        return None

    async def record_step(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        event_type: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.steps.append(
            {
                "event_type": event_type,
                "status": status,
                "capability_id": capability_id,
                "error_code": error_code,
                "attributes": attributes or {},
            }
        )

    async def record_policy_decision(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        return None

    async def record_gateway_call(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        await self.record_step(
            trace_id,
            task_id,
            session_id,
            "gateway_pre_recorded",
            status,
            capability_id,
            error_code,
            attributes,
        )

    async def finalize_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        return None


class AdapterSpy:
    def __init__(self, inner: AdapterPort) -> None:
        self._inner = inner
        self.call_count = 0
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        self.call_count += 1
        self.calls.append(
            {
                "capability_id": capability_id,
                "arguments": arguments,
                "execution_context": execution_context,
            }
        )
        return await self._inner.execute(capability_id, arguments, execution_context)


class FakeCapabilityRegistry:
    def __init__(self, capabilities: Sequence[dict[str, Any]]) -> None:
        self._capabilities = {
            str(capability["capability_id"]): _build_capability_spec(capability)
            for capability in capabilities
        }

    async def create(self, capability: CapabilitySpec) -> CapabilitySpec:
        self._capabilities[capability.capability_id] = capability
        return capability

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        return self._capabilities.get(capability_id)

    async def list(
        self,
        target_system: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> list[CapabilitySpec]:
        capabilities = list(self._capabilities.values())
        if target_system is not None:
            capabilities = [
                capability
                for capability in capabilities
                if capability.target_system == target_system
            ]
        if type is not None:
            capabilities = [
                capability for capability in capabilities if capability.type == type
            ]
        if status is not None:
            capabilities = [
                capability for capability in capabilities if capability.status == status
            ]
        return capabilities

    async def update(self, capability_id: str, patch: dict[str, Any]) -> CapabilitySpec:
        capability = self._capabilities[capability_id].model_copy(update=patch)
        self._capabilities[capability_id] = capability
        return capability

    async def disable(self, capability_id: str) -> CapabilitySpec:
        capability = self._capabilities[capability_id].model_copy(
            update={"status": "disabled"}
        )
        self._capabilities[capability_id] = capability
        return capability


class FakeIdentityMapping:
    def __init__(self, mappings: Sequence[dict[str, Any]]) -> None:
        self._mappings = list(mappings)

    async def resolve_execution_identity(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        execution_identity: ExecutionIdentity,
        request_context: RequestOrgContext,
    ) -> IdentityCheckResult:
        if execution_identity == "system_scope":
            return IdentityCheckResult(
                bind_status="active",
                target_system=target_system,
                execution_identity=execution_identity,
            )

        matches = [
            mapping
            for mapping in self._mappings
            if mapping.get("target_system") == target_system
        ]
        active_matches = [
            mapping for mapping in matches if mapping.get("status", "unbound") == "active"
        ]
        if (
            len(active_matches) > 1
            and request_context.account_set_id is None
            and request_context.resource_scope is None
        ):
            return IdentityCheckResult(
                bind_status="needs_binding_scope",
                target_system=target_system,
                execution_identity=execution_identity,
                reason_code="needs_binding_scope",
            )
        if not matches:
            return IdentityCheckResult(
                bind_status="unbound",
                target_system=target_system,
                execution_identity=execution_identity,
                reason_code="unbound",
            )

        mapping = matches[0]
        return IdentityCheckResult(
            bind_status=cast(
                IdentityBindStatus,
                mapping.get("status", "unbound"),
            ),
            binding_id=_optional_str(mapping.get("binding_id")),
            target_system=target_system,
            execution_identity=execution_identity,
            binding_scope=_optional_str(mapping.get("binding_scope")),
            account_set_id=_optional_str(mapping.get("account_set_id")),
            device_domain_id=_optional_str(mapping.get("device_domain_id")),
            reason_code=_optional_str(mapping.get("reason_code")),
        )

    async def get_mapping(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
    ) -> IdentityCheckResult | None:
        return await self.resolve_execution_identity(
            ai_user_id=ai_user_id,
            target_system=target_system,
            execution_identity="user_delegated",
            request_context=RequestOrgContext(
                request_id="golden-task-get-mapping",
                account_set_id=account_set_id,
                device_domain_id=device_domain_id,
                resource_scope=binding_scope,
            ),
        )

    async def list_mappings(
        self,
        ai_user_id: str,
        target_system: TargetSystem | None = None,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
    ) -> list[IdentityCheckResult]:
        results: list[IdentityCheckResult] = []
        for mapping in self._mappings:
            mapping_target = cast(TargetSystem, mapping.get("target_system"))
            if target_system is not None and mapping_target != target_system:
                continue
            result = await self.get_mapping(
                ai_user_id=ai_user_id,
                target_system=mapping_target,
                binding_scope=binding_scope,
                account_set_id=account_set_id,
                device_domain_id=device_domain_id,
            )
            if result is not None:
                results.append(result)
        return results


class FakePolicyGuard:
    def __init__(self, fixture_policy: dict[str, Any] | None) -> None:
        self._fixture_policy = fixture_policy or {"decision": "allow"}

    async def decide(
        self,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> PolicyDecision:
        decision = cast(
            PolicyDecisionValue,
            self._fixture_policy.get("decision", "allow"),
        )
        return PolicyDecision(
            decision=decision,
            reason_code=_optional_str(self._fixture_policy.get("reason_code")),
            required_action="confirm" if decision == "confirm" else None,
        )


def evaluate_golden_task(gt_id: str) -> GoldenTaskResult:
    fixture = load_fixture(gt_id)
    category = str(fixture["category"])
    reasons: list[str] = []
    primary_status: Literal["passed", "failed"] = "failed"
    injection_status: Literal["passed", "failed"] = "passed"
    clear_injection()
    try:
        envelope, trace_steps, adapter_calls = asyncio.run(_run_fixture(fixture))
        expected_trace = _expected_trace_for_matrix(fixture)
        judgement = judge_assertions(
            envelope=envelope,
            expected_response=fixture["then_response"],
            trace_steps=trace_steps,
            expected_trace=expected_trace,
            forbidden_items=fixture["then_forbidden"],
            adapter_assertion=fixture["adapter_assertion"],
            adapter_calls=adapter_calls,
        )
        primary_status = judgement.status
        reasons.extend(judgement.reasons)
    except AssertionError as exc:
        reasons.append(str(exc))
    finally:
        clear_injection()

    if _injection_enabled(fixture):
        try:
            injection_judgement = asyncio.run(_run_injection_companion(fixture))
            injection_status = injection_judgement.status
            reasons.extend(
                f"injection companion: {reason}"
                for reason in injection_judgement.reasons
            )
        except AssertionError as exc:
            injection_status = "failed"
            reasons.append(f"injection companion: {exc}")
        finally:
            clear_injection()

    return GoldenTaskResult(
        golden_task_id=gt_id,
        category=category,
        status="passed"
        if primary_status == "passed" and injection_status == "passed"
        else "failed",
        reasons=reasons,
    )


def judge_injection_companion_assertions(
    *,
    envelope: Any,
    trace_steps: list[Any],
    expected_error_code: str,
    adapter_assertion: Mapping[str, Any],
    adapter_calls: Mapping[str, Any],
    forbidden_items: Iterable[str] = (),
) -> RunnerAssertionJudgement:
    expected_trace: dict[str, Any] = {
        "event_sequence": [
            "task_created",
            "capability_selected",
            "gateway_pre_recorded",
            "adapter_called",
            "gateway_post_recorded",
            "response_envelope_created",
        ]
    }
    if expected_error_code == "adapter_timeout":
        expected_trace["reason"] = "adapter_timeout"

    judgement = judge_assertions(
        envelope=envelope,
        expected_response={
            "status": _expected_injection_response_status(expected_error_code)
        },
        trace_steps=trace_steps,
        expected_trace=expected_trace,
        forbidden_items=forbidden_items,
        adapter_assertion=adapter_assertion,
        adapter_calls=adapter_calls,
    )
    reasons = list(judgement.reasons)
    try:
        _assert_expected_error_code_observed(
            envelope,
            trace_steps,
            expected_error_code,
        )
    except AssertionError as exc:
        reasons.append(str(exc))
    return RunnerAssertionJudgement(
        status="failed" if reasons else "passed",
        reasons=reasons,
    )


async def _run_injection_companion(fixture: dict[str, Any]) -> RunnerAssertionJudgement:
    injection = _required_enabled_injection(fixture)
    payload = cast(dict[str, Any], injection["payload"])
    capability_id = _fixture_capability_id(fixture)
    clear_injection()
    try:
        set_injection(
            capability_id,
            error_mode=_mock_error_mode(payload.get("error_mode")),
            duration=_mock_injection_duration(payload.get("duration")),
            error_detail=_serialize_error_detail(payload.get("error_detail")),
        )
        envelope, trace_steps, adapter_calls = await _run_fixture(fixture)
        return judge_injection_companion_assertions(
            envelope=envelope,
            trace_steps=trace_steps,
            expected_error_code=str(injection["expected_error_code"]),
            adapter_assertion=cast(Mapping[str, Any], fixture["adapter_assertion"]),
            adapter_calls=adapter_calls,
            forbidden_items=cast(Iterable[str], fixture["then_forbidden"]),
        )
    finally:
        clear_injection()


def evaluate_all_golden_tasks() -> list[GoldenTaskResult]:
    return [evaluate_golden_task(gt_id) for gt_id in GT_IDS]


def build_summary(results: Sequence[GoldenTaskResult]) -> dict[str, Any]:
    status_counts = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "not_applicable": 0,
    }
    for result in results:
        status_counts[result.status] += 1
    return {
        "total": len(results),
        "passed": status_counts["passed"],
        "failed": status_counts["failed"],
        "skipped": status_counts["skipped"],
        "not_applicable": status_counts["not_applicable"],
        "positive_passed": sum(
            1
            for result in results
            if result.category == "positive" and result.status == "passed"
        ),
        "negative_passed": sum(
            1
            for result in results
            if result.category == "negative" and result.status == "passed"
        ),
        "positive_total": sum(1 for result in results if result.category == "positive"),
        "negative_total": sum(1 for result in results if result.category == "negative"),
        "positive_not_applicable": sum(
            1
            for result in results
            if result.category == "positive" and result.status == "not_applicable"
        ),
        "negative_not_applicable": sum(
            1
            for result in results
            if result.category == "negative" and result.status == "not_applicable"
        ),
        "results": [result.to_summary_item() for result in results],
    }


async def _run_fixture(
    fixture: dict[str, Any],
) -> tuple[ResponseEnvelope, list[dict[str, Any]], dict[str, int]]:
    given = cast(dict[str, Any], fixture["given"])
    when = cast(dict[str, Any], fixture["when"])
    adapters, adapter_calls, reset_adapters = _build_adapter_spies(given)
    trace_port = SpyTracePort()
    capability_registry = FakeCapabilityRegistry(
        cast(Sequence[dict[str, Any]], given["registered_capabilities"])
    )
    runtime = build_runtime(
        task_store=SpyTaskStore(),
        session_store=ExistingSessionStore(),
        capability_registry=capability_registry,
        gateway=CapabilityGateway(
            capability_registry=capability_registry,
            identity_mapping=FakeIdentityMapping(
                cast(Sequence[dict[str, Any]], given["identity_mappings"])
            ),
            policy_guard=FakePolicyGuard(
                cast(dict[str, Any] | None, given.get("policy_fixture"))
            ),
            trace_port=trace_port,
            adapters=adapters,
        ),
        trace_port=trace_port,
        llm_provider=MockLLMProvider(),
        structured_output=_structured_output_for_fixture(fixture),
        intent_model="golden-task-intent-model",
    )
    try:
        envelope = await runtime.handle_user_message(
            channel=cast(RequestChannel, when.get("channel", "web")),
            ai_user_id=str(given["ai_user_id"]),
            session_id=f"session-{fixture['golden_task_id'].lower()}",
            message=str(when["message"]),
            client_capabilities={},
        )
        return envelope, trace_port.steps, adapter_calls()
    finally:
        reset_adapters()
        clear_injection()


def _build_adapter_spies(
    given: dict[str, Any],
) -> tuple[dict[str, AdapterPort], Callable[[], dict[str, int]], Callable[[], None]]:
    oa_adapter = MockOAAdapter()
    u8_adapter = MockU8Adapter()
    ivms_adapter = MockHikvisionIVMSAdapter()
    _apply_state_if_present(given, "mock_oa_state", oa_adapter)
    _apply_state_if_present(given, "mock_u8_state", u8_adapter)
    _apply_state_if_present(given, "mock_ivms_state", ivms_adapter)

    spies = {
        "oa": AdapterSpy(InjectionAwareAdapter(oa_adapter)),
        "u8": AdapterSpy(InjectionAwareAdapter(u8_adapter)),
        "hikvision_ivms": AdapterSpy(InjectionAwareAdapter(ivms_adapter)),
    }

    def adapter_calls() -> dict[str, int]:
        return {name: spy.call_count for name, spy in spies.items()}

    def reset_adapters() -> None:
        oa_adapter.reset_state()
        u8_adapter.reset_state()
        ivms_adapter.reset_state()

    return cast(dict[str, AdapterPort], spies), adapter_calls, reset_adapters


def _apply_state_if_present(given: dict[str, Any], key: str, adapter: Any) -> None:
    if key in given:
        apply_mock_state(adapter, given[key])


def _structured_output_for_fixture(fixture: dict[str, Any]) -> MockStructuredOutputProvider:
    provider = MockStructuredOutputProvider()
    given = cast(dict[str, Any], fixture["given"])
    when = cast(dict[str, Any], fixture["when"])
    capabilities = cast(Sequence[dict[str, Any]], given["registered_capabilities"])
    message = str(when["message"])
    if not capabilities:
        provider.register_malformed(message, CapabilityRef)
        return provider
    selector = cast(
        dict[str, Any],
        given.get("capability_selector") or capabilities[0],
    )
    provider.register(
        message,
        CapabilityRef,
        CapabilityRef(
            capability_id=str(selector["capability_id"]),
            arguments=cast(dict[str, Any], when.get("arguments", {})),
            target_system=cast(
                CapabilityTargetSystem | None,
                selector.get("target_system"),
            ),
            capability_type=cast(
                CapabilityType | None,
                selector.get("capability_type"),
            ),
        ),
    )
    return provider


def _expected_trace_for_matrix(fixture: dict[str, Any]) -> dict[str, Any]:
    expected_trace = dict(cast(dict[str, Any], fixture["then_trace"]))
    response_status = cast(dict[str, Any], fixture["then_response"]).get("status")
    injection = cast(dict[str, Any], fixture.get("mock_failure_injection") or {})
    expected_error_code = injection.get("expected_error_code")
    if (
        "reason" not in expected_trace
        and response_status != "completed"
        and isinstance(expected_error_code, str)
    ):
        expected_trace["reason"] = expected_error_code
    return expected_trace


def _injection_enabled(fixture: dict[str, Any]) -> bool:
    injection = fixture.get("mock_failure_injection")
    return isinstance(injection, Mapping) and injection.get("enabled") is True


def _required_enabled_injection(fixture: dict[str, Any]) -> Mapping[str, Any]:
    injection = fixture.get("mock_failure_injection")
    if not isinstance(injection, Mapping) or injection.get("enabled") is not True:
        raise AssertionError("mock failure injection is not enabled")
    payload = injection.get("payload")
    if not isinstance(payload, Mapping):
        raise AssertionError("enabled mock failure injection missing payload")
    expected_error_code = injection.get("expected_error_code")
    if not isinstance(expected_error_code, str) or not expected_error_code:
        raise AssertionError("enabled mock failure injection missing expected_error_code")
    return injection


def _fixture_capability_id(fixture: dict[str, Any]) -> str:
    given = cast(dict[str, Any], fixture["given"])
    capabilities = cast(Sequence[dict[str, Any]], given["registered_capabilities"])
    if not capabilities:
        raise AssertionError("enabled mock failure injection has no capability")
    return str(capabilities[0]["capability_id"])


def _mock_error_mode(value: Any) -> MockErrorMode:
    if value not in {
        "timeout",
        "permission_denied",
        "malformed_json",
        "empty_response",
        "http_500",
        "missing_required_field",
    }:
        raise AssertionError(f"unknown mock error mode: {value!r}")
    return cast(MockErrorMode, value)


def _mock_injection_duration(value: Any) -> MockInjectionDuration:
    if value not in {"next_1_call", "next_3_calls", "permanent"}:
        raise AssertionError(f"unknown mock injection duration: {value!r}")
    return cast(MockInjectionDuration, value)


def _serialize_error_detail(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _expected_injection_response_status(expected_error_code: str) -> str:
    if expected_error_code == "upstream_permission_denied":
        return "blocked"
    return "failed"


def _assert_expected_error_code_observed(
    envelope: Any,
    trace_steps: Iterable[Any],
    expected_error_code: str,
) -> None:
    observed = set(_iter_error_codes(envelope))
    for step in trace_steps:
        observed.update(_iter_error_codes(step))
    if expected_error_code not in observed:
        raise AssertionError(
            f"expected injected error_code {expected_error_code!r} not observed; "
            f"observed={sorted(observed)!r}"
        )


def _iter_error_codes(value: Any) -> Iterable[str]:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        value = model_dump()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "error_code" and isinstance(item, str):
                yield item
            else:
                yield from _iter_error_codes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_error_codes(item)
    elif isinstance(value, tuple):
        for item in value:
            yield from _iter_error_codes(item)


def _build_capability_spec(raw: dict[str, Any]) -> CapabilitySpec:
    capability_id = str(raw["capability_id"])
    return CapabilitySpec(
        capability_id=capability_id,
        name=capability_id,
        type=cast(CapabilityType, raw.get("type", "query")),
        intent_tags=cast(list[str], raw.get("intent_tags", [])),
        input_schema=cast(dict[str, Any], raw.get("input_schema", {})),
        output_schema=cast(dict[str, Any], raw.get("output_schema", {})),
        input_schema_digest=str(
            raw.get("input_schema_digest", f"digest_input_{capability_id}")
        ),
        output_schema_digest=str(
            raw.get("output_schema_digest", f"digest_output_{capability_id}")
        ),
        risk_level=cast(CapabilityRiskLevel, raw.get("risk_level", "low")),
        owner="golden_tasks",
        version="0.0.0",
        status=cast(CapabilityStatus, raw.get("status", "active")),
        short_description=str(raw.get("short_description", capability_id)),
        target_system=cast(
            CapabilityTargetSystem | None,
            raw.get("target_system") or _infer_target_system(capability_id),
        ),
        execution_identity=cast(
            CapabilityExecutionIdentity,
            raw.get("execution_identity", "user_delegated"),
        ),
        binding_required=bool(raw.get("binding_required", False)),
        policy_digest=cast(str | None, raw.get("policy_digest")),
    )


def _infer_target_system(capability_id: str) -> str | None:
    if capability_id.startswith("oa."):
        return "oa"
    if capability_id.startswith("u8."):
        return "u8"
    if capability_id.startswith("ivms."):
        return "hikvision_ivms"
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
