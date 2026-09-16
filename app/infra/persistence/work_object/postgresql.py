"""PostgreSQL Work Object persistence."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
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
    OAObservation,
    OAPendingWorkSnapshot,
    OAPendingWorkSnapshotCollection,
    OASyncClockInvalid,
    OASyncFailureCode,
    OASyncOutcomeUnknown,
    OASyncStatus,
    OASyncStatusView,
    OASyncStream,
    OASyncSubject,
    OASyncTicket,
    OAView,
    WorkObjectHandlingMark,
    WorkObjectReadBatch,
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
            await self._upsert_pending(
                session,
                assignee_ai_user_id=assignee_ai_user_id,
                assignee_display_name=assignee_display_name,
                snapshots=snapshots,
                fetched_at=fetched_at,
            )
            await session.commit()

    async def _upsert_pending(
        self, session: AsyncSession, *, assignee_ai_user_id: str,
        assignee_display_name: str, snapshots: list[OAPendingWorkSnapshot], fetched_at: datetime,
    ) -> None:
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

    async def begin_oa_sync(
        self, subject: OASyncSubject, stream: OASyncStream, started_at: datetime,
    ) -> OASyncTicket:
        # Validate before a transaction; ticket allocation is server-side only.
        OASyncTicket(subject=subject, stream=stream, generation=1, started_at=started_at)
        params = {**subject.model_dump(), "stream": stream, "started_at": started_at}
        async with self._session_factory() as session:
            generation = (await session.execute(text(
                "INSERT INTO oa_work_sync_state "
                "(tenant_id, ai_user_id, stream, issued_generation, last_attempt_status, "
                "last_attempt_started_at) VALUES "
                "(:tenant_id, :ai_user_id, :stream, 1, 'running', :started_at) "
                "ON CONFLICT (tenant_id, ai_user_id, stream) DO UPDATE SET "
                "issued_generation = oa_work_sync_state.issued_generation + 1, "
                "last_attempt_status = 'running', last_attempt_started_at = :started_at, "
                "last_attempt_finished_at = NULL, last_error_code = NULL "
                "RETURNING issued_generation"
            ), params)).scalar_one()
            await _commit_publication(session)
        return OASyncTicket(
            subject=subject, stream=stream, generation=generation, started_at=started_at
        )

    async def _sync_status(
        self, session: AsyncSession, subject: OASyncSubject, stream: OASyncStream,
        *, lock: bool = False,
    ) -> OASyncStatus:
        row = (await session.execute(text(
            "SELECT * FROM oa_work_sync_state WHERE tenant_id = :tenant_id "
            "AND ai_user_id = :ai_user_id AND stream = :stream" + (" FOR UPDATE" if lock else "")
        ), {**subject.model_dump(), "stream": stream})).mappings().one_or_none()
        if row is None:
            return OASyncStatus(
                subject=subject, stream=stream, issued_generation=0, applied_generation=0,
                last_attempt_status="never", last_attempt_started_at=None,
                last_attempt_finished_at=None, last_success_at=None, last_error_code=None,
            )
        values = dict(row)
        values.pop("tenant_id")
        values.pop("ai_user_id")
        return OASyncStatus(subject=subject, **values)

    async def get_oa_sync_status(
        self, subject: OASyncSubject, stream: OASyncStream,
    ) -> OASyncStatus:
        async with self._session_factory() as session:
            return await self._sync_status(session, subject, stream)

    async def apply_oa_pending_snapshot(
        self, ticket: OASyncTicket, *, assignee_display_name: str,
        collection: OAPendingWorkSnapshotCollection, fetched_at: datetime,
    ) -> Literal["applied", "superseded"]:
        ticket = OASyncTicket.model_validate(ticket.model_dump())
        collection = OAPendingWorkSnapshotCollection.model_validate(collection.model_dump())
        if ticket.stream != "pending":
            raise ValueError("pending reconciliation requires a pending ticket")
        if fetched_at.utcoffset() != timedelta(0) or fetched_at < ticket.started_at:
            raise OASyncClockInvalid()
        params = {**ticket.subject.model_dump(), "generation": ticket.generation,
                  "fetched_at": fetched_at}
        async with self._session_factory() as session:
            state = await self._sync_status(session, ticket.subject, "pending", lock=True)
            if (ticket.generation != state.issued_generation
                    or ticket.generation <= state.applied_generation):
                return "superseded"
            if ticket.started_at != state.last_attempt_started_at:
                raise ValueError("ticket does not match issued attempt")
            if state.last_success_at is not None and fetched_at < state.last_success_at:
                raise OASyncClockInvalid()
            # FK alone does not prove ownership; reject corrupted associations before writing.
            invalid = (await session.execute(text(
                "SELECT EXISTS (SELECT 1 FROM oa_work_pending_observations o "
                "JOIN work_objects w ON w.work_object_id = o.work_object_id "
                "WHERE o.tenant_id = :tenant_id AND o.ai_user_id = :ai_user_id "
                "AND (w.state_authority IS DISTINCT FROM 'external_snapshot' "
                "OR w.source_system IS DISTINCT FROM 'oa' "
                "OR w.source_kind IS DISTINCT FROM 'pending_workflow' "
                "OR w.assignee_ai_user_id IS DISTINCT FROM o.ai_user_id "
                "OR w.source_ref IS DISTINCT FROM o.source_ref))"
            ), params)).scalar_one()
            if invalid:
                raise ValueError("OA observation ownership mismatch")
            latest = (await session.execute(text(
                "SELECT max(source_fetched_at) FROM work_objects WHERE " + _PENDING_OWNER
            ), params)).scalar_one()
            if latest is not None and fetched_at < latest:
                raise OASyncClockInvalid()
            await self._upsert_pending(
                session, assignee_ai_user_id=ticket.subject.ai_user_id,
                assignee_display_name=assignee_display_name,
                snapshots=collection.workflows, fetched_at=fetched_at,
            )
            params["refs"] = [item.source_ref for item in collection.workflows]
            await session.execute(text(
                "INSERT INTO oa_work_pending_observations "
                "(tenant_id, ai_user_id, source_ref, work_object_id, pending_state, "
                "revision, last_seen_at, last_checked_at) SELECT "
                ":tenant_id, :ai_user_id, source_ref, work_object_id, "
                "CASE WHEN source_ref = ANY(CAST(:refs AS text[])) THEN 'current' "
                "ELSE 'unconfirmed' END, :generation, source_fetched_at, :fetched_at "
                "FROM work_objects WHERE " + _PENDING_OWNER + " "
                "ON CONFLICT (tenant_id, ai_user_id, source_ref) DO UPDATE SET "
                "pending_state = EXCLUDED.pending_state, revision = EXCLUDED.revision, "
                "last_seen_at = EXCLUDED.last_seen_at, last_checked_at = EXCLUDED.last_checked_at "
                "WHERE oa_work_pending_observations.work_object_id = EXCLUDED.work_object_id"
            ), params)
            await session.execute(text(
                "UPDATE oa_work_sync_state SET applied_generation = :generation, "
                "last_attempt_status = 'succeeded', last_success_at = :fetched_at, "
                "last_attempt_finished_at = :fetched_at, last_error_code = NULL "
                "WHERE tenant_id = :tenant_id AND ai_user_id = :ai_user_id AND stream = 'pending'"
            ), params)
            await _commit_publication(session)
        return "applied"

    async def finish_oa_sync_failure(
        self, ticket: OASyncTicket, *, failure_code: OASyncFailureCode, finished_at: datetime,
    ) -> None:
        ticket = OASyncTicket.model_validate(ticket.model_dump())
        if finished_at.utcoffset() != timedelta(0):
            raise OASyncClockInvalid()
        code = "clock_invalid" if finished_at < ticket.started_at else failure_code
        async with self._session_factory() as session:
            await session.execute(text(
                "UPDATE oa_work_sync_state SET last_attempt_status = 'failed', "
                "last_attempt_finished_at = :finished_at, last_error_code = :failure_code "
                "WHERE tenant_id = :tenant_id AND ai_user_id = :ai_user_id AND stream = :stream "
                "AND issued_generation = :generation AND applied_generation < :generation "
                "AND last_attempt_started_at = :started_at"
            ), {**ticket.subject.model_dump(), "stream": ticket.stream,
                "generation": ticket.generation, "started_at": ticket.started_at,
                "finished_at": finished_at, "failure_code": code})
            await session.commit()

    async def _list_records(
        self, session: AsyncSession, scope: AuthorizedWorkObjectScope, *,
        search_term: str | None, oa_view: OAView, limit: int,
    ) -> list[WorkObjectRecord]:
        if not 1 <= limit <= WORK_OBJECT_LIST_FETCH_LIMIT:
            raise ValueError("Work Object list limit is outside the allowed range")
        if oa_view not in {"active", "unconfirmed", "all"}:
            raise ValueError("Unknown OA view")
        normalized = normalize_search_query(search_term)
        visibility, parameters = _visibility_predicate(scope)
        parameters["limit"] = limit
        search_clause = ""
        if normalized:
            query = "LOWER(BTRIM(regexp_replace(:search_term, :ws_pattern, ' ', 'g')))"
            title = (
                "LOWER(BTRIM(regexp_replace(CASE WHEN state_authority = 'internal' "
                "AND source_kind = 'manual_dispatch' THEN title ELSE source_title END, "
                ":ws_pattern, ' ', 'g')))"
            )
            reference = "LOWER(BTRIM(regexp_replace(source_ref, :ws_pattern, ' ', 'g')))"
            assignee = "LOWER(BTRIM(regexp_replace(assignee_display_name, :ws_pattern, ' ', 'g')))"
            search_clause = (f"AND (STRPOS({title}, {query}) > 0 "
                             f"OR {reference} = {query} OR {assignee} = {query}) ")
            parameters.update(ws_pattern=SEARCH_WHITESPACE_PATTERN, search_term=normalized)
        columns, source = _observed_source(scope)
        view_clause = ""
        if oa_view == "active" and scope.principal_tenant_id == "default":
            view_clause = "AND obs_pending_state IS DISTINCT FROM 'unconfirmed' "
        elif oa_view == "unconfirmed":
            view_clause = ("AND obs_pending_state = 'unconfirmed' "
                           if scope.principal_tenant_id == "default" else "AND FALSE ")
        rows = (await session.execute(text(
            "SELECT " + columns + " FROM " + source + " WHERE " + visibility + " "
            + search_clause + view_clause
            + "ORDER BY due_at ASC NULLS LAST, created_at DESC NULLS LAST, "
            + 'work_object_id COLLATE "C" ASC LIMIT :limit'
        ), parameters)).fetchall()
        return [_record_from_row(row) for row in rows]

    async def list_for_scope(
        self, scope: AuthorizedWorkObjectScope, *, search_term: str | None = None,
        limit: int = WORK_OBJECT_LIST_FETCH_LIMIT,
    ) -> list[WorkObjectRecord]:
        async with self._session_factory() as session:
            return await self._list_records(
                session, scope, search_term=search_term, oa_view="all", limit=limit,
            )

    async def list_with_oa_sync_for_scope(
        self, scope: AuthorizedWorkObjectScope, *, search_term: str | None = None,
        oa_view: OAView = "active", limit: int = WORK_OBJECT_LIST_FETCH_LIMIT,
    ) -> WorkObjectReadBatch:
        async with self._session_factory() as session:
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            records = await self._list_records(
                session, scope, search_term=search_term, oa_view=oa_view, limit=limit,
            )
            if scope.principal_tenant_id == "default":
                state = await self._sync_status(session, OASyncSubject(
                    tenant_id="default", ai_user_id=scope.principal_ai_user_id,
                ), "pending")
                view = state.to_view()
            else:
                view = OASyncStatusView(
                    status="unsupported_scope", revision=0, attempt_revision=0,
                    last_attempt_at=None, last_success_at=None, failure_code=None,
                )
            return WorkObjectReadBatch(records=records, oa_sync=view)

    async def _get_record(
        self, session: AsyncSession, work_object_id: str, scope: AuthorizedWorkObjectScope,
    ) -> WorkObjectRecord | None:
        visibility, parameters = _visibility_predicate(scope)
        parameters["work_object_id"] = work_object_id
        columns, source = _observed_source(scope)
        row = (await session.execute(text(
            "SELECT " + columns + " FROM " + source + " WHERE work_object_id = :work_object_id "
            "AND " + visibility
        ), parameters)).fetchone()
        return None if row is None else _record_from_row(row)

    async def get_for_scope(
        self, work_object_id: str, scope: AuthorizedWorkObjectScope,
    ) -> WorkObjectRecord | None:
        async with self._session_factory() as session:
            return await self._get_record(session, work_object_id, scope)

    async def set_handling_mark_for_scope(
        self, work_object_id: str, scope: AuthorizedWorkObjectScope,
        mark: WorkObjectHandlingMark, *, marked_at: datetime,
    ) -> WorkObjectRecord | None:
        async with self._session_factory() as session:
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            row = (await session.execute(text(
                "UPDATE work_objects SET handling_mark = :handling_mark, "
                "handling_marked_by_ai_user_id = :assignee_ai_user_id, "
                "handling_marked_at = :marked_at, updated_at = :marked_at "
                "WHERE work_object_id = :work_object_id "
                "AND assignee_ai_user_id = :assignee_ai_user_id "
                "AND state_authority = 'external_snapshot' RETURNING work_object_id"
            ), {"handling_mark": mark, "assignee_ai_user_id": scope.principal_ai_user_id,
                "marked_at": marked_at, "work_object_id": work_object_id})).fetchone()
            if row is None:
                return None
            record = await self._get_record(session, work_object_id, scope)
            await session.commit()
            return record

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


_PENDING_OWNER = (
    "state_authority = 'external_snapshot' AND source_system = 'oa' "
    "AND source_kind = 'pending_workflow' AND assignee_ai_user_id = :ai_user_id"
)


async def _commit_publication(session: AsyncSession) -> None:
    try:
        await session.commit()
    except Exception:
        # The server may have committed even if its acknowledgement never arrived.
        raise OASyncOutcomeUnknown() from None


def _observed_source(scope: AuthorizedWorkObjectScope) -> tuple[str, str]:
    if scope.principal_tenant_id != "default":
        return _WORK_OBJECT_COLUMNS, "work_objects"
    return (
        _WORK_OBJECT_COLUMNS + ", obs_pending_state, obs_revision, obs_seen, obs_checked",
        "work_objects LEFT JOIN LATERAL (SELECT o.pending_state AS obs_pending_state, "
        "o.revision AS obs_revision, o.last_seen_at AS obs_seen, "
        "o.last_checked_at AS obs_checked FROM oa_work_pending_observations o "
        "WHERE o.tenant_id = :principal_tenant_id AND o.ai_user_id = :principal_ai_user_id "
        "AND o.ai_user_id = work_objects.assignee_ai_user_id "
        "AND o.work_object_id = work_objects.work_object_id "
        "AND o.source_ref = work_objects.source_ref "
        "AND work_objects.state_authority = 'external_snapshot' "
        "AND work_objects.source_system = 'oa' "
        "AND work_objects.source_kind = 'pending_workflow') observation ON TRUE",
    )


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
    pending_state = values.pop("obs_pending_state", None)
    revision = values.pop("obs_revision", None)
    seen = values.pop("obs_seen", None)
    checked = values.pop("obs_checked", None)
    if values["state_authority"] == "external_snapshot":
        values["oa_observation"] = OAObservation(
            pending_state=pending_state or "legacy_unverified", revision=revision or 0,
            last_seen_at=seen or values["source_fetched_at"], last_checked_at=checked,
        )
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
