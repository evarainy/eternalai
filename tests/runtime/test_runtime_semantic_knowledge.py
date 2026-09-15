"""Runtime integration for bounded global Semantic/System Knowledge."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.orchestration.agent_adapter import AgentOrchestrationAdapter
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.capability_registry import CapabilitySpec, CapabilityStatus
from app.ports.response_envelope import ResponseEnvelope
from app.runtime.models import IntentOutput, MatchedIntent, UnmatchedIntent
from app.runtime.runtime import RuntimeImpl
from tests.runtime.principal_fakes import runtime_principal
from tests.runtime.registry_fakes import runtime_output_schema, schema_digest
from tests.runtime.test_runtime_capability_selection import (
    ExistingSessionStore,
    RecordingGateway,
    RecordingTaskStore,
    RecordingTracePort,
    StaticRegistry,
)


def _capability(
    capability_id: str,
    *,
    status: CapabilityStatus = "active",
    description: str | None = None,
    owner: str = "semantic-knowledge-test",
    name: str | None = None,
    intent_tags: list[str] | None = None,
) -> CapabilitySpec:
    output_schema = runtime_output_schema("test_runtime_semantic_knowledge.default")
    return CapabilitySpec(
        capability_id=capability_id,
        name=name or capability_id,
        type="query",
        intent_tags=intent_tags or [],
        input_schema_digest=f"input-{capability_id}",
        output_schema=output_schema,
        output_schema_digest=schema_digest(output_schema),
        risk_level="low",
        owner=owner,
        version="1.0.0",
        status=status,
        short_description=description or capability_id,
        target_system="oa",
        execution_identity="user_delegated",
        binding_required=False,
    )


def _candidate_prompt(call: dict[str, Any]) -> str:
    return next(
        message.content
        for message in call["messages"]
        if '{"capability_candidates":' in message.content
    )


def _run(
    selector: str,
    capabilities: list[CapabilitySpec],
    *,
    match_none: bool = False,
) -> tuple[
    ResponseEnvelope,
    RecordingTaskStore,
    RecordingTracePort,
    RecordingGateway,
    StaticRegistry,
    MockLLMProvider,
]:
    message = f"select {selector}"
    task_store = RecordingTaskStore()
    trace = RecordingTracePort()
    gateway = RecordingGateway()
    registry = StaticRegistry(capabilities)
    llm_provider = MockLLMProvider()
    structured_output = MockStructuredOutputProvider()
    structured_output.register(
        message,
        IntentOutput,
        UnmatchedIntent(match="none")
        if match_none
        else MatchedIntent(match="capability", capability_id=selector),
    )
    orchestration_registry = registry
    orchestration_workflow = None
    orchestration_builder = ResponseEnvelopeBuilder()
    runtime = RuntimeImpl(
        candidate_policy=MinimalPolicyGuard(),
        task_store=task_store,
        session_store=ExistingSessionStore(),
        capability_registry=orchestration_registry,
        orchestration=AgentOrchestrationAdapter(
            capability_registry=orchestration_registry,
            gateway=gateway,
            workflow_engine=orchestration_workflow,
            response_builder=orchestration_builder,
        ),
        trace_port=trace,
        llm_provider=llm_provider,
        structured_output=structured_output,
        intent_model="test-intent-model",
        response_builder=orchestration_builder,
    )

    envelope = asyncio.run(
        runtime.handle_user_message(
            channel="mock",
            principal=runtime_principal("ai-user-1"),
            session_id="session-1",
            message=message,
            client_capabilities={},
        )
    )
    return envelope, task_store, trace, gateway, registry, llm_provider


@pytest.mark.parametrize(
    ("selector", "capabilities"),
    [
        (
            "oa.disabled.query",
            [
                _capability(
                    "oa.disabled.query",
                    status="disabled",
                )
            ],
        ),
        ("oa.missing.query", []),
    ],
    ids=["disabled", "missing"],
)
def test_knowledge_never_authorizes_disabled_or_missing_selector(
    selector: str,
    capabilities: list[CapabilitySpec],
) -> None:
    envelope, task_store, trace, gateway, _registry, llm_provider = _run(
        selector,
        capabilities,
    )

    assert envelope.status == "no_capability_found"
    assert task_store.status_updates[-1][1:] == (
        "no_capability_found",
        "capability_not_found",
    )
    assert gateway.calls == []
    no_capability = next(
        step for step in trace.steps if step["event_type"] == "no_capability_found"
    )
    assert no_capability["error_code"] == "capability_not_found"
    assert "Admin Lite > Registry" not in envelope.message
    # Nothing is visible, so the model is never offered any candidate at all.
    assert llm_provider.calls == []
    assert selector not in envelope.message
    assert selector not in repr(trace.steps)


def test_runtime_injects_only_active_registry_capabilities() -> None:
    capabilities = [
        _capability("oa.active.query"),
        _capability("oa.draft.query", status="draft"),
        _capability("oa.disabled.query", status="disabled"),
        _capability("oa.deprecated.query", status="deprecated"),
    ]

    envelope, task_store, _trace, gateway, registry, llm_provider = _run(
        "oa.missing.query",
        capabilities,
    )

    prompt = _candidate_prompt(llm_provider.calls[0])
    all_prompts = "\n".join(message.content for message in llm_provider.calls[0]["messages"])
    assert '"capability_id":"oa.active.query"' in prompt
    assert '"status":"active"' in prompt
    assert '"capability_type":' in prompt
    for inactive_id in (
        "oa.draft.query",
        "oa.disabled.query",
        "oa.deprecated.query",
    ):
        assert inactive_id not in all_prompts
    assert registry.list_calls[0] == {
        "target_system": None,
        "type": None,
        "status": "active",
    }
    # A selector outside the admitted candidates is rejected, never executed.
    assert envelope.status == "failed"
    assert task_store.status_updates[-1][1:] == (
        "failed",
        "capability_candidate_out_of_scope",
    )
    assert gateway.calls == []


def test_no_capability_guidance_lists_only_active_registry_capabilities() -> None:
    active = _capability("oa.active.query")
    disabled = _capability(
        "oa.disabled.query",
        status="disabled",
    )

    envelope, task_store, _trace, gateway, registry, _llm = _run(
        "oa.missing.query",
        [disabled, active],
        match_none=True,
    )

    assert envelope.status == "no_capability_found"
    assert task_store.status_updates[-1][1:] == (
        "no_capability_found",
        "capability_not_found",
    )
    assert "oa.active.query" in envelope.message
    assert "query/oa/active" not in envelope.message
    assert "oa.disabled.query" not in envelope.message
    assert "oa.active.query" in envelope.fallback_text
    assert "query/oa/active" not in envelope.fallback_text
    assert "oa.disabled.query" not in envelope.fallback_text
    assert gateway.calls == []
    assert registry.list_calls[-1] == {
        "target_system": None,
        "type": None,
        "status": "active",
    }


def test_sensitive_registry_values_do_not_reach_prompt_trace_state_or_response() -> None:
    unsafe_id_value = "synthetic-runtime-token-value"
    description_marker = "unique-runtime-description-1b7a"
    name_marker = "unique-runtime-name-2c8b"
    owner_marker = "unique-runtime-owner-3d9c"
    intent_marker = "unique-runtime-intent-4e0d"
    safe_capability = _capability(
        "oa.safe.query",
        description=description_marker,
        owner=owner_marker,
        name=name_marker,
        intent_tags=[intent_marker],
    )
    unsafe_capability = _capability(
        f"token={unsafe_id_value}",
        description="credential=synthetic-free-text",
    )

    envelope, task_store, trace, gateway, _registry, llm_provider = _run(
        "oa.missing.query",
        [safe_capability, unsafe_capability],
    )
    safe_envelope, safe_store, safe_trace, safe_gateway, _safe_registry, safe_llm = _run(
        "oa.missing.query",
        [safe_capability],
        match_none=True,
    )

    observed: list[Any] = [
        llm_provider.calls,
        trace.steps,
        task_store.created,
        task_store.status_updates,
        envelope.model_dump(),
    ]
    serialized = repr(observed)
    for sensitive in (
        unsafe_id_value,
        description_marker,
        name_marker,
        owner_marker,
        intent_marker,
        "synthetic-free-text",
    ):
        assert sensitive not in serialized
    # An unsafe visible identifier invalidates the whole snapshot before the model.
    assert envelope.status == "failed"
    assert task_store.status_updates[-1][1:] == ("failed", "capability_catalog_invalid")
    assert llm_provider.calls == []
    assert gateway.calls == []
    safe_host_state = repr(
        [
            safe_trace.steps,
            safe_store.created,
            safe_store.status_updates,
            safe_envelope.model_dump(),
        ]
    )
    for free_text in (description_marker, name_marker, owner_marker, intent_marker):
        assert free_text not in safe_host_state
    safe_prompt = "\n".join(message.content for message in safe_llm.calls[0]["messages"])
    assert name_marker not in safe_prompt
    assert intent_marker not in safe_prompt
    assert safe_envelope.status == "no_capability_found"
    assert safe_gateway.calls == []


def test_runtime_refreshes_registry_knowledge_on_every_request() -> None:
    first_active = _capability(
        "oa.first.query",
    )
    first_disabled = _capability(
        "oa.first.query",
        status="disabled",
    )
    replacement = _capability("oa.second.query")
    registry = StaticRegistry([first_active])
    llm_provider = MockLLMProvider()
    structured_output = MockStructuredOutputProvider()
    for message in ("first request", "second request", "third request"):
        structured_output.register(
            message,
            IntentOutput,
            MatchedIntent(match="capability", capability_id="oa.missing.query"),
        )
    orchestration_registry = registry
    orchestration_workflow = None
    orchestration_builder = ResponseEnvelopeBuilder()
    runtime = RuntimeImpl(
        candidate_policy=MinimalPolicyGuard(),
        task_store=RecordingTaskStore(),
        session_store=ExistingSessionStore(),
        capability_registry=orchestration_registry,
        orchestration=AgentOrchestrationAdapter(
            capability_registry=orchestration_registry,
            gateway=RecordingGateway(),
            workflow_engine=orchestration_workflow,
            response_builder=orchestration_builder,
        ),
        trace_port=RecordingTracePort(),
        llm_provider=llm_provider,
        structured_output=structured_output,
        intent_model="test-intent-model",
        response_builder=orchestration_builder,
    )

    async def exercise() -> None:
        await runtime.handle_user_message(
            channel="mock",
            principal=runtime_principal("ai-user-1"),
            session_id="session-1",
            message="first request",
            client_capabilities={},
        )
        registry.capabilities = [first_disabled]
        await runtime.handle_user_message(
            channel="mock",
            principal=runtime_principal("ai-user-1"),
            session_id="session-1",
            message="second request",
            client_capabilities={},
        )
        registry.capabilities = [replacement]
        await runtime.handle_user_message(
            channel="mock",
            principal=runtime_principal("ai-user-1"),
            session_id="session-1",
            message="third request",
            client_capabilities={},
        )

    asyncio.run(exercise())

    # The second request sees no active capability, so only two model calls happen.
    assert len(llm_provider.calls) == 2
    first_prompt = _candidate_prompt(llm_provider.calls[0])
    third_prompt = _candidate_prompt(llm_provider.calls[1])
    assert "oa.first.query" in first_prompt
    assert '"status":"active"' in first_prompt
    assert "oa.second.query" not in first_prompt
    assert "oa.second.query" in third_prompt
    assert '"status":"active"' in third_prompt
    assert all(
        "oa.first.query" not in message.content for message in llm_provider.calls[1]["messages"]
    )
