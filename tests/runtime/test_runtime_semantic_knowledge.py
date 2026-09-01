"""Runtime integration for bounded global Semantic/System Knowledge."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.capability_registry import CapabilitySpec, CapabilityStatus
from app.ports.response_envelope import ResponseEnvelope
from app.runtime.models import CapabilityRef
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


def _run(
    selector: str,
    capabilities: list[CapabilitySpec],
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
        CapabilityRef,
        CapabilityRef(capability_id=selector),
    )
    runtime = RuntimeImpl(
        task_store=task_store,
        session_store=ExistingSessionStore(),
        capability_registry=registry,
        gateway=gateway,
        trace_port=trace,
        llm_provider=llm_provider,
        structured_output=structured_output,
        intent_model="test-intent-model",
        response_builder=ResponseEnvelopeBuilder(),
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
    assert "Admin Lite > Registry" in envelope.message
    knowledge_prompt = llm_provider.calls[0]["messages"][1].content
    assert selector not in knowledge_prompt


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

    prompt = llm_provider.calls[0]["messages"][1].content
    assert '"capability_id":"oa.active.query"' in prompt
    assert '"status":"active"' in prompt
    assert '"capability_type":' in prompt
    for inactive_id in (
        "oa.draft.query",
        "oa.disabled.query",
        "oa.deprecated.query",
    ):
        assert inactive_id not in prompt
    assert registry.list_calls[0] == {
        "target_system": None,
        "type": None,
        "status": "active",
    }
    assert envelope.status == "no_capability_found"
    assert task_store.status_updates[-1][1:] == (
        "no_capability_found",
        "capability_not_found",
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
    )

    assert envelope.status == "no_capability_found"
    assert task_store.status_updates[-1][1:] == (
        "no_capability_found",
        "capability_not_found",
    )
    assert "oa.active.query" in envelope.message
    assert "query/oa/active" in envelope.message
    assert "oa.disabled.query" not in envelope.message
    assert "oa.active.query" in envelope.fallback_text
    assert "query/oa/active" in envelope.fallback_text
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
    assert "[REDACTED]" in serialized
    assert envelope.status == "no_capability_found"
    assert gateway.calls == []


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
            CapabilityRef,
            CapabilityRef(capability_id="oa.missing.query"),
        )
    runtime = RuntimeImpl(
        task_store=RecordingTaskStore(),
        session_store=ExistingSessionStore(),
        capability_registry=registry,
        gateway=RecordingGateway(),
        trace_port=RecordingTracePort(),
        llm_provider=llm_provider,
        structured_output=structured_output,
        intent_model="test-intent-model",
        response_builder=ResponseEnvelopeBuilder(),
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

    first_prompt = llm_provider.calls[0]["messages"][1].content
    second_messages = llm_provider.calls[1]["messages"]
    third_prompt = llm_provider.calls[2]["messages"][1].content
    assert "oa.first.query" in first_prompt
    assert "status=active" in first_prompt
    assert "oa.second.query" not in first_prompt
    assert [message.role for message in second_messages] == ["system", "user"]
    assert all("semantic_system_knowledge" not in item.content for item in second_messages)
    assert all("oa.first.query" not in item.content for item in second_messages)
    assert "oa.second.query" in third_prompt
    assert "status=active" in third_prompt
    assert "oa.first.query" not in third_prompt
