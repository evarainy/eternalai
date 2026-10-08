"""Private Task operations in an existing caller-owned transaction.

The caller authenticates and checks current grants independently; matching an
owner only establishes row ownership. These functions never begin, commit,
roll back or close a transaction. Returned values are uncommitted snapshots,
not durable acceptance facts. Browser Run acceptance/finalization must perform
their other writes in this same transaction and await its successful commit.
"""

from __future__ import annotations

import json
import re
from typing import get_args

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.persistence.task_store.errors import TaskNotFoundError
from app.ports.task_store import TaskEventRecord, TaskRecord, TaskStatus

_COLUMNS = (
    "task_id, session_id, ai_user_id, tenant_id, status,"
    " trace_id, capability_id, error_code"
)
_OWNER = (
    "tenant_id=:tenant_id AND ai_user_id=:ai_user_id"
    " AND session_id=:session_id AND task_id=:task_id"
)
_STATUSES = frozenset(get_args(TaskStatus))
_NONTERMINAL = frozenset({"created", "running", "waiting_user"})


def _owner(
    *, tenant_id: str, ai_user_id: str, session_id: str, task_id: str,
) -> dict[str, str]:
    values = {
        "tenant_id": tenant_id, "ai_user_id": ai_user_id,
        "session_id": session_id, "task_id": task_id,
    }
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ValueError("task_owner_invalid")
    return values


def _status(status: TaskStatus, error_code: str | None) -> None:
    if not isinstance(status, str) or status not in _STATUSES:
        raise ValueError("task_status_invalid")
    if error_code is not None and (
        not isinstance(error_code, str)
        or re.fullmatch(r"[a-z][a-z0-9_]{0,95}", error_code) is None
    ):
        raise ValueError("task_error_code_invalid")


def _require_transaction(session: AsyncSession) -> None:
    if not session.in_transaction():
        raise RuntimeError("task_transaction_required")


async def lock_owned_task(
    session: AsyncSession, *, tenant_id: str, ai_user_id: str,
    session_id: str, task_id: str,
) -> TaskRecord | None:
    """Lock exactly one owner's Task; absence and owner mismatch both return None."""
    parameters = _owner(
        tenant_id=tenant_id, ai_user_id=ai_user_id, session_id=session_id, task_id=task_id,
    )
    _require_transaction(session)
    result = await session.execute(
        text(f"SELECT {_COLUMNS} FROM tasks WHERE {_OWNER} FOR UPDATE"), parameters,
    )
    row = result.mappings().one_or_none()
    return TaskRecord.model_validate(row) if row is not None else None


async def create_task(session: AsyncSession, record: TaskRecord) -> TaskRecord:
    """Insert an uncommitted Task; uniqueness errors belong to the outer transaction."""
    checked = TaskRecord.model_validate(record.model_dump())
    if checked.tenant_id is None:
        raise ValueError("task_owner_invalid")
    _owner(
        tenant_id=checked.tenant_id, ai_user_id=checked.ai_user_id,
        session_id=checked.session_id, task_id=checked.task_id,
    )
    _status(checked.status, checked.error_code)
    _require_transaction(session)
    await session.execute(
        text(
            f"INSERT INTO tasks ({_COLUMNS}) VALUES"
            " (:task_id, :session_id, :ai_user_id, :tenant_id, :status,"
            " :trace_id, :capability_id, :error_code)"
        ),
        checked.model_dump(),
    )
    return checked


async def transition_owned_task(
    session: AsyncSession, *, tenant_id: str, ai_user_id: str,
    session_id: str, task_id: str, expected_statuses: tuple[TaskStatus, ...],
    status: TaskStatus, error_code: str | None = None,
) -> TaskRecord | None:
    """CAS a nonterminal Task; terminal rows cannot be overwritten by this helper."""
    parameters: dict[str, object] = dict(_owner(
        tenant_id=tenant_id, ai_user_id=ai_user_id, session_id=session_id, task_id=task_id,
    ))
    if (
        not isinstance(expected_statuses, tuple) or not expected_statuses
        or any(not isinstance(item, str) or item not in _NONTERMINAL
               for item in expected_statuses)
    ):
        raise ValueError("task_expected_status_invalid")
    _status(status, error_code)
    # Placeholder names are generated only from integer indices, never input text.
    placeholders = ",".join(f":expected_{index}" for index in range(len(expected_statuses)))
    parameters.update({f"expected_{index}": value
                       for index, value in enumerate(expected_statuses)})
    parameters.update(status=status, error_code=error_code)
    _require_transaction(session)
    result = await session.execute(
        text(
            "UPDATE tasks SET status=:status, error_code=:error_code"
            f" WHERE {_OWNER} AND status IN ({placeholders}) RETURNING {_COLUMNS}"
        ),
        parameters,
    )
    row = result.mappings().one_or_none()
    return TaskRecord.model_validate(row) if row is not None else None


async def append_owned_event(
    session: AsyncSession, *, tenant_id: str, ai_user_id: str,
    session_id: str, task_id: str, event: TaskEventRecord,
) -> None:
    """Insert only for the exact parent owner, without masking event-ID collisions.

    Payload follows the existing TaskEventRecord/repository JSON contract. The
    caller supplies a sanitized event and, for terminal events, wins its Run CAS
    first. This primitive neither chooses a terminal event nor proves uniqueness
    of logical terminalization on its own.
    """
    if event.task_id != task_id:
        raise ValueError("task_event_parent_mismatch")
    parameters: dict[str, object] = dict(_owner(
        tenant_id=tenant_id, ai_user_id=ai_user_id, session_id=session_id, task_id=task_id,
    ))
    parameters.update(
        event_id=event.event_id, event_type=event.event_type,
        timestamp=event.timestamp, payload=json.dumps(event.payload),
    )
    _require_transaction(session)
    result = await session.execute(
        text(
            "INSERT INTO task_events (event_id, task_id, event_type, timestamp, payload)"
            " SELECT :event_id, task_id, :event_type, :timestamp, CAST(:payload AS JSONB)"
            f" FROM tasks WHERE {_OWNER} RETURNING event_id"
        ),
        parameters,
    )
    if result.scalar_one_or_none() is None:
        raise TaskNotFoundError("task_not_found")
