"""PostgreSQL Work Object persistence."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ports.work_object import (
    WORK_OBJECT_LIST_FETCH_LIMIT,
    OAPendingWorkSnapshot,
    WorkObjectHandlingMark,
    WorkObjectRecord,
    WorkObjectStorePort,
)

_WORK_OBJECT_COLUMNS = (
    "work_object_id, source_system, source_kind, source_ref, "
    "assignee_ai_user_id, assignee_display_name, due_at, source_title, "
    "source_status, source_received_at, source_created_at, "
    "source_workflow_type_id, source_fetched_at, handling_mark, "
    "handling_marked_by_ai_user_id, handling_marked_at, task_record_id, "
    "created_at, updated_at"
)


class PostgreSQLWorkObjectStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def upsert_oa_pending_workflows(
        self,
        *,
        assignee_ai_user_id: str,
        assignee_display_name: str,
        snapshots: list[OAPendingWorkSnapshot],
        fetched_at: datetime,
    ) -> None:
        if not snapshots:
            return
        async with self._session_factory() as session:
            for snapshot in snapshots:
                await session.execute(
                    text(
                        "INSERT INTO work_objects ("
                        + _WORK_OBJECT_COLUMNS
                        + ") VALUES ("
                        ":work_object_id, 'oa', 'pending_workflow', :source_ref, "
                        ":assignee_ai_user_id, :assignee_display_name, NULL, "
                        ":source_title, :source_status, :source_received_at, "
                        ":source_created_at, :source_workflow_type_id, "
                        ":source_fetched_at, NULL, NULL, NULL, NULL, "
                        ":created_at, :updated_at) "
                        "ON CONFLICT (assignee_ai_user_id, source_system, source_ref) "
                        "DO UPDATE SET "
                        "assignee_display_name = EXCLUDED.assignee_display_name, "
                        "source_title = EXCLUDED.source_title, "
                        "source_status = EXCLUDED.source_status, "
                        "source_received_at = EXCLUDED.source_received_at, "
                        "source_created_at = EXCLUDED.source_created_at, "
                        "source_workflow_type_id = EXCLUDED.source_workflow_type_id, "
                        "source_fetched_at = EXCLUDED.source_fetched_at, "
                        "updated_at = EXCLUDED.updated_at"
                    ),
                    {
                        "work_object_id": uuid4().hex,
                        "source_ref": snapshot.source_ref,
                        "assignee_ai_user_id": assignee_ai_user_id,
                        "assignee_display_name": assignee_display_name,
                        "source_title": snapshot.title,
                        "source_status": snapshot.status,
                        "source_received_at": snapshot.received_at,
                        "source_created_at": snapshot.created_at,
                        "source_workflow_type_id": snapshot.workflow_type_id,
                        "source_fetched_at": fetched_at,
                        "created_at": fetched_at,
                        "updated_at": fetched_at,
                    },
                )
            await session.commit()

    async def list_for_assignee(
        self,
        assignee_ai_user_id: str,
        *,
        limit: int = WORK_OBJECT_LIST_FETCH_LIMIT,
    ) -> list[WorkObjectRecord]:
        if not 1 <= limit <= WORK_OBJECT_LIST_FETCH_LIMIT:
            raise ValueError("Work Object list limit is outside the allowed range")
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT "
                        + _WORK_OBJECT_COLUMNS
                        + " FROM work_objects "
                        "WHERE assignee_ai_user_id = :assignee_ai_user_id "
                        "LIMIT :limit"
                    ),
                    {
                        "assignee_ai_user_id": assignee_ai_user_id,
                        "limit": limit,
                    },
                )
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    async def get_for_assignee(
        self,
        work_object_id: str,
        assignee_ai_user_id: str,
    ) -> WorkObjectRecord | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT "
                        + _WORK_OBJECT_COLUMNS
                        + " FROM work_objects "
                        "WHERE work_object_id = :work_object_id "
                        "AND assignee_ai_user_id = :assignee_ai_user_id"
                    ),
                    {
                        "work_object_id": work_object_id,
                        "assignee_ai_user_id": assignee_ai_user_id,
                    },
                )
            ).fetchone()
        return None if row is None else _record_from_row(row)

    async def set_handling_mark_for_assignee(
        self,
        work_object_id: str,
        assignee_ai_user_id: str,
        mark: WorkObjectHandlingMark,
        *,
        marked_at: datetime,
    ) -> WorkObjectRecord | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "UPDATE work_objects SET "
                        "handling_mark = :handling_mark, "
                        "handling_marked_by_ai_user_id = :assignee_ai_user_id, "
                        "handling_marked_at = :marked_at, updated_at = :marked_at "
                        "WHERE work_object_id = :work_object_id "
                        "AND assignee_ai_user_id = :assignee_ai_user_id "
                        "RETURNING "
                        + _WORK_OBJECT_COLUMNS
                    ),
                    {
                        "handling_mark": mark,
                        "assignee_ai_user_id": assignee_ai_user_id,
                        "marked_at": marked_at,
                        "work_object_id": work_object_id,
                    },
                )
            ).fetchone()
            if row is not None:
                await session.commit()
        return None if row is None else _record_from_row(row)


def _record_from_row(row: Any) -> WorkObjectRecord:
    return WorkObjectRecord(
        work_object_id=row.work_object_id,
        source_system=row.source_system,
        source_kind=row.source_kind,
        source_ref=row.source_ref,
        assignee_ai_user_id=row.assignee_ai_user_id,
        assignee_display_name=row.assignee_display_name,
        due_at=row.due_at,
        source_title=row.source_title,
        source_status=row.source_status,
        source_received_at=row.source_received_at,
        source_created_at=row.source_created_at,
        source_workflow_type_id=row.source_workflow_type_id,
        source_fetched_at=row.source_fetched_at,
        handling_mark=row.handling_mark,
        handling_marked_by_ai_user_id=row.handling_marked_by_ai_user_id,
        handling_marked_at=row.handling_marked_at,
        task_record_id=row.task_record_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


if TYPE_CHECKING:

    def _protocol_check(store: PostgreSQLWorkObjectStore) -> WorkObjectStorePort:
        return store


__all__ = ("PostgreSQLWorkObjectStore",)
