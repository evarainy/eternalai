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
) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        name=capability_id,
        type="query",
        intent_tags=[],
        input_schema_digest=f"input-{capability_id}",
        output_schema_digest=f"output-{capability_id}",
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
            ai_user_id="ai-user-1",
            session_id="session-1",
            message=message,
            client_capabilities={},
        )
    )
    return envelope, task_store, trace, gateway, registry, llm_provider


@pytest.mark.parametrize(
    ("selector", "capabilities", "knowledge_contains"),
    [
        (
            "oa.disabled.query",
            [
                _capability(
                    "oa.disabled.query",
                    status="disabled",
                    description="visible but disabled",
                )
            ],
            "status=disabled",
        ),
        ("oa.missing.query", [], None),
    ],
    ids=["disabled", "missing"],
)
def test_knowledge_never_authorizes_disabled_or_missing_selector(
    selector: str,
    capabilities: list[CapabilitySpec],
    knowledge_contains: str | None,
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
    if knowledge_contains is None:
        assert selector not in knowledge_prompt
    else:
        assert selector in knowledge_prompt
        assert knowledge_contains in knowledge_prompt


def test_no_capability_guidance_lists_only_active_registry_capabilities() -> None:
    active = _capability("oa.active.query", description="active overview")
    disabled = _capability(
        "oa.disabled.query",
        status="disabled",
        description="disabled overview",
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
    assert "active overview" in envelope.message
    assert "oa.disabled.query" not in envelope.message
    assert "disabled overview" not in envelope.message
    assert gateway.calls == []
    assert registry.list_calls[-1] == {
        "target_system": None,
        "type": None,
        "status": "active",
    }


def test_sensitive_registry_values_do_not_reach_prompt_trace_state_or_response() -> None:
    credential_key = "client_" + "secret"
    credential_value = "synthetic-runtime-credential"
    private_address = "https://172.16.10.20/internal"
    personal_name = "李四"
    owner_marker = "synthetic-real-owner"
    capability = _capability(
        "oa.safe.query",
        description=(
            f"{credential_key}:{credential_value} address={private_address} "
            f"负责人:{personal_name}"
        ),
        owner=owner_marker,
    )

    envelope, task_store, trace, gateway, _registry, llm_provider = _run(
        "oa.missing.query",
        [capability],
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
        credential_value,
        private_address,
        personal_name,
        owner_marker,
    ):
        assert sensitive not in serialized
    assert "[REDACTED]" in serialized
    assert envelope.status == "no_capability_found"
    assert gateway.calls == []
