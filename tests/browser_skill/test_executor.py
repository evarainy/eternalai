import asyncio
import secrets
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.browser_skill import executor as executor_module
from app.browser_skill.executor import BrowserExecutor
from app.browser_skill.models import ActionCommand, BrowserOperationError, TargetRef
from app.browser_skill.site_rules import FrozenSiteAdapter
from tests.browser_skill.fakes import FakeWorld


@pytest.mark.parametrize("stage", [
    "confirmation", "authorization", "target_observation", "target_candidates",
    "decision_request", "target_revalidation", "dispatch", "verification",
])
def test_failure_diagnostic_identifies_the_await_without_changing_outcome(
    stage: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        world = FakeWorld("read")
        world.context = replace(world.context, deadline_monotonic=time.monotonic() + 0.05)

        async def blocked(*args: object, **kwargs: object) -> None:
            await asyncio.Event().wait()

        if stage == "confirmation":
            monkeypatch.setattr(executor_module, "confirmed_spec", blocked)
        elif stage == "authorization":
            monkeypatch.setattr(executor_module, "authorize_current", blocked)
        elif stage == "decision_request":
            world.matches = tuple(candidate.ref.target_id for candidate in world.view.candidates)
            monkeypatch.setattr(world.decision, "decide", blocked)
        elif stage == "verification":
            world.verifier = SimpleNamespace(verify=blocked)
        else:
            method = {"target_observation": "observe", "target_candidates": "target_candidates",
                      "target_revalidation": "revalidate", "dispatch": "execute"}[stage]
            monkeypatch.setattr(world, method, blocked)
        executor = world.executor()
        result = await executor.run(world.session, world.context)
        assert result.sequence == "stopped" and result.verification is None
        assert result.failure.code == ("effect_unknown" if stage == "dispatch" else "timeout")
        diagnostic = executor._last_failure_diagnostic
        assert diagnostic is not None and diagnostic[0] == stage
        assert type(diagnostic[1]) is int and 0 <= diagnostic[1] <= 5000
        assert diagnostic[2] == result.failure.code
        assert len(world.sends) == (1 if stage == "verification" else 0)
        assert world.reads == 0
    asyncio.run(scenario())


def run(world: FakeWorld):
    return asyncio.run(world.executor().run(world.session, world.context))


@pytest.mark.parametrize("operation", ["click", "fill", "read", "navigate", "select_option"])
def test_frozen_steps_dispatch_once_and_independent_read_verifies(operation: str) -> None:
    world = FakeWorld(operation)
    outcome = run(world)
    assert outcome.sequence == "completed" and outcome.verification.status == "verified"
    assert outcome.failure is None and outcome.durability == "memory_only"
    assert len(world.sends) == 1 and world.reads == 1
    assert outcome.model_calls == 0 and world.decision.calls == []


def test_ambiguous_locator_uses_only_registered_criteria_and_existing_ids() -> None:
    world = FakeWorld()
    world.matches = tuple(c.ref.target_id for c in world.view.candidates)
    result = run(world)
    assert result.model_calls == 1 and result.verification.status == "verified"
    request = world.decision.calls[0]
    assert request.criteria == world.plan.steps[0].target_criteria
    assert world.sends[0].target in tuple(c.ref for c in request.candidates)
    assert set(request.model_dump()) == {
        "task_kind",
        "request_id",
        "scope",
        "criteria",
        "candidates",
    }


@pytest.mark.parametrize("status", ["abstained", "ambiguous", "unsupported"])
def test_model_nonselection_stops_without_dispatch_and_preserves_status(status: str) -> None:
    world = FakeWorld()
    world.matches = tuple(c.ref.target_id for c in world.view.candidates)
    world.decision.status = status
    result = run(world)
    assert result.sequence == "stopped" and result.decisions[0].status == status
    assert len(world.sends) == 0 and world.reads == 0


def test_model_error_retains_exact_code_without_retry() -> None:
    world = FakeWorld()
    world.matches = tuple(c.ref.target_id for c in world.view.candidates)
    world.decision.error = "overloaded"
    result = run(world)
    assert result.decisions[0].error == "overloaded" and result.model_calls == 1
    assert len(world.sends) == 0 and result.verification is None


def test_model_invented_target_is_rejected() -> None:
    world = FakeWorld()
    world.matches = tuple(c.ref.target_id for c in world.view.candidates)
    world.decision.override = TargetRef(
        target_id="invented", candidate_epoch=1, scope=world.view.scope
    )
    result = run(world)
    assert result.failure.code == "invalid_response" and len(world.sends) == 0


def test_live_generation_callback_catches_mutation_during_decision() -> None:
    world = FakeWorld()
    world.matches = tuple(c.ref.target_id for c in world.view.candidates)

    def mutate() -> None:
        first = world.view.candidates[0]
        changed = first.model_copy(
            update={"ref": first.ref.model_copy(update={"candidate_epoch": 2})}
        )
        world.view = world.view.model_copy(
            update={"candidates": (changed, world.view.candidates[1])}
        )

    world.decision.hook = mutate
    result = run(world)
    assert result.failure.code == "stale" and len(world.sends) == 0


@pytest.mark.parametrize("mutation", ["auth", "lease", "node", "frame", "disabled"])
def test_execute_rechecks_after_executor_preflight(mutation: str) -> None:
    world = FakeWorld()

    def mutate() -> None:
        if mutation == "auth":
            world.allowed = False
        elif mutation == "lease":
            world.binding = world.binding.model_copy(update={"lease_epoch": 4})
        else:
            candidate = world.view.candidates[0]
            if mutation == "disabled":
                changed = candidate.model_copy(update={"enabled": False})
            else:
                ref = candidate.ref
                if mutation == "node":
                    ref = ref.model_copy(update={"candidate_epoch": 2})
                else:
                    hops = ref.scope.frame_path
                    stamp = ref.scope.model_copy(
                        update={
                            "frame_path": (hops[0].model_copy(update={"frame_epoch": 3}), *hops[1:])
                        }
                    )
                    ref = ref.model_copy(update={"scope": stamp})
                changed = candidate.model_copy(update={"ref": ref})
            world.view = world.view.model_copy(
                update={"candidates": (changed, world.view.candidates[1])}
            )

    world.before_execute = mutate
    result = run(world)
    assert result.failure.code in {"denied", "stale"} and len(world.sends) == 0


def test_direct_execute_cannot_bypass_authorization() -> None:
    world = FakeWorld()
    world.allowed = False
    command = ActionCommand(
        skill_digest=world.skill.digest,
        step=world.skill.steps[0],
        binding=world.binding,
        target=world.view.candidates[0].ref,
    )
    with pytest.raises(BrowserOperationError) as error:
        asyncio.run(world.execute(world.session, command, world.context))
    assert error.value.failure.code == "denied" and len(world.sends) == 0


@pytest.mark.parametrize("actual_effect", ["may_write", "unknown"])
def test_actual_effect_facts_deny_even_registered_synthetic_readonly_label(
    actual_effect: str,
) -> None:
    world = FakeWorld()
    rule = world.plan.steps[0]
    changed = rule.model_copy(
        update={"effect": rule.effect.model_copy(update={"actual_effect": actual_effect})}
    )
    world.plan = world.plan.model_copy(update={"steps": (changed,)})
    world.site = FrozenSiteAdapter((world.plan,))
    result = run(world)
    assert result.failure.code == "unsupported" and len(world.sends) == 0


def test_wrong_receipt_is_unknown_effect_and_never_replayed() -> None:
    world = FakeWorld(two_steps=True)
    world.bad_receipt = True
    result = run(world)
    assert result.failure.code == "effect_unknown" and result.cleanup_required
    assert result.failure.dispatch_state == "possibly_sent" and len(world.sends) == 1
    assert world.reads == 0


def test_possibly_sent_preserves_failure_and_stops_following_steps() -> None:
    world = FakeWorld(two_steps=True)
    world.receipt_state = "possibly_sent"
    result = run(world)
    assert result.receipts[0].state == "possibly_sent" and result.failure.code == "effect_unknown"
    assert result.cleanup_required and len(world.sends) == 1 and world.reads == 0


def test_click_ack_is_not_success_when_independent_record_mismatches() -> None:
    world = FakeWorld()
    world.records[0].fields["state"] = secrets.token_urlsafe(24)
    result = run(world)
    assert result.receipts[0].state == "acknowledged" and result.verification.status == "mismatch"
    assert len(world.sends) == 1 and world.reads == 1


def test_private_value_never_enters_decision_receipts_or_result_serialization() -> None:
    world = FakeWorld("fill")
    world.matches = tuple(c.ref.target_id for c in world.view.candidates)
    result = run(world)
    wire = result.model_dump_json() + world.decision.calls[0].model_dump_json()
    # Keep private operands out of pytest's comparison rendering.
    leaked = any(isinstance(value, str) and value in wire for value in world._inputs.values())
    assert leaked is False
    assert result.verification.status == "verified"


def test_cancellation_before_send_remains_pending_without_claiming_barrier_ack() -> None:
    async def scenario():
        world = FakeWorld(two_steps=True)
        world.dispatch_wait = asyncio.Event()
        task = asyncio.create_task(world.executor().run(world.session, world.context))
        await asyncio.wait_for(world.entered_dispatch.wait(), timeout=1)
        world.context.cancellation.set()
        result = await task
        assert result.cancellation == "pending" and result.sequence == "stopped"
        assert result.failure.code == "effect_unknown" and len(world.sends) == 0
        assert world.reads == 0

    asyncio.run(scenario())


def test_cancellation_after_ack_stops_next_action_and_retains_ack_receipt() -> None:
    world = FakeWorld(two_steps=True)
    world.after_send = world.context.cancellation.set
    result = run(world)
    assert result.cancellation == "pending" and result.failure.code == "cancelled"
    assert result.receipts[0].state == "acknowledged" and len(world.sends) == 1


@pytest.mark.parametrize(
    "field", ["source", "manifest", "budget", "deadline", "cancellation", "live"]
)
def test_decision_context_must_match_trusted_registered_execution(field: str) -> None:
    world = FakeWorld()
    world.matches = tuple(c.ref.target_id for c in world.view.candidates)

    def contexts(ctx, request):
        original = world.decision_context(ctx, request)
        if field == "source":
            return replace(
                original, source=original.source.model_copy(update={"source_id": "other"})
            )
        if field == "manifest":
            return replace(
                original,
                manifest=original.manifest.model_copy(update={"deployment_model": "other"}),
            )
        if field == "budget":
            return replace(
                original, budget=original.budget.model_copy(update={"max_candidates": 1})
            )
        if field == "deadline":
            return replace(original, deadline_monotonic=original.deadline_monotonic + 1)
        if field == "cancellation":
            return replace(original, cancellation=asyncio.Event())
        return replace(original, current_targets=None)

    executor = BrowserExecutor(
        world, world.decision, world.verifier, world.site, world.read_spec, contexts
    )
    result = asyncio.run(executor.run(world.session, world.context))
    assert result.failure.code == "denied" and result.model_calls == 0
    assert len(world.sends) == 0


def test_select_option_filters_private_value_set_before_decision() -> None:
    world = FakeWorld("select_option")
    result = run(world)
    assert result.verification.status == "verified" and result.model_calls == 0
    assert world.sends[0].option == world.options[0].ref
    assert world.sends[0].option != world.options[1].ref


def test_option_membership_change_at_dispatch_is_rejected() -> None:
    world = FakeWorld("select_option")
    world.before_execute = lambda: world._option_values.update(
        {"option_a": secrets.token_urlsafe(24)}
    )
    result = run(world)
    assert result.failure.code == "stale" and len(world.sends) == 0


def test_navigation_cross_origin_redirect_stops_without_sending_redirect() -> None:
    world = FakeWorld("navigate", two_steps=True)
    world.redirects = ("https://unregistered.invalid/path",)
    result = run(world)
    assert result.failure.code == "effect_unknown" and len(world.sends) == 1
    assert result.cleanup_required and world.reads == 0


@pytest.mark.parametrize("boundary", ["region", "source", "origin", "partial"])
def test_registered_observation_and_source_cannot_be_substituted(boundary: str) -> None:
    world = FakeWorld()
    if boundary == "region":
        world.view = world.view.model_copy(
            update={"scope": world.view.scope.model_copy(update={"region_id": "unregistered"})}
        )
    elif boundary == "source":
        world.context = replace(
            world.context,
            source=world.context.source.model_copy(update={"fixture_digest": "b" * 64}),
        )
    elif boundary == "origin":
        world.context = replace(world.context, navigation_origins=("https://other.invalid",))
    else:
        from app.browser_skill.models import Coverage

        world.view = world.view.model_copy(
            update={"coverage": Coverage(state="partial", reason="virtualized")}
        )
    result = run(world)
    assert result.sequence == "stopped" and result.failure is not None
    assert len(world.sends) == 0 and result.model_calls == 0


def test_wrong_option_parent_cannot_reuse_an_allowed_option() -> None:
    world = FakeWorld("select_option")
    world.before_execute = lambda: setattr(world, "option_parent", "different_parent")
    result = run(world)
    assert result.failure.code == "denied" and len(world.sends) == 0


def test_target_subset_cannot_invent_a_locator_match() -> None:
    world = FakeWorld()

    async def invented(session, step, projection, ctx):
        candidate = projection.candidates[0]
        return (
            candidate.model_copy(
                update={"ref": candidate.ref.model_copy(update={"target_id": "invented"})}
            ),
        )

    world.target_candidates = invented
    result = run(world)
    assert result.failure.code == "invalid_response" and len(world.sends) == 0


def test_timeout_before_dispatch_retains_timeout_code() -> None:
    world = FakeWorld()
    world.context = replace(world.context, deadline_monotonic=time.monotonic() - 1)
    result = run(world)
    assert result.failure.code == "timeout" and len(world.sends) == 0
    assert result.verification is None


def test_cancellation_resistant_read_stays_owned_pending_and_requires_cleanup() -> None:
    async def scenario():
        world = FakeWorld()
        entered = asyncio.Event()
        release = asyncio.Event()
        settled = asyncio.Event()

        async def resistant(session, request, policy):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            finally:
                settled.set()
            return world.view

        world.observe = resistant
        executor = world.executor()
        running = asyncio.create_task(executor.run(world.session, world.context))
        await asyncio.wait_for(entered.wait(), 1)
        world.context.cancellation.set()
        result = await asyncio.wait_for(running, 1)
        assert result.cancellation == "pending" and result.cleanup_required
        assert result.failure.code == "cancelled" and len(world.sends) == 0
        release.set()
        await asyncio.wait_for(settled.wait(), 1)
        await asyncio.sleep(0)
        assert len(world.sends) == 0

    asyncio.run(scenario())


def test_actual_node_change_while_dispatch_waits_is_rechecked_before_send() -> None:
    async def scenario():
        world = FakeWorld()
        world.dispatch_wait = asyncio.Event()
        task = asyncio.create_task(world.executor().run(world.session, world.context))
        await asyncio.wait_for(world.entered_dispatch.wait(), 1)
        candidate = world.view.candidates[0]
        changed = candidate.model_copy(update={"enabled": False})
        world.view = world.view.model_copy(
            update={"candidates": (changed, world.view.candidates[1])}
        )
        world.dispatch_wait.set()
        result = await task
        assert result.failure.code == "stale" and len(world.sends) == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["unselected_epoch", "new_alternative", "region_epoch"])
