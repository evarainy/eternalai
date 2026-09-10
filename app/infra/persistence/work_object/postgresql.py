"""PostgreSQL Work Object persistence."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ports.work_object import (
    DISPATCH_RECORD_FIELDS,
    WORK_OBJECT_LIST_FETCH_LIMIT,
    DispatchReceipt,
    InternalWorkObjectRecord,
    OAPendingWorkSnapshot,
    WorkObjectHandlingMark,
    WorkObjectRecord,
    WorkObjectStorePort,
)
from app.ports.work_object_scope import AuthorizedWorkObjectScope
from app.ports.work_object_search import (
    SEARCH_WHITESPACE_PATTERN,
    normalize_search_query,
)

_OA_INSERT_COLUMNS = (
    "work_object_id, state_authority, source_system, source_kind, source_ref, "
    "assignee_ai_user_id, assignee_display_name, due_at, source_title, "
    "source_status, source_received_at, source_created_at, "
    "source_workflow_type_id, source_fetched_at, handling_mark, "
    "handling_marked_by_ai_user_id, handling_marked_at, task_record_id, "
    "created_at, updated_at"
)
_WORK_OBJECT_COLUMNS = _OA_INSERT_COLUMNS + ", " + ", ".join(DISPATCH_RECORD_FIELDS)
_WORK_OBJECT_RECORD_ADAPTER: TypeAdapter[WorkObjectRecord] = TypeAdapter(WorkObjectRecord)


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
                        "INSERT INTO work_objects (" + _OA_INSERT_COLUMNS + ") VALUES ("
                        ":work_object_id, 'external_snapshot', 'oa', "
                        "'pending_workflow', :source_ref, "
                        ":assignee_ai_user_id, :assignee_display_name, NULL, "
                        ":source_title, :source_status, :source_received_at, "
                        ":source_created_at, :source_workflow_type_id, "
                        ":source_fetched_at, NULL, NULL, NULL, NULL, "
                        ":created_at, :updated_at) "
                        "ON CONFLICT (assignee_ai_user_id, source_system, source_ref) "
                        "WHERE state_authority = 'external_snapshot' "
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

    async def list_for_scope(
        self,
        scope: AuthorizedWorkObjectScope,
        *,
        search_term: str | None = None,
        limit: int = WORK_OBJECT_LIST_FETCH_LIMIT,
    ) -> list[WorkObjectRecord]:
        if not 1 <= limit <= WORK_OBJECT_LIST_FETCH_LIMIT:
            raise ValueError("Work Object list limit is outside the allowed range")
        normalized_search_term = normalize_search_query(search_term)
        search_clause = ""
        visibility, parameters = _visibility_predicate(scope)
        parameters["limit"] = limit
        if normalized_search_term:
            # Fixed SQL expressions only; all user input stays in bound parameters.
            query = "LOWER(BTRIM(regexp_replace(:search_term, :ws_pattern, ' ', 'g')))"
            title = (
                "LOWER(BTRIM(regexp_replace(CASE WHEN state_authority = 'internal' "
                "AND source_kind = 'manual_dispatch' THEN title ELSE source_title END, "
                ":ws_pattern, ' ', 'g')))"
            )
            reference = "LOWER(BTRIM(regexp_replace(source_ref, :ws_pattern, ' ', 'g')))"
            assignee = "LOWER(BTRIM(regexp_replace(assignee_display_name, :ws_pattern, ' ', 'g')))"
            search_clause = (
                f"AND (STRPOS({title}, {query}) > 0 "
                f"OR {reference} = {query} OR {assignee} = {query}) "
            )
            parameters["ws_pattern"] = SEARCH_WHITESPACE_PATTERN
            parameters["search_term"] = normalized_search_term
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT " + _WORK_OBJECT_COLUMNS + " FROM work_objects "
                        "WHERE " + visibility + " " + search_clause + "LIMIT :limit"
                    ),
                    parameters,
                )
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    async def get_for_scope(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
    ) -> WorkObjectRecord | None:
        visibility, parameters = _visibility_predicate(scope)
        parameters["work_object_id"] = work_object_id
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT " + _WORK_OBJECT_COLUMNS + " FROM work_objects "
                        "WHERE work_object_id = :work_object_id "
                        "AND " + visibility
                    ),
                    parameters,
                )
            ).fetchone()
        return None if row is None else _record_from_row(row)

    async def set_handling_mark_for_scope(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
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
                        "AND state_authority = 'external_snapshot' "
                        "RETURNING " + _WORK_OBJECT_COLUMNS
                    ),
                    {
                        "handling_mark": mark,
                        "assignee_ai_user_id": scope.principal_ai_user_id,
                        "marked_at": marked_at,
                        "work_object_id": work_object_id,
                    },
                )
            ).fetchone()
            if row is not None:
                await session.commit()
        return None if row is None else _record_from_row(row)

    async def get_dispatch_receipt(
        self,
        *,
        tenant_id: str,
        initiator_ai_user_id: str,
        idempotency_key: UUID,
    ) -> DispatchReceipt | None:
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM work_object_dispatch_receipts "
                            "WHERE tenant_id = :tenant_id "
                            "AND initiator_ai_user_id = "
                            ":initiator_ai_user_id AND operation = 'dispatch' "
                            "AND idempotency_key = :idempotency_key"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "initiator_ai_user_id": initiator_ai_user_id,
                            "idempotency_key": idempotency_key,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else DispatchReceipt.model_validate(dict(row))

    async def create_internal_dispatch(
        self,
        *,
        records: list[InternalWorkObjectRecord],
        receipt: DispatchReceipt,
    ) -> tuple[DispatchReceipt, bool]:
        async with self._session_factory() as session:
            async with session.begin():
                inserted = (
                    await session.execute(
                        text(
                            "INSERT INTO work_object_dispatch_receipts "
                            "(tenant_id, initiator_ai_user_id, operation, idempotency_key, "
                            "request_fingerprint, result, "
                            "authorization_summary, created_at) VALUES "
                            "(:tenant_id, :initiator_ai_user_id, :operation, :idempotency_key, "
                            ":request_fingerprint, :result, :authorization_summary, :created_at) "
                            "ON CONFLICT (tenant_id, initiator_ai_user_id, "
                            "operation, idempotency_key) "
                            "DO NOTHING RETURNING idempotency_key"
                        ).bindparams(
                            bindparam("result", type_=JSONB),
                            bindparam("authorization_summary", type_=JSONB),
                        ),
                        receipt.model_dump(),
                    )
                ).scalar_one_or_none()
                if inserted is None:
                    winner = (
                        (
                            await session.execute(
                                text(
                                    "SELECT * FROM "
                                    "work_object_dispatch_receipts WHERE tenant_id = :tenant_id "
                                    "AND initiator_ai_user_id = "
                                    ":initiator_ai_user_id AND operation = 'dispatch' "
                                    "AND idempotency_key = :idempotency_key"
                                ),
                                receipt.model_dump(),
                            )
                        )
                        .mappings()
                        .one()
                    )
                    return DispatchReceipt.model_validate(dict(winner)), False
                for record in records:
                    values = record.model_dump()
                    columns = _WORK_OBJECT_COLUMNS.split(", ")
                    await session.execute(
                        text(
                            "INSERT INTO work_objects ("
                            + _WORK_OBJECT_COLUMNS
                            + ") VALUES ("
                            + ", ".join(":" + name for name in columns)
                            + ")"
                        ).bindparams(
                            bindparam(
                                "reminder_choices",
                                type_=JSONB,
                            )
                        ),
                        values,
                    )
        return receipt, True


def _visibility_predicate(scope: AuthorizedWorkObjectScope) -> tuple[str, dict[str, object]]:
    return (
        """(
      (state_authority = 'external_snapshot' AND assignee_ai_user_id = :principal_ai_user_id)
      OR (state_authority = 'internal' AND source_kind <> 'manual_dispatch'
          AND version IS NULL AND assignee_ai_user_id = :principal_ai_user_id)
      OR (state_authority = 'internal' AND source_kind = 'manual_dispatch'
          AND tenant_id = :principal_tenant_id AND version IS NOT NULL
          AND owner_department_id IS NOT NULL AND initiator_ai_user_id IS NOT NULL
          AND ((CAST(:principal_department_id AS TEXT) IS NOT NULL
                AND owner_department_id = :principal_department_id)
               OR initiator_ai_user_id = :principal_ai_user_id))
    )""",
        {
            "principal_tenant_id": scope.principal_tenant_id,
            "principal_ai_user_id": scope.principal_ai_user_id,
            "principal_department_id": scope.principal_department_id,
        },
    )


def _record_from_row(row: Any) -> WorkObjectRecord:
    values = dict(row._mapping)
    if values["state_authority"] == "external_snapshot":
        for name in DISPATCH_RECORD_FIELDS:
            values.pop(name)
    return _WORK_OBJECT_RECORD_ADAPTER.validate_python(
        values,
        strict=True,
    )


if TYPE_CHECKING:

    def _protocol_check(store: PostgreSQLWorkObjectStore) -> WorkObjectStorePort:
        return store


__all__ = ("PostgreSQLWorkObjectStore",)
