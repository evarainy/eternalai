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

BASE_ATTRIBUTES = {
    "browser_read_outcome": "failed",
    "browser_completion_scope": "adapter_execution",
    "browser_timing_clock": "monotonic_relative_ms",
    "browser_stage_parent": "worker_adapter_execution",
    "browser_stage_offsets_origin": "adapter_execution",
    "read_stage_offsets_origin": "read_bridge_executor",
}


def test_diagnostic_uses_only_fixed_attributes_and_original_run_reference() -> None:
    writer = AsyncMock()
    execution = VerifiedBrowserReadExecution(
        Mock(), Mock(), Mock(), Mock(), result_digest_key=b"d" * 32,
        record_diagnostic=writer,
    )
    run = Mock()
    asyncio.run(execution._record_failure_diagnostic(run, ("target_observation", 51, "timeout")))
    assert writer.await_args_list == [call(run, {
        **BASE_ATTRIBUTES,
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


def test_success_writes_one_bounded_adapter_summary() -> None:
    writer = AsyncMock()
    execution = VerifiedBrowserReadExecution(
        Mock(), Mock(), Mock(), Mock(), result_digest_key=b"d" * 32,
        record_diagnostic=writer,
    )
    run = Mock()
    asyncio.run(execution._record_failure_diagnostic(run, None, verified=True))
    assert writer.await_args_list == [call(run, {
        **BASE_ATTRIBUTES, "browser_read_outcome": "verified",
    })]


@pytest.mark.parametrize("diagnostic,projected", [
    ({"http_status": 504, "http_headers_received": True, "http_exception": "none",
      "private": "must_not_leave"},
     {"decision_http_status": 504, "decision_http_headers_received": True,
      "decision_http_exception": "none"}),
    ({"http_status": "unknown", "http_headers_received": False, "http_exception": "timeout"},
     {"decision_http_status": "unknown", "decision_http_headers_received": False,
      "decision_http_exception": "timeout"}),
    ({"http_status": True, "http_headers_received": 1, "http_exception": "private_text"}, {}),
    ({"http_exception_kind": "connect_timeout", "http_timeout_phase": "connect"},
     {"decision_http_exception_kind": "connect_timeout", "decision_http_timeout_phase": "connect"}),
    ({"http_exception_kind": "http_error", "http_timeout_phase": "unknown"},
     {"decision_http_exception_kind": "http_error", "decision_http_timeout_phase": "unknown"}),
    ({"http_exception_kind": "read_error", "http_timeout_phase": "none"},
     {"decision_http_exception_kind": "read_error", "decision_http_timeout_phase": "none"}),
    ({"http_exception_kind": "unclassified arbitrary detail",
      "http_timeout_phase": "arbitrary phase"}, {}),
    ({"http_exception_kind": True, "http_timeout_phase": 1}, {}),
    ({"http_exception_kind": ["connect_error"], "http_timeout_phase": {"phase": "connect"}}, {}),
])
def test_http_summary_projects_only_closed_transport_fields(diagnostic, projected) -> None:
    writer = AsyncMock()
    execution = VerifiedBrowserReadExecution(
        Mock(), Mock(), Mock(), Mock(), result_digest_key=b"d" * 32, record_diagnostic=writer,
    )
    run = Mock()
    asyncio.run(execution._record_failure_diagnostic(run, None, http_diagnostic=diagnostic))
    assert writer.await_args_list == [call(run, {**BASE_ATTRIBUTES, **projected})]


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
            *BASE_ATTRIBUTES,
            "browser_read_stage", "stage_elapsed_ms", "browser_failure_code",
            "observe_wait_stage", "observe_wait_elapsed_ms",
            "adapter_authority_calls", "observer_observe_calls", "observer_snapshot_calls",
        }
        if boundary == "authority_after":
            expected_keys |= {
                "observer_identity_batch_attempts",
                "observer_identity_batch_successes",
            }
            assert payload["observer_identity_batch_attempts"] == 1
            assert payload["observer_identity_batch_successes"] == 1
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
