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
