"""Browser projections for owner-authorized durable Run state.

Trusted composition must authenticate/authorize each read against current state.
Owner checks here supplement that boundary; identifiers never confer access.
CommitFact is process-local provenance, not proof of a database transaction. Its
private mint seam is reserved for the store's completed transaction path;
tests mint explicitly synthetic facts, never durable-worker acceptance evidence.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Never, Self, SupportsIndex

from pydantic import Field, field_validator, model_validator

from app.browser_skill.models import BrowserOwner, Contract, Digest, OpaqueId, ScopeBinding
from app.browser_skill.registry import Publication
from app.browser_skill.results import ResultState
from app.browser_skill.scheduling import CancelSnapshot
from app.browser_skill.session import require_aware
from app.browser_skill.trajectory import SkillDraft

SafeRevision = Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]
BrowserErrorCode = Literal[
    "browser_async_required",
    "browser_request_id_required",
    "request_key_conflict",
    "browser_target_stale",
    "browser_effect_unknown",
    "browser_resource_quarantined",
    "browser_decision_unavailable",
    "browser_decision_overloaded",
    "browser_decision_timeout",
    "browser_decision_cancelled",
    "browser_decision_invalid_response",
    "browser_decision_model_mismatch",
    "browser_decision_input_unsupported",
    "browser_verification_failed",
    "browser_not_sent",
    "browser_cancelled",
    "browser_read_execution_failed",
]


def _raw_fields(value: object) -> object:
    """Preserve scalar types before serializers can normalize unchecked copies."""
    if isinstance(value, Contract):
        return {name: _raw_fields(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, tuple):
        return tuple(_raw_fields(item) for item in value)
    return value


def _checked[T: Contract](value: T) -> T:
    return type(value).model_validate(_raw_fields(value))


class BrowserRunRef(Contract):
    owner: BrowserOwner = Field(exclude=True, repr=False)
    task_id: OpaqueId
    run_id: OpaqueId
    state_revision: SafeRevision


class _Private:
    __slots__ = ()

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("browser_projection_serialization_forbidden")

    def __getstate__(self) -> Never:
        raise TypeError("browser_projection_serialization_forbidden")


_COMMIT_SEAL = object()


@dataclass(frozen=True, slots=True, repr=False, init=False)
class CommitFact(_Private):
    _reference: BrowserRunRef
    __getstate__ = _Private.__getstate__

    def __init__(self, reference: BrowserRunRef, *, _seal: object) -> None:
        if _seal is not _COMMIT_SEAL or type(reference) is not BrowserRunRef:
            raise TypeError("browser_commit_fact_required")
        owner = reference.owner
        checked = BrowserRunRef(
            owner=BrowserOwner(
                tenant_id=owner.tenant_id, user_id=owner.user_id, session_id=owner.session_id
            ),
            task_id=reference.task_id,
            run_id=reference.run_id,
            state_revision=reference.state_revision,
        )
        object.__setattr__(self, "_reference", checked)


def _mint_commit_fact(reference: BrowserRunRef) -> CommitFact:
    """Private store seam, invoked only after the Run/Task transaction commits."""
    return CommitFact(reference, _seal=_COMMIT_SEAL)


def _owned_fact(fact: CommitFact, reader: BrowserOwner) -> BrowserRunRef:
    if type(fact) is not CommitFact:
        raise TypeError("browser_commit_fact_required")
    reference = fact._reference
    if reference.owner != reader:
        raise ValueError("browser_projection_owner_mismatch")
    return reference


class BrowserAcceptedView(Contract):
    kind: Literal["accepted"] = "accepted"
    task_id: OpaqueId
    run_id: OpaqueId
    state_revision: SafeRevision


def project_accepted(fact: CommitFact, reader: BrowserOwner) -> BrowserAcceptedView:
    """Project a store-supplied fact; never infer acceptance from local execution."""
    reference = _owned_fact(fact, reader)
    return BrowserAcceptedView(
        task_id=reference.task_id, run_id=reference.run_id, state_revision=reference.state_revision
    )


class BrowserProgressView(Contract):
    phase: Literal["queued", "acquiring", "running", "verifying", "waiting_user"]
    completed_steps: SafeRevision | None = None
    total_steps: SafeRevision | None = None

    @model_validator(mode="after")
    def consistent_counts(self) -> Self:
        if (self.completed_steps is None) != (self.total_steps is None):
            raise ValueError("browser_progress_counts_incomplete")
        if (
            self.completed_steps is not None
            and self.total_steps is not None
            and self.completed_steps > self.total_steps
        ):
            raise ValueError("browser_progress_counts_invalid")
        return self


class BrowserCancelView(Contract):
    requested: bool
    acknowledged: bool

    @model_validator(mode="after")
    def consistent_cancel(self) -> Self:
        if self.acknowledged and not self.requested:
            raise ValueError("browser_cancel_request_required")
        return self


class BrowserResultView(Contract):
    business: Literal["completed", "failed", "cancelled"]
    effect: Literal["not_sent", "acknowledged", "unknown"]
    verification: Literal["verified", "mismatch", "incomplete", "unsupported"] | None
    cleanup: Literal["pending", "released", "terminated", "quarantined", "failed"]
    error_code: BrowserErrorCode | None
    # Fixed provider diagnostics preserve the failure without exposing vendor data.
    dispatch_failure_code: (
        Literal[
            "unavailable",
            "overloaded",
            "invalid_request",
            "resource_not_found",
            "unsupported",
            "denied",
            "stale",
            "subject_mismatch",
            "invalid_response",
            "timeout",
            "cancelled",
            "effect_unknown",
            "quarantined",
        ]
        | None
    ) = None
    terminal_revision: SafeRevision
    automatic_replay: Literal[False] = False

    @field_validator("automatic_replay", mode="before")
    @classmethod
    def replay_is_false(cls, value: object) -> object:
        if value is not False:
            raise ValueError("browser_replay_forbidden")
        return value

    @model_validator(mode="after")
    def consistent_result(self) -> Self:
        if self.business == "completed" and self.verification != "verified":
            raise ValueError("browser_result_verification_inconsistent")
        if self.business == "completed" and self.error_code is not None:
            raise ValueError("browser_result_success_has_error")
        if self.business == "failed" and self.error_code is None:
            raise ValueError("browser_result_failure_code_required")
        if self.business == "cancelled" and self.error_code != "browser_cancelled":
            raise ValueError("browser_result_cancel_code_required")
        if (
            self.effect == "unknown"
            and self.verification != "verified"
            and (self.business != "failed" or self.error_code != "browser_effect_unknown")
        ):
            raise ValueError("browser_result_unknown_not_resolved")
        return self


def project_result(state: ResultState, terminal_revision: int) -> BrowserResultView:
    checked = _checked(state)
    if checked.business == "running":
        raise ValueError("browser_result_not_terminal")
    return BrowserResultView(
        business=checked.business,
        effect=checked.effect,
        verification=checked.verification.status if checked.verification else None,
        cleanup=checked.cleanup,
        error_code=checked.error_code,
        dispatch_failure_code=checked.dispatch_failure.code if checked.dispatch_failure else None,
        terminal_revision=terminal_revision,
    )


class BrowserArtifactView(Contract):
    """Metadata only. Even available artifacts require a fresh owner-authorized fetch."""

    artifact_id: OpaqueId
    kind: Literal["result", "report", "download", "draft"]
    media_type: Literal[
        "application/json", "application/pdf", "text/plain", "text/csv", "image/png"
    ]
    size_bytes: SafeRevision
    expires_at: datetime
    availability: Literal["available", "expired", "unavailable"]

    @model_validator(mode="after")
    def aware_expiry(self) -> Self:
        require_aware(self.expires_at)
        return self


class BrowserDraftView(Contract):
    draft_id: OpaqueId
    draft_revision: Annotated[int, Field(ge=1, le=9_007_199_254_740_991)]
    base_revision: SafeRevision
    base_publication_digest: Digest
    draft_digest: Digest
    state: Literal["draft", "validated", "rejected"]
    rejection: Literal["invalid_pattern", "unsafe_reference", "dependency_mismatch"] | None
    parameter_names: Annotated[tuple[OpaqueId, ...], Field(max_length=64)]
    executable: Literal[False] = False

    @field_validator("executable", mode="before")
    @classmethod
    def execution_is_false(cls, value: object) -> object:
        if value is not False:
            raise ValueError("browser_draft_not_executable")
        return value

    @model_validator(mode="after")
    def consistent_draft(self) -> Self:
        if (self.state == "rejected") != (self.rejection is not None):
            raise ValueError("browser_draft_rejection_inconsistent")
        if len(self.parameter_names) != len(set(self.parameter_names)):
            raise ValueError("browser_draft_parameter_duplicate")
        return self


def project_draft(
    draft: SkillDraft,
    current_binding: ScopeBinding,
    publication: Publication,
    base_revision: int,
) -> BrowserDraftView:
    """Project only registered names; neither values nor executable Skill content."""
    draft.validate_base(current_binding, publication, base_revision)
    manifest = draft.manifest
    return BrowserDraftView(
        draft_id=manifest.draft_id,
        draft_revision=manifest.draft_revision,
        base_revision=manifest.base_revision,
        base_publication_digest=manifest.base_publication.publication_digest,
        draft_digest=manifest.digest,
        state=manifest.state,
        rejection=manifest.rejection,
        parameter_names=tuple(item.name for item in manifest.parameter_whitelist),
    )


@dataclass(frozen=True, slots=True, repr=False)
class OwnedRunValue[T: Contract](_Private):
    """Trusted store's child ownership; this carrier is never a wire value or grant."""

    reference: BrowserRunRef
    value: T
    __getstate__ = _Private.__getstate__


