"""Integration tests for PostgreSQLCapabilityRegistry.

Requires DATABASE_URL environment variable pointing to a live PostgreSQL instance.
Run: uv run alembic upgrade head before executing these tests.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
import uuid
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, update

from app.infra.persistence.capability_registry.schema import capabilities

DATABASE_URL = os.environ.get("DATABASE_URL")

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]


def _require_db() -> None:
    """Fail loudly instead of skipping: a silent skip reads as a pass.

    Matches tests/db/. To run without a database, exclude these paths
    explicitly (`--ignore=...`) so the omission is visible in the command.
    """
    if not DATABASE_URL:
        raise AssertionError("DATABASE_URL must be set by the test runner environment")


def _repository_module() -> ModuleType:
    module_name = "app.infra.persistence.capability_registry.repository"
    assert importlib.util.find_spec(module_name) is not None, "repository module is missing"
    return importlib.import_module(module_name)


def _errors_module() -> ModuleType:
    module_name = "app.infra.persistence.capability_registry.errors"
    assert importlib.util.find_spec(module_name) is not None, "errors module is missing"
    return importlib.import_module(module_name)


def _make_engine() -> Any:
    from app.db.session import make_async_engine

    return make_async_engine(DATABASE_URL)


def _make_factory(engine: Any) -> Any:
    from app.db.session import make_async_session_factory

    return make_async_session_factory(engine)


def _registry(factory: Any) -> Any:
    return _repository_module().PostgreSQLCapabilityRegistry(factory)


def _capability_data(
    *,
    capability_id: str | None = None,
    capability_type: str = "query",
    target_system: str | None = "oa",
    status: str = "active",
    risk_level: str = "low",
    execution_identity: str = "user_delegated",
) -> dict[str, Any]:
    suffix = capability_id or str(uuid.uuid4())
    return {
        "capability_id": capability_id or f"capability-{suffix}",
        "name": f"Capability {suffix}",
        "type": capability_type,
        "intent_tags": [f"intent-{suffix}"],
        "input_schema": {"type": "object", "properties": {"value": {"type": "string"}}},
        "output_schema": {"type": "object", "properties": {"result": {"type": "string"}}},
        "input_schema_digest": f"digest:input-{suffix}",
        "output_schema_digest": f"digest:output-{suffix}",
        "risk_level": risk_level,
        "owner": f"owner-{suffix}",
        "version": f"version-{suffix}",
        "status": status,
        "short_description": f"Capability description {suffix}",
        "target_system": target_system,
        "execution_identity": execution_identity,
        "binding_required": True,
        "policy_digest": f"policy-{suffix}",
    }


def _capability(**overrides: Any) -> Any:
    from app.ports.capability_registry import CapabilitySpec

    return CapabilitySpec.model_validate(_capability_data(**overrides))


def _ids(records: list[Any]) -> set[str]:
    return {record.capability_id for record in records}


def test_create_happy_path() -> None:
    _require_db()
    from app.ports.capability_registry import CapabilitySpec

    capability = _capability()

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            result = await registry.create(capability)
            assert isinstance(result, CapabilitySpec)
            assert result == capability
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_create_duplicate_rejected() -> None:
    _require_db()
    DuplicateCapabilityError = _errors_module().DuplicateCapabilityError
    capability = _capability()
    duplicate = _capability(capability_id=capability.capability_id, capability_type="action")

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            await registry.create(capability)
            with pytest.raises(DuplicateCapabilityError):
                await registry.create(duplicate)
            fetched = await registry.get(capability.capability_id)
            assert fetched == capability
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_get_found() -> None:
    _require_db()
    from app.ports.capability_registry import CapabilitySpec

    capability = _capability()

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            await registry.create(capability)
            result = await registry.get(capability.capability_id)
            assert isinstance(result, CapabilitySpec)
            assert result == capability
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_get_missing_returns_none() -> None:
    _require_db()

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            result = await registry.get(f"missing-{uuid.uuid4()}")
            assert result is None
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_list_all() -> None:
    _require_db()
    first = _capability()
    second = _capability(capability_type="action", target_system="u8")

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            await registry.create(first)
            await registry.create(second)
            result = await registry.list()
            assert {first.capability_id, second.capability_id}.issubset(_ids(result))
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_list_filter_target_system() -> None:
    _require_db()
    matching = _capability(target_system="hikvision_ivms")
    non_matching = _capability(target_system="u8")

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            await registry.create(matching)
            await registry.create(non_matching)
            result = await registry.list(target_system="hikvision_ivms")
            ids = _ids(result)
            assert matching.capability_id in ids
            assert non_matching.capability_id not in ids
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_list_filter_type() -> None:
    _require_db()
    matching = _capability(capability_type="workflow")
    non_matching = _capability(capability_type="mock")

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            await registry.create(matching)
            await registry.create(non_matching)
            result = await registry.list(type="workflow")
            ids = _ids(result)
            assert matching.capability_id in ids
            assert non_matching.capability_id not in ids
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_list_filter_status() -> None:
    _require_db()
    matching = _capability(status="draft")
    non_matching = _capability(status="deprecated")

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            await registry.create(matching)
            await registry.create(non_matching)
            result = await registry.list(status="draft")
            ids = _ids(result)
            assert matching.capability_id in ids
            assert non_matching.capability_id not in ids
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_update_valid_patch() -> None:
    _require_db()
    capability = _capability(status="draft")

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            await registry.create(capability)
            result = await registry.update(
                capability.capability_id,
                {"status": "active", "target_system": "u8", "intent_tags": ["updated"]},
            )
            assert result.status == "active"
            assert result.target_system == "u8"
            assert result.intent_tags == ["updated"]
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_update_unknown_field_rejected() -> None:
    _require_db()
    capability = _capability()

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            await registry.create(capability)
            with pytest.raises(ValidationError, match="extra_forbidden"):
                await registry.update(capability.capability_id, {"unknown_field": "value"})
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_update_invalid_literal_rejected() -> None:
    _require_db()
    capability = _capability()

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            await registry.create(capability)
            with pytest.raises(ValidationError):
                await registry.update(capability.capability_id, {"status": "archived"})
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_update_revalidates_displayable_fields_against_merged_input_schema() -> None:
    _require_db()
    from app.ports.capability_registry import CapabilitySpec

    capability = CapabilitySpec.model_validate(
        {
            **_capability_data(),
            "displayable_argument_fields": ["value"],
        }
    )

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            await registry.create(capability)
            with pytest.raises(ValidationError, match="input_schema.properties"):
                await registry.update(
                    capability.capability_id,
                    {"input_schema": {"type": "object", "properties": {}}},
                )
            assert await registry.get(capability.capability_id) == capability
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(capabilities).where(
                        capabilities.c.capability_id == capability.capability_id
                    )
                )
            await engine.dispose()

    asyncio.run(_run())


def test_update_missing_raises() -> None:
    _require_db()
    CapabilityNotFoundError = _errors_module().CapabilityNotFoundError

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            with pytest.raises(CapabilityNotFoundError):
                await registry.update(f"missing-{uuid.uuid4()}", {"status": "active"})
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_disable_happy_path() -> None:
    _require_db()
    capability = _capability(status="active")

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            await registry.create(capability)
            result = await registry.disable(capability.capability_id)
            assert result.status == "disabled"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_disable_missing_raises() -> None:
    _require_db()
    CapabilityNotFoundError = _errors_module().CapabilityNotFoundError

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            with pytest.raises(CapabilityNotFoundError):
                await registry.disable(f"missing-{uuid.uuid4()}")
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_returns_capability_spec_not_dict() -> None:
    _require_db()
    from app.ports.capability_registry import CapabilitySpec

    capability = _capability()

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            result = await registry.create(capability)
            assert isinstance(result, CapabilitySpec)
            assert not isinstance(result, dict)
        finally:
            await engine.dispose()

    asyncio.run(_run())


class _NoGateway:
    async def execute_capability(self, **kwargs: Any) -> Any:
        raise AssertionError("Gateway must not run for an invalid persisted catalog")


class _DeferPolicy:
    async def preview_capability(self, **kwargs: Any) -> str:
        return "defer"


@pytest.mark.parametrize("invalid_timing", ["before_prompt", "after_prompt"])
def test_topk_uses_validated_persisted_metadata(
    invalid_timing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_db()
    from app.infra.llm.json_structured_output import JSONStructuredOutputProvider
    from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
    from app.infra.orchestration.agent_adapter import AgentOrchestrationAdapter
    from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
    from app.knowledge.capability_selection import select_capability_candidates
    from app.ports.llm_provider import LLMCompletionResponse
    from app.runtime.runtime import RuntimeImpl
    from tests.runtime.principal_fakes import runtime_principal
    from tests.runtime.test_runtime_capability_selection import (
        ExistingSessionStore,
        RecordingTaskStore,
        RecordingTracePort,
    )

    run_id = uuid.uuid4().hex[:12]
    fillers = [_capability(capability_id=f"topk-{run_id}-filler-{index}") for index in range(8)]
    target = _capability(capability_id=f"zz-topk-{run_id}-target")
    created = [*fillers, target]
    invalid = _capability(capability_id=f"topk-{run_id}-invalid")
    owned_ids = [*(item.capability_id for item in created), invalid.capability_id]
    canary = f"persisted-invalid-canary-{run_id}"

    async def _run() -> None:
        engine = _make_engine()
        try:
            registry = _registry(_make_factory(engine))
            for item in created:
                await registry.create(item)
            persisted = [
                item
                for item in await registry.list(status="active")
                if item.capability_id in owned_ids
            ]
            from_db = select_capability_candidates(target.capability_id, persisted)
            in_memory = select_capability_candidates(target.capability_id, created)
            assert from_db.outcome == "ready"
            assert from_db.bindings[0].capability_id == target.capability_id
            assert from_db.payload_json == in_memory.payload_json
            assert from_db.bindings == in_memory.bindings

            async def corrupt_row(capability_id: str) -> None:
                async with engine.begin() as connection:
                    await connection.execute(
                        update(capabilities)
                        .where(capabilities.c.capability_id == capability_id)
                        .values(short_description=f"{canary} {{system}}")
                    )

            if invalid_timing == "before_prompt":
                await registry.create(invalid)
                await corrupt_row(invalid.capability_id)
                with pytest.raises(ValidationError):
                    await registry.list(status="active")

            task_store = RecordingTaskStore()
            trace_port = RecordingTracePort()
            llm_provider = MockLLMProvider()
            if invalid_timing == "after_prompt":
                import json

                llm_provider.register(
                    target.capability_id,
                    LLMCompletionResponse(
                        content=json.dumps(
                            {
                                "match": "capability",
                                "capability_id": target.capability_id,
                                "arguments": {},
                            }
                        )
                    ),
                )
                original_complete = llm_provider.complete

                async def complete_after_corruption(
                    *args: Any, **kwargs: Any
                ) -> LLMCompletionResponse:
                    await corrupt_row(target.capability_id)
                    return await original_complete(*args, **kwargs)

                monkeypatch.setattr(llm_provider, "complete", complete_after_corruption)
            builder = ResponseEnvelopeBuilder()
            runtime = RuntimeImpl(
                candidate_policy=_DeferPolicy(),
                task_store=task_store,
                session_store=ExistingSessionStore(),
                capability_registry=registry,
                orchestration=AgentOrchestrationAdapter(
                    capability_registry=registry,
                    gateway=_NoGateway(),
                    workflow_engine=None,
                    response_builder=builder,
                ),
                trace_port=trace_port,
                llm_provider=llm_provider,
                structured_output=JSONStructuredOutputProvider(),
                intent_model="test-intent-model",
                response_builder=builder,
            )
            envelope = await runtime.handle_user_message(
                channel="web",
                principal=runtime_principal("ai-user-topk-pg"),
                session_id="session-topk-pg",
                message=target.capability_id,
                client_capabilities={},
            )
            assert envelope.status == "failed"
            expected_error = (
                "capability_catalog_invalid"
                if invalid_timing == "before_prompt"
                else "capability_candidate_stale"
            )
            assert task_store.status_updates[-1][1:] == ("failed", expected_error)
            assert len(llm_provider.calls) == int(invalid_timing == "after_prompt")
            intent = [step for step in trace_port.steps if step["event_type"] == "intent_parsed"]
            assert len(intent) == 1
            assert intent[0]["error_code"] == expected_error
            assert [step["event_type"] for step in trace_port.steps][-3:] == [
                "response_envelope_created",
                "task_failed",
                "evaluation_recorded",
            ]
            assert canary not in repr((envelope, trace_port.steps))
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(capabilities).where(capabilities.c.capability_id.in_(owned_ids))
                )
            await engine.dispose()

    asyncio.run(_run())
