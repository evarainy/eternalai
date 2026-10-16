"""Value-free preparation for one confirmed browser write Run.

The request is presented through the existing HumanGate. This object is only an
immutable digest binding; the PostgreSQL Run store consumes the actual persisted
decision under its own Run fence before the Executor may send a write.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Self, cast
from uuid import NAMESPACE_URL, uuid5

from pydantic import model_validator

from app.browser_skill.models import (
    BrowserOwner,
    ConfirmedBusinessKey,
    Contract,
    Digest,
    OpaqueId,
)
from app.browser_skill.publication_contracts import BrowserPublicationManifest
from app.browser_skill.registry import canonical_digest
from app.ports.browser_run_store import RunSnapshot
from app.ports.human_gate import HumanGateRequest, TaskVersionBindingManifest


def _owner_digest(owner: BrowserOwner) -> str:
    return canonical_digest("browser.write_owner.v1", {
        "owner": [owner.tenant_id, owner.user_id, owner.session_id],
    })


def _write_steps_digest(manifest: BrowserPublicationManifest) -> str:
    steps = tuple(rule for rule in manifest.site.steps if (
        rule.step.effect == "may_write" or rule.effect.actual_effect == "may_write"
    ))
    if not steps or any(
        rule.step.effect != "may_write" or rule.effect.actual_effect != "may_write"
        for rule in steps
    ):
        raise ValueError("browser_write_effect_unproved")
    return canonical_digest("browser.write_steps.v1", {
        "steps": [rule.model_dump(mode="json") for rule in steps],
    })


class BrowserWritePreparation(Contract):
    """Exact Run/owner/object/parameter/version/expiry digest, with no values."""

    request_id: OpaqueId
    task_id: OpaqueId
    run_id: OpaqueId
    owner_digest: Digest
    publication_digest: Digest
    skill_id: OpaqueId
    skill_version: OpaqueId
    skill_digest: Digest
    site_id: OpaqueId
    site_digest: Digest
    verifier_id: OpaqueId
    verifier_digest: Digest
    binding_id: OpaqueId
    binding_revision: int
    input_revision: int
    parameter_set_digest: Digest
    object_type: OpaqueId
    business_key_digest: Digest
    business_key_confirmation_ref: OpaqueId
    write_steps_digest: Digest
    binding_manifest_digest: Digest
    requested_at: datetime
    expires_at: datetime
    action_digest: Digest
    request_digest: Digest

    def _action_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={
            "action_digest", "request_digest", "request_id",
            "binding_manifest_digest", "requested_at",
        })

    @model_validator(mode="after")
    def immutable_digest(self) -> Self:
        if (
            self.requested_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.expires_at <= self.requested_at
            or self.expires_at > self.requested_at + timedelta(minutes=15)
            or self.binding_revision < 1 or self.input_revision < 1
        ):
            raise ValueError("browser_write_preparation_window_invalid")
        action = canonical_digest("browser.write_action.v1", self._action_payload())
        request = canonical_digest("browser.write_request.v1", {
            "request_id": self.request_id,
            "action_digest": action,
            "binding_manifest_digest": self.binding_manifest_digest,
            "requested_at": self.requested_at.astimezone(UTC).isoformat(),
        })
        if self.action_digest != action or self.request_digest != request:
            raise ValueError("browser_write_preparation_digest_invalid")
        return self

    def gate_request(self, owner: BrowserOwner) -> HumanGateRequest:
        if self.owner_digest != _owner_digest(owner):
            raise ValueError("browser_write_preparation_owner_invalid")
        return HumanGateRequest(
            request_id=self.request_id, task_id=self.task_id,
            requested_for_ai_user_id=owner.user_id,
            requested_session_id=owner.session_id,
            requested_tenant_id=owner.tenant_id,
            action_digest=self.action_digest,
            request_digest=self.request_digest,
            binding_manifest_digest=self.binding_manifest_digest,
            requested_at=self.requested_at, expires_at=self.expires_at,
        )


def prepare_browser_write(
    run: RunSnapshot, manifest: BrowserPublicationManifest,
    business_key: ConfirmedBusinessKey,
    task_binding: TaskVersionBindingManifest,
    *, now: datetime,
) -> BrowserWritePreparation:
    """Build a preview only from a frozen Run and trusted confirmed-key input."""
    if (
        run.status != "running" or run.phase != "queued"
        or run.worker_id is not None or run.lease_epoch is not None
        or run.cancel_requested or run.effect != "not_sent"
        or run.verification is not None
        or manifest.digest != run.admission.publication_digest.hex()
        or task_binding.task_id != run.task_id
        or not any(
            binding.resource_type == "skill"
            and binding.resource_id == manifest.skill.skill_id
            and binding.version == manifest.skill.version
            and binding.digest == manifest.skill.digest
            for binding in task_binding.bindings
        )
        or business_key.object_type != manifest.site.read_rule.object_type
        or now.tzinfo is None
    ):
        raise ValueError("browser_write_preparation_invalid")
    requested_at = now.astimezone(UTC)
    expires_at = min(requested_at + timedelta(minutes=15),
                     run.admission.auth_expires_at.astimezone(UTC))
    if expires_at <= requested_at:
        raise ValueError("browser_write_preparation_expired")
    request_id = uuid5(NAMESPACE_URL, f"browser-write-v1:{run.run_id}").hex
    fields: dict[str, object] = dict(
        request_id=request_id, task_id=run.task_id, run_id=run.run_id,
        owner_digest=_owner_digest(run.owner),
        publication_digest=manifest.digest,
        skill_id=manifest.skill.skill_id,
        skill_version=manifest.skill.version,
        skill_digest=manifest.skill.digest,
        site_id=manifest.site.site_id,
        site_digest=manifest.site.digest,
        verifier_id=manifest.verifier.verifier_id,
        verifier_digest=manifest.verifier.digest,
        binding_id=run.admission.binding_id,
        binding_revision=run.admission.binding_revision,
        input_revision=run.admission.input_revision,
        parameter_set_digest=run.admission.input_digest.hex(),
        object_type=business_key.object_type,
        business_key_digest=business_key.key_digest,
        business_key_confirmation_ref=business_key.confirmation_ref,
        write_steps_digest=_write_steps_digest(manifest),
        binding_manifest_digest=task_binding.manifest_digest,
        requested_at=requested_at, expires_at=expires_at,
    )
    provisional = BrowserWritePreparation.model_construct(
        **cast(Any, fields), action_digest="0" * 64, request_digest="0" * 64,
    )
    action = canonical_digest("browser.write_action.v1", provisional._action_payload())
    request = canonical_digest("browser.write_request.v1", {
        "request_id": request_id,
        "action_digest": action,
        "binding_manifest_digest": task_binding.manifest_digest,
        "requested_at": requested_at.isoformat(),
    })
    return BrowserWritePreparation.model_validate({
        **fields, "action_digest": action, "request_digest": request,
    })
