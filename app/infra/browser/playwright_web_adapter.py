"""Single Playwright WebAdapter; all DOM and sealed values remain process-local.

The execution registration is installed by trusted composition. It is not an
admission API, durable send journal, or replacement for the domain Executor.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from functools import wraps
from typing import Any, Awaitable, Callable, Coroutine, ParamSpec, Protocol, TypeVar, cast

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
    RegisteredDOMRules,
    bounded_children,
    read_private,
)
from app.infra.browser.playwright_observer import PlaywrightObserver
from app.ports.browser import SiteAdapter


class BusinessRegistry(Protocol):
    async def assert_business_authority(
        self, session: BrowserSessionRef, source: DecisionSource
    ) -> Any: ...


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


@dataclass(slots=True, repr=False)
class _Option:
    candidate: VisibleCandidate
    element: Any
    signature: str


@dataclass(slots=True, repr=False)
class _OptionSet:
    parent: TargetRef
    options: list[_Option]


@dataclass(slots=True, repr=False)
class _OriginGuard:
    context: Any
    execution: ExecutionContext
    failed: bool = False


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
        await authorize_current(session, context, subject)
        live = await self._registry.assert_business_authority(session, context.source)
        if live.session != session:
            raise failure("denied")
        await authorize_current(session, context, subject)
        await self._origin_guard(session, context, live)
        check_liveness(context)
        return plan, live

    async def _origin_guard(
        self, session: BrowserSessionRef, ctx: ExecutionContext, live: Any
    ) -> None:
        async with self._guard_lock:
            guard = self._guards.get(session.session_ref)
            if guard is not None:
                if guard.context is not live.context or guard.failed:
                    raise failure("denied")
                return
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

            live.context.on("request", inspect_request)
            live.context.on("page", page_created)
            await live.context.route("**/*", route_request)
            self._guards[session.session_ref] = guard

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
        self,
        session: BrowserSessionRef,
        step: SkillStep,
        projection: VisibleProjection,
        context: ExecutionContext,
    ) -> tuple[VisibleCandidate, ...]:
        self._observe_wait[session.session_ref] = ("candidate_authority_before", time.monotonic())
        plan, _ = await self._authority(session, context)
        rule = next((r for r in plan.steps if r.step == step), None)
        if rule is None or projection.scope.region_id != rule.observation.region_id:
            raise failure("denied")
        dom = next(r for r in self._rules[plan.skill_digest].steps if r.step_id == step.step_id)
        key = None
        if step.locator.kind == "business_key":
            if dom.key is None:
                raise failure("unsupported")
            self._observe_wait[session.session_ref] = ("candidate_key_resolution", time.monotonic())
            key = await self._sealed(context, plan.read_rule.key_ref, "business_key", step.step_id)
        matches = []
        self._observe_wait[session.session_ref] = ("candidate_batch", time.monotonic())
        async with self._observer._candidate_batch(session, projection, plan.policy) as (
            region, nodes,
        ):
            self._origins(region, context)
            for candidate, node in nodes:
                self._origins(node, context)
                self._observe_wait[session.session_ref] = ("candidate_selector", time.monotonic())
                if not await node.element.evaluate(
                    "(el, selector) => el.matches(selector)", dom.selector
                ):
                    continue
                if key is not None and dom.key is not None:
                    self._observe_wait[session.session_ref] = ("candidate_key_read", time.monotonic())
                    row = await node.element.evaluate_handle(
                        "(el, selector) => el.closest(selector)",
                        dom.row_selector,
                    )
                    try:
                        if not await region.element.evaluate(
                            "(region, row) => row && region.contains(row)",
                            row,
                        ):
                            raise failure("denied")
                        raw = await read_private(row, dom.key, 4096)
                        matched = self._consume(
                            key,
                            context,
                            plan.read_rule.key_ref,
                            "business_key",
                            step.step_id,
                            lambda expected: raw == expected,
                        )
                    finally:
                        await row.dispose()
                    if not matched:
                        continue
                matches.append(candidate)
            self._observe_wait[session.session_ref] = ("candidate_authority_after", time.monotonic())
            await self._authority(session, context)
            self._observe_wait[session.session_ref] = ("candidate_batch_after", time.monotonic())
        return tuple(matches)

    async def _options_current(
        self,
        session: BrowserSessionRef,
        target: TargetRef,
        step: SkillStep,
        context: ExecutionContext,
        plan: RegisteredSitePlan,
    ) -> tuple[VisibleCandidate, ...]:
        if step.operation != "select_option" or step.option_ref is None:
            raise failure("denied")
        if "option" not in plan.policy.allowed_roles:
            raise failure("unsupported")
        parent = await self._observer.resolve_exact(session, target, plan.policy)
        self._origins(parent, context)
        if not await parent.element.evaluate("el => el.tagName === 'SELECT' && !el.multiple"):
            raise failure("unsupported")
        sealed = await self._sealed(context, step.option_ref, "option_values", step.step_id)
        handles = await bounded_children(parent.element, "option", plan.policy.maximum_candidates)
        current: list[_Option] = []
        key = (session.session_ref, step.step_id)
        previous = self._options.get(key)
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
            self._options[key] = _OptionSet(target, current)
            if previous is not None:
                for old in previous.options:
                    await old.element.dispose()
            return tuple(item.candidate for item in current)
        except BaseException:
            self._options.pop(key, None)
            if previous is not None:
                for old in previous.options:
                    await old.element.dispose()
            raise
        finally:
            kept = {
                id(item.element) for item in self._options.get(key, _OptionSet(target, [])).options
            }
            for handle in handles:
                if id(handle) not in kept:
                    await handle.dispose()

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
    ) -> Any:
        """Bind a read action to its live registered row before acknowledging it.

        IndependentVerifier later reads the region again by confirmed key; this
        check only establishes that the selected action target is that row.
        """
        target = command.target
        dom = self._rules[plan.skill_digest].read
        if target is None or target.scope.region_id != dom.observation.region_id:
            raise failure("stale")
        sealed = await self._sealed(
            context, plan.read_rule.key_ref, "business_key", plan.verifier_id
        )
        exact = await self._observer.resolve_region(
            session,
            ObservationRequest(region_id=target.scope.region_id, expected_scope=target.scope),
            plan.policy,
        )
        self._origins(exact, context)
        region = await exact.element.evaluate_handle("el => el")
        try:
            node = await self._observer.resolve_exact(session, target, plan.policy)
            self._origins(node, context)
            row = await node.element.evaluate_handle(
                "(el, selector) => el.closest(selector)", dom.row_selector
            )
            try:
                async def matches() -> bool:
                    if not await region.evaluate(
                        "(region, row) => row && region.contains(row)", row
                    ):
                        return False
                    raw_key = await read_private(row, dom.key, dom.maximum_value_bytes)
                    matched_key = self._consume(
                        sealed, context, plan.read_rule.key_ref, "business_key",
                        plan.verifier_id, lambda approved: raw_key == approved,
                    )
                    if not matched_key:
                        return False
                    tenant = await read_private(row, dom.tenant, dom.maximum_value_bytes)
                    user = await read_private(row, dom.user, dom.maximum_value_bytes)
                    if (tenant != context.expected_binding.owner.tenant_id
                            or user != context.expected_binding.owner.user_id):
                        return False
                    if isinstance(plan.read_rule, RegisteredQueryReadRule):
                        assert dom.object_type is not None
                        actual_type = await read_private(
                            row, dom.object_type, dom.maximum_value_bytes
                        )
                        if actual_type != plan.read_rule.object_type:
                            return False
                    return True

                if not await matches():
                    raise failure("stale")
                await self._authority(session, context, command)
                if not await matches():
                    raise failure("stale")
            finally:
                await row.dispose()
        finally:
            await region.dispose()
        # The last awaited DOM work before read acknowledgment checks the same
        # selected node, after both borrowed row/region handles are released.
        if (
            not await node.element.evaluate("el => el.isConnected")
            or not await node.element.is_visible()
            or not await node.element.is_enabled()
        ):
            raise failure("stale")
        return node

    async def _validate(
        self,
        session: BrowserSessionRef,
        command: ActionCommand,
        context: ExecutionContext,
    ) -> tuple[Any, SealedParameter | None, Any | None]:
        plan, _ = await self._authority(session, context, command)
        rule = next(r for r in plan.steps if r.step == command.step)
        exact = await self._observer.resolve_region(session, rule.observation, plan.policy)
        self._origins(exact, context)
        node: Any = exact
        sealed: SealedParameter | None = None
        option: Any = None
        if command.target is not None:
            candidates = await self.target_candidates(
                session, command.step, exact.projection, context
            )
            if command.target not in tuple(c.ref for c in candidates):
                raise failure("stale")
            if command.step.operation in {"fill", "select_option"}:
                node = await self._observer.resolve_exact(session, command.target, plan.policy)
        if command.step.operation == "select_option":
            assert command.target is not None
            candidates = await self.option_candidates(
                session, command.target, command.step, context
            )
            if command.option not in tuple(c.ref for c in candidates):
                raise failure("stale")
            option = next(
                item.element
                for item in self._options[(session.session_ref, command.step.step_id)].options
                if item.candidate.ref == command.option
            )
            # Parent re-observation during option enumeration may dispose the old handle.
            node = await self._observer.resolve_exact(session, command.target, plan.policy)
            if not await option.evaluate(
                "(el, parent) => el.isConnected && el.closest('select') === parent", node.element
            ):
                raise failure("stale")
        elif command.step.operation == "fill":
            assert command.step.value_ref is not None
            sealed = await self._sealed(
                context, command.step.value_ref, "fill_value", command.step.step_id
            )
            if not await node.element.is_editable():
                raise failure("stale")
        elif command.step.operation == "navigate":
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
        await self._authority(session, context, command)
        if command.step.operation == "select_option":
            assert command.target is not None
            refreshed = await self._options_current(
                session,
                command.target,
                command.step,
                context,
                plan,
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
            node = await self._observer.resolve_exact(session, command.target, plan.policy)
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
                current_region = await self._observer.resolve_region(
                    session,
                    ObservationRequest(
                        region_id=command.target.scope.region_id,
                        expected_scope=command.target.scope,
                    ),
                    plan.policy,
                )
                region_handle = await current_region.element.evaluate_handle("el => el")
                try:
                    node = await self._observer.resolve_exact(session, command.target, plan.policy)
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
                        await row.dispose()
                finally:
                    await region_handle.dispose()
        else:
            node = await self._observer.resolve_region(
                session,
                ObservationRequest(
                    region_id=exact.projection.scope.region_id,
                    expected_scope=exact.projection.scope,
                ),
                plan.policy,
            )
        self._origins(node, context)
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
            node = await self._read_target_row(session, command, context, plan)
        if option is not None:
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
        return node, sealed, option

    @_neutral
    async def revalidate(
        self, session: BrowserSessionRef, command: ActionCommand, context: ExecutionContext
    ) -> None:
        await self._validate(session, command, context)

    @_neutral
    async def execute(
        self,
        session: BrowserSessionRef,
        command: ActionCommand,
        context: ExecutionContext,
    ) -> DispatchReceipt:
        self._registration(session, context)
        permit: DispatchPermit | None = None
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
                node, sealed, option = await self._validate(session, command, context)
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
                tenant = await read_private(row, dom.tenant, dom.maximum_value_bytes)
                user = await read_private(row, dom.user, dom.maximum_value_bytes)
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
                for index, (field_id, location) in enumerate(dom.fields):
                    raw = await read_private(row, location, dom.maximum_value_bytes)
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
            for row in rows:
                await row.dispose()

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
            await region.dispose()

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
