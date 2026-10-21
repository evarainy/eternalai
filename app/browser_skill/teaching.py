"""Controlled teaching replay for newly composed, registered browser primitives.

Only trusted composition may install the callbacks. This service never accepts
browser code, raw DOM, parameter values, a claimed approval, or a claimed replay
result from a request. The replay callback must use the sole Executor and the
independent Verifier; the ordinary PG Run remains the result authority.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Never, SupportsIndex

from pydantic import TypeAdapter

from app.browser_skill.compiler import (
    DurableTeachingCompilerAuthority,
    DurableTeachingDraft,
    compile_authorized_run,
)
from app.browser_skill.models import Digest, OpaqueId, ScopeBinding
from app.browser_skill.registry import canonical_digest
from app.browser_skill.trajectory import (
    _binding_copy,
)
from app.ports.browser_run_store import RunSnapshot


class ReplayError(ValueError):
    """Fixed codes only; never include the parameter set or provider details."""


@dataclass(frozen=True, slots=True, repr=False)
class TeachingReplayProof:
    """A handle to a stored, verified Run; never a substitute for rereading it."""

    binding: ScopeBinding
    draft_digest: str
    publication_digest: str
    source_run_id: str
    replay_task_id: str
    replay_run_id: str
    heldout_parameter_set_ref: str
    verifier_evidence_digest: str
    digest: str

    def __post_init__(self) -> None:
        binding = _binding_copy(self.binding)
        for value in (self.draft_digest, self.publication_digest,
                      self.verifier_evidence_digest, self.digest):
            TypeAdapter(Digest).validate_python(value, strict=True)
        for value in (self.source_run_id, self.replay_task_id, self.replay_run_id,
                      self.heldout_parameter_set_ref):
            TypeAdapter(OpaqueId).validate_python(value, strict=True)
        expected = canonical_digest("browser_teaching_replay.v1", {
            "draft_digest": self.draft_digest,
            "publication_digest": self.publication_digest,
            "source_run_id": self.source_run_id,
            "replay_task_id": self.replay_task_id,
            "replay_run_id": self.replay_run_id,
            "heldout_parameter_set_ref": self.heldout_parameter_set_ref,
            "verifier_evidence_digest": self.verifier_evidence_digest,
        })
        if self.digest != expected:
            raise ReplayError("teaching_replay_digest_invalid")
        object.__setattr__(self, "binding", binding)

    def __repr__(self) -> str:
        return "<TeachingReplayProof: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("teaching_private_serialization_forbidden")

    def __getstate__(self) -> Never:
        raise TypeError("teaching_private_serialization_forbidden")


@dataclass(frozen=True, slots=True, repr=False)
class TeachingReplayAuthority:
    """Exact source/Run/parameter lookups and sole Executor dispatch adapter.

    ``heldout_set`` checks an owner-scoped sealed input collection against the
    draft whitelist and source set, including current object type and subject.
    ``execute_draft`` must persist a Run and independently verify its result.
    ``record_proof`` must store the proof with that Run, not in a second catalog.
    """

    compiler: DurableTeachingCompilerAuthority
    current_binding: Callable[[], ScopeBinding]
    authorize_replay: Callable[[DurableTeachingDraft, ScopeBinding], Awaitable[bool]]
    heldout_set: Callable[[DurableTeachingDraft, str, ScopeBinding], Awaitable[bool]]
    execute_draft: Callable[
        [DurableTeachingDraft, str, ScopeBinding], Awaitable[tuple[str, str]]
    ]
    read_run: Callable[[str, str, ScopeBinding], Awaitable[RunSnapshot]]
    same_subject: Callable[[str, str, ScopeBinding], Awaitable[bool]]
    record_proof: Callable[[TeachingReplayProof], Awaitable[bool]]
    deadline_monotonic: float
    clock: Callable[[], float]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.compiler, DurableTeachingCompilerAuthority)
            or not all(callable(value) for value in (
                self.current_binding, self.authorize_replay, self.heldout_set,
                self.execute_draft, self.read_run, self.same_subject,
                self.record_proof, self.clock,
            ))
            or type(self.deadline_monotonic) not in (int, float)
            or not math.isfinite(self.deadline_monotonic)
        ):
            raise ReplayError("teaching_replay_authority_required")

    def __repr__(self) -> str:
        return "<TeachingReplayAuthority: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("teaching_private_serialization_forbidden")

    def __getstate__(self) -> Never:
        raise TypeError("teaching_private_serialization_forbidden")


def _fresh(
    draft: DurableTeachingDraft, binding: ScopeBinding,
    authority: TeachingReplayAuthority,
) -> None:
    now = authority.clock()
    if type(now) not in (int, float) or not math.isfinite(now) or now >= (
        authority.deadline_monotonic
    ):
        raise ReplayError("teaching_replay_expired")
    if _binding_copy(authority.current_binding()) != binding:
        raise ReplayError("teaching_replay_binding_stale")
    candidate = compile_authorized_run(
        draft.source, binding=binding,
        target_skill_id=draft.publication.skill.skill_id,
        version=draft.publication.skill.version,
        site_version=draft.publication.site.version,
        parameter_whitelist=draft.parameter_whitelist,
        authority=authority.compiler,
    )
    if candidate != draft:
        raise ReplayError("teaching_replay_draft_stale")


async def replay_draft(
    draft: DurableTeachingDraft,
    *,
    binding: ScopeBinding,
    heldout_parameter_set_ref: str,
    authority: TeachingReplayAuthority,
) -> TeachingReplayProof:
    """Run an untrained parameter set through one controlled Run and Verifier."""
    try:
        draft = DurableTeachingDraft(
            draft.binding, draft.source, draft.publication,
            draft.parameter_whitelist, draft.digest,
        )
        binding = _binding_copy(binding)
        heldout_parameter_set_ref = TypeAdapter(OpaqueId).validate_python(
            heldout_parameter_set_ref, strict=True,
        )
        if draft.binding != binding or heldout_parameter_set_ref == (
            draft.source.manifest.parameter_set_ref
        ):
            raise ReplayError("teaching_replay_not_heldout")
        _fresh(draft, binding, authority)
        if await authority.authorize_replay(draft, binding) is not True or await (
            authority.heldout_set(draft, heldout_parameter_set_ref, binding)
        ) is not True:
            raise ReplayError("teaching_replay_denied")
        _fresh(draft, binding, authority)
        # This callback is the ONLY action seam. Its implementation must enter
        # the existing Run/Executor/Verifier path and obey current authorization.
        task_id, run_id = await authority.execute_draft(
            draft, heldout_parameter_set_ref, binding,
        )
        task_id = TypeAdapter(OpaqueId).validate_python(task_id, strict=True)
        run_id = TypeAdapter(OpaqueId).validate_python(run_id, strict=True)
        run = await authority.read_run(task_id, run_id, binding)
        if (
            run.task_id != task_id or run.run_id != run_id
            or run.owner != binding.owner
            or run.admission.binding_id != binding.binding_id
            or run.admission.binding_revision != binding.binding_revision
            or run.admission.publication_digest.hex() != draft.publication.digest
            or run.status != "completed" or run.effect != "acknowledged"
            or run.verification != "verified"
            or run.verification_evidence_digest is None
            or run.error_code is not None or run.cancel_requested
            or run.cancel_acknowledged
            or await authority.same_subject(
                draft.source.manifest.source_run_id, run_id, binding,
            ) is not True
        ):
            raise ReplayError("teaching_replay_unverified")
        _fresh(draft, binding, authority)
        if await authority.heldout_set(
            draft, heldout_parameter_set_ref, binding,
        ) is not True or await authority.authorize_replay(draft, binding) is not True:
            raise ReplayError("teaching_replay_denied")
        verifier_digest = run.verification_evidence_digest.hex()
        data = dict(
            draft_digest=draft.digest,
            publication_digest=draft.publication.digest,
            source_run_id=draft.source.manifest.source_run_id,
            replay_task_id=task_id,
            replay_run_id=run_id,
            heldout_parameter_set_ref=heldout_parameter_set_ref,
            verifier_evidence_digest=verifier_digest,
        )
        proof = TeachingReplayProof(
            binding=binding, **data,
            digest=canonical_digest("browser_teaching_replay.v1", data),
        )
        if await authority.record_proof(proof) is not True:
            raise ReplayError("teaching_replay_proof_unavailable")
        return proof
    except ReplayError:
        raise
    except Exception:
        raise ReplayError("teaching_replay_unavailable") from None