class BrowserRunView(Contract):
    schema_version: Literal["browser.run.v1"] = "browser.run.v1"
    task_id: OpaqueId
    run_id: OpaqueId
    state_revision: SafeRevision
    status: Literal["running", "waiting_user", "completed", "failed", "cancelled"]
    progress: BrowserProgressView | None
    cancel: BrowserCancelView
    result: BrowserResultView | None
    artifacts: Annotated[tuple[BrowserArtifactView, ...], Field(max_length=64)] = ()
    draft: BrowserDraftView | None = None

    @model_validator(mode="after")
    def consistent_snapshot(self) -> Self:
        terminal = self.status in {"completed", "failed", "cancelled"}
        if terminal:
            if (
                self.result is None
                or self.progress is not None
                or self.result.business != self.status
                or self.result.terminal_revision > self.state_revision
            ):
                raise ValueError("browser_run_terminal_inconsistent")
        elif (
            self.result is not None
            or self.progress is None
            or (self.status == "waiting_user") != (self.progress.phase == "waiting_user")
        ):
            raise ValueError("browser_run_progress_inconsistent")
        if self.status == "cancelled" and not self.cancel.acknowledged:
            raise ValueError("browser_run_cancel_not_acknowledged")
        if len({item.artifact_id for item in self.artifacts}) != len(self.artifacts):
            raise ValueError("browser_run_artifact_duplicate")
        return self


