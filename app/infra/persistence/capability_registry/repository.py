"""PostgreSQL implementation of CapabilityRegistryPort."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.persistence.capability_registry.errors import (
    CapabilityNotFoundError,
    DuplicateCapabilityError,
)
from app.infra.persistence.capability_registry.schema import capabilities
from app.ports.capability_registry import (
    CapabilityExecutionIdentity,
    CapabilityRegistryPort,
    CapabilityRiskLevel,
    CapabilitySpec,
    CapabilityStatus,
    CapabilityTargetSystem,
    CapabilityType,
)


class CapabilitySpecPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str | None = None
    name: str | None = None
    type: CapabilityType | None = None
    intent_tags: list[str] | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    input_schema_digest: str | None = None
    output_schema_digest: str | None = None
    risk_level: CapabilityRiskLevel | None = None
    owner: str | None = None
    version: str | None = None
    status: CapabilityStatus | None = None
    short_description: str | None = None
    target_system: CapabilityTargetSystem | None = None
    execution_identity: CapabilityExecutionIdentity | None = None
    binding_required: bool | None = None
    policy_digest: str | None = None


class PostgreSQLCapabilityRegistry:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, capability: CapabilitySpec) -> CapabilitySpec:
        values = capability.model_dump(mode="python")
        async with self._session_factory() as session:
            try:
                row = (
                    await session.execute(
                        sa.insert(capabilities).values(**values).returning(capabilities)
                    )
                ).mappings().one()
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise DuplicateCapabilityError(
                    f"Capability {capability.capability_id!r} already exists"
                )
        return _row_to_capability_spec(row)

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    sa.select(capabilities).where(
                        capabilities.c.capability_id == capability_id
                    )
                )
            ).mappings().first()
        if row is None:
            return None
        return _row_to_capability_spec(row)

    async def list(
        self,
        target_system: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> list[CapabilitySpec]:
        conditions = []
        if target_system is not None:
            conditions.append(capabilities.c.target_system == target_system)
        if type is not None:
            conditions.append(capabilities.c.type == type)
        if status is not None:
            conditions.append(capabilities.c.status == status)

        statement = sa.select(capabilities).order_by(capabilities.c.capability_id)
        if conditions:
            statement = statement.where(*conditions)

        async with self._session_factory() as session:
            rows = (await session.execute(statement)).mappings().all()
        return [_row_to_capability_spec(row) for row in rows]

    async def update(self, capability_id: str, patch: dict[str, Any]) -> CapabilitySpec:
        patch_model = CapabilitySpecPatch.model_validate(patch)
        patch_values = patch_model.model_dump(exclude_unset=True)

        async with self._session_factory() as session:
            existing_row = (
                await session.execute(
                    sa.select(capabilities).where(
                        capabilities.c.capability_id == capability_id
                    )
                )
            ).mappings().first()
            if existing_row is None:
                raise CapabilityNotFoundError(f"Capability {capability_id!r} not found")

            existing = _row_to_capability_spec(existing_row)
            merged = existing.model_dump(mode="python")
            merged.update(patch_values)
            updated = CapabilitySpec.model_validate(merged)

            try:
                row = (
                    await session.execute(
                        sa.update(capabilities)
                        .where(capabilities.c.capability_id == capability_id)
                        .values(**updated.model_dump(mode="python"))
                        .returning(capabilities)
                    )
                ).mappings().one()
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise DuplicateCapabilityError(
                    f"Capability {updated.capability_id!r} already exists"
                )
        return _row_to_capability_spec(row)

    async def disable(self, capability_id: str) -> CapabilitySpec:
        return await self.update(capability_id, {"status": "disabled"})


def _row_to_capability_spec(row: RowMapping) -> CapabilitySpec:
    return CapabilitySpec.model_validate(dict(row))


if TYPE_CHECKING:

    def _capability_registry_protocol_check(
        registry: PostgreSQLCapabilityRegistry,
    ) -> CapabilityRegistryPort:
        return registry
