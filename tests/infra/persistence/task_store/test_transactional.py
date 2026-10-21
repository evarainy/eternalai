"""Pure caller-transaction checks, not PostgreSQL durability evidence."""

import asyncio
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.persistence.task_store.errors import TaskNotFoundError
from app.infra.persistence.task_store.transactional import (
    append_owned_event,
    create_task,
    lock_owned_task,
    transition_owned_task,
)
from app.ports.task_store import TaskEventRecord, TaskRecord, TaskStatus

OWNER = {"tenant_id": "tenant", "ai_user_id": "user", "session_id": "chat", "task_id": "task"}


def _session(*, active: bool = True) -> MagicMock:
    session = MagicMock(spec=AsyncSession)
    session.in_transaction.return_value = active
    session.execute = AsyncMock()
    session.execute.return_value = MagicMock()
    return session


def _event() -> TaskEventRecord:
    return TaskEventRecord(
        event_id="event", task_id="task", event_type="task_completed",
        timestamp=datetime.now(UTC), payload={"status": "completed"},
    )


def test_operations_share_callers_session_without_owning_transaction() -> None:
    async def exercise() -> None:
        session = _session()
        record = TaskRecord(**OWNER, status="running")
        session.execute.return_value.mappings.return_value.one_or_none.return_value = (
            record.model_dump()
        )
        session.execute.return_value.scalar_one_or_none.return_value = "event"
        assert await create_task(session, record) == record
        assert await lock_owned_task(session, **OWNER) == record
        assert await transition_owned_task(
            session, **OWNER, expected_statuses=("created",), status="running",
        ) == record
        await append_owned_event(session, **OWNER, event=_event())
        assert session.execute.await_count == 4
        session.begin.assert_not_called()
        session.begin_nested.assert_not_called()
        session.commit.assert_not_called()
        session.rollback.assert_not_called()
        session.close.assert_not_called()
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        assert "FOR UPDATE" in statements[1]
        for call in session.execute.await_args_list[1:]:
            for key, value in OWNER.items():
                assert f"{key}=:{key}" in str(call.args[0])
                assert call.args[1][key] == value
        assert "status IN (:expected_0)" in statements[2]
        assert session.execute.await_args_list[2].args[1]["expected_0"] == "created"
        assert "FROM tasks WHERE" in statements[3]

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["create", "lock", "transition", "event"])
def test_missing_caller_transaction_fails_before_sql(operation: str) -> None:
    async def exercise() -> None:
        session = _session(active=False)
        with pytest.raises(RuntimeError, match="^task_transaction_required$"):
            if operation == "create":
                await create_task(session, TaskRecord(**OWNER, status="running"))
            elif operation == "lock":
                await lock_owned_task(session, **OWNER)
            elif operation == "transition":
                await transition_owned_task(
                    session, **OWNER, expected_statuses=("running",), status="completed",
                )
            else:
                await append_owned_event(session, **OWNER, event=_event())
        session.execute.assert_not_awaited()
        session.begin.assert_not_called()

    asyncio.run(exercise())


@pytest.mark.parametrize("terminal", [
    "completed", "failed", "cancelled", "no_capability_found", "confirmation_invalidated",
])
def test_terminal_cannot_be_admitted_as_expected_status(terminal: str) -> None:
    async def exercise() -> None:
        session = _session()
        with pytest.raises(ValueError, match="^task_expected_status_invalid$"):
            await transition_owned_task(
                session, **OWNER, expected_statuses=("running", cast(TaskStatus, terminal)),
                status="completed",
            )
        session.execute.assert_not_awaited()

    asyncio.run(exercise())


def test_no_matching_owner_or_status_does_not_return_task() -> None:
    async def exercise() -> None:
        session = _session()
        session.execute.return_value.mappings.return_value.one_or_none.return_value = None
        assert await lock_owned_task(session, **OWNER) is None
        assert await transition_owned_task(
            session, **OWNER, expected_statuses=("running",), status="completed",
        ) is None
        session.execute.return_value.scalar_one_or_none.return_value = None
        with pytest.raises(TaskNotFoundError, match="^task_not_found$"):
            await append_owned_event(session, **OWNER, event=_event())
        session.commit.assert_not_called()

    asyncio.run(exercise())


def test_event_parent_mismatch_rejected_before_sql() -> None:
    async def exercise() -> None:
        session = _session()
        event = _event().model_copy(update={"task_id": "other"})
        with pytest.raises(ValueError, match="^task_event_parent_mismatch$"):
            await append_owned_event(session, **OWNER, event=event)
        session.execute.assert_not_awaited()

    asyncio.run(exercise())


def test_event_collision_propagates_without_rollback_or_overwrite() -> None:
    async def exercise() -> None:
        session = _session()
        collision = IntegrityError("insert", {}, RuntimeError("duplicate"))
        session.execute.side_effect = collision
        with pytest.raises(IntegrityError) as raised:
            await append_owned_event(session, **OWNER, event=_event())
        assert raised.value is collision
        session.execute.assert_awaited_once()
        assert "ON CONFLICT" not in str(session.execute.await_args.args[0])
        session.rollback.assert_not_called()
        session.commit.assert_not_called()

    asyncio.run(exercise())
