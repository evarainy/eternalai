"""Phase 0 in-memory identity mapping mock."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TypeAlias, cast

from app.ports.capability_gateway import RequestOrgContext
from app.ports.identity_mapping import (
    ExecutionIdentity,
    IdentityBindStatus,
    IdentityCheckResult,
    IdentityMappingMutationResult,
    TargetSystem,
)

_RowValue: TypeAlias = str | None
_RowInput: TypeAlias = Mapping[str, _RowValue]
_AI_USER_ID_RE = re.compile(r"^usr_v1_[A-Za-z0-9_-]{43}$")
_CREDENTIAL_REF_PREFIX = "oa-session-v1:"


@dataclass(frozen=True, slots=True)
class _IdentityMappingRow:
    ai_user_id: str
    result: IdentityCheckResult


_DEFAULT_ROWS: tuple[dict[str, _RowValue], ...] = (
    {
        "ai_user_id": "ai-user-001",
        "bind_status": "active",
        "binding_id": "bind-oa-user-001",
        "target_system": "oa",
        "execution_identity": "user_delegated",
        "binding_scope": "oa-finance",
        "account_set_id": None,
        "device_domain_id": None,
        "reason_code": None,
    },
    {
        "ai_user_id": "ai-user-001",
        "bind_status": "active",
        "binding_id": "bind-u8-system-001",
        "target_system": "u8",
        "execution_identity": "system_scope",
        "binding_scope": "u8-system",
        "account_set_id": "u8-system-acct",
        "device_domain_id": None,
        "reason_code": None,
    },
    {
        "ai_user_id": "ai-user-001",
        "bind_status": "active",
        "binding_id": "bind-hikvision-user-east",
        "target_system": "hikvision_ivms",
        "execution_identity": "user_delegated",
        "binding_scope": "hikvision-east",
        "account_set_id": None,
        "device_domain_id": "camera-domain-east",
        "reason_code": None,
    },
    {
        "ai_user_id": "ai-user-001",
        "bind_status": "active",
        "binding_id": "bind-oa-admin-001",
        "target_system": "oa",
        "execution_identity": "admin_approved_proxy",
        "binding_scope": "oa-admin-proxy",
        "account_set_id": None,
        "device_domain_id": None,
        "reason_code": None,
    },
    {
        "ai_user_id": "ai-user-multi-u8",
        "bind_status": "active",
        "binding_id": "bind-u8-user-a",
        "target_system": "u8",
        "execution_identity": "user_delegated",
        "binding_scope": "u8-account-a",
        "account_set_id": "u8-acct-a",
        "device_domain_id": None,
        "reason_code": None,
    },
    {
        "ai_user_id": "ai-user-multi-u8",
        "bind_status": "active",
        "binding_id": "bind-u8-user-b",
        "target_system": "u8",
        "execution_identity": "user_delegated",
        "binding_scope": "u8-account-b",
        "account_set_id": "u8-acct-b",
        "device_domain_id": None,
        "reason_code": None,
    },
    {
        "ai_user_id": "ai-user-multi-hikvision",
        "bind_status": "active",
        "binding_id": "bind-hikvision-user-west",
        "target_system": "hikvision_ivms",
        "execution_identity": "user_delegated",
        "binding_scope": "hikvision-west",
        "account_set_id": None,
        "device_domain_id": "camera-domain-west",
        "reason_code": None,
    },
    {
        "ai_user_id": "ai-user-expired",
        "bind_status": "expired",
        "binding_id": "bind-oa-expired-001",
        "target_system": "oa",
        "execution_identity": "user_delegated",
        "binding_scope": "oa-expired",
        "account_set_id": None,
        "device_domain_id": None,
        "reason_code": "identity_expired",
    },
    {
        "ai_user_id": "ai-user-revoked",
        "bind_status": "revoked",
        "binding_id": "bind-oa-revoked-001",
        "target_system": "oa",
        "execution_identity": "user_delegated",
        "binding_scope": "oa-revoked",
        "account_set_id": None,
        "device_domain_id": None,
        "reason_code": "identity_revoked",
    },
    {
        "ai_user_id": "ai-user-unbound",
        "bind_status": "unbound",
        "binding_id": "bind-oa-unbound-001",
        "target_system": "oa",
        "execution_identity": "user_delegated",
        "binding_scope": "oa-unbound",
        "account_set_id": None,
        "device_domain_id": None,
        "reason_code": "identity_unbound",
    },
)


class MockIdentityMapping:
    """Deterministic mock implementation of IdentityMappingPort."""

    def __init__(self, rows: Iterable[_RowInput] | None = None) -> None:
        source_rows = _DEFAULT_ROWS if rows is None else rows
        self._rows = tuple(self._coerce_row(row) for row in source_rows)

    async def resolve_execution_identity(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        execution_identity: ExecutionIdentity,
        request_context: RequestOrgContext,
    ) -> IdentityCheckResult:
        return self._resolve_identity_sync(
            ai_user_id=ai_user_id,
            target_system=target_system,
            execution_identity=execution_identity,
            request_context=request_context,
        )

    def _resolve_identity_sync(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        execution_identity: ExecutionIdentity,
        request_context: RequestOrgContext,
    ) -> IdentityCheckResult:
        matches = [
            row.result
            for row in self._rows
            if row.ai_user_id == ai_user_id
            and row.result.target_system == target_system
            and row.result.execution_identity == execution_identity
        ]

        binding_scope = request_context.resource_scope
        account_set_id = request_context.account_set_id
        device_domain_id = request_context.device_domain_id
        has_scope_filter = any(
            value is not None for value in (binding_scope, account_set_id, device_domain_id)
        )

        if has_scope_filter:
            matches = [
                result
                for result in matches
                if self._matches_filters(
                    result,
                    binding_scope=binding_scope,
                    account_set_id=account_set_id,
                    device_domain_id=device_domain_id,
                )
            ]

        if not matches:
            return self._unbound(target_system, execution_identity)

        active_matches = [result for result in matches if result.bind_status == "active"]
        if len(active_matches) > 1:
            return IdentityCheckResult(
                bind_status="needs_binding_scope",
                target_system=target_system,
                execution_identity=execution_identity,
                reason_code="needs_binding_scope",
            )
        if active_matches:
            return active_matches[0]

        return matches[0]

    async def get_mapping(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
    ) -> IdentityCheckResult | None:
        results = await self.list_mappings(
            ai_user_id=ai_user_id,
            target_system=target_system,
            binding_scope=binding_scope,
            account_set_id=account_set_id,
            device_domain_id=device_domain_id,
        )
        if len(results) != 1:
            return None
        return results[0]

    async def list_mappings(
        self,
        ai_user_id: str,
        target_system: TargetSystem | None = None,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
    ) -> list[IdentityCheckResult]:
        return [
            row.result
            for row in self._rows
            if row.ai_user_id == ai_user_id
            and (target_system is None or row.result.target_system == target_system)
            and self._matches_filters(
                row.result,
                binding_scope=binding_scope,
                account_set_id=account_set_id,
                device_domain_id=device_domain_id,
            )
        ]

    async def revoke_mapping(
        self,
        binding_id: str,
    ) -> IdentityMappingMutationResult | None:
        return self._mutate_mapping(binding_id)

    async def reset_mapping(
        self,
        binding_id: str,
    ) -> IdentityMappingMutationResult | None:
        return self._mutate_mapping(binding_id)

    def _mutate_mapping(
        self,
        binding_id: str,
    ) -> IdentityMappingMutationResult | None:
        ai_user_id = _parse_binding_id(binding_id)
        if ai_user_id is None:
            return None

        matching_indexes = [
            index
            for index, row in enumerate(self._rows)
            if row.ai_user_id == ai_user_id
            and f"{_CREDENTIAL_REF_PREFIX}{row.ai_user_id}" == binding_id
            and row.result.target_system == "oa"
            and row.result.execution_identity == "user_delegated"
        ]
        if len(matching_indexes) != 1:
            return None

        row_index = matching_indexes[0]
        row = self._rows[row_index]
        previous_bind_status = row.result.bind_status
        changed = previous_bind_status != "revoked"
        revoked_mapping = row.result.model_copy(
            update={
                "bind_status": "revoked",
                "binding_id": binding_id,
                "reason_code": "identity_revoked",
            }
        )
        if changed:
            rows = list(self._rows)
            rows[row_index] = _IdentityMappingRow(
                ai_user_id=row.ai_user_id,
                result=revoked_mapping,
            )
            self._rows = tuple(rows)

        return IdentityMappingMutationResult(
            mapping=revoked_mapping,
            previous_bind_status=previous_bind_status,
            changed=changed,
        )

    def precheck(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        execution_identity: ExecutionIdentity,
        request_context: RequestOrgContext,
    ) -> bool:
        result = self._resolve_identity_sync(
            ai_user_id=ai_user_id,
            target_system=target_system,
            execution_identity=execution_identity,
            request_context=request_context,
        )
        return result.bind_status == "active"

    @classmethod
    def _coerce_row(cls, row: _RowInput) -> _IdentityMappingRow:
        return _IdentityMappingRow(
            ai_user_id=cls._required(row, "ai_user_id"),
            result=IdentityCheckResult(
                bind_status=cast(IdentityBindStatus, cls._required(row, "bind_status")),
                binding_id=row.get("binding_id"),
                target_system=cast(TargetSystem, cls._required(row, "target_system")),
                execution_identity=cast(
                    ExecutionIdentity,
                    cls._required(row, "execution_identity"),
                ),
                binding_scope=row.get("binding_scope"),
                account_set_id=row.get("account_set_id"),
                device_domain_id=row.get("device_domain_id"),
                reason_code=row.get("reason_code"),
            ),
        )

    @staticmethod
    def _required(row: _RowInput, field_name: str) -> str:
        value = row.get(field_name)
        if value is None:
            raise ValueError(f"Missing mock identity mapping field: {field_name}")
        return value

    @staticmethod
    def _matches_filters(
        result: IdentityCheckResult,
        *,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
    ) -> bool:
        return (
            (binding_scope is None or result.binding_scope == binding_scope)
            and (account_set_id is None or result.account_set_id == account_set_id)
            and (device_domain_id is None or result.device_domain_id == device_domain_id)
        )

    @staticmethod
    def _unbound(
        target_system: TargetSystem,
        execution_identity: ExecutionIdentity,
    ) -> IdentityCheckResult:
        return IdentityCheckResult(
            bind_status="unbound",
            target_system=target_system,
            execution_identity=execution_identity,
            reason_code="identity_unbound",
        )


def _parse_binding_id(binding_id: str) -> str | None:
    if not isinstance(binding_id, str) or not binding_id.startswith(_CREDENTIAL_REF_PREFIX):
        return None
    ai_user_id = binding_id[len(_CREDENTIAL_REF_PREFIX) :]
    if _AI_USER_ID_RE.fullmatch(ai_user_id) is None:
        return None
    return ai_user_id
