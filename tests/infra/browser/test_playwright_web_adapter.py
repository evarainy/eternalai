"""Generated private inputs and mutable DOM-shaped facts, no verified-result oracle."""

from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser_skill.executor import BrowserExecutor
from app.browser_skill.models import (
    ActionCommand,
    BrowserOperationError,
    DispatchPermit,
    ObservationRequest,
)
from app.browser_skill.verifier import IndependentVerifier, failure
from app.infra.browser.playwright_dom_rules import DOMRead, DOMStep, DOMValue, RegisteredDOMRules
from app.infra.browser.playwright_web_adapter import PlaywrightWebAdapter, RegisteredExecution
from tests.browser_skill.fakes import FakeWorld


@dataclass(repr=False)
class Node:
    selector: str = "#control"
    connected: bool = True
    enabled: bool = True
    editable: bool = True
    visible: bool = True
    tag: str = "BUTTON"
    values: dict[str, str] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)
    parent: Node | None = None
    label: str = "Allowed"
    value: str = field(default="", repr=False)


class Handle:
    def __init__(self, node: Node, world: World) -> None:
        self.node, self.world, self.disposed = node, world, False

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        if self.disposed:
            raise RuntimeError("disposed")
        if "old === fresh" in script:
            return self.node is arg.node and self.node.connected
        if "querySelectorAll(config.selector)" in script:
            value = self.node.values.get(arg["selector"])
            if value is not None and len(value.encode()) > arg["limit"]:
                raise ValueError("bounded")
            return value
        if "querySelectorAll(selector).length" in script:
            return len([n for n in self.node.children if n.selector == arg])
        if "el.matches(selector)" in script:
            return self.node.selector == arg
        if "tagName === 'SELECT'" in script:
            return self.node.tag == "SELECT"
        if "return {value:" in script:
            if self.node.parent is not arg.node or not self.node.connected:
                raise ValueError("member")
            return {
                "value": self.node.value,
                "label": self.node.label,
                "enabled": self.node.enabled,
            }
        if "closest('select') === parent" in script:
            return self.node.connected and self.node.parent is arg.node
        if "return el.value;" in script:
            if not self.node.connected or self.node.parent is not arg.node or not self.node.enabled:
                raise ValueError("option_stale")
            return self.node.value
        if "region.contains(row)" in script:
            return arg.node in self.node.children
        if "isConnected" in script:
            return self.node.connected
        raise AssertionError("unexpected_fixed_dom_primitive")

    async def evaluate_handle(self, script: str, arg: Any = None) -> Handle:
        if "closest(selector)" in script:
            assert self.node.parent is not None
            return Handle(self.node.parent, self.world)
        assert script == "el => el"
        return Handle(self.node, self.world)

    async def query_selector_all(self, selector: str) -> list[Handle]:
        return [Handle(n, self.world) for n in self.node.children if n.selector == selector]

    async def dispose(self) -> None:
        self.disposed = True

    async def is_editable(self) -> bool:
        return self.node.editable

    async def is_visible(self) -> bool:
        return self.node.visible

    async def is_enabled(self) -> bool:
        return self.node.enabled

    async def click(self, **kwargs: Any) -> None:
        await self.world.sent("click")

    async def fill(self, value: str, **kwargs: Any) -> None:
        self.node.value = value
        await self.world.sent("fill")

    async def select_option(self, *, element: Handle, **kwargs: Any) -> list[str]:
        assert element.node.parent is self.node
        self.node.value = element.node.value
        await self.world.sent("select_option")
        return [self.node.value]


class Context:
    def __init__(self) -> None:
        self.events: dict[str, Any] = {}
        self.routes: list[Any] = []

    def on(self, name: str, callback: Any) -> None:
        self.events[name] = callback

    async def route(self, pattern: str, callback: Any) -> None:
        assert pattern == "**/*"
        self.routes.append(callback)


class Observer:
    def __init__(self, world: World) -> None:
        self.world = world
        self.calls = 0
        self.borrowed: list[Handle] = []

    def borrow(self, node: Node) -> Handle:
        for previous in self.borrowed:
            previous.disposed = True
        result = Handle(node, self.world)
        self.borrowed = [result]
        return result

    async def resolve_region(self, session: Any, request: Any, policy: Any) -> Any:
        self.calls += 1
        w = self.world
        if request.expected_scope is not None and request.expected_scope != w.view.scope:
            raise failure("stale")
        return SimpleNamespace(
            page=w.page, frame=w.frame, element=self.borrow(w.region), projection=w.view
        )

    async def resolve_exact(self, session: Any, ref: Any, policy: Any) -> Any:
        w = self.world
        if ref not in tuple(c.ref for c in w.view.candidates):
            raise failure("stale")
        node = w.controls[ref.target_id]
        if not (node.connected and node.visible and node.enabled):
            raise failure("stale")
        return SimpleNamespace(page=w.page, frame=w.frame, element=self.borrow(node), ref=ref)