def test_async_refresh_rejects_other_candidate_changes_when_sync_cache_is_stale(kind: str) -> None:
    world = FakeWorld()
    world.matches = tuple(c.ref.target_id for c in world.view.candidates)
    original_context = world.decision_context

    def cached_context(ctx, request):
        # Model the legitimate interval before an asynchronous observer has
        # refreshed the registry. A synchronous callback cannot query the DOM.
        cached = tuple(c.ref for c in request.candidates)
        return replace(original_context(ctx, request), current_targets=lambda: cached)

    def change_unselected() -> None:
        first, second = world.view.candidates
        if kind == "unselected_epoch":
            second = second.model_copy(
                update={"ref": second.ref.model_copy(update={"candidate_epoch": 2})}
            )
            world.view = world.view.model_copy(update={"candidates": (first, second)})
        elif kind == "new_alternative":
            third = second.model_copy(
                update={"ref": second.ref.model_copy(update={"target_id": "third"})}
            )
            world.view = world.view.model_copy(update={"candidates": (first, second, third)})
            world.matches = (*world.matches, "third")
        else:
            world.view = world.view.model_copy(
                update={"scope": world.view.scope.model_copy(update={"page_epoch": 2})}
            )

    world.decision_context = cached_context
    world.decision.hook = change_unselected
    outcome = run(world)
    assert outcome.failure.code == "stale" and len(world.sends) == 0
    assert outcome.model_calls == 1 and world.reads == 0


