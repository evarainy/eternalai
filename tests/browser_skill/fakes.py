"""Neutral mutable browser/record facts, not a precomputed verification oracle.

Private synthetic inputs are generated per instance and consumed only by this
adapter/reader. No real credentials, resources, network, or external state.
"""

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

from app.browser_skill.executor import BrowserExecutor
from app.browser_skill.models import (
    ActionCommand,
    BrowserSessionRef,
    BrowserSkill,
    ConfirmedBusinessKey,
    Coverage,
    DecisionBudget,
    DecisionCallContext,
    DecisionRequest,
    DecisionResult,
    DispatchPermit,
    DispatchReceipt,
    ExecutionContext,
    LocatorHint,
    ObservationPolicy,
    ObservationRequest,
    ParameterPurpose,
    ParameterRef,
    ReadEvidence,
    ReadFieldEvidence,
    ReadSpec,
    ScopeBinding,
    SealedParameter,
    SkillStep,
    TargetRef,
    VisibleCandidate,
    VisibleProjection,
)
from app.browser_skill.site_rules import (
    EffectFact,
    ExpectedField,
    FrozenSiteAdapter,
    RegisteredReadRule,
    RegisteredSitePlan,
    SiteStepRule,
    navigation_allowed,
)
from app.browser_skill.verifier import IndependentVerifier, authorize_current, failure
from tests.browser_skill.factories import DIGEST, binding, context, projection, source


def make_skill(operation: str = "click", *, two_steps: bool = False) -> BrowserSkill:
    step = SkillStep.model_validate(
        {
            "step_id": "open",
            "operation": operation,
            "locator": LocatorHint(kind="test_id", value="registered_control"),
            "effect": "read_only",
            "value_ref": ParameterRef(name="input") if operation == "fill" else None,
            "url_ref": ParameterRef(name="url") if operation == "navigate" else None,
            "option_ref": ParameterRef(name="options") if operation == "select_option" else None,
        }
    )
    return BrowserSkill(
        skill_id="read_item",
        version="v1",
        digest=DIGEST,
        site_id="mock",
        site_digest=DIGEST,
        verifier_id="readback",
        verifier_digest=DIGEST,
        parameters=("key", "expected", "input", "url", "options"),
        steps=(step, step.model_copy(update={"step_id": "open_again"})) if two_steps else (step,),
    )


def make_plan(skill: BrowserSkill) -> RegisteredSitePlan:
    return RegisteredSitePlan(
        site_id=skill.site_id,
        site_digest=skill.site_digest,
        skill_id=skill.skill_id,
        skill_version=skill.version,
        skill_digest=skill.digest,
        verifier_id=skill.verifier_id,
        verifier_digest=skill.verifier_digest,
        source=source(),
        navigation_origins=(source().origin,),
        policy=ObservationPolicy(
            policy_id="public_labels",
            digest=DIGEST,
            allowed_names=("Open", "Close", "Allowed", "Other"),
            allowed_roles=("button", "combobox", "option", "textbox"),
        ),
        steps=tuple(
            SiteStepRule(
                step=step,
                observation=ObservationRequest(region_id="inbox"),
                target_criteria=("Open the registered synthetic item",),
                option_criteria=("Choose the permitted option",)
                if step.operation == "select_option"
                else (),
                effect=EffectFact(
                    operation=step.operation,
                    actual_effect="read_only",
                    proof_ref="fixture_effect",
                    evidence_digest=DIGEST,
                ),
            )
            for step in skill.steps
        ),
        read_rule=RegisteredReadRule(
            object_type="item",
            key_ref=ParameterRef(name="key"),
            fields=(ExpectedField(field_id="state", value_ref=ParameterRef(name="expected")),),
        ),
        decision_manifest=context().manifest,
        decision_budget=DecisionBudget(),
    )


@dataclass(slots=True, repr=False)
class SyntheticRecord:
    key: str = field(repr=False)
    owner: ScopeBinding = field(repr=False)
    fields: dict[str, str] = field(repr=False)


class FakeDecision:
    def __init__(self) -> None:
        self.calls: list[DecisionRequest] = []
        self.hook: Callable[[], None] = lambda: None
        self.status: Literal["selected", "abstained", "ambiguous", "unsupported"] = "selected"
        self.error: Literal["overloaded", "invalid_response"] | None = None
        self.override: TargetRef | None = None

    async def decide(self, request: DecisionRequest, ctx: DecisionCallContext) -> DecisionResult:
        self.calls.append(request)
        self.hook()
        if self.error:
            return DecisionResult(
                request_id=request.request_id, scope=request.scope, error=self.error
            )
        return DecisionResult(
            request_id=request.request_id,
            scope=request.scope,
            status=self.status,
            selected=(self.override or request.candidates[0].ref)
            if self.status == "selected"
            else None,
        )


