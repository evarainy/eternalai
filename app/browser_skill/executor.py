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
    ReadSpec,
    ScopedSnapshot,
    ScopeStamp,
    SkillStep,
    TargetRef,
    VerificationResult,
    VisibleCandidate,
    VisibleProjection,
)
from app.browser_skill.scoping import scope_snapshot
from app.browser_skill.site_rules import RegisteredSitePlan
from app.browser_skill.verifier import (
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
    model_calls: int = 0
    dispatch_inflight: bool = False
    unsettled: bool = False
    stage: ReadAwaitStage = "bootstrap"
    stage_started: float = field(default_factory=time.monotonic)

    def mark(self, stage: ReadAwaitStage) -> None:
        self.stage = stage
        self.stage_started = time.monotonic()


class _DecisionStopped(Exception):
    """A nonselection is preserved as a DecisionResult, not turned into success."""


class BrowserExecutor:
    __slots__ = (
        "_web", "_decision", "_verifier", "_site", "_resolve", "_contexts", "_pending",
        "_last_failure_diagnostic",
        "_last_decision_diagnostic",
    )

    def __init__(
        self,
        web: WebAdapter,
        decision: DecisionProvider,
        verifier: BrowserVerifierPort,
        site: SiteAdapter,
        resolver: ReadSpecResolver,
        decision_contexts: DecisionContextFactory,
    ) -> None:
        self._web = web
        self._decision = decision
        self._verifier = verifier
        self._site = site
        self._resolve = resolver
        self._contexts = decision_contexts
        # Retain ownership of cancellation-resistant adapter tasks until settled.
        # Their execution context is cancelled and no new action is scheduled.
        self._pending: set[asyncio.Task[ExecutionOutcome]] = set()
        self._last_failure_diagnostic: tuple[ReadAwaitStage, int, str] | None = None
        self._last_decision_diagnostic: dict[str, str] | None = None

    def _finish(
        self, outcome: ExecutionOutcome, progress: _Progress, *,
        stage: ReadAwaitStage | None = None, started: float | None = None,
    ) -> ExecutionOutcome:
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
            problem = failure(
                "effect_unknown" if progress.dispatch_inflight else reason,
                dispatched=progress.dispatch_inflight,
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
        if snapshot.coverage.state != "complete":
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
    ) -> tuple[VisibleCandidate, ...]:
        snapshot, targets = await self._observe_targets(
            session,
            ObservationRequest(region_id=scope.region_id, expected_scope=scope),
            step,
            context,
            plan,
            spec,
            progress,
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
            for rule in plan.steps:
                check_liveness(context)
                if rule.step.effect != "read_only" or rule.effect.actual_effect != "read_only":
                    raise failure("unsupported")
                target = option = None
                if rule.step.operation == "navigate":
                    progress.mark("authorization")
                    await authorize_current(session, context, spec)
                else:
                    snapshot, candidates = await self._observe_targets(
                        session, rule.observation, rule.step, context, plan, spec, progress
                    )
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
                check_liveness(context)
                progress.dispatch_inflight = True
                progress.mark("dispatch")
                receipt = await self._web.execute(session, command, context)
                receipt.validate_for(command)
                progress.receipts.append(receipt)
                progress.dispatch_inflight = False
                if receipt.state != "acknowledged":
                    return self._outcome(progress, context, problem=receipt.failure)
            progress.mark("confirmation")
            spec = await confirmed_spec(session, context, plan, self._resolve)
            progress.mark("verification")
            verification = await self._verifier.verify(session, spec, context)
            check_liveness(context)
            return self._outcome(progress, context, verification=verification)
        except _DecisionStopped:
            return self._outcome(progress, context)
        except BrowserOperationError as exc:
            # An adapter may provide an authoritative not_sent failure after
            # entry. Unexpected/cancelled transport interruption cannot do so.
            progress.dispatch_inflight = exc.failure.dispatch_state == "possibly_sent"
            return self._outcome(progress, context, problem=exc.failure)
        except Exception:
            problem = failure(
                "effect_unknown" if progress.dispatch_inflight else "invalid_response",
                dispatched=progress.dispatch_inflight,
            ).failure
            return self._outcome(progress, context, problem=problem)
