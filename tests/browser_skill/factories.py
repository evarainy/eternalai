"""Synthetic neutral fixtures; no business material or credential values."""

import time

from app.browser_skill.models import (
    BrowserOwner,
    Coverage,
    DecisionCallContext,
    DecisionCandidate,
    DecisionRequest,
    DecisionSource,
    FrameHop,
    FrameObservation,
    ModelManifest,
    ObservationPolicy,
    ScopeBinding,
    ScopeStamp,
    TargetRef,
    VisibleCandidate,
    VisibleProjection,
)

DIGEST = "a" * 64


def binding() -> ScopeBinding:
    return ScopeBinding(
        owner=BrowserOwner(tenant_id="tenant", user_id="user", session_id="session"),
        binding_id="binding",
        binding_revision=1,
        authorization_revision=2,
        lease_epoch=3,
    )


def scope() -> ScopeStamp:
    return ScopeStamp(
        page_id="page",
        page_epoch=1,
        frame_path=(
            FrameHop(frame_id="main", frame_epoch=1),
            FrameHop(frame_id="nested", frame_epoch=2),
        ),
        region_id="inbox",
        region_digest=DIGEST,
    )


def request() -> DecisionRequest:
    stamp = scope()
    return DecisionRequest(
        request_id="request",
        scope=stamp,
        criteria=("Open the pending synthetic item",),
        candidates=tuple(
            DecisionCandidate(
                ref=TargetRef(target_id=target, candidate_epoch=1, scope=stamp),
                role="button",
                name=name,
            )
            for target, name in (("target_a", "Open"), ("target_b", "Close"))
        ),
    )


def source() -> DecisionSource:
    return DecisionSource(
        source_id="mock_oa", origin="https://fixture.invalid", fixture_digest=DIGEST
    )


def context() -> DecisionCallContext:
    return DecisionCallContext(
        deadline_monotonic=time.monotonic() + 10,
        manifest=ModelManifest(
            request_model="jev-test-alias",
            deployment_model="jev-test-1",
            manifest_digest=DIGEST,
        ),
        source=source(),
        current_targets=lambda: tuple(c.ref for c in request().candidates),
    )


def policy() -> ObservationPolicy:
    return ObservationPolicy(
        policy_id="public_labels",
        digest=DIGEST,
        allowed_names=("Open", "Close"),
        allowed_roles=("button",),
    )


def projection() -> VisibleProjection:
    req = request()
    coverage = Coverage(state="complete", reason="complete")
    return VisibleProjection(
        policy_id="public_labels",
        policy_digest=DIGEST,
        binding=binding(),
        scope=req.scope,
        frames=FrameObservation(
            frame=req.scope.frame_path[0],
            coverage=coverage,
            children=(FrameObservation(frame=req.scope.frame_path[1], coverage=coverage),),
        ),
        coverage=coverage,
        candidates=tuple(
            VisibleCandidate(
                ref=c.ref,
                role=c.role,
                name=c.name,
                value_state="not_applicable",
                visible=True,
                enabled=True,
            )
            for c in req.candidates
        ),
    )