def _owned_child[T: Contract](reference: BrowserRunRef, child: OwnedRunValue[T]) -> T:
    actual = child.reference
    BrowserRunRef(
        owner=actual.owner,
        task_id=actual.task_id,
        run_id=actual.run_id,
        state_revision=actual.state_revision,
    )
    if (
        actual.owner != reference.owner
        or actual.task_id != reference.task_id
        or actual.run_id != reference.run_id
        or actual.state_revision > reference.state_revision
    ):
        raise ValueError("browser_projection_child_mismatch")
    return _checked(child.value)


def project_run(
    fact: CommitFact,
    reader: BrowserOwner,
    cancellation: CancelSnapshot,
    *,
    progress: BrowserProgressView | None = None,
    result: OwnedRunValue[ResultState] | None = None,
    terminal_revision: int | None = None,
    artifacts: tuple[OwnedRunValue[BrowserArtifactView], ...] = (),
    draft: OwnedRunValue[BrowserDraftView] | None = None,
) -> BrowserRunView:
    reference = _owned_fact(fact, reader)
    if (
        cancellation.binding.owner != reader
        or cancellation.run_id != reference.run_id
        or cancellation.revision > reference.state_revision
    ):
        raise ValueError("browser_projection_cancel_mismatch")
    terminal = None
    if result is not None:
        if terminal_revision is None:
            raise ValueError("browser_projection_terminal_revision_required")
        terminal = project_result(_owned_child(reference, result), terminal_revision)
    elif terminal_revision is not None:
        raise ValueError("browser_projection_terminal_missing")
    status: Literal["running", "waiting_user", "completed", "failed", "cancelled"] = (
        terminal.business
        if terminal is not None
        else "waiting_user"
        if progress is not None and progress.phase == "waiting_user"
        else "running"
    )
    projected = BrowserRunView(
        task_id=reference.task_id,
        run_id=reference.run_id,
        state_revision=reference.state_revision,
        status=status,
        progress=progress,
        cancel=BrowserCancelView(
            requested=cancellation.requested, acknowledged=cancellation.acknowledged
        ),
        result=terminal,
        artifacts=tuple(_owned_child(reference, item) for item in artifacts),
        draft=_owned_child(reference, draft) if draft else None,
    )
    # Pydantic normally trusts nested instances, including unchecked model_copy.
    return _checked(projected)


def compare_run_update(
    current: BrowserRunView | None,
    incoming: BrowserRunView,
    *,
    task_id: str,
    run_id: str,
    request_generation: int,
    current_generation: int,
) -> Literal["apply", "drop", "noop", "conflict"]:
    """Client-local stale-response protection, never an authentication mechanism."""
    incoming = _checked(incoming)
    if current is not None:
        current = _checked(current)
    for generation in (request_generation, current_generation):
        if type(generation) is not int or not 0 <= generation <= 9_007_199_254_740_991:
            raise ValueError("browser_client_generation_invalid")
    if request_generation != current_generation:
        return "drop"
    if (incoming.task_id, incoming.run_id) != (task_id, run_id):
        return "drop"
    if current is None:
        return "apply"
    if (current.task_id, current.run_id) != (task_id, run_id):
        return "conflict"
    if incoming.state_revision < current.state_revision:
        return "drop"
    if incoming.state_revision == current.state_revision:
        return "noop" if incoming == current else "conflict"
    if (current.cancel.requested and not incoming.cancel.requested) or (
        current.cancel.acknowledged and not incoming.cancel.acknowledged
    ):
        return "conflict"
    if current.result is not None:
        if incoming.result is None:
            return "drop"
        if current.result.model_dump(exclude={"cleanup"}) != incoming.result.model_dump(
            exclude={"cleanup"},
        ):
            return "conflict"
    if current.progress is not None and incoming.progress is not None:
        before, after = current.progress, incoming.progress
        if before.total_steps is not None and (
            before.total_steps != after.total_steps
            or after.completed_steps is None
            or (
                before.completed_steps is not None
                and after.completed_steps < before.completed_steps
            )
        ):
            return "conflict"
    return "apply"
