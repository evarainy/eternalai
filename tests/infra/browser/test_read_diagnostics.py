"""Bounded diagnostic storage with no browser, database or private input."""

import asyncio
import secrets
from unittest.mock import AsyncMock, Mock, call

import pytest

from app.infra.browser.read_execution import VerifiedBrowserReadExecution


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
