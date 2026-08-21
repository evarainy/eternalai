"""Human decision and immutable Task version-binding contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

VersionedResourceType: TypeAlias = Literal[
    "workflow",
    "skill",
    "tool",
    "prompt",
    "policy",
]
HumanGateDecision: TypeAlias = Literal["confirmed", "rejected"]

VERSIONED_RESOURCE_TYPES: tuple[VersionedResourceType, ...] = (
    "workflow",
    "skill",
    "tool",
    "prompt",
    "policy",
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class VersionBinding(BaseModel):
    """One immutable, value-free resource marker used by a Task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_type: VersionedResourceType
    resource_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    digest: str = Field(pattern=_SHA256_PATTERN)


class TaskVersionBindingManifest(BaseModel):
    """Complete immutable version tuple locked before Task execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1)
    bindings: tuple[VersionBinding, ...] = Field(min_length=1)
    unused_resource_types: tuple[VersionedResourceType, ...]
    manifest_digest: str = Field(pattern=_SHA256_PATTERN)
    locked_at: datetime

    @model_validator(mode="after")
    def validate_canonical_manifest(self) -> TaskVersionBindingManifest:
        canonical = _canonical_bindings(self.bindings)
        if canonical != self.bindings:
            raise ValueError("Task version bindings must be unique and canonically ordered")
        used_types = {binding.resource_type for binding in canonical}
        expected_unused = tuple(
            resource_type
            for resource_type in VERSIONED_RESOURCE_TYPES
            if resource_type not in used_types
        )
        if self.unused_resource_types != expected_unused:
            raise ValueError("Unused Task resource types must be explicit and canonical")
        expected = task_version_manifest_digest(
            self.task_id,
            canonical,
            expected_unused,
        )
        if self.manifest_digest != expected:
            raise ValueError("Task version binding manifest digest is invalid")
        return self


class HumanGateRequest(BaseModel):
    """Immutable request presented to exactly one authenticated actor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    requested_for_ai_user_id: str = Field(min_length=1)
    requested_session_id: str = Field(min_length=1)
    requested_tenant_id: str = Field(min_length=1)
    action_digest: str = Field(pattern=_SHA256_PATTERN)
    request_digest: str = Field(pattern=_SHA256_PATTERN)
    binding_manifest_digest: str = Field(pattern=_SHA256_PATTERN)
    requested_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_validity_window(self) -> HumanGateRequest:
        if self.expires_at <= self.requested_at:
            raise ValueError("Human gate request expiry must follow creation")
        return self


class HumanGateDecisionRecord(BaseModel):
    """The actor and decision bound back to one immutable request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    decided_by_ai_user_id: str = Field(min_length=1)
    decided_session_id: str = Field(min_length=1)
    decided_tenant_id: str = Field(min_length=1)
    decision: HumanGateDecision
    request_digest: str = Field(pattern=_SHA256_PATTERN)
    binding_manifest_digest: str = Field(pattern=_SHA256_PATTERN)
    decided_at: datetime


class HumanGateConflictError(RuntimeError):
    """An immutable request, manifest, or decision conflicts with stored state."""


class VersionBindingMismatchError(ValueError):
    """The resource tuple about to run differs from the Task's locked tuple."""


class HumanGatePort(Protocol):
    async def bind_task(
        self,
        manifest: TaskVersionBindingManifest,
    ) -> TaskVersionBindingManifest: ...

    async def assert_task_bindings(
        self,
        task_id: str,
        bindings: tuple[VersionBinding, ...],
        *,
        exact: bool = False,
    ) -> None: ...

    async def get_task_binding(
        self,
        task_id: str,
    ) -> TaskVersionBindingManifest | None: ...

    async def create_request(self, request: HumanGateRequest) -> HumanGateRequest: ...

    async def get_request(self, request_id: str) -> HumanGateRequest | None: ...

    async def record_decision(
        self,
        decision: HumanGateDecisionRecord,
    ) -> HumanGateDecisionRecord: ...

    async def get_decision(
        self,
        request_id: str,
    ) -> HumanGateDecisionRecord | None: ...


def build_task_version_binding_manifest(
    *,
    task_id: str,
    bindings: tuple[VersionBinding, ...],
    locked_at: datetime,
) -> TaskVersionBindingManifest:
    canonical = _canonical_bindings(bindings)
    used_types = {binding.resource_type for binding in canonical}
    unused_resource_types = tuple(
        resource_type
        for resource_type in VERSIONED_RESOURCE_TYPES
        if resource_type not in used_types
    )
    return TaskVersionBindingManifest(
        task_id=task_id,
        bindings=canonical,
        unused_resource_types=unused_resource_types,
        manifest_digest=task_version_manifest_digest(
            task_id,
            canonical,
            unused_resource_types,
        ),
        locked_at=locked_at,
    )


def task_version_manifest_digest(
    task_id: str,
    bindings: tuple[VersionBinding, ...],
    unused_resource_types: tuple[VersionedResourceType, ...],
) -> str:
    payload = {
        "task_id": task_id,
        "bindings": [binding.model_dump(mode="json") for binding in bindings],
        "unused_resource_types": list(unused_resource_types),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_version_bindings(
    bindings: tuple[VersionBinding, ...],
) -> tuple[VersionBinding, ...]:
    """Return a deterministic tuple and reject conflicting duplicate resources."""

    return _canonical_bindings(bindings)


def _canonical_bindings(
    bindings: tuple[VersionBinding, ...],
) -> tuple[VersionBinding, ...]:
    by_resource: dict[tuple[str, str], VersionBinding] = {}
    for binding in bindings:
        key = (binding.resource_type, binding.resource_id)
        existing = by_resource.get(key)
        if existing is not None and existing != binding:
            raise ValueError("Task version bindings contain a conflicting resource")
        by_resource[key] = binding
    return tuple(by_resource[key] for key in sorted(by_resource))


__all__ = (
    "HumanGateConflictError",
    "HumanGateDecision",
    "HumanGateDecisionRecord",
    "HumanGatePort",
    "HumanGateRequest",
    "TaskVersionBindingManifest",
    "VERSIONED_RESOURCE_TYPES",
    "VersionBinding",
    "VersionBindingMismatchError",
    "VersionedResourceType",
    "build_task_version_binding_manifest",
    "canonical_version_bindings",
    "task_version_manifest_digest",
)