def test_option_decision_refreshes_parent_and_all_permitted_options() -> None:
    world = FakeWorld("select_option")
    world._inputs["options"] = tuple(world._option_values.values())
    original_context = world.decision_context

    def cached_context(ctx, request):
        cached = tuple(c.ref for c in request.candidates)
        return replace(original_context(ctx, request), current_targets=lambda: cached)

    def change_alternative() -> None:
        first, second = world.options
        second = second.model_copy(
            update={"ref": second.ref.model_copy(update={"candidate_epoch": 2})}
        )
        world.options = (first, second)

    world.decision_context = cached_context
    world.decision.hook = change_alternative
    result = run(world)
    assert result.failure.code == "stale" and len(world.sends) == 0
    assert world.decision.calls[0].criteria == world.plan.steps[0].option_criteria


def test_multiple_permitted_options_are_grounded_and_refresh_before_selection() -> None:
    world = FakeWorld("select_option")
    world._inputs["options"] = tuple(world._option_values.values())
    result = run(world)
    assert result.verification.status == "verified" and result.model_calls == 1
    assert world.sends[0].option in tuple(c.ref for c in world.decision.calls[0].candidates)


def test_ack_receipt_evidence_is_bound_to_each_fixed_step() -> None:
    world = FakeWorld(two_steps=True)
    result = run(world)
    assert result.verification.status == "verified" and len(result.receipts) == 2
    assert result.receipts[0].step_id != result.receipts[1].step_id
    assert result.receipts[0].evidence_digest != result.receipts[1].evidence_digest