class FakeWorld:
    """One admitted context and a mutable private DOM plus independent record store."""

    def __init__(self, operation: str = "click", *, two_steps: bool = False) -> None:
        self.skill = make_skill(operation, two_steps=two_steps)
        self.plan = make_plan(self.skill)
        self.site = FrozenSiteAdapter((self.plan,))
        self.binding = binding()
        self.session = BrowserSessionRef(session_ref="reserved", binding=self.binding)
        self.allowed = True
        self.lock = asyncio.Lock()
        self.view = projection()
        if operation in {"fill", "select_option"}:
            role = "textbox" if operation == "fill" else "combobox"
            self.view = self.view.model_copy(
                update={
                    "candidates": tuple(
                        c.model_copy(update={"role": role}) for c in self.view.candidates
                    )
                }
            )
        self.matches = (self.view.candidates[0].ref.target_id,)
        self.options = tuple(
            VisibleCandidate(
                ref=TargetRef(target_id=name, candidate_epoch=1, scope=self.view.scope),
                role="option",
                name=label,
                value_state="not_applicable",
                visible=True,
                enabled=True,
            )
            for name, label in (("option_a", "Allowed"), ("option_b", "Other"))
        )
        self.option_parent = self.view.candidates[0].ref.target_id
        self._inputs: dict[str, str | tuple[str, ...]] = {
            name: secrets.token_urlsafe(24) for name in ("key", "expected", "input")
        }
        self._inputs["url"] = source().origin + "/registered"
        self._inputs["options"] = (secrets.token_urlsafe(24),)
        self._option_values = {
            self.options[0].ref.target_id: self._inputs["options"][0],
            self.options[1].ref.target_id: secrets.token_urlsafe(24),
        }
        self.records = [
            SyntheticRecord(
                str(self._inputs["key"]), self.binding, {"state": str(self._inputs["expected"])}
            )
        ]
        self.confirmed = ReadSpec(
            binding=self.binding,
            verifier_id=self.skill.verifier_id,
            verifier_digest=self.skill.verifier_digest,
            fields=("state",),
            business_key=ConfirmedBusinessKey(
                confirmation_ref="confirmation",
                object_type="item",
                key_digest=hashlib.sha256(str(self._inputs["key"]).encode()).hexdigest(),
                value_ref=ParameterRef(name="key"),
            ),
        )
        self.context = ExecutionContext(
            execution_id="execution",
            skill=self.skill,
            expected_binding=self.binding,
            source=source(),
            deadline_monotonic=time.monotonic() + 10,
            cancellation=asyncio.Event(),
            navigation_origins=self.plan.navigation_origins,
            current_binding=self.current_binding,
            authorize=self.authorize,
            resolve_parameter=self.resolve,
            dispatch_barrier=self.barrier,
        )
        self.decision = FakeDecision()
        self.sends: list[ActionCommand] = []
        self.reads = 0
        self.before_execute: Callable[[], None] = lambda: None
        self.after_read: Callable[[], None] = lambda: None
        self.after_send: Callable[[], None] = lambda: None
        self.coverage = Coverage(state="complete", reason="complete")
        self.receipt_state: Literal["acknowledged", "possibly_sent"] = "acknowledged"
        self.bad_receipt = False
        self.dispatch_wait: asyncio.Event | None = None
        self.entered_dispatch = asyncio.Event()
        self.redirects: tuple[str, ...] = ()
        self.verifier = IndependentVerifier(self, self.site, self.read_spec)

    def executor(self) -> BrowserExecutor:
        return BrowserExecutor(
            self, self.decision, self.verifier, self.site, self.read_spec, self.decision_context
        )

    async def current_binding(self, session: BrowserSessionRef) -> ScopeBinding:
        if session != self.session:
            raise failure("denied")
        return self.binding

    async def authorize(
        self,
        session: BrowserSessionRef,
        skill: BrowserSkill,
        subject: ActionCommand | ReadSpec,
        current: ScopeBinding,
    ) -> None:
        if (
            not self.allowed
            or session != self.session
            or skill != self.skill
            or current != self.binding
        ):
            raise failure("denied")

    async def read_spec(
        self, session: BrowserSessionRef, context: ExecutionContext, binding: ScopeBinding
    ) -> ReadSpec:
        self.admitted(context)
        return self.confirmed

    def admitted(self, ctx: ExecutionContext) -> None:
        if ctx is not self.context:
            raise failure("denied")
        self.plan.validate_context(ctx)

    async def resolve(
        self,
        ref: ParameterRef,
        purpose: ParameterPurpose,
        current: ScopeBinding,
        digest: str,
        step_id: str,
    ) -> SealedParameter:
        if current != self.binding or not self.allowed or digest != self.skill.digest:
            raise failure("denied")
        permitted = {
            (self.skill.verifier_id, "business_key", "key"),
            (self.skill.verifier_id, "expected_field", "expected"),
        }
        for step in self.skill.steps:
            for arg, use in (
                (step.value_ref, "fill_value"),
                (step.url_ref, "navigation_url"),
                (step.option_ref, "option_values"),
            ):
                if arg:
                    permitted.add((step.step_id, use, arg.name))
        if (step_id, purpose, ref.name) not in permitted:
            raise failure("denied")
        return SealedParameter(
            self._inputs[ref.name],
            ref=ref,
            purpose=purpose,
            binding=current,
            skill_digest=digest,
            step_id=step_id,
        )

    async def _consume(
        self, ref: ParameterRef, purpose: ParameterPurpose, ctx: ExecutionContext, step_id: str
    ) -> str | tuple[str, ...]:
        sealed = await ctx.resolve_parameter(ref, purpose, self.binding, self.skill.digest, step_id)
        return sealed.consume(
            lambda value: value,
            ref=ref,
            purpose=purpose,
            binding=self.binding,
            skill_digest=self.skill.digest,
            step_id=step_id,
        )

    @asynccontextmanager
    async def barrier(
        self, session: BrowserSessionRef, command: ActionCommand, current: ScopeBinding
    ) -> AsyncIterator[DispatchPermit]:
        async with self.lock:
            await authorize_current(session, self.context, command)

            def begin() -> None:
                if self.context.cancellation.is_set():
                    raise failure("cancelled")
                if not self.allowed or self.binding != current:
                    raise failure("stale")
                if time.monotonic() >= self.context.deadline_monotonic:
                    raise failure("timeout")

            yield DispatchPermit(self.context.execution_id, command, begin)

    async def observe(
        self, session: BrowserSessionRef, request: ObservationRequest, policy: ObservationPolicy
    ) -> VisibleProjection:
        if request.region_id != self.view.scope.region_id or policy != self.plan.policy:
            raise failure("denied")
        if request.expected_scope and request.expected_scope != self.view.scope:
            raise failure("stale")
        return self.view

    async def target_candidates(
        self,
        session: BrowserSessionRef,
        step: SkillStep,
        view: VisibleProjection,
        ctx: ExecutionContext,
    ) -> tuple[VisibleCandidate, ...]:
        self.admitted(ctx)
        await authorize_current(session, ctx, self.confirmed)
        if (
            step not in self.skill.steps
            or view.scope != self.view.scope
            or view.candidates != self.view.candidates
            or view.binding != self.view.binding
        ):
            raise failure("stale")
        return tuple(c for c in self.view.candidates if c.ref.target_id in self.matches)

    async def option_candidates(
        self, session: BrowserSessionRef, target: TargetRef, step: SkillStep, ctx: ExecutionContext
    ) -> tuple[VisibleCandidate, ...]:
        self.admitted(ctx)
        await authorize_current(session, ctx, self.confirmed)
        if step.option_ref is None or target.target_id != self.option_parent:
            raise failure("denied")
        values = await self._consume(step.option_ref, "option_values", ctx, step.step_id)
        if not isinstance(values, tuple):
            raise failure("denied")
        return tuple(c for c in self.options if self._option_values[c.ref.target_id] in values)

    async def revalidate(
        self, session: BrowserSessionRef, command: ActionCommand, ctx: ExecutionContext
    ) -> None:
        self.admitted(ctx)
        await authorize_current(session, ctx, command)
        if not self.site.permits(self.skill, command):
            raise failure("denied")
        if command.target and not any(
            c.ref == command.target and c.visible and c.enabled and c.ref.target_id in self.matches
            for c in self.view.candidates
        ):
            raise failure("stale")
        if command.option:
            if command.target is None or command.option not in tuple(
                c.ref
                for c in await self.option_candidates(session, command.target, command.step, ctx)
            ):
                raise failure("stale")

    async def execute(
        self, session: BrowserSessionRef, command: ActionCommand, ctx: ExecutionContext
    ) -> DispatchReceipt:
        self.before_execute()
        self.admitted(ctx)
        async with ctx.dispatch_barrier(session, command, ctx.expected_binding) as permit:
            await self.revalidate(session, command, ctx)
            if command.step.value_ref:
                await self._consume(command.step.value_ref, "fill_value", ctx, command.step.step_id)
            if command.step.url_ref:
                url = await self._consume(
                    command.step.url_ref, "navigation_url", ctx, command.step.step_id
                )
                if not isinstance(url, str) or not navigation_allowed(url, ctx.navigation_origins):
                    raise failure("denied")
            self.entered_dispatch.set()
            if self.dispatch_wait is not None:
                await self.dispatch_wait.wait()
            await self.revalidate(session, command, ctx)
            # The final live identity check is synchronous after all private
            # resolver/auth awaits, including the parent of select_option.
            if command.target and not any(
                c.ref == command.target and c.visible and c.enabled for c in self.view.candidates
            ):
                raise failure("stale")
            if command.option and (
                command.target is None
                or command.target.target_id != self.option_parent
                or command.option
                not in tuple(c.ref for c in self.options if c.visible and c.enabled)
                or self._option_values[command.option.target_id] not in self._inputs["options"]
            ):
                raise failure("stale")
            # No await between the final barrier check and the actual effect.
            permit.begin_send()
            self.sends.append(command)
            self.after_send()
            if any(not navigation_allowed(url, ctx.navigation_origins) for url in self.redirects):
                return DispatchReceipt(
                    skill_digest=command.skill_digest,
                    step_id=command.step.step_id,
                    state="possibly_sent",
                    failure=failure("effect_unknown", dispatched=True).failure,
                )
            return DispatchReceipt(
                skill_digest=command.skill_digest,
                step_id="wrong_step" if self.bad_receipt else command.step.step_id,
                state=self.receipt_state,
                evidence_digest=hashlib.sha256(
                    f"{command.skill_digest}:{command.step.step_id}:{permit.execution_id}"
                    f":{len(self.sends)}".encode()
                ).hexdigest()
                if self.receipt_state == "acknowledged"
                else None,
                failure=failure("effect_unknown", dispatched=True).failure
                if self.receipt_state == "possibly_sent"
                else None,
            )

    async def read(
        self, session: BrowserSessionRef, spec: ReadSpec, ctx: ExecutionContext
    ) -> ReadEvidence:
        self.admitted(ctx)
        await authorize_current(session, ctx, spec)
        if spec != self.confirmed:
            raise failure("denied")
        self.reads += 1
        key = await self._consume(
            spec.business_key.value_ref, "business_key", ctx, self.skill.verifier_id
        )
        records = [r for r in self.records if r.key == key]
        comparisons = []
        for rule in self.plan.read_rule.fields:
            expected = await self._consume(
                rule.value_ref, "expected_field", ctx, self.skill.verifier_id
            )
            status: Literal["matched", "mismatch", "missing"] = "missing"
            if records and rule.field_id in records[0].fields:
                status = "matched" if records[0].fields[rule.field_id] == expected else "mismatch"
            comparisons.append(ReadFieldEvidence(field_id=rule.field_id, status=status))
        digest = hashlib.sha256(
            json.dumps(
                {
                    "key_digest": spec.business_key.key_digest,
                    "binding_revision": self.binding.binding_revision,
                    "authorization_revision": self.binding.authorization_revision,
                    "lease_epoch": self.binding.lease_epoch,
                    "count": len(records),
                    "owner_match": bool(records) and all(r.owner == self.binding for r in records),
                    "coverage": self.coverage.state,
                    "fields": [(item.field_id, item.status) for item in comparisons],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        evidence = ReadEvidence(
            binding=self.binding,
            business_key=spec.business_key,
            match_count=len(records),
            key_match=bool(records),
            owner_match=bool(records) and all(r.owner == self.binding for r in records),
            coverage=self.coverage,
            fields=tuple(comparisons),
            evidence_digest=digest,
        )
        self.after_read()
        return evidence

    def decision_context(
        self, context: ExecutionContext, request: DecisionRequest
    ) -> DecisionCallContext:
        def live() -> tuple[TargetRef, ...]:
            current = {c.ref.target_id: c.ref for c in (*self.view.candidates, *self.options)}
            return tuple(
                current[c.ref.target_id] for c in request.candidates if c.ref.target_id in current
            )

        return DecisionCallContext(
            deadline_monotonic=context.deadline_monotonic,
            manifest=self.plan.decision_manifest,
            source=self.plan.source,
            budget=self.plan.decision_budget,
            current_targets=live,
            cancellation=context.cancellation,
        )
