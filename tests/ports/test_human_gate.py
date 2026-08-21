from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.ports.human_gate import (
    HumanGateRequest,
    TaskVersionBindingManifest,
    VersionBinding,
    build_task_version_binding_manifest,
)


def _binding(resource_id: str, digest_character: str) -> VersionBinding:
    return VersionBinding(
        resource_type="tool",
        resource_id=resource_id,
        version="1.0.0",
        digest=digest_character * 64,
    )


def test_manifest_builder_canonicalizes_resources_and_binds_task_id() -> None:
    first = _binding("oa.first", "a")
    second = _binding("oa.second", "b")

    manifest = build_task_version_binding_manifest(
        task_id="task-1",
        bindings=(second, first, first),
        locked_at=datetime(2026, 8, 21, tzinfo=UTC),
    )
    other_task = build_task_version_binding_manifest(
        task_id="task-2",
        bindings=(first, second),
        locked_at=manifest.locked_at,
    )

    assert manifest.bindings == (first, second)
    assert manifest.unused_resource_types == (
        "workflow",
        "skill",
        "prompt",
        "policy",
    )
    assert manifest.manifest_digest != other_task.manifest_digest

    invalid_payload = manifest.model_dump()
    invalid_payload["unused_resource_types"] = ()
    with pytest.raises(ValidationError, match="Unused Task resource types"):
        TaskVersionBindingManifest.model_validate(invalid_payload)


def test_manifest_rejects_conflicting_duplicate_resource() -> None:
    first = _binding("oa.same", "a")
    drifted = first.model_copy(update={"digest": "b" * 64})

    with pytest.raises(ValueError, match="conflicting resource"):
        build_task_version_binding_manifest(
            task_id="task-1",
            bindings=(first, drifted),
            locked_at=datetime(2026, 8, 21, tzinfo=UTC),
        )


def test_binding_requires_a_sha256_digest() -> None:
    with pytest.raises(ValidationError):
        VersionBinding(
            resource_type="prompt",
            resource_id="runtime.intent_router",
            version="v1",
            digest="not-a-digest",
        )


def test_human_gate_request_requires_a_future_expiry() -> None:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    with pytest.raises(ValidationError, match="expiry must follow creation"):
        HumanGateRequest(
            request_id="request-1",
            task_id="task-1",
            requested_for_ai_user_id="user-1",
            requested_session_id="session-1",
            requested_tenant_id="tenant-1",
            action_digest="a" * 64,
            request_digest="b" * 64,
            binding_manifest_digest="c" * 64,
            requested_at=now,
            expires_at=now,
        )
