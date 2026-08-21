"""Value-free digest builders for Task version binding manifests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Mapping

from app.ports.capability_registry import CapabilitySpec
from app.ports.human_gate import VersionBinding, canonical_version_bindings

if TYPE_CHECKING:
    from app.workflow.models import WorkflowDefinition


def capability_version_bindings(
    capability: CapabilitySpec,
) -> tuple[VersionBinding, ...]:
    """Bind the registered Tool and any declared capability-specific Policy."""

    if capability.type == "workflow":
        raise ValueError("Workflow capabilities require their definition snapshot")
    capability_digest = _digest(capability.model_dump(mode="json"))
    bindings = [
        VersionBinding(
            resource_type="tool",
            resource_id=capability.capability_id,
            version=capability.version,
            digest=capability_digest,
        )
    ]
    if capability.policy_digest is not None:
        bindings.append(
            VersionBinding(
                resource_type="policy",
                resource_id=capability.capability_id,
                version=capability.version,
                digest=_digest(
                    {
                        "capability_id": capability.capability_id,
                        "capability_version": capability.version,
                        "policy_digest": capability.policy_digest,
                    }
                ),
            )
        )
    return tuple(bindings)


def workflow_confirmation_action_digest(
    *,
    workflow_id: str,
    workflow_version: str,
    step_id: str,
    step_index: int,
    waiting_capability_id: str,
    confirmed_capability_id: str,
    arguments: Mapping[str, Any],
) -> str:
    """Hash the exact resolved Workflow action without persisting its values."""

    return _digest(
        {
            "workflow_id": workflow_id,
            "workflow_version": workflow_version,
            "step_id": step_id,
            "step_index": step_index,
            "waiting_capability_id": waiting_capability_id,
            "confirmed_capability_id": confirmed_capability_id,
            "arguments": dict(arguments),
        }
    )


def workflow_version_binding(
    capability: CapabilitySpec,
    definition: WorkflowDefinition,
) -> VersionBinding:
    if capability.type != "workflow":
        raise ValueError("Workflow binding requires a Workflow capability")
    if (
        capability.capability_id != definition.workflow_id
        or capability.version != definition.version
    ):
        raise ValueError("Workflow registry and definition versions must match")
    return VersionBinding(
        resource_type="workflow",
        resource_id=definition.workflow_id,
        version=definition.version,
        digest=_digest(
            {
                "registry": capability.model_dump(mode="json"),
                "definition": asdict(definition),
            }
        ),
    )


def prompt_version_binding(
    *,
    resource_id: str,
    version: str,
    model: str,
    prompts: tuple[str, ...],
    response_schema: Mapping[str, Any],
) -> VersionBinding:
    return VersionBinding(
        resource_type="prompt",
        resource_id=resource_id,
        version=version,
        digest=_digest(
            {
                "model": model,
                "prompts": prompts,
                "response_schema": response_schema,
            }
        ),
    )


def immutable_request_digest(
    *,
    task_id: str,
    action_digest: str,
    preview: Mapping[str, Any],
    binding_manifest_digest: str,
) -> str:
    """Hash the exact server-generated preview, action, and version manifest."""

    return _digest(
        {
            "task_id": task_id,
            "action_digest": action_digest,
            "preview": dict(preview),
            "binding_manifest_digest": binding_manifest_digest,
        }
    )


def merge_version_bindings(
    *groups: tuple[VersionBinding, ...],
) -> tuple[VersionBinding, ...]:
    return canonical_version_bindings(tuple(item for group in groups for item in group))


def _digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = (
    "capability_version_bindings",
    "immutable_request_digest",
    "merge_version_bindings",
    "prompt_version_binding",
    "workflow_confirmation_action_digest",
    "workflow_version_binding",
)
