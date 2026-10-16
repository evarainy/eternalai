"""Caller-owned SQL read shape; PostgreSQL locking is tested separately."""

import asyncio
from unittest.mock import AsyncMock, Mock

from sqlalchemy.dialects import postgresql

from app.infra.persistence.capability_registry.repository import PostgreSQLCapabilityRegistry
from tests.infra.persistence.capability_registry.test_postgresql_capability_registry import (
    _capability,
)


def test_locked_read_reuses_conversion_without_owning_session_lifetime() -> None:
    capability = _capability(capability_id="same_session_synthetic")
    session = Mock()
    result = Mock()
    result.mappings.return_value.first.return_value = capability.model_dump(mode="python")
    session.execute = AsyncMock(return_value=result)
    factory = Mock(side_effect=AssertionError("must_use_supplied_session"))
    registry = PostgreSQLCapabilityRegistry(factory)
    assert (
        asyncio.run(registry._get_in_session(session, capability.capability_id, for_share=True))
        == capability
    )
    factory.assert_not_called()
    assert session.execute.await_count == 1
    sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "FOR SHARE" in sql and "capabilities.capability_id" in sql
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


def test_missing_locked_row_returns_none_without_fallback() -> None:
    session, result, factory = Mock(), Mock(), Mock()
    result.mappings.return_value.first.return_value = None
    session.execute = AsyncMock(return_value=result)
    assert (
        asyncio.run(
            PostgreSQLCapabilityRegistry(factory)._get_in_session(session, "absent", for_share=True)
        )
        is None
    )
    factory.assert_not_called()
    assert session.execute.await_count == 1
