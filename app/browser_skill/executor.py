"""Serial, memory-only execution of published Skills through a single WebAdapter.

This module never opens sealed values, creates selectors, or dispatches browser
commands itself. Every execute call independently enforces the adapter contract.
Resource lifecycle/cleanup belongs to the session owner, not this orchestrator.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args
from uuid import uuid4

from app.browser_skill.models import (
    ActionCommand,
    BrowserFailure,
    BrowserOperationError,
    BrowserSessionRef,
    Contract,
    DecisionCallContext,
    DecisionCandidate,
    DecisionRequest,
    DecisionResult,
    DispatchReceipt,
    ExecutionContext,
    ObservationRequest,
    PageEvidence,
    ReadSpec,
    ScopeBinding,
    ScopedSnapshot,
    ScopeStamp,
    SkillStep,
    TargetRef,
    VerificationResult,
    VisibleCandidate,
    VisibleProjection,
)
from app.browser_skill.publication_contracts import BrowserPublicationManifest
from app.browser_skill.repair import (
    ExplorationFeedback,
    ExplorationRegionFeedback,
    ReadOnlyExplorationAuthority,
    RepairError,
    explore_registered_reads,
)
from app.browser_skill.scoping import scope_snapshot
from app.browser_skill.site_rules import RegisteredSitePlan, SiteStepRule
from app.browser_skill.trajectory import RunTeachingStep
from app.browser_skill.verifier import (
    IndependentVerifier,
    ReadSpecResolver,
    authorize_current,
    check_liveness,
    confirmed_spec,
    failure,
)
from app.ports.browser import BrowserVerifierPort, DecisionProvider, SiteAdapter, WebAdapter


class DecisionContextFactory(Protocol):
    """Trusted composition supplies the last refreshed live-registry generation.

    current_targets must not close over request.candidates as immutable truth.
    This synchronous cache check cannot prove asynchronous DOM freshness. The
    executor re-observes all admitted candidates after a selected Decision.
    The factory and provider both enforce the registered deployment/source gate.
    """

    def __call__(
        self, context: ExecutionContext, request: DecisionRequest
    ) -> DecisionCallContext: ...


class StepAudit(Protocol):
    """Trusted Run audit. It must persist a sanitized receipt before the next send.

    The callback is installed by the Run owner, never by a caller or Decision.
    An absent callback preserves legacy reads but produces no teaching evidence.
    """

    async def __call__(
        self, command: ActionCommand, rule: SiteStepRule, scope: ScopeStamp,
        receipt: DispatchReceipt, policy_digest: str, sequence: int,
    ) -> RunTeachingStep: ...


class TraversalAudit(Protocol):
    async def __call__(
        self, pages: tuple[PageEvidence, ...], sequence: int,
    ) -> None: ...


class ExplorationSuggester(Protocol):
    async def __call__(
        self, run_id: str, prior_primitive_ids: tuple[str, ...],
        feedback: ExplorationFeedback,
    ) -> str | None: ...


class WriteGate(Protocol):
    """Trusted Run reservation; must read HumanGate under the PG Run fence."""

    async def __call__(
        self, session: BrowserSessionRef, context: ExecutionContext,
        plan: RegisteredSitePlan, spec: ReadSpec, command: ActionCommand,
        rule: SiteStepRule, sequence: int,
    ) -> None: ...


class ExecutionOutcome(Contract):
    """Local evidence report, not a second durable session/job state authority.

    Completed means the sequence reached its independent verification result;
    only verification.status=verified establishes business success. Cancellation
    remains pending until the resource owner obtains actual barrier/cleanup proof.
    """

    sequence: Literal["completed", "stopped"]
    receipts: tuple[DispatchReceipt, ...] = ()
    decisions: tuple[DecisionResult, ...] = ()
    verification: VerificationResult | None = None
    failure: BrowserFailure | None = None
    cancellation: Literal["not_requested", "pending"] = "not_requested"
    cleanup_required: bool = False
    model_calls: int = 0
    durability: Literal["memory_only"] = "memory_only"


ReadAwaitStage = Literal[
    "bootstrap", "confirmation", "authorization", "target_observation",
    "target_candidates", "decision_request", "target_revalidation", "dispatch", "verification",
]
_DIAGNOSTIC_CODES = frozenset(get_args(BrowserFailure.model_fields["code"].annotation))


@dataclass(slots=True)
class _Progress:
    receipts: list[DispatchReceipt] = field(default_factory=list)
    decisions: list[DecisionResult] = field(default_factory=list)
    pages: list[PageEvidence] = field(default_factory=list)
    model_calls: int = 0
    dispatch_inflight: bool = False
    audit_inflight: bool = False
    write_gate_inflight: bool = False
    write_reserved: bool = False
    unsettled: bool = False
    stage: ReadAwaitStage = "bootstrap"
    stage_started: float = field(default_factory=time.monotonic)
    elapsed: dict[ReadAwaitStage, float] = field(default_factory=dict)
    first_started: dict[ReadAwaitStage, float] = field(default_factory=dict)
    last_ended: dict[ReadAwaitStage, float] = field(default_factory=dict)
    timing_origin: float = field(default_factory=time.monotonic)

    def mark(self, stage: ReadAwaitStage) -> None:
        now = time.monotonic()
        self.first_started.setdefault(self.stage, self.stage_started)
        self.last_ended[self.stage] = now
        self.elapsed[self.stage] = self.elapsed.get(self.stage, 0.0) + now - self.stage_started
        self.stage = stage
        self.stage_started = now

    def diagnostic(self, result: str) -> dict[str, str | int]:
        now = time.monotonic()
        elapsed = dict(self.elapsed)
        elapsed[self.stage] = elapsed.get(self.stage, 0.0) + now - self.stage_started
        diagnostic: dict[str, str | int] = {
            "read_total_" + stage + "_ms": max(0, min(300000, int(duration * 1000)))
            for stage, duration in elapsed.items() if stage in get_args(ReadAwaitStage)
        }
        for stage in elapsed:
            if stage not in get_args(ReadAwaitStage):
                continue
            prefix = "read_" + stage
            start = self.first_started.get(stage, self.stage_started)
            end = now if stage == self.stage else self.last_ended[stage]
            # Revisited stages expose first/last offsets and cumulative active time,
            # not a second total to add to their enclosing executor span.
            diagnostic[prefix + "_start_ms"] = max(0, min(
                300000, int((start - self.timing_origin) * 1000),
            ))
            diagnostic[prefix + "_end_ms"] = max(0, min(
                300000, int((end - self.timing_origin) * 1000),
            ))
            diagnostic[prefix + "_parent"] = "read_bridge_executor"
            diagnostic[prefix + "_result"] = result if stage == self.stage else "ok"
        return diagnostic


class _DecisionStopped(Exception):
    """A nonselection is preserved as a DecisionResult, not turned into success."""


class BrowserExecutor:
    __slots__ = (
        "_web", "_decision", "_verifier", "_site", "_resolve", "_contexts", "_pending",
        "_last_failure_diagnostic",
        "_last_decision_diagnostic",
        "_last_stage_diagnostic",
        "_step_audit",
        "_traversal_audit",
        "_write_gate",
        "_exploration_publication",
        "_exploration_suggest",
    )

    def __init__(
        self,
        web: WebAdapter,
        decision: DecisionProvider,
        verifier: BrowserVerifierPort,
        site: SiteAdapter,
        resolver: ReadSpecResolver,
        decision_contexts: DecisionContextFactory,
        step_audit: StepAudit | None = None,
        traversal_audit: TraversalAudit | None = None,
        write_gate: WriteGate | None = None,
        exploration_publication: BrowserPublicationManifest | None = None,
        exploration_suggest: ExplorationSuggester | None = None,
    ) -> None:
        self._web = web
        self._decision = decision
        self._verifier = verifier
        self._site = site
        self._resolve = resolver
        self._contexts = decision_contexts
        self._step_audit = step_audit
        self._traversal_audit = traversal_audit
        self._write_gate = write_gate
        self._exploration_publication = exploration_publication
        self._exploration_suggest = exploration_suggest
        # Retain ownership of cancellation-resistant adapter tasks until settled.
        # Their execution context is cancelled and no new action is scheduled.
        self._pending: set[asyncio.Task[ExecutionOutcome]] = set()
        self._last_failure_diagnostic: tuple[ReadAwaitStage, int, str] | None = None
        self._last_decision_diagnostic: dict[str, str] | None = None
        self._last_stage_diagnostic: dict[str, str | int] = {}

    def _finish(
        self, outcome: ExecutionOutcome, progress: _Progress, *,
        stage: ReadAwaitStage | None = None, started: float | None = None,
    ) -> ExecutionOutcome:
        result = (
            "ok" if outcome.verification is not None and outcome.verification.status == "verified"
            else "cancelled" if outcome.failure is not None and outcome.failure.code == "cancelled"
            else "failed" if outcome.failure is not None or outcome.verification is not None
            else "stopped"
        )
        self._last_stage_diagnostic = progress.diagnostic(result)
        if outcome.failure is not None and outcome.failure.code in _DIAGNOSTIC_CODES:
            began = progress.stage_started if started is None else started
            elapsed_ms = max(0, min(300000, int((time.monotonic() - began) * 1000)))
            self._last_failure_diagnostic = (
                progress.stage if stage is None else stage, elapsed_ms, outcome.failure.code,
            )
        return outcome

    def _settled(self, task: asyncio.Task[ExecutionOutcome]) -> None:
        self._pending.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve without exposing raw exception text.

    @staticmethod
    def _outcome(
        progress: _Progress,
        context: ExecutionContext,
        *,
        problem: BrowserFailure | None = None,
        verification: VerificationResult | None = None,
    ) -> ExecutionOutcome:
        return ExecutionOutcome(
            sequence="completed" if verification is not None else "stopped",
            receipts=tuple(progress.receipts),
            decisions=tuple(progress.decisions),
            verification=verification,
            failure=problem,
            cancellation="pending" if context.cancellation.is_set() else "not_requested",
            cleanup_required=progress.unsettled
            or bool(problem and problem.cleanup_required)
            or any(r.failure and r.failure.cleanup_required for r in progress.receipts),
            model_calls=progress.model_calls,
        )

    async def run(self, session: BrowserSessionRef, context: ExecutionContext) -> ExecutionOutcome:
        self._last_failure_diagnostic = None
        self._last_decision_diagnostic = None
        self._last_stage_diagnostic = {}
        progress = _Progress()
        job = asyncio.create_task(self._run(session, context, progress))
        cancelled = asyncio.create_task(context.cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                (job, cancelled),
                timeout=max(0, context.deadline_monotonic - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if job in done:
                return self._finish(job.result(), progress)
            reason: Literal["cancelled", "timeout"] = (
                "cancelled" if context.cancellation.is_set() else "timeout"
            )
            stopped_stage, stopped_started = progress.stage, progress.stage_started
            context.cancellation.set()
            job.cancel()
            await asyncio.wait((job,), timeout=0.05)
            if not job.done():
                progress.unsettled = True
                self._pending.add(job)
                job.add_done_callback(self._settled)
            elif not job.cancelled():
                job.exception()
            uncertain = (
                progress.dispatch_inflight or progress.audit_inflight
                or progress.write_gate_inflight or progress.write_reserved
            )
            problem = failure(
                "effect_unknown" if uncertain else reason,
                dispatched=uncertain,
            ).failure
            return self._finish(
                self._outcome(progress, context, problem=problem), progress,
                stage=stopped_stage, started=stopped_started,
            )
        except asyncio.CancelledError:
            context.cancellation.set()
            job.cancel()
            self._pending.add(job)
            job.add_done_callback(self._settled)
            # Caller cancellation is not an acknowledgment of browser shutdown.
            raise
        finally:
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)

    async def _choose(
        self,
        candidates: tuple[VisibleCandidate, ...],
        scope: ScopeStamp,
        criteria: tuple[str, ...],
        context: ExecutionContext,
        plan: RegisteredSitePlan,
        progress: _Progress,
        refresh: Callable[[], Awaitable[tuple[VisibleCandidate, ...]]],
    ) -> TargetRef:
        if not candidates:
            raise failure("resource_not_found")
        if any(not c.visible or not c.enabled or c.ref.scope != scope for c in candidates):
            raise failure("invalid_response")
        if len({c.ref.target_id for c in candidates}) != len(candidates):
            raise failure("invalid_response")
        if len(candidates) == 1:
            return candidates[0].ref
        request = DecisionRequest(
            request_id=uuid4().hex,
            scope=scope,
            criteria=criteria,
            candidates=tuple(
                DecisionCandidate(
                    ref=c.ref,
                    role=c.role,
                    name=c.name,
                    context=c.context,
                    row_label=c.row_label,
                    column_label=c.column_label,
                )
                for c in candidates
            ),
        )
        decision_context = self._contexts(context, request)
        if (
            decision_context.source != plan.source
            or decision_context.manifest != plan.decision_manifest
            or decision_context.budget != plan.decision_budget
            or decision_context.deadline_monotonic != context.deadline_monotonic
            or decision_context.cancellation is not context.cancellation
            or decision_context.current_targets is None
        ):
            raise failure("denied")
        expected = tuple(c.ref for c in candidates)
        if decision_context.current_targets() != expected:
            raise failure("stale")
        check_liveness(context)
        progress.model_calls += 1
        progress.mark("decision_request")
        decision = await self._decision.decide(request, decision_context)
        decision.validate_for(request, scope)
        # Fixed response classifications are diagnostics only. A selected result
        # still enters progress.decisions only after every fresh-target check.
        self._last_decision_diagnostic = {}
        for name in ("status", "error", "reason"):
            value = getattr(decision, name)
            if value is not None:
                self._last_decision_diagnostic[name] = value
        if decision.selected is None:
            progress.decisions.append(decision)
            if decision.error is not None:
                code = {
                    "input_unsupported": "unsupported",
                    "model_mismatch": "invalid_response",
                }.get(decision.error, decision.error)
                raise BrowserOperationError(BrowserFailure.model_validate({
                    "code": code, "phase": "observe", "dispatch_state": "not_sent",
                    "cleanup_required": False,
                }))
            raise _DecisionStopped
        check_liveness(context)
        # A selected target requires a new observation of all alternatives.
        # Failure/abstention retains its original result without private DOM IO.
        refreshed = await refresh()
        if refreshed != candidates:
            raise failure("stale")
        if decision_context.current_targets() != expected:
            raise failure("stale")
        progress.decisions.append(decision)
        check_liveness(context)
        return decision.selected

    async def _observe_targets(
        self,
        session: BrowserSessionRef,
        request: ObservationRequest,
        step: SkillStep,
        context: ExecutionContext,
        plan: RegisteredSitePlan,
        spec: ReadSpec,
        progress: _Progress,
        *, allow_partial: bool = False,
    ) -> tuple[ScopedSnapshot, tuple[VisibleCandidate, ...]]:
        progress.mark("authorization")
        await authorize_current(session, context, spec)
        progress.mark("target_observation")
        observed = await self._web.observe(session, request, plan.policy)
        snapshot = scope_snapshot(observed, context.expected_binding, plan.policy)
        if snapshot.scope.region_id != request.region_id:
            raise failure("invalid_response")
        if request.expected_scope is not None and snapshot.scope != request.expected_scope:
            raise failure("stale")
        if snapshot.coverage.state != "complete" and not (
            allow_partial
            and snapshot.coverage.state == "partial"
            and snapshot.coverage.reason in {"pagination", "virtualized"}
        ):
            raise failure("unsupported")
        progress.mark("target_candidates")
        candidates = await self._web.target_candidates(session, step, snapshot, context)
        if any(c not in snapshot.candidates for c in candidates):
            raise failure("invalid_response")
        return snapshot, candidates

    async def _refresh_candidates(
        self,
        session: BrowserSessionRef,
        step: SkillStep,
        scope: ScopeStamp,
        context: ExecutionContext,
        plan: RegisteredSitePlan,
        spec: ReadSpec,
        progress: _Progress,
        *,
        parent: TargetRef | None = None,
        original_targets: tuple[VisibleCandidate, ...] = (),
        allow_partial: bool = False,
    ) -> tuple[VisibleCandidate, ...]:
        snapshot, targets = await self._observe_targets(
            session,
            ObservationRequest(region_id=scope.region_id, expected_scope=scope),
            step,
            context,
            plan,
            spec,
            progress,
            allow_partial=allow_partial,
        )
        if parent is None:
            return targets
        if targets != original_targets or parent not in tuple(c.ref for c in targets):
            raise failure("stale")
        options = await self._web.option_candidates(session, parent, step, context)
        self._validate_options(options, snapshot, context, plan)
        return options

    @staticmethod
    def _validate_options(
        options: tuple[VisibleCandidate, ...],
        snapshot: ScopedSnapshot,
        context: ExecutionContext,
        plan: RegisteredSitePlan,
    ) -> None:
        option_view = VisibleProjection(
            policy_id=snapshot.policy_id,
            policy_digest=snapshot.policy_digest,
            binding=snapshot.binding,
            scope=snapshot.scope,
            frames=snapshot.frames,
            coverage=snapshot.coverage,
            candidates=options,
        )
        scope_snapshot(option_view, context.expected_binding, plan.policy)

    async def _traverse(
        self, session: BrowserSessionRef, context: ExecutionContext,
        plan: RegisteredSitePlan, spec: ReadSpec, rule: SiteStepRule,
        progress: _Progress,
    ) -> BrowserFailure | None:
        """Advance only the frozen read-only step inside this same Executor."""
        traversal = plan.traversal
        if traversal is None or rule.step.step_id != traversal.advance_step_id:
            raise failure("denied")
        if rule.step.effect != "read_only" or rule.effect.actual_effect != "read_only":
            raise failure("unsupported")
        page_keys: set[str] = set()
        row_by_ordinal: dict[int, str] = {}
        ordinal_by_row: dict[str, int] = {}
        previous_page: PageEvidence | None = None
        for index in range(traversal.max_pages):
            check_liveness(context)
            progress.mark("authorization")
            await authorize_current(session, context, spec)
            progress.mark("target_observation")
            page = await self._web.page_evidence(
                session, traversal.region_id, context,
            )
            if page.scope.region_id != traversal.region_id:
                raise failure("invalid_response")
            if page.page_key_digest in page_keys:
                if (
                    traversal.mode == "virtualized" and previous_page is not None
                    and page.row_key_digests == previous_page.row_key_digests
                    and page.row_ordinals == previous_page.row_ordinals
                    and page.declared_total == previous_page.declared_total
                    and not page.terminal_seen
                ):
                    # The selected scroll container made no observable progress.
                    # The verifier will report incomplete from the last real page.
                    return None
                raise failure("invalid_response")
            progress.pages.append(page)
            previous_row_count = len(row_by_ordinal)
            for key, ordinal in zip(page.row_key_digests, page.row_ordinals):
                old_key = row_by_ordinal.get(ordinal)
                old_ordinal = ordinal_by_row.get(key)
                if (
                    (old_key is not None and old_key != key)
                    or (old_ordinal is not None and old_ordinal != ordinal)
                    or (traversal.mode == "paged" and old_key is not None)
                ):
                    raise failure("invalid_response")
                row_by_ordinal[ordinal] = key
                ordinal_by_row[key] = ordinal
            if len(row_by_ordinal) > traversal.max_rows:
                raise failure("invalid_response")
            if (
                traversal.mode == "virtualized" and index > 0
                and len(row_by_ordinal) == previous_row_count
                and not page.terminal_seen
            ):
                # A changed page marker alone is not collection progress.
                return None
            page_keys.add(page.page_key_digest)
            previous_page = page
            if page.terminal_seen or index == traversal.max_pages - 1:
                return None
            target = None
            if rule.step.operation == "navigate":
                observed = await self._web.observe(
                    session,
                    ObservationRequest(
                        region_id=traversal.region_id,
                        expected_scope=page.scope,
                    ),
                    plan.policy,
                )
                if scope_snapshot(
                    observed, context.expected_binding, plan.policy,
                ).scope != page.scope:
                    raise failure("stale")
            else:
                snapshot, candidates = await self._observe_targets(
                    session,
                    ObservationRequest(
                        region_id=traversal.region_id,
                        expected_scope=page.scope,
                    ),
                    rule.step, context, plan, spec, progress,
                    allow_partial=True,
                )
                target = await self._choose(
                    candidates, snapshot.scope, rule.target_criteria,
                    context, plan, progress,
                    lambda: self._refresh_candidates(
                        session, rule.step, snapshot.scope, context, plan,
                        spec, progress, allow_partial=True,
                    ),
                )
            command = ActionCommand(
                skill_digest=context.skill.digest, step=rule.step,
                binding=context.expected_binding, target=target,
            )
            if not self._site.permits(context.skill, command):
                raise failure("denied")
            progress.mark("authorization")
            await authorize_current(session, context, command)
            progress.mark("target_revalidation")
            await self._web.revalidate(session, command, context)
            check_liveness(context)
            progress.dispatch_inflight = True
            progress.mark("dispatch")
            receipt = (
                await self._web.advance_virtualized(
                    session, command, page.scope, context,
                ) if traversal.advance_mode == "scroll"
                else await self._web.execute(session, command, context)
            )
            receipt.validate_for(command)
            progress.receipts.append(receipt)
            if receipt.state != "acknowledged":
                progress.dispatch_inflight = receipt.state == "possibly_sent"
                return receipt.failure
            if self._step_audit is not None:
                progress.audit_inflight = True
                await self._step_audit(
                    command, rule, page.scope, receipt,
                    plan.policy.digest, len(progress.receipts),
                )
                progress.audit_inflight = False
            progress.dispatch_inflight = False
        return None

    async def _execute_exploration_step(
        self, session: BrowserSessionRef, context: ExecutionContext,
        plan: RegisteredSitePlan, spec: ReadSpec, rule: SiteStepRule,
        progress: _Progress,
    ) -> RunTeachingStep:
        """Dispatch one catalog primitive through this Executor's normal fences."""
        if (
            rule.step.operation not in {"read", "click"}
            or rule.step.effect != "read_only"
            or rule.effect.actual_effect != "read_only"
            or self._step_audit is None
        ):
            raise failure("unsupported")
        snapshot, candidates = await self._observe_targets(
            session, rule.observation, rule.step, context, plan, spec, progress,
        )
        target = await self._choose(
            candidates, snapshot.scope, rule.target_criteria,
            context, plan, progress,
            lambda: self._refresh_candidates(
                session, rule.step, snapshot.scope, context, plan, spec, progress,
            ),
        )
        command = ActionCommand(
            skill_digest=context.skill.digest, step=rule.step,
            binding=context.expected_binding, target=target,
        )
        if not self._site.permits(context.skill, command):
            raise failure("denied")
        progress.mark("authorization")
        await authorize_current(session, context, command)
        progress.mark("target_revalidation")
        await self._web.revalidate(session, command, context)
        check_liveness(context)
        progress.dispatch_inflight = True
        progress.mark("dispatch")
        receipt = await self._web.execute(session, command, context)
        receipt.validate_for(command)
        progress.receipts.append(receipt)
        if receipt.state != "acknowledged":
            progress.dispatch_inflight = receipt.state == "possibly_sent"
            if receipt.failure is None:
                raise failure("invalid_response", dispatched=progress.dispatch_inflight)
            raise BrowserOperationError(receipt.failure)
        progress.audit_inflight = True
        fact = await self._step_audit(
            command, rule, snapshot.scope, receipt,
            plan.policy.digest, len(progress.receipts),
        )
        progress.audit_inflight = False
        progress.dispatch_inflight = False
        return fact

    async def _explore(
        self, session: BrowserSessionRef, context: ExecutionContext,
        plan: RegisteredSitePlan, spec: ReadSpec, progress: _Progress,
    ) -> None:
        exploration = plan.exploration
        publication = self._exploration_publication
        suggest = self._exploration_suggest
        audit = self._step_audit
        if (
            exploration is None or publication is None or suggest is None
            or audit is None or publication.source_kind != "registered_site"
            or publication.skill != context.skill
            or publication.site_plan() != plan
        ):
            raise failure("denied")
        assert exploration is not None
        run_id = context.expected_binding.authorization_run_id
        if run_id is None:
            raise failure("denied")

        async def authorize(
            requested_run: str, binding: ScopeBinding,
            requested_publication: BrowserPublicationManifest,
        ) -> bool:
            if (
                requested_run != run_id or binding != context.expected_binding
                or requested_publication != publication
            ):
                return False
            check_liveness(context)
            await authorize_current(session, context, spec)
            return True

        async def execute_step(
            requested_run: str, primitive_id: str, binding: ScopeBinding,
        ) -> RunTeachingStep:
            if requested_run != run_id or binding != context.expected_binding:
                raise failure("denied")
            rule = next((
                item for item in plan.primitive_rules
                if item.step.step_id == primitive_id
            ), None)
            if rule is None:
                raise failure("denied")
            return await self._execute_exploration_step(
                session, context, plan, spec, rule, progress,
            )

        async def observe_current(
            requested_run: str, binding: ScopeBinding,
            requested_publication: BrowserPublicationManifest,
            prior: tuple[RunTeachingStep, ...],
        ) -> ExplorationFeedback:
            if (requested_run != run_id or binding != context.expected_binding
                    or requested_publication != publication):
                raise failure("denied")
            regions: list[ExplorationRegionFeedback] = []
            for region_id in exploration.allowed_regions:
                check_liveness(context)
                await authorize_current(session, context, spec)
                projection = await self._web.observe(
                    session, ObservationRequest(region_id=region_id), plan.policy,
                )
                if (projection.binding != binding or projection.policy_id != plan.policy.policy_id
                        or projection.policy_digest != plan.policy.digest
                        or projection.scope.region_id != region_id):
                    raise failure("stale")
                names = tuple(sorted({
                    item.name for item in projection.candidates
                    if item.name is not None and item.name in plan.policy.allowed_names
                }))[:16]
                regions.append(ExplorationRegionFeedback(
                    region_id=region_id, coverage=projection.coverage,
                    candidate_count=len(projection.candidates),
                    actionable_count=sum(
                        item.visible and item.enabled for item in projection.candidates
                    ),
                    roles=tuple(sorted({item.role for item in projection.candidates})),
                    approved_names=names,
                ))
                await authorize_current(session, context, spec)
            last = prior[-1] if prior else None
            return ExplorationFeedback(
                regions=tuple(regions),
                last_primitive_id=last.primitive_id if last is not None else None,
                last_region_id=last.event.scope.region_id if last is not None else None,
                last_dispatch_state=last.dispatch_state if last is not None else None,
            )

        deadline = min(
            context.deadline_monotonic,
            time.monotonic() + exploration.max_seconds,
        )
        authority = ReadOnlyExplorationAuthority(
            current_binding=lambda: context.expected_binding,
            authorize=authorize,
            registered_rule=lambda primitive_id, frozen: next((
                item for item in frozen.site.primitive_rules
                if item.step.step_id == primitive_id
            ), None),
            suggest_next=suggest,
            observe_current=observe_current,
            execute_step=execute_step,
            clock=time.monotonic,
            deadline_monotonic=deadline,
            maximum_steps=exploration.max_steps,
            allowed_regions=exploration.allowed_regions,
        )
        try:
            async with asyncio.timeout_at(deadline):
                steps = await explore_registered_reads(
                    run_id, binding=context.expected_binding,
                    publication=publication, authority=authority,
                )
        except TimeoutError:
            raise failure("timeout") from None
        except RepairError:
            raise failure("invalid_response") from None
        if not steps:
            raise failure("unsupported")

    async def _run(
        self, session: BrowserSessionRef, context: ExecutionContext, progress: _Progress
    ) -> ExecutionOutcome:
        try:
            plan = self._site.bootstrap(context.skill)
            plan.validate_context(context)
            if self._site.observation_policy(context.skill) != plan.policy:
                raise failure("denied")
            # Establish independently confirmed admission before observing private DOM.
            progress.mark("confirmation")
            spec = await confirmed_spec(session, context, plan, self._resolve)
            if plan.exploration is not None:
                await self._explore(session, context, plan, spec, progress)
            for rule in (() if plan.exploration is not None else plan.steps):
                check_liveness(context)
                if (
                    plan.traversal is not None
                    and rule.step.step_id == plan.traversal.advance_step_id
                ):
                    problem = await self._traverse(
                        session, context, plan, spec, rule, progress,
                    )
                    if problem is not None:
                        return self._outcome(progress, context, problem=problem)
                    if not progress.pages or not progress.pages[-1].terminal_seen:
                        break
                    # A later step may write. Prove the registered collection
                    # boundary now so a malformed terminal page cannot release it.
                    if IndependentVerifier._page_chain(
                        plan.traversal, tuple(progress.pages),
                    ) is None:
                        raise failure("invalid_response")
                    if self._traversal_audit is not None:
                        progress.audit_inflight = True
                        await self._traversal_audit(
                            tuple(progress.pages), len(progress.receipts),
                        )
                        progress.audit_inflight = False
                    continue
                if (
                    rule.effect.actual_effect == "unknown"
                    or rule.step.effect != rule.effect.actual_effect
                ):
                    raise failure("unsupported")
                writing = rule.effect.actual_effect == "may_write"
                if writing and (self._write_gate is None or self._step_audit is None):
                    raise failure("unsupported")
                target = option = None
                audit_scope: ScopeStamp | None = None
                if rule.step.operation == "navigate":
                    progress.mark("authorization")
                    await authorize_current(session, context, spec)
                else:
                    snapshot, candidates = await self._observe_targets(
                        session, rule.observation, rule.step, context, plan, spec, progress
                    )
                    audit_scope = snapshot.scope
                    target = await self._choose(
                        candidates,
                        snapshot.scope,
                        rule.target_criteria,
                        context,
                        plan,
                        progress,
                        lambda: self._refresh_candidates(
                            session, rule.step, snapshot.scope, context, plan, spec, progress
                        ),
                    )
                    if rule.step.operation == "select_option":
                        options = await self._web.option_candidates(
                            session, target, rule.step, context
                        )
                        # Options are independently enumerated under the selected
                        # parent, then subject to exactly the same projection policy.
                        self._validate_options(options, snapshot, context, plan)
                        option = await self._choose(
                            options,
                            snapshot.scope,
                            rule.option_criteria,
                            context,
                            plan,
                            progress,
                            lambda: self._refresh_candidates(
                                session,
                                rule.step,
                                snapshot.scope,
                                context,
                                plan,
                                spec,
                                progress,
                                parent=target,
                                original_targets=candidates,
                            ),
                        )
                command = ActionCommand(
                    skill_digest=context.skill.digest,
                    step=rule.step,
                    binding=context.expected_binding,
                    target=target,
                    option=option,
                )
                if not self._site.permits(context.skill, command):
                    raise failure("denied")
                progress.mark("authorization")
                await authorize_current(session, context, command)
                progress.mark("target_revalidation")
                await self._web.revalidate(session, command, context)
                if writing:
                    assert self._write_gate is not None
                    progress.mark("confirmation")
                    progress.write_gate_inflight = True
                    await self._write_gate(
                        session, context, plan, spec, command, rule,
                        len(progress.receipts) + 1,
                    )
                    progress.write_reserved = True
                    progress.write_gate_inflight = False
                    # Confirmation may have taken time. The adapter repeats
                    # these checks under its final dispatch barrier as well.
                    progress.mark("authorization")
                    await authorize_current(session, context, command)
                    progress.mark("target_revalidation")
                    await self._web.revalidate(session, command, context)
                check_liveness(context)
                progress.dispatch_inflight = True
                progress.mark("dispatch")
                receipt = await self._web.execute(session, command, context)
                receipt.validate_for(command)
                progress.receipts.append(receipt)
                if receipt.state != "acknowledged":
                    progress.dispatch_inflight = receipt.state == "possibly_sent"
                    return self._outcome(progress, context, problem=receipt.failure)
                if self._step_audit is not None:
                    progress.audit_inflight = True
                    if audit_scope is None:
                        # Navigation has no selected target. Obtain its scope
                        # from a real post-send observation; failure remains
                        # unknown and cannot release the next action.
                        progress.mark("target_observation")
                        observed = await self._web.observe(
                            session, rule.observation, plan.policy,
                        )
                        audit_scope = scope_snapshot(
                            observed, context.expected_binding, plan.policy,
                        ).scope
                    await self._step_audit(
                        command, rule, audit_scope, receipt, plan.policy.digest,
                        len(progress.receipts),
                    )
                    progress.audit_inflight = False
                progress.dispatch_inflight = False
                progress.write_reserved = False
            progress.mark("confirmation")
            spec = await confirmed_spec(session, context, plan, self._resolve)
            progress.mark("verification")
            verification = await self._verifier.verify(
                session, spec, context, pages=tuple(progress.pages),
            )
            check_liveness(context)
            return self._outcome(progress, context, verification=verification)
        except _DecisionStopped:
            return self._outcome(progress, context)
        except BrowserOperationError as exc:
            # An adapter may provide an authoritative not_sent failure after
            # entry. Unexpected/cancelled transport interruption cannot do so.
            if progress.audit_inflight or progress.write_gate_inflight or progress.write_reserved:
                progress.dispatch_inflight = True
                return self._outcome(
                    progress, context,
                    problem=failure("effect_unknown", dispatched=True).failure,
                )
            progress.dispatch_inflight = exc.failure.dispatch_state == "possibly_sent"
            return self._outcome(progress, context, problem=exc.failure)
        except Exception:
            uncertain = (
                progress.dispatch_inflight or progress.audit_inflight
                or progress.write_gate_inflight or progress.write_reserved
            )
            problem = failure(
                "effect_unknown" if uncertain else "invalid_response",
                dispatched=uncertain,
            ).failure
            return self._outcome(progress, context, problem=problem)
