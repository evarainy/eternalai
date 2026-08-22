"""Tests for JobQueuePort protocol and InMemoryJobQueue adapter."""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from app.infra.job_queue.in_memory import InMemoryJobQueue
from app.ports.job_queue import JobQueuePort

# -- Protocol contract tests --


class TestJobQueuePortProtocol:
    def test_is_protocol(self) -> None:
        assert hasattr(JobQueuePort, "__protocol_attrs__")

    def test_has_enqueue(self) -> None:
        assert hasattr(JobQueuePort, "enqueue")
        assert inspect.iscoroutinefunction(JobQueuePort.enqueue)

    def test_has_get_status(self) -> None:
        assert hasattr(JobQueuePort, "get_status")
        assert inspect.iscoroutinefunction(JobQueuePort.get_status)

    def test_has_get_result(self) -> None:
        assert hasattr(JobQueuePort, "get_result")
        assert inspect.iscoroutinefunction(JobQueuePort.get_result)

    def test_not_runtime_checkable(self) -> None:
        assert not getattr(JobQueuePort, "_is_runtime_protocol", False)


# -- Handler fixtures --


async def _async_square(payload: dict[str, Any]) -> int:
    return payload["n"] ** 2


def _sync_greet(payload: dict[str, Any]) -> str:
    return f"hello {payload['name']}"


async def _async_boom(payload: dict[str, Any]) -> None:
    raise RuntimeError("boom")


def _sync_boom(payload: dict[str, Any]) -> None:
    raise RuntimeError("boom")


# -- InMemoryJobQueue tests --


class TestInMemoryJobQueueAsyncHandler:
    @pytest.mark.anyio
    async def test_enqueue_async_handler(self) -> None:
        q = InMemoryJobQueue(handlers={"square": _async_square})
        job_id = await q.enqueue("square", {"n": 5})
        assert await q.get_status(job_id) == "complete"
        assert await q.get_result(job_id) == 25


class TestInMemoryJobQueueSyncHandler:
    @pytest.mark.anyio
    async def test_enqueue_sync_handler(self) -> None:
        q = InMemoryJobQueue(handlers={"greet": _sync_greet})
        job_id = await q.enqueue("greet", {"name": "world"})
        assert await q.get_status(job_id) == "complete"
        assert await q.get_result(job_id) == "hello world"


class TestInMemoryJobQueueFailure:
    @pytest.mark.anyio
    async def test_async_handler_exception_returns_failed(self) -> None:
        q = InMemoryJobQueue(handlers={"boom": _async_boom})
        job_id = await q.enqueue("boom", {})
        assert await q.get_status(job_id) == "failed"
        assert await q.get_result(job_id) is None

    @pytest.mark.anyio
    async def test_sync_handler_exception_returns_failed(self) -> None:
        q = InMemoryJobQueue(handlers={"boom": _sync_boom})
        job_id = await q.enqueue("boom", {})
        assert await q.get_status(job_id) == "failed"
        assert await q.get_result(job_id) is None

    @pytest.mark.anyio
    async def test_missing_handler_returns_failed(self) -> None:
        q = InMemoryJobQueue(handlers={})
        job_id = await q.enqueue("unknown", {})
        assert await q.get_status(job_id) == "failed"
        assert await q.get_result(job_id) is None


class TestInMemoryJobQueueNotFound:
    @pytest.mark.anyio
    async def test_unknown_job_status_is_not_found(self) -> None:
        q = InMemoryJobQueue(handlers={})
        assert await q.get_status("nonexistent") == "not_found"

    @pytest.mark.anyio
    async def test_unknown_job_result_is_none(self) -> None:
        q = InMemoryJobQueue(handlers={})
        assert await q.get_result("nonexistent") is None


class TestInMemoryJobQueueDuplicateTaskId:
    @pytest.mark.anyio
    async def test_duplicate_explicit_task_id_raises(self) -> None:
        q = InMemoryJobQueue(handlers={"square": _async_square})
        await q.enqueue("square", {"n": 1}, task_id="fixed-id")
        with pytest.raises(ValueError, match="fixed-id"):
            await q.enqueue("square", {"n": 2}, task_id="fixed-id")


class TestInMemoryJobQueueRetention:
    @pytest.mark.anyio
    async def test_bounded_terminal_records_evict_oldest_job(self) -> None:
        q = InMemoryJobQueue(
            handlers={"square": _async_square},
            max_terminal_records=1,
        )

        first = await q.enqueue("square", {"n": 1})
        second = await q.enqueue("square", {"n": 2})

        assert await q.get_status(first) == "not_found"
        assert await q.get_status(second) == "complete"
        assert await q.get_result(second) == 4

    def test_terminal_record_limit_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="max_terminal_records"):
            InMemoryJobQueue(handlers={}, max_terminal_records=0)
