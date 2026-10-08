"""Independent confirmed-key verification, with current authorization on both sides."""

import re
import time
from typing import Literal, Protocol

from app.browser_skill.models import (
    ActionCommand,
    BrowserFailure,
    BrowserOperationError,
    BrowserSessionRef,
    ExecutionContext,
    PageEvidence,
    ReadSpec,
    ScopeBinding,
    VerificationResult,
)
from app.browser_skill.registry import canonical_digest
from app.browser_skill.site_rules import (
    ExpectedDownload,
    RegisteredSitePlan,
    RegisteredTraversal,
)
from app.ports.browser import SiteAdapter, WebAdapter


class ReadSpecResolver(Protocol):
    """Trusted process-local admission lookup, independent of DOM/Decision.

    Consult the current confirmation and approved input records; a key supplied
    by the caller or selected row is not a confirmation. No raw value is returned.
    """

    async def __call__(
        self, session: BrowserSessionRef, context: ExecutionContext, binding: ScopeBinding
    ) -> ReadSpec: ...


class DownloadVerifier(Protocol):
    """A separate private file reader, never a page or dispatch receipt."""

    async def __call__(
        self, session: BrowserSessionRef, context: ExecutionContext,
        expected: tuple[ExpectedDownload, ...],
    ) -> str: ...


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
    __slots__ = (
        "_web", "_site", "_resolve", "_expected_downloads", "_verify_downloads",
    )

    def __init__(
        self, web: WebAdapter, site: SiteAdapter, resolver: ReadSpecResolver,
        *, expected_downloads: tuple[ExpectedDownload, ...] = (),
        verify_downloads: DownloadVerifier | None = None,
    ) -> None:
        if (
            type(expected_downloads) is not tuple
            or len({item.step_id for item in expected_downloads})
            != len(expected_downloads)
        ):
            raise ValueError("browser_download_expectations_invalid")
        self._web = web
        self._site = site
        self._resolve = resolver
        self._expected_downloads = expected_downloads
        self._verify_downloads = verify_downloads

    @staticmethod
    def _page_chain(
        traversal: RegisteredTraversal, pages: tuple[PageEvidence, ...],
    ) -> str | None:
        """Judge full collection coverage from adapter facts, never a model flag."""
        if not pages or len(pages) > traversal.max_pages:
            return None
        page_keys: set[str] = set()
        row_keys: set[str] = set()
        ordinals: set[int] = set()
        total: int | None = None
        checked: list[dict[str, object]] = []
        for index, raw in enumerate(pages):
            if not isinstance(raw, PageEvidence):
                return None
            try:
                page = PageEvidence.model_validate_json(raw.model_dump_json())
            except ValueError:
                return None
            if (
                page.scope.region_id != traversal.region_id
                or page.page_key_digest in page_keys
                or (total is not None and page.declared_total != total)
                or (page.terminal_seen != (index == len(pages) - 1))
                or len(row_keys) + len(page.row_key_digests) > traversal.max_rows
                or any(key in row_keys for key in page.row_key_digests)
                or any(ordinal in ordinals for ordinal in page.row_ordinals)
            ):
                return None
            total = page.declared_total
            page_keys.add(page.page_key_digest)
            row_keys.update(page.row_key_digests)
            ordinals.update(page.row_ordinals)
            checked.append(page.model_dump(mode="json"))
        if total is None or len(row_keys) != total or ordinals != set(range(total)):
            return None
        return canonical_digest("browser.collection_page_chain.v1", {
            "traversal": traversal.model_dump(mode="json"),
            "pages": checked,
        })

    async def verify(
        self, session: BrowserSessionRef, spec: ReadSpec, context: ExecutionContext,
        pages: tuple[PageEvidence, ...] = (),
    ) -> VerificationResult:
        try:
            plan = self._site.bootstrap(context.skill)
            plan.validate_context(context)
            if self._expected_downloads:
                rules = {item.step.step_id: item for item in plan.steps}
                if self._verify_downloads is None or any(
                    (rule := rules.get(expected.step_id)) is None
                    or rule.step.operation != "click"
                    or rule.step.effect != "read_only"
                    or rule.effect.actual_effect != "read_only"
                    or expected.object_type != plan.read_rule.object_type
                    for expected in self._expected_downloads
                ):
                    return VerificationResult(status="unsupported")
            approved = await confirmed_spec(session, context, plan, self._resolve)
            if approved != spec:
                raise failure("denied")
            chain_digest: str | None = None
            if plan.traversal is not None:
                chain_digest = self._page_chain(plan.traversal, pages)
                if chain_digest is None:
                    return VerificationResult(status="incomplete")
            elif pages:
                raise failure("invalid_response")
            evidence = await self._web.read(session, approved, context)
            # Confirmation revocation/input revision and owner changes during
            # reading cannot turn an earlier authorized read into success.
            refreshed = await confirmed_spec(session, context, plan, self._resolve)
            if refreshed != approved:
                raise failure("denied")
            evidence.validate_for(approved, await authorize_current(session, context, refreshed))
            file_digest: str | None = None
            if self._expected_downloads:
                assert self._verify_downloads is not None
                file_digest = await self._verify_downloads(
                    session, context, self._expected_downloads,
                )
                if not isinstance(file_digest, str) or re.fullmatch(
                    r"[0-9a-f]{64}", file_digest,
                ) is None:
                    return VerificationResult(status="incomplete")
                if await confirmed_spec(session, context, plan, self._resolve) != approved:
                    raise failure("denied")
            coverage_complete = evidence.coverage.state == "complete" or (
                chain_digest is not None
                and evidence.coverage.state == "partial"
                and plan.traversal is not None
                and evidence.coverage.reason == (
                    "pagination" if plan.traversal.mode == "paged" else "virtualized"
                )
            )
            verified_digest = (
                canonical_digest("browser.verified_collection_read.v1", {
                    "read_evidence_digest": evidence.evidence_digest,
                    "page_chain_digest": chain_digest,
                }) if chain_digest is not None and evidence.evidence_digest is not None
                else evidence.evidence_digest
            )
            if file_digest is not None and verified_digest is not None:
                verified_digest = canonical_digest("browser.verified_read_files.v1", {
                    "read_evidence_digest": verified_digest,
                    "file_proof_digest": file_digest,
                })
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
                    evidence.match_count == 0 or not coverage_complete
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
                    status="verified", evidence_digest=verified_digest,
                    read_evidence_digest=(
                        evidence.evidence_digest
                        if chain_digest is not None or file_digest is not None else None
                    ),
                )
            if (
                not coverage_complete
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
            return VerificationResult(
                status="verified", evidence_digest=verified_digest,
                read_evidence_digest=(
                    evidence.evidence_digest
                    if chain_digest is not None or file_digest is not None else None
                ),
            )
        except BrowserOperationError:
            raise
        except Exception:
            raise failure("invalid_response") from None
