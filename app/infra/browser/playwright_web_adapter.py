"""Single Playwright WebAdapter; all DOM and sealed values remain process-local.

The execution registration is installed by trusted composition. It is not an
admission API, durable send journal, or replacement for the domain Executor.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import wraps
from typing import (
    Any,
    Awaitable,
    Callable,
    Coroutine,
    Literal,
    ParamSpec,
    Protocol,
    TypeVar,
    cast,
    get_args,
)

from jsonschema import Draft202012Validator
from referencing import Registry

from app.browser_skill.models import (
    ActionCommand,
    BrowserOperationError,
    BrowserSessionRef,
    ConfirmedBusinessKey,
    DecisionSource,
    DispatchPermit,
    DispatchReceipt,
    ExecutionContext,
    ObservationPolicy,
    ObservationRequest,
    ParameterPurpose,
    ParameterRef,
    ReadEvidence,
    ReadFieldEvidence,
    ReadSpec,
    SealedParameter,
    SkillStep,
    TargetRef,
    VerificationResult,
    VisibleCandidate,
    VisibleProjection,
)
from app.browser_skill.site_rules import (
    RegisteredQueryReadRule,
    RegisteredReadRule,
    RegisteredSitePlan,
    navigation_allowed,
)
from app.browser_skill.verifier import authorize_current, check_liveness, failure
from app.infra.browser.browserless_wire import BrowserProviderError
from app.infra.browser.playwright_dom_rules import (
    DOMValue,
    DOMBatchError,
    RegisteredDOMRules,
    ROW_IDENTITY_MATCH,
    bounded_children,
    filter_private_candidates,
    private_row_identity,
    read_private,
    read_private_many,
)
from app.infra.browser.playwright_observer import (
    ExactNode,
    ExactRegion,
    PlaywrightObserver,
    ResolutionRound,
    _dispose,
    _dispose_many,
)
from app.ports.browser import SiteAdapter


class BusinessRegistry(Protocol):
    async def assert_business_authority(
        self, session: BrowserSessionRef, source: DecisionSource
    ) -> Any: ...


OperationAuthority = Callable[
    [BrowserSessionRef, ExecutionContext, ActionCommand | ReadSpec], Awaitable[Any]
]


_OPTION_IDENTITY = """el => {
  const label = el.label;
  if (new TextEncoder().encode(el.value).length > 4096 || label.length > 256)
    throw new Error('bound');
  return [el.value, label];
}"""


_FINAL_IDENTITY = ("(identity, config) => { const privateIdentity = " + ROW_IDENTITY_MATCH + ";"
                + """
              const node = identity.node, region = identity.region;
              if (node !== config.element || !node.isConnected || !region.isConnected ||
                  node.ownerDocument !== region.ownerDocument || !region.contains(node)) return false;
              const regions = node.ownerDocument.querySelectorAll(config.region_selector);
              if (regions.length !== 1 || regions[0] !== region) return false;
              if (config.selector && !node.matches(config.selector)) return false;
              if (config.row_selector) {
                const row = node.closest(config.row_selector);
                if (!row || row !== identity.row || !row.isConnected || !region.contains(row))
                  return false;
                if (!privateIdentity(region, node, row, config, false)) return false;
              }
              if (config.target) {
                // The nearest explicit ARIA value overrides ancestors, including
                // across shadow hosts; Playwright parses true/false without case.
                let ariaDisabled = false;
                for (let ancestor = node; ancestor;
                     ancestor = ancestor.parentElement || ancestor.getRootNode().host || null) {
                  const value = (ancestor.getAttribute('aria-disabled') || '').toLowerCase();
                  if (value === 'true' || value === 'false') {
                    ariaDisabled = value === 'true';
                    break;
                  }
                }
                const style = getComputedStyle(node), rect = node.getBoundingClientRect();
                if (style.visibility === 'hidden' || style.visibility === 'collapse' ||
                    rect.width <= 0 || rect.height <= 0 || node.matches(':disabled') ||
                    ariaDisabled) return false;
                if (config.operation === 'fill' && (node.readOnly ||
                    node.getAttribute('aria-readonly') === 'true' ||
                    !(node.isContentEditable || node.matches('input,textarea')))) return false;
              }
              const option = config.option;
              if (option && (!option.isConnected || option.closest('select') !== node ||
                  option.disabled || option.closest('optgroup[disabled]') || node.multiple ||
                  option.value !== config.option_value || option.label !== config.option_label))
                return false;
              return true;
            }""")


@dataclass(frozen=True, slots=True, repr=False)
class RegisteredExecution:
    """Installed only by admission composition, never deserialized from callers.

    The context's authorize callback must check current confirmation/input
    authority as well as owner/lease; this frozen key is an identity, not a
    substitute for that current-state check.
    """

    session: BrowserSessionRef
    context: ExecutionContext
    confirmed_key: ConfirmedBusinessKey
    project_output: Callable[[Mapping[str, str]], Mapping[str, object]] | None = None
    stop_after_observe: bool = False
    operation_authority: OperationAuthority | None = None
    authority_diagnostic: Callable[[], dict[str, int]] | None = None


@dataclass(slots=True, repr=False)
class _Option:
    candidate: VisibleCandidate
    element: Any
    signature: str


@dataclass(slots=True, repr=False)
class _OptionSet:
    parent: TargetRef
    options: list[_Option]


@dataclass(frozen=True, slots=True, repr=False)
class _ValidatedNode:
    page: Any
    frame: Any
    element: Any
    identity: Any


async def _dispose_validated_node(node: _ValidatedNode, *, failed: bool = False) -> None:
    await _dispose_many((node.element, node.identity), suppress_cancel=failed)


@dataclass(slots=True, repr=False)
class _OriginGuard:
    context: Any
    execution: ExecutionContext
    failed: bool = False


_ValidationStage = Literal[
    "authority", "region_resolution", "candidate_enumeration", "target_resolution",
    "parameter_resolution", "target_identity", "row_identity", "option_identity",
]


@dataclass(slots=True, repr=False)
class _ValidationProgress:
    stage: _ValidationStage | None = None
    started: float = field(default_factory=time.monotonic)
    elapsed: dict[_ValidationStage, float] = field(default_factory=dict)
    calls: int = 0
    scope: Literal["pre_dispatch", "dispatch_barrier"] = "pre_dispatch"

    def mark(self, stage: _ValidationStage | None) -> None:
        now = time.monotonic()
        if self.stage is not None:
            self.elapsed[self.stage] = self.elapsed.get(self.stage, 0.0) + now - self.started
        self.stage, self.started = stage, now

    def diagnostic(self) -> dict[str, str | int]:
        now = time.monotonic()
        elapsed = dict(self.elapsed)
        result: dict[str, str | int] = {
            "validation_scope": self.scope, "validation_calls": self.calls,
        }
        if self.stage is not None:
            current = max(0.0, now - self.started)
            elapsed[self.stage] = elapsed.get(self.stage, 0.0) + current
            result["validation_wait_stage"] = self.stage
            result["validation_wait_elapsed_ms"] = min(300000, int(current * 1000))
        for stage in get_args(_ValidationStage):
            result["validation_total_" + stage + "_ms"] = max(
                0, min(300000, int(elapsed.get(stage, 0.0) * 1000)),
            )
        return result


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _neutral(method: Callable[_P, Awaitable[_R]]) -> Callable[_P, Coroutine[Any, Any, _R]]:
    @wraps(method)
    async def invoke(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            self = cast(PlaywrightWebAdapter, args[0])
            session = cast(BrowserSessionRef, args[1] if len(args) > 1 else kwargs.get("session"))
            if method.__name__ in {"observe", "target_candidates"}:
                self._observe_wait.pop(session.session_ref, None)
                if method.__name__ == "target_candidates" and isinstance(
                    self._observer, PlaywrightObserver,
                ):
                    self._observer._observe_wait.pop(session.session_ref, None)
            admitted = self._registration(session)
            check_liveness(admitted.context)
            async with asyncio.timeout_at(admitted.context.deadline_monotonic):
                return await method(*args, **kwargs)
        except BrowserProviderError as error:
            raise BrowserOperationError(error.failure) from None
        except BrowserOperationError:
            raise
        except asyncio.CancelledError:
            raise failure(
                "timeout"
                if time.monotonic() >= admitted.context.deadline_monotonic
                else "cancelled"
            ) from None
        except TimeoutError:
            raise failure("timeout") from None
        except Exception:
            raise failure("invalid_response") from None

    return invoke


class PlaywrightWebAdapter:
    def __init__(
        self,
        *,
        registry: BusinessRegistry,
        observer: PlaywrightObserver,
        site: SiteAdapter,
        rules: tuple[RegisteredDOMRules, ...],
        executions: tuple[RegisteredExecution, ...],
    ) -> None:
        if (
            not executions
            or len({e.session.session_ref for e in executions}) != len(executions)
            or len({r.skill_digest for r in rules}) != len(rules)
        ):
            raise ValueError("browser_adapter_registration_invalid")
        self._registry, self._observer, self._site = registry, observer, site
        self._executions = {e.session.session_ref: e for e in executions}
        self._rules = {r.skill_digest: r for r in rules}
        self._options: dict[tuple[str, str], _OptionSet] = {}
        self._guards: dict[str, _OriginGuard] = {}
        self._guard_lock = asyncio.Lock()
        self._observe_wait: dict[str, tuple[str, float]] = {}
        self._observe_only_completed: dict[str, int] = {}
        self._authority_calls: dict[str, int] = {}
        self._filter_counts: dict[str, dict[str, int]] = {}
        self._validation_progress: dict[str, _ValidationProgress] = {}
        self._salt = secrets.token_bytes(32)
        # One bounded private result per registered execution. Never included in
        # ReadEvidence, projections, model input, diagnostics, or adapter repr.
        self._private_reads: dict[
            str, tuple[ReadSpec, ReadEvidence, tuple[tuple[str, str], ...]]
        ] = {}
        for registration in executions:
            plan = site.bootstrap(registration.context.skill)
            plan.validate_context(registration.context)
            dom = self._rules.get(plan.skill_digest)
            if (
                registration.session.binding != registration.context.expected_binding
                or dom is None
                or dom.site_digest != plan.site_digest
                or dom.verifier_digest != plan.verifier_digest
                or tuple(s.step_id for s in dom.steps) != tuple(s.step.step_id for s in plan.steps)
                or tuple(name for name, _ in dom.read.fields)
                != tuple(f.field_id for f in plan.read_rule.fields)
                or registration.confirmed_key.object_type != plan.read_rule.object_type
                or registration.confirmed_key.value_ref != plan.read_rule.key_ref
                or (isinstance(plan.read_rule, RegisteredQueryReadRule) and (
                    dom.read.object_type is None or registration.project_output is None
                ))
            ):
                raise ValueError("browser_adapter_registration_invalid")

    def _registration(
        self,
        session: BrowserSessionRef,
        context: ExecutionContext | None = None,
    ) -> RegisteredExecution:
        item = self._executions.get(session.session_ref)
        if (
            item is None
            or item.session != session
            or (context is not None and context is not item.context)
        ):
            raise failure("denied")
        return item

    async def _authority(
        self,
        session: BrowserSessionRef,
        context: ExecutionContext,
        subject: ActionCommand | ReadSpec | None = None,
    ) -> tuple[RegisteredSitePlan, Any]:
        registration = self._registration(session, context)
        self._authority_calls[session.session_ref] = min(
            10000, self._authority_calls.get(session.session_ref, 0) + 1,
        )
        check_liveness(context)
        plan = self._site.bootstrap(context.skill)
        plan.validate_context(context)
        # Even observe/candidate enumeration uses current authorization for the
        # registered confirmed read; ActionCommand requires a selected target.
        if subject is None:
            subject = ReadSpec(
                binding=session.binding,
                business_key=registration.confirmed_key,
                verifier_id=plan.verifier_id,
                verifier_digest=plan.verifier_digest,
                fields=tuple(f.field_id for f in plan.read_rule.fields),
                mode=("independent_query_detail_v1"
                      if isinstance(plan.read_rule, RegisteredQueryReadRule)
                      else "independent_confirmed_key_v1"),
            )
        if isinstance(subject, ReadSpec):
            plan.validate_read(subject)
            if subject.business_key != registration.confirmed_key:
                raise failure("denied")
        else:
            if not self._site.permits(context.skill, subject):
                raise failure("unsupported")
        assert subject is not None
        async def current_live() -> Any:
            if registration.operation_authority is not None:
                # An installed callback is authoritative: failure never falls back.
                value = await registration.operation_authority(session, context, subject)
            else:
                await authorize_current(session, context, subject)
                value = await self._registry.assert_business_authority(session, context.source)
                if value.session != session:
                    raise failure("denied")
                await authorize_current(session, context, subject)
            if value.session != session:
                raise failure("denied")
            check_liveness(context)
            return value

        live = await current_live()
        if await self._origin_guard(session, context, live):
            # route installation awaited external work. Renew operation authority
            # afterward, then synchronously inspect the installed guard again.
            live = await current_live()
            self._check_origin_guard(session, context, live)
        check_liveness(context)
        return plan, live

    def _check_origin_guard(
        self, session: BrowserSessionRef, ctx: ExecutionContext, live: Any,
    ) -> None:
        guard = self._guards.get(session.session_ref)
        if (guard is None or guard.context is not live.context
                or guard.execution is not ctx or guard.failed):
            raise failure("denied")
        check_liveness(ctx)

    async def _origin_guard(
        self, session: BrowserSessionRef, ctx: ExecutionContext, live: Any
    ) -> bool:
        if session.session_ref in self._guards:
            self._check_origin_guard(session, ctx, live)
            return False
        async with self._guard_lock:
            guard = self._guards.get(session.session_ref)
            if guard is not None:
                # Acquiring this lock may have waited for another installation.
                self._check_origin_guard(session, ctx, live)
                return True
            guard = _OriginGuard(live.context, ctx)

            def inspect_request(request: Any) -> None:
                # Includes redirected requests, which Playwright route handlers
                # do not necessarily intercept. Detection is not M2 egress proof.
                if not navigation_allowed(request.url, ctx.navigation_origins):
                    guard.failed = True
                    ctx.cancellation.set()

            async def route_request(route: Any) -> None:
                inspect_request(route.request)
                if guard.failed:
                    await route.abort("blockedbyclient")
                else:
                    await route.fallback()

            def page_created(page: Any) -> None:
                # Popups never silently replace the explicitly registered Page.
                guard.failed = True
                ctx.cancellation.set()

            try:
                live.context.on("request", inspect_request)
                live.context.on("page", page_created)
                await live.context.route("**/*", route_request)
            except BaseException:
                guard.failed = True
                ctx.cancellation.set()
                raise
            self._guards[session.session_ref] = guard
            return True

    @staticmethod
    def _origins(exact: Any, context: ExecutionContext) -> None:
        frame = exact.frame
        for _ in range(32):
            if not navigation_allowed(frame.url, context.navigation_origins):
                raise failure("denied")
            frame = frame.parent_frame
            if frame is None:
                return
        raise failure("unsupported")

    @staticmethod
    async def _sealed(
        context: ExecutionContext,
        ref: ParameterRef,
        purpose: ParameterPurpose,
        step_id: str,
    ) -> SealedParameter:
        value = await context.resolve_parameter(
            ref,
            purpose,
            context.expected_binding,
            context.skill.digest,
            step_id,
        )
        if not isinstance(value, SealedParameter):
            raise failure("denied")
        check_liveness(context)
        return value

    @staticmethod
    def _consume(
        sealed: SealedParameter,
        context: ExecutionContext,
        ref: ParameterRef,
        purpose: ParameterPurpose,
        step_id: str,
        consumer: Callable[..., Any],
    ) -> Any:
        return sealed.consume(
            consumer,
            ref=ref,
            purpose=purpose,
            binding=context.expected_binding,
            skill_digest=context.skill.digest,
            step_id=step_id,
        )

    def _validation_mark(
        self, session: BrowserSessionRef, stage: _ValidationStage | None,
    ) -> None:
        self._validation_progress.setdefault(session.session_ref, _ValidationProgress()).mark(stage)

    def _execution_diagnostic(self, session_ref: str) -> dict[str, str | int]:
        result: dict[str, str | int] = {
            "adapter_authority_calls": self._authority_calls.get(session_ref, 0),
        }
        registration = self._executions.get(session_ref)
        if registration is not None and registration.authority_diagnostic is not None:
            counters = registration.authority_diagnostic()
            for name in ("factory_fence_attempts", "factory_fence_successes",
                         "factory_resource_authorization_attempts",
                         "factory_resource_authorization_successes"):
                value = counters.get(name)
                if type(value) is int and 0 <= value <= 10000:
                    result[name] = value
        if isinstance(self._observer, PlaywrightObserver):
            result["observer_observe_calls"] = self._observer._observe_calls.get(session_ref, 0)
            result["observer_snapshot_calls"] = self._observer._snapshot_calls.get(session_ref, 0)
            for name, value in self._observer._batch_counts.get(session_ref, {}).items():
                if name in {"observer_identity_batch_attempts", "observer_identity_batch_successes",
                            "observer_projection_check_attempts", "observer_projection_check_successes"}:
                    result[name] = value
        result.update(self._filter_counts.get(session_ref, {}))
        progress = self._validation_progress.get(session_ref)
        if progress is not None:
            result.update(progress.diagnostic())
        return result

    def _observation_diagnostic(self, session_ref: str) -> dict[str, str | int]:
        """Only fixed wait names and bounded monotonic durations leave this adapter."""
        wait = self._observe_wait.get(session_ref)
        if wait is None:
            return {}
        stage, started = wait
        if stage not in {
            "authority_before", "region_resolution", "authority_after",
            "candidate_authority_before", "candidate_key_resolution", "candidate_batch",
            "candidate_selector", "candidate_key_read", "candidate_authority_after",
            "candidate_batch_after",
        }:
            return {}
        now = time.monotonic()
        result: dict[str, str | int] = {
            "observe_wait_stage": stage,
            "observe_wait_elapsed_ms": max(0, min(300000, int((now - started) * 1000))),
        }
        if stage in {"region_resolution", "candidate_batch", "candidate_batch_after"} and isinstance(
            self._observer, PlaywrightObserver,
        ):
            inner = self._observer._observe_wait.get(session_ref)
            if inner is not None and inner[0] in {
                "region_lock", "live_authority", "dom_transport",
                "candidate_contract", "candidate_region_lock", "candidate_live_authority",
                "candidate_page", "candidate_frame", "candidate_region_lookup",
                "candidate_region_identity", "candidate_region_disposal", "candidate_snapshot",
                "candidate_metadata", "candidate_identity", "candidate_availability",
                "candidate_handle_disposal", "candidate_topology", "candidate_selection",
                "candidate_reobserve", "candidate_reobserve_stale",
                "candidate_scope_mismatch", "candidate_frame_mismatch",
                "candidate_region_mismatch", "candidate_metadata_mismatch",
                "candidate_identity_mismatch", "candidate_availability_mismatch",
                "candidate_topology_mismatch", "candidate_projection_mismatch",
            }:
                result["observe_inner_wait_stage"] = inner[0]
                result["observe_inner_wait_elapsed_ms"] = max(
                    0, min(300000, int((now - inner[1]) * 1000)),
                )
        return result

    @_neutral
    async def observe(
        self,
        session: BrowserSessionRef,
        request: ObservationRequest,
        policy: ObservationPolicy,
    ) -> VisibleProjection:
        admitted = self._registration(session)
        context = admitted.context
        started = time.monotonic()
        self._observe_wait[session.session_ref] = ("authority_before", time.monotonic())
        plan, _ = await self._authority(session, context)
        allowed = {rule.observation.region_id for rule in plan.steps}
        allowed.add(self._rules[plan.skill_digest].read.observation.region_id)
        if policy != plan.policy or request.region_id not in allowed:
            raise failure("denied")
        self._observe_wait[session.session_ref] = ("region_resolution", time.monotonic())
        exact = await self._observer.resolve_region(session, request, policy)
        self._origins(exact, context)
        self._observe_wait[session.session_ref] = ("authority_after", time.monotonic())
        await self._authority(session, context)
        if admitted.stop_after_observe:
            self._observe_only_completed[session.session_ref] = min(
                300_000, max(0, int((time.monotonic() - started) * 1000)),
            )
            context.cancellation.set()
        return exact.projection

    @_neutral
    async def target_candidates(
        self, session: BrowserSessionRef, step: SkillStep,
        projection: VisibleProjection, context: ExecutionContext,
    ) -> tuple[VisibleCandidate, ...]:
        self._observe_wait[session.session_ref] = ("candidate_authority_before", time.monotonic())
        plan, _ = await self._authority(session, context)
        self._observe_wait[session.session_ref] = ("candidate_batch", time.monotonic())
        async with self._observer._candidate_batch(session, projection, plan.policy) as (
            region, nodes,
        ):
            matches = await self._filter_candidates(session, step, context, plan, region, nodes)
            self._observe_wait[session.session_ref] = (
                "candidate_authority_after", time.monotonic(),
            )
            await self._authority(session, context)
            self._observe_wait[session.session_ref] = ("candidate_batch_after", time.monotonic())
        return matches

    async def _target_candidates_in_round(
        self, session: BrowserSessionRef, step: SkillStep,
        context: ExecutionContext, resolution: ResolutionRound,
    ) -> tuple[VisibleCandidate, ...]:
        plan, _ = await self._authority(session, context)
        matches = await self._filter_candidates(
            session, step, context, plan, resolution.region, resolution.nodes,
        )
        await self._authority(session, context)
        return matches

    async def _filter_candidates(
        self, session: BrowserSessionRef, step: SkillStep, context: ExecutionContext,
        plan: RegisteredSitePlan, region: ExactRegion,
        nodes: tuple[tuple[VisibleCandidate, ExactNode], ...],
    ) -> tuple[VisibleCandidate, ...]:
        rule = next((r for r in plan.steps if r.step == step), None)
        if rule is None or region.projection.scope.region_id != rule.observation.region_id:
            raise failure("denied")
        dom = next(r for r in self._rules[plan.skill_digest].steps if r.step_id == step.step_id)
        key = None
        if step.locator.kind == "business_key":
            if dom.key is None:
                raise failure("unsupported")
            self._observe_wait[session.session_ref] = ("candidate_key_resolution", time.monotonic())
            key = await self._sealed(context, plan.read_rule.key_ref, "business_key", step.step_id)
        matches = []
        self._origins(region, context)
        for candidate, node in nodes:
            self._origins(node, context)
        counters = self._filter_counts.setdefault(session.session_ref, {})
        counters["adapter_filter_batch_attempts"] = min(
            10000, counters.get("adapter_filter_batch_attempts", 0) + 1,
        )
        self._observe_wait[session.session_ref] = ("candidate_selector", time.monotonic())
        try:
            packet = await filter_private_candidates(
                region.element, tuple(node.element for _, node in nodes), dom.selector,
                dom.row_selector, dom.key if key is not None else None, 4096,
            )
        except DOMBatchError as error:
            raise failure(error.code) from None
        counters["adapter_filter_batch_successes"] = min(
            10000, counters.get("adapter_filter_batch_successes", 0) + 1,
        )
        for (candidate, _node), (selector_match, raw) in zip(nodes, packet, strict=True):
            if not selector_match:
                continue
            if key is not None and dom.key is not None:
                matched = self._consume(
                    key, context, plan.read_rule.key_ref, "business_key", step.step_id,
                    lambda expected: raw == expected,
                )
                if not matched:
                    continue
            matches.append(candidate)
        return tuple(matches)


    async def _options_current(
        self,
        session: BrowserSessionRef,
        target: TargetRef,
        step: SkillStep,
        context: ExecutionContext,
        plan: RegisteredSitePlan,
        resolution: ResolutionRound | None = None,
    ) -> tuple[VisibleCandidate, ...]:
        if step.operation != "select_option" or step.option_ref is None:
            raise failure("denied")
        if "option" not in plan.policy.allowed_roles:
            raise failure("unsupported")
        parent = (resolution.node(target) if resolution is not None
                  else await self._observer.resolve_exact(session, target, plan.policy))
        self._origins(parent, context)
        if not await parent.element.evaluate("el => el.tagName === 'SELECT' && !el.multiple"):
            raise failure("unsupported")
        sealed = await self._sealed(context, step.option_ref, "option_values", step.step_id)
        handles = await bounded_children(parent.element, "option", plan.policy.maximum_candidates)
        current: list[_Option] = []
        key = (session.session_ref, step.step_id)
        previous = self._options.get(key)
        committed = False
        temporary = list(handles)
        try:
            for handle in handles:
                data = await handle.evaluate(
                    """(el, parent) => {
                    if (!el.isConnected || el.closest('select') !== parent ||
                        el.ownerDocument !== parent.ownerDocument) throw new Error('member');
                    if (el.value.length > 4096 || el.label.length > 256) throw new Error('bound');
                    return {value: el.value, label: el.label,
                        enabled: !el.disabled && !el.closest('optgroup[disabled]')};
                }""",
                    parent.element,
                )
                if not isinstance(data, dict) or set(data) != {"value", "label", "enabled"}:
                    raise failure("invalid_response")
                if type(data["value"]) is not str or type(data["label"]) is not str:
                    raise failure("invalid_response")
                if len(data["value"].encode()) > 4096 or len(data["label"]) > 256:
                    raise failure("unsupported")
                approved = self._consume(
                    sealed,
                    context,
                    step.option_ref,
                    "option_values",
                    step.step_id,
                    lambda values: data["value"] in values,
                )
                if not approved or data["enabled"] is not True:
                    continue
                signature = hashlib.sha256(self._salt + data["value"].encode()).hexdigest()
                candidate = VisibleCandidate(
                    ref=TargetRef(
                        target_id=secrets.token_hex(12), candidate_epoch=0, scope=target.scope
                    ),
                    role="option",
                    name=data["label"] if data["label"] in plan.policy.allowed_names else None,
                    value_state="not_applicable",
                    visible=True,
                    enabled=True,
                )
                current.append(_Option(candidate, handle, signature))
            unchanged = (
                previous is not None
                and previous.parent == target
                and len(previous.options) == len(current)
            )
            if unchanged and previous is not None:
                for old, new in zip(previous.options, current, strict=True):
                    if (
                        old.signature != new.signature
                        or old.candidate.name != new.candidate.name
                        or not await old.element.evaluate(
                            "(old, fresh) => old === fresh && old.isConnected",
                            new.element,
                        )
                    ):
                        unchanged = False
                        break
            if unchanged and previous is not None:
                for old, new in zip(previous.options, current, strict=True):
                    new.candidate = old.candidate
            kept = {id(item.element) for item in current}
            temporary = [handle for handle in handles if id(handle) in kept]
            await _dispose_many(tuple(handle for handle in handles if id(handle) not in kept))
            if previous is not None:
                # Retire the old holder before releasing its owned handles. A
                # cancelled cleanup must never leave a partially disposed holder.
                self._options.pop(key, None)
                await _dispose_many(tuple(old.element for old in previous.options))
            self._options[key] = _OptionSet(target, current)
            committed = True
            return tuple(item.candidate for item in current)
        finally:
            if not committed:
                await _dispose_many(tuple(temporary), suppress_cancel=True)

    @_neutral
    async def option_candidates(
        self,
        session: BrowserSessionRef,
        target: TargetRef,
        step: SkillStep,
        context: ExecutionContext,
    ) -> tuple[VisibleCandidate, ...]:
        plan, _ = await self._authority(session, context)
        rule = next((r for r in plan.steps if r.step == step), None)
        if rule is None or target.scope.region_id != rule.observation.region_id:
            raise failure("denied")
        projection = await self.observe(
            session,
            ObservationRequest(
                region_id=target.scope.region_id,
                expected_scope=target.scope,
            ),
            plan.policy,
        )
        matches = await self.target_candidates(session, step, projection, context)
        if target not in tuple(c.ref for c in matches):
            raise failure("denied")
        result = await self._options_current(session, target, step, context, plan)
        await self._authority(session, context)
        return result

    async def _read_target_row(
        self,
        session: BrowserSessionRef,
        command: ActionCommand,
        context: ExecutionContext,
        plan: RegisteredSitePlan,
        resolution: ResolutionRound,
    ) -> Any:
        """Bind a read action to its live registered row before acknowledging it.

        IndependentVerifier later reads the region again by confirmed key; this
        check only establishes that the selected action target is that row.
        """
        target = command.target
        dom = self._rules[plan.skill_digest].read
        if target is None or target.scope.region_id != dom.observation.region_id:
            raise failure("stale")
        self._validation_mark(session, "parameter_resolution")
        sealed = await self._sealed(
            context, plan.read_rule.key_ref, "business_key", plan.verifier_id
        )
        self._validation_mark(session, "region_resolution")
        exact = resolution.region
        self._origins(exact, context)
        region = await exact.element.evaluate_handle("el => el")
        try:
            self._validation_mark(session, "target_resolution")
            node = resolution.node(target)
            self._origins(node, context)
            row = await node.element.evaluate_handle(
                "(el, selector) => el.closest(selector)", dom.row_selector
            )
            try:
                async def matches() -> bool:
                    values: tuple[DOMValue, ...] = (dom.key, dom.tenant, dom.user)
                    expected: tuple[str, ...] = (
                        context.expected_binding.owner.tenant_id,
                        context.expected_binding.owner.user_id,
                    )
                    if isinstance(plan.read_rule, RegisteredQueryReadRule):
                        assert dom.object_type is not None
                        values += (dom.object_type,)
                        expected += (plan.read_rule.object_type,)
                    # Merge adjacent private DOM reads only. Both rounds still
                    # surround the same fresh authority check; no proof is cached.
                    matched = await self._consume(
                        sealed, context, plan.read_rule.key_ref, "business_key",
                        plan.verifier_id, lambda approved: private_row_identity(
                            region, node.element, row,
                            {"row_selector": dom.row_selector, "selector": None,
                             "fields": self._identity_fields(
                                 values, (approved, *expected), dom.maximum_value_bytes,
                             )},
                        ),
                    )
                    if type(matched) is not bool:
                        raise failure("invalid_response")
                    return matched

                self._validation_mark(session, "row_identity")
                if not await matches():
                    raise failure("stale")
                self._validation_mark(session, "authority")
                await self._authority(session, context, command)
                self._validation_mark(session, "row_identity")
                if not await matches():
                    raise failure("stale")
            finally:
                await _dispose_many((row,), suppress_cancel=sys.exc_info()[0] is not None)
        finally:
            await _dispose_many((region,), suppress_cancel=sys.exc_info()[0] is not None)
        # The last awaited DOM work before read acknowledgment checks the same
        # selected node, after both borrowed row/region handles are released.
        self._validation_mark(session, "target_identity")
        if (
            not await node.element.evaluate("el => el.isConnected")
            or not await node.element.is_visible()
            or not await node.element.is_enabled()
        ):
            raise failure("stale")
        return node

    @staticmethod
    def _identity_fields(
        values: tuple[DOMValue, ...], expected: tuple[str | tuple[str, ...], ...], limit: int,
    ) -> list[dict[str, Any]]:
        return [{"selector": field.selector, "kind": field.kind, "attribute": field.attribute,
                 "expected": value, "limit": limit}
                for field, value in zip(values, expected, strict=True)]

    async def _final_identity_config(
        self, command: ActionCommand, context: ExecutionContext,
        plan: RegisteredSitePlan, resolution: ResolutionRound, option: Any,
    ) -> dict[str, Any]:
        """Prepare private expectations before the round's final authority wait."""
        rules = self._rules[plan.skill_digest]
        step = next(item for item in rules.steps if item.step_id == command.step.step_id)
        config: dict[str, Any] = {
            "region_selector": self._observer._regions[
                resolution.region.projection.scope.region_id
            ].region_selector,
            "selector": step.selector if command.target is not None else None,
            "operation": command.step.operation, "fields": [], "row_selector": None,
            "target": command.target is not None,
        }
        if command.step.operation == "read" or command.step.locator.kind == "business_key":
            read = rules.read
            row_selector = (
                read.row_selector if command.step.operation == "read" else step.row_selector
            )
            key_field = read.key if command.step.operation == "read" else step.key
            if row_selector is None or key_field is None:
                raise failure("unsupported")
            purpose_id = (
                plan.verifier_id if command.step.operation == "read" else command.step.step_id
            )
            key = await self._sealed(context, plan.read_rule.key_ref, "business_key", purpose_id)
            expected_key = self._consume(
                key, context, plan.read_rule.key_ref, "business_key", purpose_id, lambda value: value,
            )
            fields = [
                (key_field, expected_key,
                 read.maximum_value_bytes if command.step.operation == "read" else 4096),
                (read.tenant, context.expected_binding.owner.tenant_id, read.maximum_value_bytes),
                (read.user, context.expected_binding.owner.user_id, read.maximum_value_bytes),
            ]
            if isinstance(plan.read_rule, RegisteredQueryReadRule):
                if read.object_type is None:
                    raise failure("unsupported")
                fields.append(
                    (read.object_type, plan.read_rule.object_type, read.maximum_value_bytes)
                )
            config.update(row_selector=row_selector, fields=[
                packet for value, expected, limit in fields
                for packet in self._identity_fields((value,), (expected,), limit)
            ])
        if option is not None:
            option_identity = await option.evaluate(_OPTION_IDENTITY)
            registered = next(
                item for item in self._options[
                    (resolution.session.session_ref, command.step.step_id)
                ].options
                if item.candidate.ref == command.option
            )
            if (not isinstance(option_identity, list) or len(option_identity) != 2
                    or any(type(value) is not str for value in option_identity)
                    or hashlib.sha256(self._salt + option_identity[0].encode()).hexdigest()
                    != registered.signature):
                raise failure("stale")
            config.update(option_value=option_identity[0], option_label=option_identity[1])
        return config

    async def _validate(
        self, session: BrowserSessionRef, command: ActionCommand,
        context: ExecutionContext,
        *, scope: Literal["pre_dispatch", "dispatch_barrier"] = "pre_dispatch",
    ) -> tuple[Any, SealedParameter | None, Any | None]:
        progress = self._validation_progress.setdefault(session.session_ref, _ValidationProgress())
        progress.calls = min(10000, progress.calls + 1)
        progress.scope = scope
        self._validation_mark(session, "authority")
        plan, _ = await self._authority(session, context, command)
        rule = next(r for r in plan.steps if r.step == command.step)
        self._validation_mark(session, "region_resolution")
        owned: Any = None
        try:
            async with self._observer.resolution_round(
                session, rule.observation, plan.policy,
            ) as resolution:
                node, sealed, option = await self._validate_in_round(
                    session, command, context, plan, resolution,
                )
                config = await self._final_identity_config(
                    command, context, plan, resolution, option,
                )
                # Own node and actual ancestor identities before releasing the
                # round. Borrowed handles never escape their lock lifetime.
                element = await node.element.evaluate_handle("el => el")
                try:
                    identity = await element.evaluate_handle(
                        "(el, config) => ({node: el, region: config.region, "
                        "row: config.selector ? el.closest(config.selector) : null})",
                        {"region": resolution.region.element, "selector": config["row_selector"]},
                    )
                except BaseException:
                    await _dispose(element, suppress_cancel=True)
                    raise
                owned = _ValidatedNode(node.page, node.frame, element, identity)
            # resolution_round has now completed its last current-authority
            # await. Only live DOM identity/state is read here, with no helper
            # that can renew authority or re-resolve a different target.
            config.update(element=owned.element, option=option)
            self._validation_mark(session, "target_identity")
            matched = await owned.identity.evaluate(_FINAL_IDENTITY, config)
            if matched is not True:
                raise failure("stale")
            self._origins(owned, context)
            check_liveness(context)
            self._validation_mark(session, None)
            return owned, sealed, option
        except BaseException:
            if owned is not None:
                await _dispose_validated_node(owned, failed=True)
            raise

    async def _validate_in_round(
        self, session: BrowserSessionRef, command: ActionCommand,
        context: ExecutionContext, plan: RegisteredSitePlan,
        resolution: ResolutionRound,
    ) -> tuple[Any, SealedParameter | None, Any | None]:
        exact = resolution.region
        self._origins(exact, context)
        node: Any = exact
        sealed: SealedParameter | None = None
        option: Any = None
        if command.target is not None:
            self._validation_mark(session, "candidate_enumeration")
            candidates = await self._target_candidates_in_round(
                session, command.step, context, resolution,
            )
            if command.target not in tuple(c.ref for c in candidates):
                raise failure("stale")
            if command.step.operation in {"fill", "select_option"}:
                self._validation_mark(session, "target_resolution")
                node = resolution.node(command.target)
        if command.step.operation == "select_option":
            self._validation_mark(session, "option_identity")
            assert command.target is not None
            await self._authority(session, context, command)
            candidates = await self._options_current(
                session, command.target, command.step, context, plan, resolution,
            )
            await self._authority(session, context, command)
            if command.option not in tuple(c.ref for c in candidates):
                raise failure("stale")
            option = next(
                item.element
                for item in self._options[(session.session_ref, command.step.step_id)].options
                if item.candidate.ref == command.option
            )
            # This round retains the parent handle while options are enumerated.
            node = resolution.node(command.target)
            if not await option.evaluate(
                "(el, parent) => el.isConnected && el.closest('select') === parent", node.element
            ):
                raise failure("stale")
        elif command.step.operation == "fill":
            self._validation_mark(session, "parameter_resolution")
            assert command.step.value_ref is not None
            sealed = await self._sealed(
                context, command.step.value_ref, "fill_value", command.step.step_id
            )
            if not await node.element.is_editable():
                raise failure("stale")
        elif command.step.operation == "navigate":
            self._validation_mark(session, "parameter_resolution")
            assert command.step.url_ref is not None
            sealed = await self._sealed(
                context, command.step.url_ref, "navigation_url", command.step.step_id
            )
            if not self._consume(
                sealed,
                context,
                command.step.url_ref,
                "navigation_url",
                command.step.step_id,
                lambda url: (
                    type(url) is str
                    and len(url) <= 2048
                    and navigation_allowed(url, context.navigation_origins)
                ),
            ):
                raise failure("denied")
        self._validation_mark(session, "authority")
        await self._authority(session, context, command)
        if command.step.operation == "select_option":
            self._validation_mark(session, "option_identity")
            assert command.target is not None
            refreshed = await self._options_current(
                session,
                command.target,
                command.step,
                context,
                plan,
                resolution,
            )
            if command.option not in tuple(item.ref for item in refreshed):
                raise failure("stale")
            option = next(
                item.element
                for item in self._options[(session.session_ref, command.step.step_id)].options
                if item.candidate.ref == command.option
            )
        # Final actual scope/ancestor check follows the independent subject IO.
        if command.target is not None:
            self._validation_mark(session, "target_resolution")
            node = resolution.node(command.target)
            self._validation_mark(session, "target_identity")
            dom = next(
                r for r in self._rules[plan.skill_digest].steps if r.step_id == command.step.step_id
            )
            if not await node.element.evaluate(
                "(el, selector) => el.matches(selector)",
                dom.selector,
            ):
                raise failure("stale")
            if command.step.locator.kind == "business_key":
                if dom.key is None:
                    raise failure("unsupported")
                approved = await self._sealed(
                    context,
                    plan.read_rule.key_ref,
                    "business_key",
                    command.step.step_id,
                )
                current_region = resolution.region
                region_handle = await current_region.element.evaluate_handle("el => el")
                try:
                    node = resolution.node(command.target)
                    row = await node.element.evaluate_handle(
                        "(el, selector) => el.closest(selector)",
                        dom.row_selector,
                    )
                    try:
                        if not await region_handle.evaluate(
                            "(region, row) => row && region.contains(row)",
                            row,
                        ):
                            raise failure("stale")
                        raw = await read_private(row, dom.key, 4096)
                        if not self._consume(
                            approved,
                            context,
                            plan.read_rule.key_ref,
                            "business_key",
                            command.step.step_id,
                            lambda value: raw == value,
                        ):
                            raise failure("stale")
                    finally:
                        await _dispose_many((row,), suppress_cancel=sys.exc_info()[0] is not None)
                finally:
                    await _dispose_many((region_handle,), suppress_cancel=sys.exc_info()[0] is not None)
        else:
            self._validation_mark(session, "region_resolution")
            node = resolution.region
        self._origins(node, context)
        self._validation_mark(session, "target_identity")
        if command.target is not None:
            if (
                not await node.element.evaluate("el => el.isConnected")
                or not await node.element.is_visible()
                or not await node.element.is_enabled()
            ):
                raise failure("stale")
            if command.step.operation == "fill" and not await node.element.is_editable():
                raise failure("stale")
        if command.step.operation == "read":
            node = await self._read_target_row(session, command, context, plan, resolution)
        if option is not None:
            self._validation_mark(session, "option_identity")
            value = await option.evaluate(
                """(el, parent) => {
                if (!el.isConnected || el.closest('select') !== parent || el.disabled ||
                    el.closest('optgroup[disabled]') || el.value.length > 4096)
                    throw new Error('option_stale');
                return el.value;
            }""",
                node.element,
            )
            registered = next(
                item
                for item in self._options[(session.session_ref, command.step.step_id)].options
                if item.candidate.ref == command.option
            )
            if (
                type(value) is not str
                or len(value.encode()) > 4096
                or hashlib.sha256(self._salt + value.encode()).hexdigest() != registered.signature
            ):
                raise failure("stale")
        self._validation_mark(session, None)
        return node, sealed, option

    @_neutral
    async def revalidate(
        self, session: BrowserSessionRef, command: ActionCommand, context: ExecutionContext
    ) -> None:
        node, _, _ = await self._validate(session, command, context)
        await _dispose_validated_node(node)

    @_neutral
    async def execute(
        self,
        session: BrowserSessionRef,
        command: ActionCommand,
        context: ExecutionContext,
    ) -> DispatchReceipt:
        self._registration(session, context)
        permit: DispatchPermit | None = None
        node: Any = None
        failed = False
        try:
            await self._authority(session, context, command)
            async with context.dispatch_barrier(
                session, command, context.expected_binding
            ) as permit:
                if (
                    not isinstance(permit, DispatchPermit)
                    or permit.execution_id != context.execution_id
                    or permit.command != command
                ):
                    raise failure("denied")
                node, sealed, option = await self._validate(
                    session, command, context, scope="dispatch_barrier",
                )
                check_liveness(context)
                timeout = max(1, (context.deadline_monotonic - time.monotonic()) * 1000)
                operation = command.step.operation
                # No await between begin_send and the actual Playwright action.
                if operation == "fill":
                    assert sealed is not None and command.step.value_ref is not None

                    async def fill(value: str | tuple[str, ...]) -> None:
                        if type(value) is not str or len(value.encode()) > 16384:
                            raise failure("unsupported")
                        assert permit is not None
                        permit.begin_send()
                        await node.element.fill(value, timeout=timeout)

                    await self._consume(
                        sealed,
                        context,
                        command.step.value_ref,
                        "fill_value",
                        command.step.step_id,
                        fill,
                    )
                elif operation == "navigate":
                    assert sealed is not None and command.step.url_ref is not None

                    async def navigate(url: str | tuple[str, ...]) -> None:
                        assert permit is not None
                        permit.begin_send()
                        await node.page.goto(url, timeout=timeout, wait_until="domcontentloaded")

                    await self._consume(
                        sealed,
                        context,
                        command.step.url_ref,
                        "navigation_url",
                        command.step.step_id,
                        navigate,
                    )
                elif operation == "select_option":
                    permit.begin_send()
                    await node.element.select_option(element=option, timeout=timeout)
                elif operation == "click":
                    permit.begin_send()
                    await node.element.click(timeout=timeout)
                else:
                    # _validate just bound this live target to the registered
                    # read row under the dispatch barrier.
                    permit.begin_send()
                check_liveness(context)
                if self._guards[session.session_ref].failed:
                    raise failure("denied")
            return DispatchReceipt(
                skill_digest=command.skill_digest,
                step_id=command.step.step_id,
                state="acknowledged",
                evidence_digest=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            )
        except BaseException as error:
            failed = True
            if permit is not None and permit.send_started:
                return DispatchReceipt(
                    skill_digest=command.skill_digest,
                    step_id=command.step.step_id,
                    state="possibly_sent",
                    failure=failure("effect_unknown", dispatched=True).failure,
                )
            if isinstance(
                error, (BrowserOperationError, BrowserProviderError, asyncio.CancelledError)
            ):
                raise
            raise failure("invalid_response") from None
        finally:
            if node is not None:
                await _dispose_validated_node(node, failed=failed)

    async def _compare_rows(
        self,
        region: Any,
        spec: ReadSpec,
        context: ExecutionContext,
        plan: RegisteredSitePlan,
        key: SealedParameter,
        expected: dict[str, SealedParameter],
    ) -> tuple[int, bool, bool | None, tuple[ReadFieldEvidence, ...], str,
               tuple[tuple[str, str], ...]]:
        dom = self._rules[plan.skill_digest].read
        query = isinstance(plan.read_rule, RegisteredQueryReadRule)
        step_id = plan.verifier_id
        rows = await bounded_children(region, dom.row_selector, dom.maximum_rows)
        count, owner = 0, False
        object_match: bool | None = False if query else None
        fields = tuple(ReadFieldEvidence(field_id=f, status="missing") for f in spec.fields)
        digest = hashlib.sha256(self._salt)
        byte_count = 0
        values: list[tuple[str, str]] = []

        def fingerprint(value: Any) -> None:
            nonlocal byte_count
            encoded = json.dumps(value).encode()
            byte_count += len(encoded)
            if byte_count > 262144:
                raise failure("unsupported")
            digest.update(len(encoded).to_bytes(8, "big") + encoded)

        try:
            for row in rows:
                raw_key = await read_private(row, dom.key, dom.maximum_value_bytes)
                fingerprint(raw_key)
                matched_key = self._consume(
                    key,
                    context,
                    spec.business_key.value_ref,
                    "business_key",
                    step_id,
                    lambda approved: raw_key is not None and raw_key == approved,
                )
                if not matched_key:
                    continue
                count += 1
                tenant, user = await read_private_many(
                    row, (dom.tenant, dom.user), dom.maximum_value_bytes,
                )
                fingerprint([tenant, user])
                owner = (
                    tenant == context.expected_binding.owner.tenant_id
                    and user == context.expected_binding.owner.user_id
                )
                if not owner:
                    continue
                if query:
                    assert dom.object_type is not None
                    actual_type = await read_private(row, dom.object_type, dom.maximum_value_bytes)
                    fingerprint(actual_type)
                    object_match = actual_type == plan.read_rule.object_type
                    if not object_match:
                        continue
                comparisons = []
                values = []
                field_values = await read_private_many(
                    row, tuple(location for _, location in dom.fields), dom.maximum_value_bytes,
                )
                for index, ((field_id, _location), raw) in enumerate(
                    zip(dom.fields, field_values, strict=True)
                ):
                    fingerprint(raw)
                    if isinstance(raw, str):
                        values.append((field_id, raw))
                    if isinstance(plan.read_rule, RegisteredReadRule):
                        registered = plan.read_rule.fields[index]
                        matched = self._consume(
                            expected[field_id], context, registered.value_ref,
                            "expected_field", step_id,
                            lambda approved: raw is not None and raw == approved,
                        )
                    else:
                        matched = False
                    comparisons.append(
                        ReadFieldEvidence(
                            field_id=field_id,
                            status="missing"
                            if raw is None
                            else "present"
                            if query
                            else "matched"
                            if matched
                            else "mismatch",
                        )
                    )
                fields = tuple(comparisons)
            return count, owner, object_match, fields, digest.hexdigest(), tuple(values)
        finally:
            await _dispose_many(tuple(rows), suppress_cancel=sys.exc_info()[0] is not None)

    @_neutral
    async def read(
        self, session: BrowserSessionRef, spec: ReadSpec, context: ExecutionContext
    ) -> ReadEvidence:
        self._private_reads.pop(session.session_ref, None)
        plan, _ = await self._authority(session, context, spec)
        dom = self._rules[plan.skill_digest].read
        exact = await self._observer.resolve_region(session, dom.observation, plan.policy)
        self._origins(exact, context)
        # A verifier-specific purpose binding is independent of any action step.
        step_id = plan.verifier_id
        key = await self._sealed(context, spec.business_key.value_ref, "business_key", step_id)
        expected = {}
        if isinstance(plan.read_rule, RegisteredReadRule):
            expected = {
                f.field_id: await self._sealed(context, f.value_ref, "expected_field", step_id)
                for f in plan.read_rule.fields
            }
        # Observer owns its borrowed handle. This clone survives its next observation.
        region = await exact.element.evaluate_handle("el => el")
        try:
            before = await self._compare_rows(region, spec, context, plan, key, expected)
            await self._authority(session, context, spec)
            final = await self._observer.resolve_region(
                session,
                ObservationRequest(
                    region_id=exact.projection.scope.region_id,
                    expected_scope=exact.projection.scope,
                ),
                plan.policy,
            )
            self._origins(final, context)
            if not await region.evaluate(
                "(old, fresh) => old === fresh && old.isConnected", final.element
            ):
                raise failure("stale")
            after = await self._compare_rows(region, spec, context, plan, key, expected)
            if before != after:
                raise failure("stale")
            await self._authority(session, context, spec)
            check_liveness(context)
            count, owner, object_match, fields, _private_signature, values = after
            query = isinstance(plan.read_rule, RegisteredQueryReadRule)
            schema_validated = None
            if query:
                schema_validated = False
                if (count == 1 and owner and object_match
                        and all(f.status == "present" for f in fields)
                        and final.projection.coverage.state == "complete"):
                    schema_validated = self._query_schema_valid(
                        session, plan, dict(values),
                    )
            # Hash only safe facts. Private record fingerprints never leave this method.
            safe = {
                "scope": final.projection.scope.model_dump(),
                "confirmation": spec.business_key.confirmation_ref,
                "source": context.source.fixture_digest,
                "count": count,
                "owner": owner,
                "fields": [field.model_dump() for field in fields],
            }
            if query:
                safe.update({
                    "mode": spec.mode, "object_type_match": object_match,
                    "schema_validated": schema_validated,
                    "verifier": plan.verifier_digest,
                    # Bind the result to this independent read, including when
                    # two successful reads have identical safe metadata.
                    "read_instance": secrets.token_hex(16),
                })
            evidence = ReadEvidence(
                binding=session.binding,
                business_key=spec.business_key,
                match_count=count,
                key_match=count == 1,
                owner_match=count == 1 and owner,
                coverage=final.projection.coverage,
                fields=fields,
                mode=spec.mode,
                object_type_match=object_match,
                schema_validated=schema_validated,
                evidence_digest=hashlib.sha256(
                    json.dumps(safe, sort_keys=True).encode()
                ).hexdigest(),
            )
            evidence.validate_for(spec, session.binding)
            if (
                count == 1 and owner and evidence.coverage.state == "complete"
                and len(values) == len(spec.fields)
                and all(item.status == ("present" if query else "matched") for item in fields)
                and (not query or (object_match and schema_validated))
            ):
                self._private_reads[session.session_ref] = (spec, evidence, values)
            return evidence
        finally:
            await _dispose_many((region,), suppress_cancel=sys.exc_info()[0] is not None)

    def _query_schema_valid(
        self, session: BrowserSessionRef, plan: RegisteredSitePlan, fields: dict[str, str],
    ) -> bool:
        rule = plan.read_rule
        projector = self._registration(session).project_output
        if not isinstance(rule, RegisteredQueryReadRule) or projector is None:
            raise failure("denied")

        def local(node: object) -> None:
            if isinstance(node, dict):
                for key in ("$ref", "$dynamicRef"):
                    if key in node and (
                        not isinstance(node[key], str) or not node[key].startswith("#/")
                    ):
                        raise ValueError("query_schema_reference_invalid")
                for child in node.values():
                    local(child)
            elif isinstance(node, list):
                for child in node:
                    local(child)

        try:
            schema = json.loads(rule.output_schema_json)
            local(schema)
            Draft202012Validator.check_schema(schema)
            value = dict(projector(fields))
            Draft202012Validator(schema, registry=Registry()).validate(value)
            encoded = json.dumps(value, ensure_ascii=True, allow_nan=False,
                                 sort_keys=True, separators=(",", ":")).encode("ascii")
            return len(encoded) <= rule.maximum_result_bytes
        except Exception:
            return False

    def _consume_verified_result(
        self,
        session: BrowserSessionRef,
        context: ExecutionContext,
        verification: VerificationResult,
        consumer: Callable[[dict[str, str]], _R],
    ) -> _R:
        """Trusted bridge only: consume values from the exact verifier read once.

        This never performs another DOM read. IndependentVerifier must have
        completed its post-read authorization/confirmation checks before entry.
        """
        self._registration(session, context)
        item = self._private_reads.pop(session.session_ref, None)
        check_liveness(context)
        if (
            item is None or verification.status != "verified"
            or verification.evidence_digest is None
            or item[1].evidence_digest != verification.evidence_digest
            or item[0].binding != context.expected_binding
        ):
            raise failure("denied")
        return consumer(dict(item[2]))

    def _discard_private_result(self, session: BrowserSessionRef) -> None:
        self._private_reads.pop(session.session_ref, None)
