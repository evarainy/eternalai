"""Independent confirmed-key verification, with current authorization on both sides."""

import time
from typing import Literal, Protocol

from app.browser_skill.models import (
    ActionCommand,
    BrowserFailure,
    BrowserOperationError,
    BrowserSessionRef,
    ExecutionContext,
    ReadSpec,
    ScopeBinding,
    VerificationResult,
)
from app.browser_skill.site_rules import RegisteredSitePlan
from app.ports.browser import SiteAdapter, WebAdapter


class ReadSpecResolver(Protocol):
    """Trusted process-local admission lookup, independent of DOM/Decision.

    Consult the current confirmation and approved input records; a key supplied
    by the caller or selected row is not a confirmation. No raw value is returned.
    """

    async def __call__(
        self, session: BrowserSessionRef, context: ExecutionContext, binding: ScopeBinding
    ) -> ReadSpec: ...


def failure(
    code: Literal[
        "cancelled",
        "timeout",
        "stale",
        "denied",
        "invalid_response",
        "unsupported",
        "resource_not_found",
        "effect_unknown",
        "unavailable",
    ],
    *,
    dispatched: bool = False,
) -> BrowserOperationError:
    """Closed, sanitized error transport; never propagate private exception text."""
    return BrowserOperationError(
        BrowserFailure(
            code=code,
            phase="dispatch" if dispatched else "observe",
            dispatch_state="possibly_sent" if dispatched else "not_sent",
            cleanup_required=dispatched,
        )
    )


def check_liveness(context: ExecutionContext) -> None:
    if context.cancellation.is_set():
        raise failure("cancelled")
    if time.monotonic() >= context.deadline_monotonic:
        raise failure("timeout")


async def current_binding(session: BrowserSessionRef, context: ExecutionContext) -> ScopeBinding:
    check_liveness(context)
    current = await context.current_binding(session)
    check_liveness(context)
    if current != context.expected_binding or current != session.binding:
        raise failure("stale")
    return current


async def authorize_current(
    session: BrowserSessionRef,
    context: ExecutionContext,
    subject: ActionCommand | ReadSpec,
) -> ScopeBinding:
    current = await current_binding(session, context)
    if subject.binding != current:
        raise failure("stale")
    await context.authorize(session, context.skill, subject, current)
    if await current_binding(session, context) != current:
        raise failure("stale")
    return current


async def confirmed_spec(
    session: BrowserSessionRef,
    context: ExecutionContext,
    plan: RegisteredSitePlan,
    resolver: ReadSpecResolver,
) -> ReadSpec:
    current = await current_binding(session, context)
    spec = await resolver(session, context, current)
    plan.validate_read(spec)
    await authorize_current(session, context, spec)
    return spec


class IndependentVerifier:
    __slots__ = ("_web", "_site", "_resolve")

    def __init__(self, web: WebAdapter, site: SiteAdapter, resolver: ReadSpecResolver) -> None:
        self._web = web
        self._site = site
        self._resolve = resolver

    async def verify(
        self, session: BrowserSessionRef, spec: ReadSpec, context: ExecutionContext
    ) -> VerificationResult:
        try:
            plan = self._site.bootstrap(context.skill)
            plan.validate_context(context)
            approved = await confirmed_spec(session, context, plan, self._resolve)
            if approved != spec:
                raise failure("denied")
            evidence = await self._web.read(session, approved, context)
            # Confirmation revocation/input revision and owner changes during
            # reading cannot turn an earlier authorized read into success.
            refreshed = await confirmed_spec(session, context, plan, self._resolve)
            if refreshed != approved:
                raise failure("denied")
            evidence.validate_for(approved, await authorize_current(session, context, refreshed))
            if evidence.coverage.state == "unsupported" or any(
                item.status == "unsupported" for item in evidence.fields
            ):
                return VerificationResult(status="unsupported")
            if approved.mode == "independent_query_detail_v1":
                if evidence.match_count > 1 or (evidence.match_count == 1 and (
                    not evidence.key_match or not evidence.owner_match
                    or evidence.object_type_match is not True
                )):
                    return VerificationResult(
                        status="mismatch", evidence_digest=evidence.evidence_digest,
                    )
                if (
                    evidence.match_count == 0 or evidence.coverage.state != "complete"
                    or any(item.status == "missing" for item in evidence.fields)
                    or evidence.evidence_digest is None
                ):
                    return VerificationResult(status="incomplete")
                if evidence.schema_validated is not True or any(
                    item.status != "present" for item in evidence.fields
                ):
                    return VerificationResult(
                        status="mismatch", evidence_digest=evidence.evidence_digest,
                    )
                return VerificationResult(
                    status="verified", evidence_digest=evidence.evidence_digest,
                )
            if (
                evidence.coverage.state != "complete"
                or any(item.status == "missing" for item in evidence.fields)
                or evidence.evidence_digest is None
            ):
                return VerificationResult(status="incomplete")
            if (
                evidence.match_count != 1
                or not evidence.key_match
                or not evidence.owner_match
                or any(item.status != "matched" for item in evidence.fields)
            ):
                return VerificationResult(
                    status="mismatch", evidence_digest=evidence.evidence_digest
                )
            return VerificationResult(status="verified", evidence_digest=evidence.evidence_digest)
        except BrowserOperationError:
            raise
        except Exception:
            raise failure("invalid_response") from None
