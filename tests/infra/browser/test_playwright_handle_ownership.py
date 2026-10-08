"""Owned temporary handles, cancellation cleanup and borrowed option lifetime."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.browser_skill.models import BrowserOperationError
from app.infra.browser.playwright_observer import PlaywrightObserver, _dispose_many
from tests.infra.browser.test_playwright_observer import fixture
from tests.infra.browser.test_playwright_web_adapter import Handle, IdentityHandle, World


@pytest.mark.parametrize("cancel", [True, False])
def test_snapshot_failure_releases_every_allocated_property_and_preserves_error(cancel) -> None:
    _, _, _, _, _, _, policy = fixture(nested=False)
    first, second, extra = Mock(), Mock(), Mock()
    for handle in (first, second, extra):
        handle.dispose = AsyncMock()
    first.as_element.return_value = first
    second.as_element.side_effect = (
        asyncio.CancelledError() if cancel else ValueError("synthetic malformed node")
    )
    nodes = SimpleNamespace(
        get_properties=AsyncMock(return_value={"0": first, "1": second, "extra": extra}),
        dispose=AsyncMock(),
    )
    metadata = SimpleNamespace(json_value=AsyncMock(return_value={
        "coverage": {}, "candidates": [{}, {}], "overflow": False,
    }), dispose=AsyncMock())
    packet = SimpleNamespace(
        get_property=AsyncMock(side_effect=[metadata, nodes]), dispose=AsyncMock()
    )
    element = SimpleNamespace(evaluate_handle=AsyncMock(return_value=packet))
    site = SimpleNamespace(complete_selector=None, empty_selector=None,
                           pagination_selector=None, virtualized_selector=None)
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(PlaywrightObserver._snapshot(element, policy, site))
    else:
        with pytest.raises(BrowserOperationError) as caught:
            asyncio.run(PlaywrightObserver._snapshot(element, policy, site))
        assert caught.value.failure.code == "invalid_response"
    for handle in (first, second, extra, nodes, metadata, packet):
        handle.dispose.assert_awaited_once_with()


@pytest.mark.parametrize("suppress", [True, False])
def test_cleanup_visits_remaining_owned_handles_once_before_propagating_cancellation(
    suppress,
) -> None:
    first = SimpleNamespace(dispose=AsyncMock(side_effect=asyncio.CancelledError()))
    second = SimpleNamespace(dispose=AsyncMock())
    if suppress:
        asyncio.run(_dispose_many((first, first, second), suppress_cancel=True))
    else:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(_dispose_many((first, first, second)))
    first.dispose.assert_awaited_once_with()
    second.dispose.assert_awaited_once_with()


def test_failed_owned_identity_barrier_disposes_clones_without_disposing_option_holder(
    monkeypatch,
) -> None:
    async def scenario():
        w = World("select_option")
        command = await w.command()
        holder = w.adapter._options[(w.session.session_ref, command.step.step_id)]
        owned = []
        original = Handle.evaluate_handle

        async def capture(self, script, arg=None):
            handle = await original(self, script, arg)
            if script == "el => el" or isinstance(handle, IdentityHandle):
                owned.append(handle)
            return handle

        monkeypatch.setattr(Handle, "evaluate_handle", capture)
        w.after_round_hook = lambda: setattr(w.control, "enabled", False)
        with pytest.raises(BrowserOperationError) as caught:
            await w.adapter.revalidate(w.session, command, w.context)
        assert caught.value.failure.code == "stale"
        assert len(owned) == 2 and all(handle.disposed for handle in owned)
        current = w.adapter._options[(w.session.session_ref, command.step.step_id)]
        assert current.parent == holder.parent and len(current.options) == 1
        assert not current.options[0].element.disposed
        assert w.dom_sends == []

    asyncio.run(scenario())


def test_failed_new_option_set_never_replaces_previous_holder_and_releases_allocations(
    monkeypatch,
) -> None:
    async def scenario():
        w = World("select_option")
        command = await w.command()
        key = (w.session.session_ref, command.step.step_id)
        previous = w.adapter._options[key]
        allocations = []
        original_query, original_evaluate = Handle.query_selector_all, Handle.evaluate

        async def query(self, selector):
            handles = await original_query(self, selector)
            if selector == "option":
                allocations.extend(handles)
            return handles

        async def evaluate(self, script, arg=None):
            if self.node.tag == "OPTION" and "closest('select') !== parent" in script:
                raise asyncio.CancelledError()
            return await original_evaluate(self, script, arg)

        monkeypatch.setattr(Handle, "query_selector_all", query)
        monkeypatch.setattr(Handle, "evaluate", evaluate)
        with pytest.raises(asyncio.CancelledError):
            await w.adapter._options_current(
                w.session, command.target, command.step, w.context, w.plan
            )
        assert w.adapter._options[key] is previous
        assert len(allocations) == 2 and all(handle.disposed for handle in allocations)
        assert not previous.options[0].element.disposed
        assert w.dom_sends == []

    asyncio.run(scenario())