class World(FakeWorld):
    def __init__(self, operation: str = "click") -> None:
        super().__init__(operation)
        self.dom_sends: list[str] = []
        self.send_error: BaseException | None = None
        self.business = True
        self.authority_calls = 0
        self.authority_hook = lambda: None
        self.page = SimpleNamespace(goto=self.goto)
        self.frame = SimpleNamespace(url=self.context.source.origin, parent_frame=None)
        self.live = SimpleNamespace(session=self.session, context=Context())
        self.view = self.view.model_copy(update={"candidates": self.view.candidates[:1]})
        self.controls = {
            c.ref.target_id: Node(tag="SELECT" if operation == "select_option" else "BUTTON")
            for c in self.view.candidates
        }
        self.control = next(iter(self.controls.values()))
        self.row = Node(
            selector=".record",
            values={
                ".key": str(self._inputs["key"]),
                ".state": str(self._inputs["expected"]),
                ".tenant": self.binding.owner.tenant_id,
                ".user": self.binding.owner.user_id,
            },
        )
        self.region = Node(selector="#region", children=[self.row])
        if operation == "select_option":
            self.control.children = [
                Node(
                    selector="option",
                    tag="OPTION",
                    parent=self.control,
                    value=self._inputs["options"][0],
                    visible=False,
                ),
                Node(
                    selector="option",
                    tag="OPTION",
                    parent=self.control,
                    value=secrets.token_urlsafe(18),
                    label="Other",
                    visible=False,
                ),
            ]
        self.observer = Observer(self)
        self.rules = RegisteredDOMRules(
            self.skill.digest,
            self.skill.site_digest,
            self.skill.verifier_digest,
            (DOMStep("open", "#control"),),
            DOMRead(
                ObservationRequest(region_id="inbox"),
                ".record",
                DOMValue(".key"),
                DOMValue(".tenant"),
                DOMValue(".user"),
                (("state", DOMValue(".state")),),
            ),
        )
        self.adapter = PlaywrightWebAdapter(
            registry=self,
            observer=self.observer,
            site=self.site,
            rules=(self.rules,),
            executions=(
                RegisteredExecution(self.session, self.context, self.confirmed.business_key),
            ),
        )

    async def assert_business_authority(self, session: Any, source: Any) -> Any:
        self.authority_calls += 1
        self.authority_hook()
        if not self.business or source != self.plan.source or session.binding != self.binding:
            raise failure("denied")
        return self.live

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.frame.url = url
        await self.sent("navigate")

    async def sent(self, operation: str) -> None:
        self.dom_sends.append(operation)
        if self.send_error is not None:
            raise self.send_error

    async def command(self) -> ActionCommand:
        step = self.skill.steps[0]
        target = None if step.operation == "navigate" else self.view.candidates[0].ref
        option = None
        if step.operation == "select_option":
            candidates = await self.adapter.option_candidates(
                self.session, target, step, self.context
            )
            option = candidates[0].ref
        return ActionCommand(
            skill_digest=self.skill.digest,
            step=step,
            binding=self.binding,
            target=target,
            option=option,
        )


