"""PostgreSQL OA identity projection over encrypted credential metadata."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ports.capability_gateway import RequestOrgContext
from app.ports.identity_mapping import (
    ExecutionIdentity,
    IdentityBindStatus,
    IdentityCheckResult,
    IdentityMappingMutationError,
    IdentityMappingMutationResult,
    TargetSystem,
)

_AI_USER_ID_RE = re.compile(r"^usr_v1_[A-Za-z0-9_-]{43}$")
_CREDENTIAL_REF_PREFIX = "oa-session-v1:"


@dataclass(frozen=True, slots=True)
class _CredentialProjection:
    ai_user_id: str
    expires_at: datetime
    revoked_at: datetime | None


class PostgreSQLOAIdentityMapping:
    """Project OA binding status without selecting or decrypting credential bytes."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._now = _utc_now if now is None else now

    def __repr__(self) -> str:
        return "PostgreSQLOAIdentityMapping()"

    async def resolve_execution_identity(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        execution_identity: ExecutionIdentity,
        request_context: RequestOrgContext,
    ) -> IdentityCheckResult:
        if target_system != "oa" or execution_identity != "user_delegated":
            return _unbound_result(
                target_system=target_system,
                execution_identity=execution_identity,
            )
        if (
            request_context.resource_scope is not None
            or request_context.account_set_id is not None
            or request_context.device_domain_id is not None
        ):
            return _unbound_result(
                target_system=target_system,
                execution_identity=execution_identity,
            )
        if _AI_USER_ID_RE.fullmatch(ai_user_id) is None:
            return _verification_failed_result()

        query_failed, projection = await self._load_projection(ai_user_id)
        if query_failed:
            return _verification_failed_result()
        if projection is None:
            return _unbound_result(
                target_system=target_system,
                execution_identity=execution_identity,
            )
        return self._project_status(projection)

    async def get_mapping(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
    ) -> IdentityCheckResult | None:
        if (
            target_system != "oa"
            or binding_scope is not None
            or account_set_id is not None
            or device_domain_id is not None
            or _AI_USER_ID_RE.fullmatch(ai_user_id) is None
        ):
            return None

        query_failed, projection = await self._load_projection(ai_user_id)
        if query_failed:
            return _verification_failed_result()
        if projection is None:
            return None
        return self._project_status(projection)

    async def list_mappings(
        self,
        ai_user_id: str,
        target_system: TargetSystem | None = None,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
    ) -> list[IdentityCheckResult]:
        if (
            target_system not in (None, "oa")
            or binding_scope is not None
            or account_set_id is not None
            or device_domain_id is not None
            or _AI_USER_ID_RE.fullmatch(ai_user_id) is None
        ):
            return []

        query_failed, projection = await self._load_projection(ai_user_id)
        if query_failed:
            return [_verification_failed_result()]
        if projection is None:
            return []
        return [self._project_status(projection)]

    async def revoke_mapping(
        self,
        binding_id: str,
    ) -> IdentityMappingMutationResult | None:
        return await self._mutate_mapping(binding_id)

    async def reset_mapping(
        self,
        binding_id: str,
    ) -> IdentityMappingMutationResult | None:
        return await self._mutate_mapping(binding_id)

    async def _mutate_mapping(
        self,
        binding_id: str,
    ) -> IdentityMappingMutationResult | None:
        ai_user_id = _parse_binding_id(binding_id)
        if ai_user_id is None:
            return None

        result: IdentityMappingMutationResult | None = None
        mutation_failed = False
        try:
            async with self._session_factory() as session:
                query_result = await session.execute(
                    text(
                        "SELECT ai_user_id, expires_at, revoked_at"
                        " FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                        " AND target_system = 'oa'"
                        " FOR UPDATE"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                row = query_result.mappings().one_or_none()
                if row is None:
                    return None

                projection = _coerce_projection(row, ai_user_id)
                if projection.revoked_at is None:
                    now = self._validated_now()
                    previous_bind_status = _status_at(projection, now)
                    updated_ai_user_id = (
                        await session.execute(
                            text(
                                "UPDATE oa_session_credentials"
                                " SET revoked_at = :revoked_at"
                                " WHERE ai_user_id = :ai_user_id"
                                " AND target_system = 'oa'"
                                " AND revoked_at IS NULL"
                                " RETURNING ai_user_id"
                            ),
                            {
                                "ai_user_id": ai_user_id,
                                "revoked_at": now,
                            },
                        )
                    ).scalar_one()
                    if updated_ai_user_id != ai_user_id:
                        raise RuntimeError
                    changed = True
                else:
                    previous_bind_status = "revoked"
                    changed = False

                await session.commit()
                result = IdentityMappingMutationResult(
                    mapping=_revoked_result(ai_user_id),
                    previous_bind_status=previous_bind_status,
                    changed=changed,
                )
        except Exception:
            mutation_failed = True

        if mutation_failed:
            raise IdentityMappingMutationError("identity mapping mutation failed")
        return result

    async def _load_projection(
        self,
        ai_user_id: str,
    ) -> tuple[bool, _CredentialProjection | None]:
        row: RowMapping | None = None
        query_failed = False
        try:
            async with self._session_factory() as session:
                query_result = await session.execute(
                    text(
                        "SELECT ai_user_id, expires_at, revoked_at"
                        " FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                        " AND target_system = 'oa'"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                row = query_result.mappings().one_or_none()
        except Exception:
            query_failed = True

        if query_failed:
            return True, None
        if row is None:
            return False, None

        projection: _CredentialProjection | None = None
        try:
            projection = _coerce_projection(row, ai_user_id)
        except Exception:
            pass

        if projection is None:
            return True, None
        return False, projection

    def _project_status(
        self,
        projection: _CredentialProjection,
    ) -> IdentityCheckResult:
        if projection.revoked_at is not None:
            return _revoked_result(projection.ai_user_id)

        now: datetime | None = None
        clock_failed = False
        try:
            now = self._validated_now()
        except Exception:
            clock_failed = True

        if clock_failed or now is None:
            return _verification_failed_result()

        if projection.expires_at <= now:
            return IdentityCheckResult(
                bind_status="expired",
                target_system="oa",
                execution_identity="user_delegated",
                reason_code="identity_expired",
            )
        credential_ref = f"{_CREDENTIAL_REF_PREFIX}{projection.ai_user_id}"
        return IdentityCheckResult(
            bind_status="active",
            binding_id=credential_ref,
            target_system="oa",
            execution_identity="user_delegated",
        )

    def _validated_now(self) -> datetime:
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise TypeError
        return now


def _coerce_projection(
    row: RowMapping,
    ai_user_id: str,
) -> _CredentialProjection:
    row_ai_user_id = row.get("ai_user_id")
    expires_at = row.get("expires_at")
    revoked_at = row.get("revoked_at")
    if row_ai_user_id != ai_user_id:
        raise ValueError
    if (
        not isinstance(expires_at, datetime)
        or expires_at.tzinfo is None
        or expires_at.utcoffset() is None
    ):
        raise TypeError
    if revoked_at is not None and (
        not isinstance(revoked_at, datetime)
        or revoked_at.tzinfo is None
        or revoked_at.utcoffset() is None
    ):
        raise TypeError
    return _CredentialProjection(
        ai_user_id=ai_user_id,
        expires_at=expires_at,
        revoked_at=revoked_at,
    )


def _status_at(
    projection: _CredentialProjection,
    now: datetime,
) -> IdentityBindStatus:
    if projection.revoked_at is not None:
        return "revoked"
    if projection.expires_at <= now:
        return "expired"
    return "active"


def _revoked_result(ai_user_id: str) -> IdentityCheckResult:
    return IdentityCheckResult(
        bind_status="revoked",
        binding_id=f"{_CREDENTIAL_REF_PREFIX}{ai_user_id}",
        target_system="oa",
        execution_identity="user_delegated",
        reason_code="identity_revoked",
    )


def _parse_binding_id(binding_id: str) -> str | None:
    if not isinstance(binding_id, str) or not binding_id.startswith(_CREDENTIAL_REF_PREFIX):
        return None
    ai_user_id = binding_id[len(_CREDENTIAL_REF_PREFIX) :]
    if _AI_USER_ID_RE.fullmatch(ai_user_id) is None:
        return None
    return ai_user_id


def _unbound_result(
    *,
    target_system: TargetSystem,
    execution_identity: ExecutionIdentity,
) -> IdentityCheckResult:
    return IdentityCheckResult(
        bind_status="unbound",
        target_system=target_system,
        execution_identity=execution_identity,
        reason_code="identity_unbound",
    )


def _verification_failed_result() -> IdentityCheckResult:
    return IdentityCheckResult(
        bind_status="verification_failed",
        target_system="oa",
        execution_identity="user_delegated",
        reason_code="verification_failed",
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = ("PostgreSQLOAIdentityMapping",)
