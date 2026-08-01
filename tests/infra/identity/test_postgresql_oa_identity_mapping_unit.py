"""Database-free guards for PostgreSQL OA identity mapping failures."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from app.infra.identity.postgresql import PostgreSQLOAIdentityMapping
from app.ports.capability_gateway import RequestOrgContext
from app.ports.identity_mapping import IdentityMappingMutationError

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
AI_USER_ID = "usr_v1_" + "a" * 43
BINDING_ID = f"oa-session-v1:{AI_USER_ID}"


class _ProjectionResult:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def mappings(self) -> _ProjectionResult:
        return self

    def one_or_none(self) -> dict[str, object]:
        return self._row


class _ProjectionSession:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    async def __aenter__(self) -> _ProjectionSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, *args: object, **kwargs: object) -> _ProjectionResult:
        return _ProjectionResult(self._row)


class _ProjectionFactory:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def __call__(self) -> _ProjectionSession:
        return _ProjectionSession(self._row)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "binding_id",
    (
        AI_USER_ID,
        f"binding:{AI_USER_ID}",
        "oa-session-v1:usr_v1_too-short",
        f"{BINDING_ID}:suffix",
        "oa-session-v1:",
    ),
)
async def test_noncanonical_binding_reference_never_queries_storage(
    binding_id: str,
) -> None:
    def fail_if_called() -> Any:
        raise AssertionError("invalid binding reference must not query storage")

    mapping = PostgreSQLOAIdentityMapping(
        session_factory=cast(Any, fail_if_called),
        now=lambda: NOW,
    )

    assert await mapping.revoke_mapping(binding_id) is None
    assert await mapping.reset_mapping(binding_id) is None


@pytest.mark.anyio
async def test_storage_failure_raises_safe_mutation_error_without_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_marker = "synthetic-sensitive-storage-marker"

    def failing_factory() -> Any:
        raise RuntimeError(sensitive_marker)

    mapping = PostgreSQLOAIdentityMapping(
        session_factory=cast(Any, failing_factory),
        now=lambda: NOW,
    )
    caplog.set_level(logging.DEBUG)

    with pytest.raises(IdentityMappingMutationError) as exc_info:
        await mapping.revoke_mapping(BINDING_ID)

    error = exc_info.value
    assert str(error) == "identity mapping mutation failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert sensitive_marker not in (str(error) + repr(error) + caplog.text)


@pytest.mark.anyio
async def test_clock_failure_raises_safe_mutation_error_without_context() -> None:
    sensitive_marker = "synthetic-sensitive-clock-marker"
    row = {
        "ai_user_id": AI_USER_ID,
        "expires_at": NOW + timedelta(minutes=5),
        "revoked_at": None,
    }

    def failing_clock() -> datetime:
        raise RuntimeError(sensitive_marker)

    mapping = PostgreSQLOAIdentityMapping(
        session_factory=cast(Any, _ProjectionFactory(row)),
        now=failing_clock,
    )

    with pytest.raises(IdentityMappingMutationError) as exc_info:
        await mapping.reset_mapping(BINDING_ID)

    error = exc_info.value
    assert error.__cause__ is None
    assert error.__context__ is None
    assert sensitive_marker not in (str(error) + repr(error))


@pytest.mark.anyio
async def test_revoked_projection_precedes_clock_and_carries_binding_reference() -> None:
    row = {
        "ai_user_id": AI_USER_ID,
        "expires_at": NOW - timedelta(minutes=5),
        "revoked_at": NOW - timedelta(minutes=1),
    }

    def fail_if_called() -> datetime:
        raise AssertionError("revoked status must not consult the expiry clock")

    mapping = PostgreSQLOAIdentityMapping(
        session_factory=cast(Any, _ProjectionFactory(row)),
        now=fail_if_called,
    )

    result = await mapping.resolve_execution_identity(
        ai_user_id=AI_USER_ID,
        target_system="oa",
        execution_identity="user_delegated",
        request_context=RequestOrgContext(request_id="revoked-projection-unit"),
    )

    assert result.bind_status == "revoked"
    assert result.binding_id == BINDING_ID
    assert result.reason_code == "identity_revoked"