@pytest.mark.parametrize("operation", ["click", "fill", "select_option", "navigate", "read"])
def test_actual_methods_dispatch_and_independent_dom_read(operation: str) -> None:
    async def run():
        w = World(operation)
        command = await w.command()
        receipt = await w.adapter.execute(w.session, command, w.context)
        assert receipt.state == "acknowledged"
        assert w.dom_sends == ([] if operation == "read" else [operation])
        verifier = IndependentVerifier(w.adapter, w.site, w.read_spec)
        result = await verifier.verify(w.session, w.confirmed, w.context)
        assert result.status == "verified" and w.observer.calls >= 2
        wire = receipt.model_dump_json() + result.model_dump_json()
        leaked = any(isinstance(value, str) and value in wire for value in w._inputs.values())
        assert leaked is False

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation", ["fresh", "denied", "lease", "clone", "detached", "disabled", "origin", "cancelled"]
)
def test_direct_execute_cannot_bypass_authority_or_actual_dom(mutation: str) -> None:
    async def run():
        w = World()
        command = await w.command()
        context = w.context
        if mutation == "fresh":
            w.business = False
        elif mutation == "denied":
            w.allowed = False
        elif mutation == "lease":
            w.binding = w.binding.model_copy(update={"lease_epoch": 9})
        elif mutation == "clone":
            context = replace(context)
        elif mutation == "detached":
            w.control.connected = False
        elif mutation == "disabled":
            w.control.enabled = False
        elif mutation == "origin":
            w.frame.url = "https://unregistered.invalid"
        else:
            context.cancellation.set()
        with pytest.raises(BrowserOperationError) as caught:
            await w.adapter.execute(w.session, command, context)
        assert caught.value.failure.code in {"denied", "stale", "cancelled"}
        assert w.dom_sends == []

    asyncio.run(run())


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError, TimeoutError])
def test_after_send_failure_is_unknown_and_not_replayed(error: type[BaseException]) -> None:
    async def run():
        w = World()
        w.send_error = error(secrets.token_urlsafe(20))
        receipt = await w.adapter.execute(w.session, await w.command(), w.context)
        assert receipt.state == "possibly_sent" and receipt.failure.code == "effect_unknown"
        assert receipt.failure.cleanup_required and w.dom_sends == ["click"]

    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["value", "node", "parent", "disabled", "extra"])
def test_native_options_are_sealed_and_generations_invalidate(mutation: str) -> None:
    async def run():
        w = World("select_option")
        command = await w.command()
        options = await w.adapter.option_candidates(
            w.session, command.target, command.step, w.context
        )
        assert len(options) == 1 and options[0].ref == command.option
        assert options[0].name == "Allowed" and w.control.children[0].visible is False
        option = w.control.children[0]
        if mutation == "value":
            option.value = secrets.token_urlsafe(20)
        elif mutation == "node":
            w.control.children[0] = replace(option)
        elif mutation == "parent":
            option.parent = Node()
        elif mutation == "disabled":
            option.enabled = False
        else:
            w.control.children.append(replace(option))
        with pytest.raises(BrowserOperationError):
            await w.adapter.execute(w.session, command, w.context)
        assert w.dom_sends == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation", ["field", "key", "owner", "duplicate", "missing", "oversize", "confirmation"]
)
def test_read_looks_up_actual_records_under_confirmed_key(mutation: str) -> None:
    async def run():
        w = World()
        if mutation == "field":
            w.row.values[".state"] = secrets.token_urlsafe(20)
        elif mutation == "key":
            w.row.values[".key"] = secrets.token_urlsafe(20)
        elif mutation == "owner":
            w.row.values[".user"] = "other"
        elif mutation == "duplicate":
            w.region.children.append(replace(w.row))
        elif mutation == "missing":
            w.row.values.pop(".state")
        elif mutation == "oversize":
            w.row.values[".state"] = "x" * 4097
        else:
            w.confirmed = w.confirmed.model_copy(
                update={
                    "business_key": w.confirmed.business_key.model_copy(
                        update={"confirmation_ref": "other"}
                    )
                }
            )
        if mutation in {"oversize", "confirmation"}:
            with pytest.raises(BrowserOperationError):
                await w.adapter.read(w.session, w.confirmed, w.context)
        else:
            result = await IndependentVerifier(w.adapter, w.site, w.read_spec).verify(
                w.session, w.confirmed, w.context
            )
            assert result.status in {"mismatch", "incomplete"}
        assert w.dom_sends == []

    asyncio.run(run())


def test_redirect_and_popup_monitor_block_subsequent_business_actions() -> None:
    async def run():
        w = World()
        await w.adapter.observe(w.session, ObservationRequest(region_id="inbox"), w.plan.policy)
        w.live.context.events["request"](SimpleNamespace(url="https://other.invalid/redirect"))
        with pytest.raises(BrowserOperationError) as caught:
            await w.adapter.execute(w.session, await w.command(), w.context)
        assert caught.value.failure.code == "cancelled" and w.dom_sends == []
        w = World()
        await w.adapter.observe(w.session, ObservationRequest(region_id="inbox"), w.plan.policy)
        w.live.context.events["page"](SimpleNamespace())
        assert w.context.cancellation.is_set()

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["click", "fill", "select_option", "navigate", "read"])
def test_domain_executor_composes_single_actual_adapter_and_verifier(operation: str) -> None:
    async def run():
        w = World(operation)
        verifier = IndependentVerifier(w.adapter, w.site, w.read_spec)
        executor = BrowserExecutor(
            w.adapter, w.decision, verifier, w.site, w.read_spec, w.decision_context
        )
        result = await executor.run(w.session, w.context)
        assert result.sequence == "completed" and result.verification.status == "verified"
        assert result.failure is None and len(result.receipts) == 1
        assert w.dom_sends == ([] if operation == "read" else [operation])

    asyncio.run(run())


