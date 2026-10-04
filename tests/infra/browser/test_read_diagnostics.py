"""Bounded diagnostic storage with no browser, database or private input."""

import asyncio
import secrets
import time
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, Mock, call

import pytest

from app.browser_skill.models import BrowserOperationError, ObservationRequest
from app.browser_skill.site_rules import FrozenSiteAdapter
from app.infra.browser.playwright_web_adapter import PlaywrightWebAdapter, RegisteredExecution
from app.infra.browser.read_execution import VerifiedBrowserReadExecution
from tests.infra.browser.test_playwright_observer import fixture as observer_fixture
from tests.infra.browser.test_playwright_web_adapter import World


def test_diagnostic_uses_only_fixed_attributes_and_original_run_reference() -> None:
    writer = AsyncMock()
    execution = VerifiedBrowserReadExecution(
        Mock(), Mock(), Mock(), Mock(), result_digest_key=b"d" * 32,
        record_diagnostic=writer,
    )
    run = Mock()
    asyncio.run(execution._record_failure_diagnostic(run, ("target_observation", 51, "timeout")))
    assert writer.await_args_list == [call(run, {
        "browser_read_stage": "target_observation", "stage_elapsed_ms": 51,
        "browser_failure_code": "timeout",
    })]


def test_diagnostic_writer_failure_preserves_fixed_warning_without_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = secrets.token_urlsafe(32)
    writer = AsyncMock(side_effect=RuntimeError(marker))
    execution = VerifiedBrowserReadExecution(
        Mock(), Mock(), Mock(), Mock(), result_digest_key=b"d" * 32,
        record_diagnostic=writer,
    )
    asyncio.run(execution._record_failure_diagnostic(Mock(), ("confirmation", 10, "timeout")))
    assert writer.await_count == 1
    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage() == "browser_read_diagnostic_trace_unavailable"
    assert caplog.records[0].exc_info is None and marker not in caplog.text


def test_absent_failure_does_not_write_a_trace() -> None:
    writer = AsyncMock()
    execution = VerifiedBrowserReadExecution(
        Mock(), Mock(), Mock(), Mock(), result_digest_key=b"d" * 32,
        record_diagnostic=writer,
    )
    asyncio.run(execution._record_failure_diagnostic(Mock(), None))
    assert writer.await_args_list == []


@pytest.mark.parametrize(
    ("boundary", "outer", "inner"),
    [
        ("authority_before", "authority_before", None),
        ("region_lock", "region_resolution", "region_lock"),
        ("live_authority", "region_resolution", "live_authority"),
        ("dom_transport", "region_resolution", "dom_transport"),
        ("authority_after", "authority_after", None),
    ],
)
def test_observe_timeout_records_actual_wait_and_clears_stale_timing(
    monkeypatch: pytest.MonkeyPatch, boundary: str, outer: str, inner: str | None,
) -> None:
    async def check() -> None:
        world = World()
        observer, registry, _, root, _, _, policy = observer_fixture(nested=False)
        world.live.context.pages = registry.live.context.pages
        registry.live = world.live
        world.plan = world.plan.model_copy(update={"policy": policy})
        world.site = FrozenSiteAdapter((world.plan,))
        world.context = replace(world.context, deadline_monotonic=time.monotonic() + 0.2)
        adapter = PlaywrightWebAdapter(
            registry=world, observer=observer, site=world.site, rules=(world.rules,),
            executions=(RegisteredExecution(
                world.session, world.context, world.confirmed.business_key,
            ),),
        )
        blocked = asyncio.Event()

        async def wait_forever(*args: object, **kwargs: object) -> None:
            await blocked.wait()

        if boundary in {"authority_before", "authority_after"}:
            original = adapter._authority
            count = 0

            async def authority(*args: Any, **kwargs: Any) -> Any:
                nonlocal count
                count += 1
                if count == (1 if boundary == "authority_before" else 2):
                    await wait_forever()
                return await original(*args, **kwargs)

            monkeypatch.setattr(adapter, "_authority", authority)
        elif boundary == "region_lock":
            lock = asyncio.Lock()
            await lock.acquire()
            observer._locks[(world.session.session_ref, "inbox")] = lock
        elif boundary == "live_authority":
            monkeypatch.setattr(registry, "resolve_live", wait_forever)
        else:
            monkeypatch.setattr(root, "evaluate", wait_forever)

        with pytest.raises(BrowserOperationError) as caught:
            await adapter.observe(world.session, ObservationRequest(region_id="inbox"), policy)
        assert caught.value.failure.code == "timeout"
        assert world.dom_sends == []

        writer = AsyncMock()
        bridge = VerifiedBrowserReadExecution(
            Mock(), Mock(), Mock(), Mock(), result_digest_key=b"d" * 32,
            record_diagnostic=writer,
        )
        run = Mock()
        await bridge._record_failure_diagnostic(
            run, ("target_observation", 200, "timeout"),
            web=adapter, session_ref=world.session.session_ref,
        )
        assert writer.await_count == 1 and writer.await_args.args[0] is run
        payload = writer.await_args.args[1]
        expected_keys = {
            "browser_read_stage", "stage_elapsed_ms", "browser_failure_code",
            "observe_wait_stage", "observe_wait_elapsed_ms",
        }
        if inner is not None:
            expected_keys |= {"observe_inner_wait_stage", "observe_inner_wait_elapsed_ms"}
            assert payload["observe_inner_wait_stage"] == inner
            assert type(payload["observe_inner_wait_elapsed_ms"]) is int
            assert 0 <= payload["observe_inner_wait_elapsed_ms"] <= 300000
        assert set(payload) == expected_keys
        assert payload["observe_wait_stage"] == outer
        assert payload["browser_read_stage"] == "target_observation"
        assert payload["browser_failure_code"] == "timeout"
        assert type(payload["observe_wait_elapsed_ms"]) is int
        assert 0 <= payload["observe_wait_elapsed_ms"] <= 300000

        # A later call rejected before observe's body cannot reuse the old wait.
        with pytest.raises(BrowserOperationError) as expired:
            await adapter.observe(world.session, ObservationRequest(region_id="inbox"), policy)
        assert expired.value.failure.code == "timeout"
        assert adapter._observation_diagnostic(world.session.session_ref) == {}

    asyncio.run(check())