def test_private_record_changes_during_subject_recheck_fail_stale() -> None:
    async def run():
        w = World()

        def change():
            if w.authority_calls == 2:
                w.row.values[".state"] = secrets.token_urlsafe(24)

        w.authority_hook = change
        with pytest.raises(BrowserOperationError) as caught:
            await w.adapter.read(w.session, w.confirmed, w.context)
        assert caught.value.failure.code == "stale" and w.dom_sends == []

    asyncio.run(run())


def test_effect_fact_and_step_claim_cannot_authorize_write() -> None:
    async def run():
        w = World()
        rule = w.plan.steps[0]
        from app.browser_skill.site_rules import FrozenSiteAdapter

        w.adapter._site = FrozenSiteAdapter(
            (
                w.plan.model_copy(
                    update={
                        "steps": (
                            rule.model_copy(
                                update={
                                    "effect": rule.effect.model_copy(
                                        update={"actual_effect": "may_write"}
                                    )
                                }
                            ),
                        )
                    }
                ),
            )
        )
        with pytest.raises(BrowserOperationError) as caught:
            await w.adapter.execute(w.session, await w.command(), w.context)
        assert caught.value.failure.code == "unsupported" and w.dom_sends == []

    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["cancel", "lease", "target", "permit"])
def test_final_barrier_mutations_fail_before_real_send(mutation: str) -> None:
    async def run():
        w = World()

        @asynccontextmanager
        async def barrier(session, command, binding):
            if mutation == "target":
                w.control.connected = False

            def begin():
                if mutation == "cancel":
                    w.context.cancellation.set()
                    raise failure("cancelled")
                if mutation == "lease":
                    w.binding = w.binding.model_copy(update={"lease_epoch": 99})
                    raise failure("stale")

            yield DispatchPermit(
                "other" if mutation == "permit" else w.context.execution_id, command, begin
            )

        w.context = replace(w.context, dispatch_barrier=barrier)
        w.adapter = PlaywrightWebAdapter(
            registry=w,
            observer=w.observer,
            site=w.site,
            rules=(w.rules,),
            executions=(RegisteredExecution(w.session, w.context, w.confirmed.business_key),),
        )
        with pytest.raises(BrowserOperationError) as caught:
            await w.adapter.execute(w.session, await w.command(), w.context)
        assert caught.value.failure.code in {"stale", "cancelled", "denied"}
        assert w.dom_sends == []

    asyncio.run(run())


def test_begin_send_immediately_precedes_real_action() -> None:
    async def run():
        w = World("fill")
        sequence = []

        @asynccontextmanager
        async def barrier(session, command, binding):
            yield DispatchPermit(w.context.execution_id, command, lambda: sequence.append("begin"))

        async def sent(operation):
            sequence.append(operation)

        w.sent = sent
        w.context = replace(w.context, dispatch_barrier=barrier)
        w.adapter = PlaywrightWebAdapter(
            registry=w,
            observer=w.observer,
            site=w.site,
            rules=(w.rules,),
            executions=(RegisteredExecution(w.session, w.context, w.confirmed.business_key),),
        )
        receipt = await w.adapter.execute(w.session, await w.command(), w.context)
        assert receipt.state == "acknowledged" and sequence == ["begin", "fill"]
        private_reached_renderer = w.control.value == w._inputs["input"]
        assert private_reached_renderer is True

    asyncio.run(run())


def test_registered_context_deadline_stops_before_subject_or_browser_calls() -> None:
    async def run():
        w = World()
        w.context = replace(w.context, deadline_monotonic=time.monotonic() - 1)
        w.adapter = PlaywrightWebAdapter(
            registry=w,
            observer=w.observer,
            site=w.site,
            rules=(w.rules,),
            executions=(RegisteredExecution(w.session, w.context, w.confirmed.business_key),),
        )
        with pytest.raises(BrowserOperationError) as caught:
            await w.adapter.observe(w.session, ObservationRequest(region_id="inbox"), w.plan.policy)
        assert caught.value.failure.code == "timeout" and w.authority_calls == 0

    asyncio.run(run())


def test_read_does_not_fetch_other_owners_private_fields() -> None:
    async def run():
        w = World()
        w.row.values[".user"] = "different"
        w.row.values[".state"] = "x" * 5000
        evidence = await w.adapter.read(w.session, w.confirmed, w.context)
        assert evidence.owner_match is False and evidence.fields[0].status == "missing"
        assert evidence.key_match and evidence.match_count == 1

    asyncio.run(run())


def test_private_errors_and_context_are_not_printable_or_serializable() -> None:
    async def run():
        w = World()
        private = secrets.token_urlsafe(24)

        async def broken(*args):
            raise RuntimeError(private)

        w.adapter._registry = SimpleNamespace(assert_business_authority=broken)
        with pytest.raises(BrowserOperationError) as caught:
            await w.adapter.read(w.session, w.confirmed, w.context)
        rendered = str(caught.value) + repr(w.adapter) + repr(w.rules)
        leaked = private in rendered or str(w._inputs["key"]) in rendered
        assert leaked is False and caught.value.failure.code == "invalid_response"
        import pickle

        with pytest.raises(TypeError, match="serialization_forbidden"):
            pickle.dumps(RegisteredExecution(w.session, w.context, w.confirmed.business_key))

    asyncio.run(run())


@pytest.mark.parametrize("scenario", ["matched", "different", "outside_region", "last_recheck"])
def test_business_key_locator_matches_private_actual_row_within_region(scenario: str) -> None:
    async def run():
        from app.browser_skill.models import LocatorHint, SealedParameter
        from app.browser_skill.site_rules import FrozenSiteAdapter
        from tests.browser_skill.fakes import make_plan

        w = World()
        step = w.skill.steps[0].model_copy(
            update={"locator": LocatorHint(kind="business_key", value="approved_key")}
        )
        w.skill = w.skill.model_copy(update={"steps": (step,)})
        w.plan = make_plan(w.skill)
        w.site = FrozenSiteAdapter((w.plan,))
        w.control.parent = w.row
        if scenario == "different":
            w.row.values[".key"] = secrets.token_urlsafe(24)
        elif scenario == "outside_region":
            w.region.children = []

        async def resolve(ref, purpose, binding, digest, step_id):
            assert purpose == "business_key" and step_id == step.step_id
            return SealedParameter(
                w._inputs["key"],
                ref=ref,
                purpose=purpose,
                binding=binding,
                skill_digest=digest,
                step_id=step_id,
            )

        w.context = replace(w.context, skill=w.skill, resolve_parameter=resolve)
        rules = replace(
            w.rules, steps=(DOMStep(step.step_id, "#control", ".record", DOMValue(".key")),)
        )
        w.adapter = PlaywrightWebAdapter(
            registry=w,
            observer=w.observer,
            site=w.site,
            rules=(rules,),
            executions=(RegisteredExecution(w.session, w.context, w.confirmed.business_key),),
        )
        if scenario == "outside_region":
            with pytest.raises(BrowserOperationError) as caught:
                await w.adapter.target_candidates(w.session, step, w.view, w.context)
            assert caught.value.failure.code == "denied"
        else:
            candidates = await w.adapter.target_candidates(w.session, step, w.view, w.context)
            assert len(candidates) == (0 if scenario == "different" else 1)
            leaked = str(w._inputs["key"]) in repr(candidates)
            assert leaked is False
            if scenario in {"matched", "last_recheck"}:
                w.authority_calls = 0
                command = await w.command()
                receipt = await w.adapter.execute(w.session, command, w.context)
                assert receipt.state == "acknowledged" and w.dom_sends == ["click"]
                if scenario == "last_recheck":
                    last_call = w.authority_calls
                    w.authority_calls = 0
                    w.dom_sends.clear()

                    def change():
                        if w.authority_calls == last_call:
                            w.row.values[".key"] = secrets.token_urlsafe(24)

                    w.authority_hook = change
                    with pytest.raises(BrowserOperationError) as caught:
                        await w.adapter.execute(w.session, command, w.context)
                    assert caught.value.failure.code == "stale" and w.dom_sends == []

    asyncio.run(run())


def test_option_value_mutation_at_last_authority_check_invalidates_selected_ref() -> None:
    async def run():
        baseline = World("select_option")
        command = await baseline.command()
        baseline.authority_calls = 0
        await baseline.adapter.execute(baseline.session, command, baseline.context)
        last_call = baseline.authority_calls
        w = World("select_option")
        command = await w.command()
        w.authority_calls = 0

        def mutate():
            if w.authority_calls == last_call:
                w.control.children[0].value = secrets.token_urlsafe(24)

        w.authority_hook = mutate
        with pytest.raises(BrowserOperationError) as caught:
            await w.adapter.execute(w.session, command, w.context)
        assert caught.value.failure.code == "stale" and w.dom_sends == []

    asyncio.run(run())
