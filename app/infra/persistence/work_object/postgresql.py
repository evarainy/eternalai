"""PostgreSQL Work Object persistence."""

from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import TypeAdapter
from sqlalchemy import bindparam
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.persistence.work_object.authorization import (
    child_visibility_sql,
    recheck_scope,
    relation_participant_sql,
    root_visibility_sql,
    scope_parameters,
)
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
from app.ports.work_object_dispatch import (
    DepartmentDispatchTarget,
    DispatchResult,
    HandlingRelationRecord,
    HandlingRelationView,
    PreparedDispatch,
    UserDispatchTarget,
    WorkDispatchError,
)
from app.ports.work_object_lifecycle import (
    CompletionFilter,
    LifecycleActor,
    LifecycleCommand,
    LifecycleEventRecord,
    LifecycleEventsView,
    LifecycleMutationResult,
    LifecycleStatus,
    LifecycleView,
    RemarkHistoryEntry,
    RemarkHistoryPage,
    RemarkMutationResult,
    RemarkView,
    compute_lifecycle_actions,
    relation_actions,
    relation_actor_allowed,
    relation_head_allowed,
    relation_private_allowed,
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
        self,
        session: AsyncSession,
        *,
        assignee_ai_user_id: str,
        assignee_display_name: str,
        snapshots: list[OAPendingWorkSnapshot],
        fetched_at: datetime,
    ) -> None:
        for snapshot in snapshots:
            await session.execute(
                sql_text(
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
        self,
        subject: OASyncSubject,
        stream: OASyncStream,
        started_at: datetime,
    ) -> OASyncTicket:
        # Validate before a transaction; ticket allocation is server-side only.
        OASyncTicket(subject=subject, stream=stream, generation=1, started_at=started_at)
        params = {**subject.model_dump(), "stream": stream, "started_at": started_at}
        async with self._session_factory() as session:
            generation = (
                await session.execute(
                    sql_text(
                        "INSERT INTO oa_work_sync_state "
                        "(tenant_id, ai_user_id, stream, issued_generation, last_attempt_status, "
                        "last_attempt_started_at) VALUES "
                        "(:tenant_id, :ai_user_id, :stream, 1, 'running', :started_at) "
                        "ON CONFLICT (tenant_id, ai_user_id, stream) DO UPDATE SET "
                        "issued_generation = oa_work_sync_state.issued_generation + 1, "
                        "last_attempt_status = 'running', last_attempt_started_at = :started_at, "
                        "last_attempt_finished_at = NULL, last_error_code = NULL "
                        "RETURNING issued_generation"
                    ),
                    params,
                )
            ).scalar_one()
            await _commit_publication(session)
        return OASyncTicket(
            subject=subject, stream=stream, generation=generation, started_at=started_at
        )

    async def _sync_status(
        self,
        session: AsyncSession,
        subject: OASyncSubject,
        stream: OASyncStream,
        *,
        lock: bool = False,
    ) -> OASyncStatus:
        row = (
            (
                await session.execute(
                    sql_text(
                        "SELECT * FROM oa_work_sync_state WHERE tenant_id = :tenant_id "
                        "AND ai_user_id = :ai_user_id AND stream = :stream"
                        + (" FOR UPDATE" if lock else "")
                    ),
                    {**subject.model_dump(), "stream": stream},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return OASyncStatus(
                subject=subject,
                stream=stream,
                issued_generation=0,
                applied_generation=0,
                last_attempt_status="never",
                last_attempt_started_at=None,
                last_attempt_finished_at=None,
                last_success_at=None,
                last_error_code=None,
            )
        values = dict(row)
        values.pop("tenant_id")
        values.pop("ai_user_id")
        return OASyncStatus(subject=subject, **values)

    async def get_oa_sync_status(
        self,
        subject: OASyncSubject,
        stream: OASyncStream,
    ) -> OASyncStatus:
        async with self._session_factory() as session:
            return await self._sync_status(session, subject, stream)

    async def apply_oa_pending_snapshot(
        self,
        ticket: OASyncTicket,
        *,
        assignee_display_name: str,
        collection: OAPendingWorkSnapshotCollection,
        fetched_at: datetime,
    ) -> Literal["applied", "superseded"]:
        ticket = OASyncTicket.model_validate(ticket.model_dump())
        collection = OAPendingWorkSnapshotCollection.model_validate(collection.model_dump())
        if ticket.stream != "pending":
            raise ValueError("pending reconciliation requires a pending ticket")
        if fetched_at.utcoffset() != timedelta(0) or fetched_at < ticket.started_at:
            raise OASyncClockInvalid()
        params = {
            **ticket.subject.model_dump(),
            "generation": ticket.generation,
            "fetched_at": fetched_at,
        }
        async with self._session_factory() as session:
            state = await self._sync_status(session, ticket.subject, "pending", lock=True)
            if (
                ticket.generation != state.issued_generation
                or ticket.generation <= state.applied_generation
            ):
                return "superseded"
            if ticket.started_at != state.last_attempt_started_at:
                raise ValueError("ticket does not match issued attempt")
            if state.last_success_at is not None and fetched_at < state.last_success_at:
                raise OASyncClockInvalid()
            # FK alone does not prove ownership; reject corrupted associations before writing.
            invalid = (
                await session.execute(
                    sql_text(
                        "SELECT EXISTS (SELECT 1 FROM oa_work_pending_observations o "
                        "JOIN work_objects w ON w.work_object_id = o.work_object_id "
                        "WHERE o.tenant_id = :tenant_id AND o.ai_user_id = :ai_user_id "
                        "AND (w.state_authority IS DISTINCT FROM 'external_snapshot' "
                        "OR w.source_system IS DISTINCT FROM 'oa' "
                        "OR w.source_kind IS DISTINCT FROM 'pending_workflow' "
                        "OR w.assignee_ai_user_id IS DISTINCT FROM o.ai_user_id "
                        "OR w.source_ref IS DISTINCT FROM o.source_ref))"
                    ),
                    params,
                )
            ).scalar_one()
            if invalid:
                raise ValueError("OA observation ownership mismatch")
            latest = (
                await session.execute(
                    sql_text(
                        "SELECT max(source_fetched_at) FROM work_objects WHERE " + _PENDING_OWNER
                    ),
                    params,
                )
            ).scalar_one()
            if latest is not None and fetched_at < latest:
                raise OASyncClockInvalid()
            await self._upsert_pending(
                session,
                assignee_ai_user_id=ticket.subject.ai_user_id,
                assignee_display_name=assignee_display_name,
                snapshots=collection.workflows,
                fetched_at=fetched_at,
            )
            params["refs"] = [item.source_ref for item in collection.workflows]
            await session.execute(
                sql_text(
                    "INSERT INTO oa_work_pending_observations "
                    "(tenant_id, ai_user_id, source_ref, work_object_id, pending_state, "
                    "revision, last_seen_at, last_checked_at) SELECT "
                    ":tenant_id, :ai_user_id, source_ref, work_object_id, "
                    "CASE WHEN source_ref = ANY(CAST(:refs AS text[])) THEN 'current' "
                    "ELSE 'unconfirmed' END, :generation, source_fetched_at, :fetched_at "
                    "FROM work_objects WHERE " + _PENDING_OWNER + " "
                    "ON CONFLICT (tenant_id, ai_user_id, source_ref) DO UPDATE SET "
                    "pending_state = EXCLUDED.pending_state, revision = EXCLUDED.revision, "
                    "last_seen_at = EXCLUDED.last_seen_at, last_checked_at = EXCLUDED.last_"
                    "checked_at "
                    "WHERE oa_work_pending_observations.work_object_id = EXCLUDED.work_object_id"
                ),
                params,
            )
            await session.execute(
                sql_text(
                    "UPDATE oa_work_sync_state SET applied_generation = :generation, "
                    "last_attempt_status = 'succeeded', last_success_at = :fetched_at, "
                    "last_attempt_finished_at = :fetched_at, last_error_code = NULL "
                    "WHERE tenant_id = :tenant_id AND ai_user_id = :ai_user_id AND stream ="
                    " 'pending'"
                ),
                params,
            )
            await _commit_publication(session)
        return "applied"

    async def finish_oa_sync_failure(
        self,
        ticket: OASyncTicket,
        *,
        failure_code: OASyncFailureCode,
        finished_at: datetime,
    ) -> None:
        ticket = OASyncTicket.model_validate(ticket.model_dump())
        if finished_at.utcoffset() != timedelta(0):
            raise OASyncClockInvalid()
        code = "clock_invalid" if finished_at < ticket.started_at else failure_code
        async with self._session_factory() as session:
            await session.execute(
                sql_text(
                    "UPDATE oa_work_sync_state SET last_attempt_status = 'failed', "
                    "last_attempt_finished_at = :finished_at, last_error_code = :failure_code "
                    "WHERE tenant_id = :tenant_id AND ai_user_id = :ai_user_id AND stream ="
                    " :stream "
                    "AND issued_generation = :generation AND applied_generation < :generation "
                    "AND last_attempt_started_at = :started_at"
                ),
                {
                    **ticket.subject.model_dump(),
                    "stream": ticket.stream,
                    "generation": ticket.generation,
                    "started_at": ticket.started_at,
                    "finished_at": finished_at,
                    "failure_code": code,
                },
            )
            await session.commit()

    async def _list_records(
        self,
        session: AsyncSession,
        scope: AuthorizedWorkObjectScope,
        *,
        search_term: str | None,
        oa_view: OAView,
        limit: int,
        completion: CompletionFilter | None = None,
        queue: str | None = None,
        history_from: datetime | None = None,
        history_to: datetime | None = None,
        cursor: str | None = None,
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
            search_clause = (
                f"AND (STRPOS({title}, {query}) > 0 "
                f"OR {reference} = {query} OR {assignee} = {query}) "
            )
            parameters.update(ws_pattern=SEARCH_WHITESPACE_PATTERN, search_term=normalized)
        completion_clause = ""
        ordering = (
            'due_at ASC NULLS LAST, created_at DESC NULLS LAST, work_object_id COLLATE "C" ASC'
        )
        if completion == "active":
            completion_clause = "AND (status IS NULL OR status NOT IN ('completed','withdrawn')) "
        elif completion == "completed":
            completion_clause = (
                "AND state_authority = 'internal' AND source_kind = 'manual_dispatch' "
                "AND status = 'completed' AND completed_at BETWEEN "
                "CURRENT_TIMESTAMP - INTERVAL '30 days' AND CURRENT_TIMESTAMP "
            )
            ordering = 'completed_at DESC, work_object_id COLLATE "C" ASC'
        elif completion == "withdrawn":
            completion_clause = "AND status='withdrawn' "
            ordering = 'withdrawn_at DESC, work_object_id COLLATE "C" ASC'
        elif completion is not None:
            raise ValueError("Unknown completion filter")
        if completion == "completed" and (
            history_from is not None or history_to is not None or cursor is not None
        ):
            completion_clause = (
                "AND state_authority='internal' AND source_kind='manual_dispatch' AND status='c"
                "ompleted' "
            )
        filter_clause = ""
        time_column = (
            "completed_at"
            if completion == "completed"
            else "withdrawn_at"
            if completion == "withdrawn"
            else "updated_at"
        )
        if history_from is not None:
            filter_clause += "AND " + time_column + ">=:history_from "
            parameters["history_from"] = history_from
        if history_to is not None:
            filter_clause += "AND " + time_column + "<:history_to "
            parameters["history_to"] = history_to
        if cursor is not None:
            if completion not in {"completed", "withdrawn"}:
                raise WorkDispatchError("work_object_lifecycle_request_invalid")
            try:
                cursor_time, cursor_id = (
                    base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
                )
                parameters.update(
                    cursor_time=datetime.fromisoformat(cursor_time), cursor_id=cursor_id
                )
            except (ValueError, UnicodeError):
                raise WorkDispatchError("work_object_lifecycle_request_invalid") from None
            filter_clause += (
                "AND ("
                + time_column
                + "<:cursor_time OR ("
                + time_column
                + '=:cursor_time AND work_object_id COLLATE "C">:cursor_id)) '
            )
        if queue == "mine_sent":
            filter_clause += "AND initiator_ai_user_id=:principal_ai_user_id "
        elif queue in {"department_unassigned", "department_review"}:
            parameters["queue_relation_status"] = (
                "department_pending"
                if queue == "department_unassigned"
                else "awaiting_department_review"
            )
            filter_clause += (
                "AND EXISTS(SELECT 1 FROM work_object_handling_relations qr "
                "WHERE qr.tenant_id=work_objects.tenant_id AND qr.work_object_id=work_objec"
                "ts.work_object_id "
                "AND qr.superseded_at IS NULL AND qr.target_kind='department' "
                "AND qr.target_department_id=ANY(CAST(:responsible_department_ids AS TEXT[])) "
                "AND qr.status=:queue_relation_status) "
            )
        elif queue == "awaiting_acceptance":
            filter_clause += (
                "AND status='awaiting_acceptance' AND initiator_ai_user_id=:principal_ai_user_id "
            )
        elif queue is not None:
            raise WorkDispatchError("work_object_lifecycle_request_invalid")
        columns, source = _observed_source(scope)
        view_clause = ""
        if oa_view == "active" and scope.principal_tenant_id == "default":
            view_clause = "AND obs_pending_state IS DISTINCT FROM 'unconfirmed' "
        elif oa_view == "unconfirmed":
            view_clause = (
                "AND obs_pending_state = 'unconfirmed' "
                if scope.principal_tenant_id == "default"
                else "AND FALSE "
            )
        rows = (
            await session.execute(
                sql_text(
                    "SELECT "
                    + columns
                    + " FROM "
                    + source
                    + " WHERE "
                    + visibility
                    + " "
                    + search_clause
                    + view_clause
                    + completion_clause
                    + filter_clause
                    + "ORDER BY "
                    + ordering
                    + " LIMIT :limit"
                ),
                parameters,
            )
        ).fetchall()
        recheck_scope(scope)
        return [_record_from_row(row) for row in rows]

    async def list_for_scope(
        self,
        scope: AuthorizedWorkObjectScope,
        *,
        search_term: str | None = None,
        limit: int = WORK_OBJECT_LIST_FETCH_LIMIT,
        completion: CompletionFilter | None = None,
        queue: str | None = None,
        history_from: datetime | None = None,
        history_to: datetime | None = None,
        cursor: str | None = None,
    ) -> list[WorkObjectRecord]:
        async with self._session_factory() as session:
            return await self._list_records(
                session,
                scope,
                search_term=search_term,
                oa_view="all",
                limit=limit,
                completion=completion,
                queue=queue,
                history_from=history_from,
                history_to=history_to,
                cursor=cursor,
            )

    async def list_with_oa_sync_for_scope(
        self,
        scope: AuthorizedWorkObjectScope,
        *,
        search_term: str | None = None,
        oa_view: OAView = "active",
        limit: int = WORK_OBJECT_LIST_FETCH_LIMIT,
        completion: CompletionFilter | None = None,
        queue: str | None = None,
        history_from: datetime | None = None,
        history_to: datetime | None = None,
        cursor: str | None = None,
    ) -> WorkObjectReadBatch:
        async with self._session_factory() as session:
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            records = await self._list_records(
                session,
                scope,
                search_term=search_term,
                oa_view=oa_view,
                limit=limit,
                completion=completion,
                queue=queue,
                history_from=history_from,
                history_to=history_to,
                cursor=cursor,
            )
            if scope.principal_tenant_id == "default":
                state = await self._sync_status(
                    session,
                    OASyncSubject(
                        tenant_id="default",
                        ai_user_id=scope.principal_ai_user_id,
                    ),
                    "pending",
                )
                view = state.to_view()
            else:
                view = OASyncStatusView(
                    status="unsupported_scope",
                    revision=0,
                    attempt_revision=0,
                    last_attempt_at=None,
                    last_success_at=None,
                    failure_code=None,
                )
            return WorkObjectReadBatch(records=records, oa_sync=view)

    async def _get_record(
        self,
        session: AsyncSession,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
    ) -> WorkObjectRecord | None:
        visibility, parameters = _visibility_predicate(scope)
        parameters["work_object_id"] = work_object_id
        columns, source = _observed_source(scope)
        row = (
            await session.execute(
                sql_text(
                    "SELECT "
                    + columns
                    + " FROM "
                    + source
                    + " WHERE work_object_id = :work_object_id "
                    "AND " + visibility
                ),
                parameters,
            )
        ).fetchone()
        recheck_scope(scope)
        return None if row is None else _record_from_row(row)

    async def get_for_scope(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
    ) -> WorkObjectRecord | None:
        async with self._session_factory() as session:
            return await self._get_record(session, work_object_id, scope)

    async def set_handling_mark_for_scope(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        mark: WorkObjectHandlingMark,
        *,
        marked_at: datetime,
    ) -> WorkObjectRecord | None:
        async with self._session_factory() as session:
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            row = (
                await session.execute(
                    sql_text(
                        "UPDATE work_objects SET handling_mark = :handling_mark, "
                        "handling_marked_by_ai_user_id = :assignee_ai_user_id, "
                        "handling_marked_at = :marked_at, updated_at = :marked_at "
                        "WHERE work_object_id = :work_object_id "
                        "AND assignee_ai_user_id = :assignee_ai_user_id "
                        "AND state_authority = 'external_snapshot' RETURNING work_object_id"
                    ),
                    {
                        "handling_mark": mark,
                        "assignee_ai_user_id": scope.principal_ai_user_id,
                        "marked_at": marked_at,
                        "work_object_id": work_object_id,
                    },
                )
            ).fetchone()
            if row is None:
                return None
            record = await self._get_record(session, work_object_id, scope)
            await session.commit()
            return record

    async def _lifecycle_event(
        self,
        session: AsyncSession,
        work_object_id: str,
        tenant_id: str,
        actor_ai_user_id: str,
        idempotency_key: UUID,
    ) -> LifecycleEventRecord | None:
        row = (
            (
                await session.execute(
                    sql_text(
                        (
                            "SELECT * FROM work_object_lifecycle_events WHERE work_o"
                            "bject_id = :id AND tenant_id = :tenant AND actor_ai_use"
                            "r_id = :actor AND idempotency_key = :key"
                        )
                    ),
                    {
                        "id": work_object_id,
                        "tenant": tenant_id,
                        "actor": actor_ai_user_id,
                        "key": idempotency_key,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else LifecycleEventRecord.model_validate(dict(row))

    async def get_lifecycle_event_for_scope(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        *,
        actor_ai_user_id: str,
        idempotency_key: UUID,
    ) -> LifecycleEventRecord | None:
        async with self._session_factory() as session:
            await load_manual_root_in_session(session, work_object_id, scope)
            row = (
                (
                    await session.execute(
                        sql_text(
                            "SELECT event_id,request_fingerprint FROM work_object_lifecycle_events "
                            "WHERE tenant_id=:tenant "
                            "AND work_object_id=:task AND actor_ai_user_id=:actor AND idemp"
                            "otency_key=:key"
                        ),
                        {
                            "tenant": scope.principal_tenant_id,
                            "task": work_object_id,
                            "actor": actor_ai_user_id,
                            "key": idempotency_key,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            if not await event_visible_in_session(session, row["event_id"], scope):
                return None
            recheck_scope(scope)
            return await load_event_in_session(session, row["event_id"])

    async def list_lifecycle_events_for_scope(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        *,
        after_version: int,
        limit: int,
    ) -> list[LifecycleEventRecord] | None:
        try:
            page = await self.get_dispatch_events(
                work_object_id, scope, after_version=after_version, limit=limit
            )
        except WorkDispatchError as exc:
            if exc.code == "work_object_not_found":
                return None
            raise
        return page.items

    async def apply_lifecycle_command_for_scope(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        *,
        actor: LifecycleActor,
        command: LifecycleCommand,
        idempotency_key: UUID,
        request_fingerprint: str,
        expected_etag: str,
        event_id: UUID,
    ) -> LifecycleMutationResult:
        return await self.command_dispatch_lifecycle(
            work_object_id,
            scope,
            command=command,
            expected_etag=expected_etag,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            event_id=event_id,
            now=datetime.now(UTC),
        )

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
                        sql_text(
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
        if any(record.dispatch_model_version is not None for record in records):
            raise WorkDispatchError("work_object_lifecycle_request_invalid")
        async with self._session_factory() as session:
            async with session.begin():
                inserted = (
                    await session.execute(
                        sql_text(
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
                                sql_text(
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
                        sql_text(
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

    async def replay_dispatch_receipt(
        self,
        receipt: DispatchReceipt,
        scope: AuthorizedWorkObjectScope,
    ) -> DispatchResult:
        from app.infra.persistence.work_object.dispatch_insert import replay_dispatch_receipt

        async with self._session_factory() as session:
            return await replay_dispatch_receipt(session, receipt, scope)

    async def create_prepared_dispatch(self, prepared: PreparedDispatch) -> DispatchResult:
        from app.infra.persistence.work_object.dispatch_insert import insert_prepared_dispatch

        try:
            async with self._session_factory() as session, session.begin():
                return await insert_prepared_dispatch(session, prepared)
        except SQLAlchemyError:
            _LOGGER.warning(
                "work_dispatch stage=persistence operation=create_prepared_dispatch "
                "code=work_object_dispatch_failed"
            )
            raise WorkDispatchError("work_object_dispatch_failed") from None

    async def get_dispatch_lifecycle(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
    ) -> LifecycleView:
        async with self._session_factory() as session:
            result = await load_lifecycle_view_in_session(session, work_object_id, scope)
            recheck_scope(scope)
            return result

    async def command_dispatch_lifecycle(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        *,
        command: LifecycleCommand,
        expected_etag: str,
        idempotency_key: UUID,
        request_fingerprint: str,
        event_id: UUID,
        now: datetime,
        replacement_relations: tuple[HandlingRelationRecord, ...] | None = None,
        replacement_department_name: str | None = None,
        legacy_conversion_relation: HandlingRelationRecord | None = None,
    ) -> LifecycleMutationResult:
        try:
            async with self._session_factory() as session, session.begin():
                record = await load_manual_root_in_session(
                    session, work_object_id, scope, for_update=True
                )
                recheck_scope(scope)
                relations = await load_relations_in_session(session, record, scope)
                virtual = (
                    legacy_relation_for_scope(record, scope)
                    if record.dispatch_model_version is None
                    else None
                )
                current = project_lifecycle(record, [virtual] if virtual else relations, scope)
                prior = (
                    (
                        await session.execute(
                            sql_text(
                                "SELECT event_id,request_fingerprint FROM "
                                "work_object_lifecycle_events WHERE tenant_id=:tenant "
                                "AND work_object_id=:task AND actor_ai_user_id=:actor AND idemp"
                                "otency_key=:key"
                            ),
                            {
                                "tenant": scope.principal_tenant_id,
                                "task": work_object_id,
                                "actor": scope.principal_ai_user_id,
                                "key": idempotency_key,
                            },
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if prior is not None:
                    if prior["request_fingerprint"] != request_fingerprint:
                        raise WorkDispatchError("idempotency_key_reused")
                    if not await event_visible_in_session(session, prior["event_id"], scope):
                        raise WorkDispatchError("work_object_action_forbidden")
                    event = await load_event_in_session(session, prior["event_id"])
                    return LifecycleMutationResult(event, True)
                if current.frozen:
                    raise WorkDispatchError("work_object_transition_invalid")
                check_etag(expected_etag, current.etag)
                if record.dispatch_model_version is None:
                    record, relations = await convert_legacy_in_session(
                        session, record, scope, now, legacy_conversion_relation
                    )
                    current = project_lifecycle(record, relations, scope)
                command_value: Any = command
                relation_id = getattr(command_value, "handling_relation_id", None)
                relation = next((r for r in relations if r.relation_id == relation_id), None)
                operation = command_value.operation
                if operation == "complete":
                    # Compatibility is only an unambiguous personal submit, never review bypass.
                    if len(relations) != 1 or relations[0].target_kind != "user":
                        raise WorkDispatchError("work_object_transition_invalid")
                    relation = relations[0]
                    operation = "submit"
                if relation_id is not None and relation is None:
                    raise WorkDispatchError("work_object_action_forbidden")
                allowed = (
                    relation_actions(relation, scope, frozen=False)
                    if relation is not None
                    else current.available_commands
                )
                if operation not in allowed:
                    raise WorkDispatchError("work_object_action_forbidden")
                root_changes: dict[str, Any] = {
                    "version": cast(int, record.version) + 1,
                    "updated_at": now,
                }
                changed_relations = list(relations)
                event_relation = relation
                relation_before = relation.status if relation else None
                visibility: Literal["shared", "department_internal"] = "shared"
                event_text = getattr(command, "text", None)
                payload: dict[str, Any] = {}
                if relation is not None:
                    changes: dict[str, Any] = {"updated_at": now}
                    if operation == "assign":
                        if relation.target_kind != "department" or not relation_head_allowed(
                            relation, scope
                        ):
                            raise WorkDispatchError("work_object_action_forbidden")
                        # Supersession revokes prior actor and private history atomically.
                        await supersede_relation(session, relation, now)
                        new_relation = relation.model_copy(
                            update={
                                "relation_id": uuid4(),
                                "assignee_directory_user_id": command_value.directory_user_id,
                                "assignee_ai_user_id": None,
                                "status": "assigned",
                                "accepted_at": None,
                                "accepted_by_ai_user_id": None,
                                "submitted_at": None,
                                "submitted_by_ai_user_id": None,
                                "submitted_result": None,
                                "reviewed_at": None,
                                "reviewed_by_ai_user_id": None,
                                "review_result": None,
                                "review_reason": None,
                                "created_at": now,
                                "updated_at": now,
                            }
                        )
                        await insert_relation_in_session(session, new_relation)
                        changed_relations = [
                            new_relation if r.relation_id == relation.relation_id else r
                            for r in relations
                        ]
                        event_relation = new_relation
                        changes = {}
                        payload = {
                            "previous_relation_id": str(relation.relation_id),
                            "replacement_relation_id": str(new_relation.relation_id),
                        }
                        visibility = "department_internal"
                    elif operation == "accept":
                        changes.update(
                            status="in_progress",
                            accepted_at=now,
                            accepted_by_ai_user_id=scope.principal_ai_user_id,
                            assignee_ai_user_id=scope.principal_ai_user_id,
                        )
                        if record.accepted_at is None:
                            root_changes.update(
                                accepted_at=now, accepted_by_ai_user_id=scope.principal_ai_user_id
                            )
                        visibility = "department_internal"
                    elif operation == "feedback":
                        visibility = "department_internal"
                    elif operation == "submit":
                        changes.update(
                            status="awaiting_department_review"
                            if relation.target_kind == "department"
                            else "submitted",
                            submitted_at=now,
                            submitted_by_ai_user_id=scope.principal_ai_user_id,
                            submitted_result=command_value.text,
                            reviewed_at=None,
                            reviewed_by_ai_user_id=None,
                            review_result=None,
                            review_reason=None,
                        )
                        visibility = (
                            "department_internal"
                            if relation.target_kind == "department"
                            else "shared"
                        )
                    elif operation == "department_review":
                        changes.update(
                            status="submitted" if command_value.approved else "in_progress",
                            reviewed_at=now,
                            reviewed_by_ai_user_id=scope.principal_ai_user_id,
                            review_result=command_value.approved,
                            review_reason=command_value.reason,
                        )
                        if not command_value.approved:
                            changes.update(
                                submitted_at=None,
                                submitted_by_ai_user_id=None,
                                submitted_result=None,
                            )
                        event_text = command_value.reason or None
                        visibility = "department_internal"
                        payload = {"approved": command_value.approved}
                        if command_value.approved:
                            from app.infra.persistence.work_object.attachments import (
                                publish_relation_results_in_session,
                            )

                            await publish_relation_results_in_session(
                                session,
                                scope=scope,
                                work_object_id=work_object_id,
                                handling_relation_id=relation.relation_id,
                                now=now,
                            )
                    if changes:
                        updated = relation.model_copy(update=changes)
                        await update_relation_in_session(session, updated)
                        changed_relations = [
                            updated if r.relation_id == updated.relation_id else r
                            for r in relations
                        ]
                        event_relation = updated
                    attachment_ids = getattr(command, "attachment_ids", [])
                    if attachment_ids:
                        from app.infra.persistence.work_object.attachments import (
                            link_attachments_in_session,
                        )

                        await link_attachments_in_session(
                            session,
                            scope=scope,
                            work_object_id=work_object_id,
                            handling_relation_id=cast(
                                HandlingRelationRecord, event_relation
                            ).relation_id,
                            attachment_ids=attachment_ids,
                            purpose="result",
                            visibility=visibility,
                            now=now,
                        )
                    if operation == "assign" and record.status == "awaiting_acceptance":
                        await invalidate_submissions(session, changed_relations, now)
                        changed_relations = await load_relations_in_session(session, record, scope)
                    if record.dispatch_mode == "independent":
                        only = changed_relations[0]
                        if only.status == "submitted":
                            root_changes["status"] = final_submission_status(record)
                        elif only.status == "awaiting_department_review":
                            root_changes["status"] = "awaiting_department_review"
                        elif only.status == "assigned" and only.target_kind == "user":
                            root_changes["status"] = "assigned"
                        elif only.status == "department_pending":
                            root_changes["status"] = "department_pending"
                        else:
                            root_changes["status"] = "in_progress"
                    else:
                        lead = next(r for r in changed_relations if r.role == "lead")
                        root_changes["status"] = (
                            "awaiting_department_review"
                            if lead.status == "awaiting_department_review"
                            else "in_progress"
                        )
                elif operation == "submit_summary":
                    if record.dispatch_mode != "collaborative" or any(
                        r.status != "submitted" for r in relations
                    ):
                        raise WorkDispatchError("work_object_transition_invalid")
                    root_changes["status"] = final_submission_status(record)
                    payload = {"submitted_relation_ids": [str(r.relation_id) for r in relations]}
                    if command_value.attachment_ids:
                        from app.infra.persistence.work_object.attachments import (
                            link_attachments_in_session,
                        )

                        await link_attachments_in_session(
                            session,
                            scope=scope,
                            work_object_id=work_object_id,
                            handling_relation_id=None,
                            attachment_ids=command_value.attachment_ids,
                            purpose="result",
                            visibility="shared",
                            now=now,
                        )
                elif operation == "confirm":
                    root_changes["status"] = "completed"
                elif operation == "return":
                    root_changes["status"] = "in_progress"
                    event_text = command_value.reason
                    await invalidate_submissions(session, relations, now)
                elif operation == "revise":
                    payload = {
                        "before": {
                            k: getattr(record, k)
                            for k in (
                                "title",
                                "requirement",
                                "receipt_requirement",
                                "completion_mode",
                            )
                        },
                        "after": command_value.model_dump(mode="json", exclude={"operation"}),
                    }
                    root_changes.update(
                        title=command_value.title,
                        requirement=command_value.requirement,
                        receipt_requirement=command_value.receipt_requirement,
                        completion_mode=command_value.completion_mode,
                    )
                    await invalidate_submissions(session, relations, now)
                    if record.status in {"awaiting_acceptance", "awaiting_department_review"}:
                        root_changes["status"] = "in_progress"
                elif operation == "withdraw":
                    root_changes.update(
                        status="withdrawn",
                        withdrawn_at=now,
                        withdrawn_by_ai_user_id=scope.principal_ai_user_id,
                    )
                    event_text = command_value.reason
                elif operation == "extend":
                    if record.due_at is not None and command_value.due_at <= record.due_at:
                        raise WorkDispatchError("work_object_transition_invalid")
                    root_changes["due_at"] = command_value.due_at
                    payload = {
                        "before_due_at": None
                        if record.due_at is None
                        else record.due_at.isoformat(),
                        "after_due_at": command_value.due_at.isoformat(),
                    }
                elif operation == "reassign":
                    if not replacement_relations:
                        raise WorkDispatchError("work_object_lifecycle_request_invalid")
                    for old in relations:
                        await supersede_relation(session, old, now)
                    for new in replacement_relations:
                        await insert_relation_in_session(session, new)
                    lead = next(r for r in replacement_relations if r.role in {"single", "lead"})
                    if lead.target_kind == "department" and replacement_department_name is None:
                        raise WorkDispatchError("dispatch_target_department_missing")
                    root_changes.update(
                        target_kind=lead.target_kind,
                        owner_department_id=lead.target_department_id,
                        assignee_directory_user_id=lead.target_directory_user_id,
                        assignee_display_name=None
                        if lead.target_kind == "user"
                        else replacement_department_name,
                        status="assigned" if lead.target_kind == "user" else "department_pending",
                        accepted_at=None,
                        accepted_by_ai_user_id=None,
                    )
                    payload = {
                        "previous_relation_ids": [str(r.relation_id) for r in relations],
                        "replacement_relation_ids": [
                            str(r.relation_id) for r in replacement_relations
                        ],
                    }
                if root_changes.get("status") == "completed":
                    root_changes.update(
                        completed_at=now, completed_by_ai_user_id=scope.principal_ai_user_id
                    )
                recheck_scope(scope)
                await update_root_in_session(session, record, root_changes)
                event = LifecycleEventRecord(
                    event_id=event_id,
                    tenant_id=scope.principal_tenant_id,
                    work_object_id=work_object_id,
                    actor_ai_user_id=scope.principal_ai_user_id,
                    operation=command_value.operation,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    from_status=cast(LifecycleStatus, record.status),
                    to_status=cast(LifecycleStatus, root_changes.get("status", record.status)),
                    result_version=cast(int, record.version) + 1,
                    occurred_at=now,
                    text=event_text,
                    event_model_version=2,
                    handling_relation_id=None
                    if event_relation is None
                    else event_relation.relation_id,
                    relation_from_status=relation_before,
                    relation_to_status=None if event_relation is None else event_relation.status,
                    payload=payload,
                    visibility=visibility,
                )
                await insert_event_in_session(session, event)
                return LifecycleMutationResult(event, False)
        except SQLAlchemyError:
            _LOGGER.warning(
                "work_dispatch stage=persistence operation=command_dispatch_lifecycle "
                "code=work_object_lifecycle_failed"
            )
            raise WorkDispatchError("work_object_lifecycle_failed") from None

    async def get_dispatch_events(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        *,
        after_version: int,
        limit: int,
    ) -> LifecycleEventsView:
        if not 1 <= limit <= 100 or after_version < 0:
            raise WorkDispatchError("work_object_lifecycle_request_invalid")
        async with self._session_factory() as session:
            await load_manual_root_in_session(session, work_object_id, scope)
            predicate, params = child_visibility_sql(
                scope,
                visibility_expr=(
                    "CASE WHEN e.event_model_version IS NULL AND (e.operation='complete' OR (wo"
                    ".dispatch_model_version IS NULL AND e.actor_ai_user_id=:principal_ai_user_"
                    "id)) THEN 'shared' ELSE COALESCE(e.visibility,'department_internal') END"
                ),
            )
            rows = (
                (
                    await session.execute(
                        sql_text(
                            "SELECT e.* FROM work_object_lifecycle_events e JOIN work_objects wo "
                            "ON wo.work_object_id=e.work_object_id AND wo.tenant_id=e.tenant_id "
                            "LEFT JOIN work_object_handling_relations r ON r.relation_id=e."
                            "handling_relation_id "
                            "AND r.work_object_id=e.work_object_id AND r.tenant_id=e.tenant_id "
                            "WHERE e.work_object_id=:task AND "
                            + predicate
                            + (
                                " AND e.result_version>:after ORDER BY e.result_version LIMIT :"
                                "limit"
                            )
                        ),
                        {
                            **params,
                            "task": work_object_id,
                            "after": after_version,
                            "limit": limit + 1,
                        },
                    )
                )
                .mappings()
                .all()
            )
            recheck_scope(scope)
            page = [LifecycleEventRecord.model_validate(dict(r)) for r in rows[:limit]]
            return LifecycleEventsView(
                items=page,
                next_after_version=page[-1].result_version if len(rows) > limit and page else None,
            )

    async def get_remark(self, work_object_id: str, scope: AuthorizedWorkObjectScope) -> RemarkView:
        async with self._session_factory() as session:
            record = await load_manual_root_in_session(session, work_object_id, scope)
            return await load_remark_in_session(session, record, scope)

    async def edit_remark(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        *,
        text: str,
        expected_etag: str,
        idempotency_key: UUID,
        request_fingerprint: str,
        now: datetime,
    ) -> RemarkMutationResult:
        async with self._session_factory() as session, session.begin():
            record = await load_manual_root_in_session(
                session, work_object_id, scope, for_update=True
            )
            recheck_scope(scope)
            current = await load_remark_in_session(session, record, scope)
            if not current.editable:
                raise WorkDispatchError("work_object_action_forbidden")
            params = {
                "tenant": scope.principal_tenant_id,
                "task": work_object_id,
                "actor": scope.principal_ai_user_id,
                "key": idempotency_key,
            }
            prior = (
                (
                    await session.execute(
                        sql_text(
                            "SELECT * FROM work_object_remark_history WHERE tenant_id=:tena"
                            "nt AND work_object_id=:task "
                            "AND actor_ai_user_id=:actor AND idempotency_key=:key"
                        ),
                        params,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if prior:
                if prior["request_fingerprint"] != request_fingerprint:
                    raise WorkDispatchError("idempotency_key_reused")
                return RemarkMutationResult(
                    RemarkView(
                        work_object_id=work_object_id,
                        text=prior["text"],
                        version=prior["remark_version"],
                        updated_by=prior["actor_ai_user_id"],
                        updated_at=prior["occurred_at"],
                        editable=True,
                    ),
                    True,
                )
            check_etag(expected_etag, current.etag)
            if len(text) > 2000:
                raise WorkDispatchError("work_object_lifecycle_request_invalid")
            values = {
                **params,
                "text": text,
                "version": current.version + 1,
                "now": now,
                "fingerprint": request_fingerprint,
            }
            await session.execute(
                sql_text(
                    "INSERT INTO work_object_remarks(tenant_id,work_object_id,text,version,"
                    "updated_by,updated_at) "
                    "VALUES(:tenant,:task,:text,:version,:actor,:now) ON CONFLICT(tenant_id"
                    ",work_object_id) "
                    "DO UPDATE SET text=EXCLUDED.text,version=EXCLUDED.version,updated_by=E"
                    "XCLUDED.updated_by,updated_at=EXCLUDED.updated_at"
                ),
                values,
            )
            await session.execute(
                sql_text(
                    "INSERT INTO work_object_remark_history(tenant_id,work_object_id,remark"
                    "_version,text,actor_ai_user_id,"
                    "occurred_at,idempotency_key,request_fingerprint) VALUES(:tenant,:task,"
                    ":version,:text,:actor,:now,:key,:fingerprint)"
                ),
                values,
            )
            return RemarkMutationResult(
                RemarkView(
                    work_object_id=work_object_id,
                    text=text,
                    version=current.version + 1,
                    updated_by=scope.principal_ai_user_id,
                    updated_at=now,
                    editable=True,
                ),
                False,
            )

    async def list_remark_history(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        *,
        before_version: int | None,
        limit: int,
    ) -> RemarkHistoryPage:
        if not 1 <= limit <= 100:
            raise WorkDispatchError("work_object_lifecycle_request_invalid")
        async with self._session_factory() as session:
            await load_manual_root_in_session(session, work_object_id, scope)
            rows = (
                (
                    await session.execute(
                        sql_text(
                            "SELECT work_object_id,remark_version,text,actor_ai_user_id,occ"
                            "urred_at "
                            "FROM work_object_remark_history WHERE tenant_id=:tenant AND wo"
                            "rk_object_id=:task "
                            "AND (CAST(:before AS BIGINT) IS NULL OR remark_version<:before) "
                            "ORDER BY remark_version DESC LIMIT :limit"
                        ),
                        {
                            "tenant": scope.principal_tenant_id,
                            "task": work_object_id,
                            "before": before_version,
                            "limit": limit + 1,
                        },
                    )
                )
                .mappings()
                .all()
            )
            recheck_scope(scope)
            page = [RemarkHistoryEntry.model_validate(dict(r)) for r in rows[:limit]]
            cursor = (
                base64.urlsafe_b64encode(str(page[-1].remark_version).encode()).decode()
                if len(rows) > limit and page
                else None
            )
            return RemarkHistoryPage(items=page, next_cursor=cursor)


_PENDING_OWNER = (
    "state_authority = 'external_snapshot' AND source_system = 'oa' "
    "AND source_kind = 'pending_workflow' AND assignee_ai_user_id = :ai_user_id"
)


def _lifecycle_representation(
    record: InternalWorkObjectRecord, actor: LifecycleActor
) -> dict[str, Any]:
    def utc(value: datetime | None) -> str | None:
        return (
            None
            if value is None
            else value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        )

    actions = compute_lifecycle_actions(record, actor)
    return {
        "work_object_id": record.work_object_id,
        "status": record.status,
        "version": record.version,
        "accepted_at": utc(record.accepted_at),
        "completed_at": utc(record.completed_at),
        "available_commands": actions,
        "unavailable_reason": None
        if actions or record.status == "completed"
        else "work_object_action_forbidden",
    }


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
    manual, parameters = root_visibility_sql(scope, "work_objects")
    return (
        "((state_authority='external_snapshot' AND assignee_ai_user_id=:principal_ai_user_id) "
        "OR (state_authority='internal' AND source_kind<>'manual_dispatch' AND version IS NULL "
        "AND assignee_ai_user_id=:principal_ai_user_id) "
        "OR (state_authority='internal' AND source_kind='manual_dispatch' AND " + manual + "))",
        parameters,
    )


def _record_from_row(row: Any) -> WorkObjectRecord:
    values = dict(row._mapping)
    pending_state = values.pop("obs_pending_state", None)
    revision = values.pop("obs_revision", None)
    seen = values.pop("obs_seen", None)
    checked = values.pop("obs_checked", None)
    if values["state_authority"] == "external_snapshot":
        values["oa_observation"] = OAObservation(
            pending_state=pending_state or "legacy_unverified",
            revision=revision or 0,
            last_seen_at=seen or values["source_fetched_at"],
            last_checked_at=checked,
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

_LOGGER = logging.getLogger(__name__)


def check_etag(expected: str, actual: str) -> None:
    if not expected:
        raise WorkDispatchError("work_object_precondition_required")
    if expected != actual:
        raise WorkDispatchError("work_object_version_conflict")


async def load_manual_root_in_session(
    session: AsyncSession,
    work_object_id: str,
    scope: AuthorizedWorkObjectScope,
    *,
    for_update: bool = False,
) -> InternalWorkObjectRecord:
    visible, params = root_visibility_sql(scope)
    row = (
        await session.execute(
            sql_text(
                "SELECT wo.* FROM work_objects wo WHERE wo.work_object_id=:task "
                "AND wo.state_authority='internal' AND wo.source_kind='manual_dispatch' AND "
                + visible
                + (" FOR UPDATE OF wo" if for_update else "")
            ),
            {**params, "task": work_object_id},
        )
    ).one_or_none()
    if row is None:
        nonmanual, nonmanual_params = _visibility_predicate(scope)
        unsupported = (
            await session.execute(
                sql_text(
                    "SELECT 1 FROM work_objects WHERE work_object_id=:task AND "
                    "(state_authority<>'internal' OR source_kind<>'manual_dispatch') AND "
                    + nonmanual
                ),
                {**nonmanual_params, "task": work_object_id},
            )
        ).scalar_one_or_none()
        if unsupported is not None:
            raise WorkDispatchError("work_object_lifecycle_unsupported")
        raise WorkDispatchError("work_object_not_found")
    return InternalWorkObjectRecord.model_validate(dict(row._mapping))


async def load_relations_in_session(
    session: AsyncSession,
    record: InternalWorkObjectRecord,
    scope: AuthorizedWorkObjectScope,
) -> list[HandlingRelationRecord]:
    private = relation_participant_sql("r")
    projected = []
    for name in HandlingRelationRecord.model_fields:
        if name == "submitted_result":
            projected.append(
                "CASE WHEN " + private + " OR r.status='submitted' THEN r.submitted_result "
                "ELSE NULL END AS submitted_result"
            )
        elif name in {"review_reason", "review_result", "reviewed_at", "reviewed_by_ai_user_id"}:
            projected.append(
                "CASE WHEN " + private + " THEN r." + name + " ELSE NULL END AS " + name
            )
        else:
            projected.append("r." + name)
    rows = (
        (
            await session.execute(
                sql_text(
                    "SELECT "
                    + ",".join(projected)
                    + " FROM work_object_handling_relations r WHERE r.tenant_id=:tenant "
                    "AND r.work_object_id=:task AND r.superseded_at IS NULL "
                    "ORDER BY r.created_at,r.relation_id"
                ),
                {
                    **scope_parameters(scope),
                    "tenant": record.tenant_id,
                    "task": record.work_object_id,
                },
            )
        )
        .mappings()
        .all()
    )
    return [HandlingRelationRecord.model_validate(dict(row)) for row in rows]


def project_lifecycle(
    record: InternalWorkObjectRecord,
    relations: list[HandlingRelationRecord],
    scope: AuthorizedWorkObjectScope,
) -> LifecycleView:
    frozen = record.status in {"completed", "withdrawn"}
    actions: list[str] = []
    if not frozen and record.initiator_ai_user_id == scope.principal_ai_user_id:
        actions.extend(["revise", "withdraw", "extend", "reassign"])
        if record.status == "awaiting_acceptance":
            actions.extend(["confirm", "return"])
    if not frozen and record.dispatch_mode == "collaborative":
        lead = next((r for r in relations if r.role == "lead"), None)
        if (
            lead
            and (
                relation_head_allowed(lead, scope)
                if lead.target_kind == "department"
                else relation_actor_allowed(lead, scope)
            )
            and all(r.status == "submitted" for r in relations)
        ):
            actions.append("submit_summary")
    views = []
    for relation in relations:
        private = relation_private_allowed(relation, scope)
        target = (
            UserDispatchTarget(
                kind="user",
                department_id=relation.target_department_id,
                directory_user_id=cast(str, relation.target_directory_user_id),
            )
            if relation.target_kind == "user"
            else DepartmentDispatchTarget(
                kind="department", department_id=relation.target_department_id
            )
        )
        views.append(
            HandlingRelationView(
                relation_id=relation.relation_id,
                target=target,
                role=relation.role,
                status=relation.status,
                assignee_directory_user_id=relation.assignee_directory_user_id if private else None,
                accepted_at=relation.accepted_at if private else None,
                submitted_at=relation.submitted_at
                if private or relation.status == "submitted"
                else None,
                submitted_result=relation.submitted_result
                if private or relation.status == "submitted"
                else None,
                reviewed_at=relation.reviewed_at if private else None,
                review_result=relation.review_result if private else None,
                review_reason=relation.review_reason if private else None,
                available_commands=relation_actions(relation, scope, frozen=frozen),
            )
        )
    return LifecycleView(
        work_object_id=record.work_object_id,
        status=cast(LifecycleStatus, record.status),
        version=cast(int, record.version),
        accepted_at=record.accepted_at,
        completed_at=record.completed_at,
        dispatch_mode=record.dispatch_mode,
        completion_mode=record.completion_mode,
        title=cast(str, record.title),
        requirement=cast(str, record.requirement),
        receipt_requirement=cast(str, record.receipt_requirement),
        due_at=record.due_at,
        frozen=frozen,
        relations=views,
        available_commands=actions,
        unavailable_reason=None
        if frozen or actions or any(v.available_commands for v in views)
        else "work_object_action_forbidden",
    )


async def load_lifecycle_view_in_session(
    session: AsyncSession,
    work_object_id: str,
    scope: AuthorizedWorkObjectScope,
    *,
    for_update: bool = False,
) -> LifecycleView:
    record = await load_manual_root_in_session(
        session, work_object_id, scope, for_update=for_update
    )
    relations = await load_relations_in_session(session, record, scope)
    if record.dispatch_model_version is None:
        virtual = legacy_relation_for_scope(record, scope)
        if virtual is not None:
            relations = [virtual]
    return project_lifecycle(record, relations, scope)


async def load_remark_in_session(
    session: AsyncSession, record: InternalWorkObjectRecord, scope: AuthorizedWorkObjectScope
) -> RemarkView:
    relations = await load_relations_in_session(session, record, scope)
    editable = record.initiator_ai_user_id == scope.principal_ai_user_id or any(
        relation_private_allowed(r, scope) for r in relations
    )
    if record.dispatch_model_version is None:
        editable = (
            editable
            or record.accepted_by_ai_user_id == scope.principal_ai_user_id
            or (
                scope.principal_directory_user_id is not None
                and scope.principal_directory_user_id == record.assignee_directory_user_id
            )
        )
    row = (
        (
            await session.execute(
                sql_text(
                    (
                        "SELECT * FROM work_object_remarks WHERE tenant_id=:tenant AND work_obj"
                        "ect_id=:task"
                    )
                ),
                {"tenant": scope.principal_tenant_id, "task": record.work_object_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    recheck_scope(scope)
    return RemarkView(
        work_object_id=record.work_object_id,
        text="" if row is None else row["text"],
        version=0 if row is None else row["version"],
        updated_by=None if row is None else row["updated_by"],
        updated_at=None if row is None else row["updated_at"],
        editable=editable,
    )


async def insert_relation_in_session(
    session: AsyncSession, relation: HandlingRelationRecord
) -> None:
    values = relation.model_dump()
    columns = ",".join(values)
    await session.execute(
        sql_text(
            "INSERT INTO work_object_handling_relations("
            + columns
            + ") VALUES("
            + ",".join(":" + k for k in values)
            + ")"
        ),
        values,
    )


async def update_relation_in_session(
    session: AsyncSession, relation: HandlingRelationRecord
) -> None:
    values = relation.model_dump()
    immutable = {"relation_id", "tenant_id", "work_object_id", "created_at"}
    await session.execute(
        sql_text(
            "UPDATE work_object_handling_relations SET "
            + ",".join(k + "=:" + k for k in values if k not in immutable)
            + (
                " WHERE tenant_id=:tenant_id AND work_object_id=:work_object_id AND relation_id"
                "=:relation_id"
            )
        ),
        values,
    )


async def supersede_relation(
    session: AsyncSession, relation: HandlingRelationRecord, now: datetime
) -> None:
    # Do not rewrite a historical relation's private result/review while superseding it.
    await session.execute(
        sql_text(
            "UPDATE work_object_handling_relations SET superseded_at=:now,updated_at=:now "
            "WHERE tenant_id=:tenant AND work_object_id=:task AND relation_id=:relation"
        ),
        {
            "now": now,
            "tenant": relation.tenant_id,
            "task": relation.work_object_id,
            "relation": relation.relation_id,
        },
    )


async def invalidate_submissions(
    session: AsyncSession, relations: list[HandlingRelationRecord], now: datetime
) -> None:
    for relation in relations:
        if relation.status in {"awaiting_department_review", "submitted"}:
            await update_relation_in_session(
                session,
                relation.model_copy(
                    update={
                        "status": "in_progress" if relation.accepted_at else "assigned",
                        "submitted_at": None,
                        "submitted_by_ai_user_id": None,
                        "submitted_result": None,
                        "reviewed_at": None,
                        "reviewed_by_ai_user_id": None,
                        "review_result": None,
                        "review_reason": None,
                        "updated_at": now,
                    }
                ),
            )


async def update_root_in_session(
    session: AsyncSession, record: InternalWorkObjectRecord, changes: dict[str, Any]
) -> None:
    await session.execute(
        sql_text(
            "UPDATE work_objects SET "
            + ",".join(k + "=:" + k for k in changes)
            + " WHERE tenant_id=:tenant AND work_object_id=:task"
        ),
        {**changes, "tenant": record.tenant_id, "task": record.work_object_id},
    )


async def insert_event_in_session(session: AsyncSession, event: LifecycleEventRecord) -> None:
    values = event.model_dump()
    await session.execute(
        sql_text(
            "INSERT INTO work_object_lifecycle_events("
            + ",".join(values)
            + ") VALUES("
            + ",".join(":" + k for k in values)
            + ")"
        ).bindparams(bindparam("payload", type_=JSONB)),
        values,
    )


async def event_visible_in_session(
    session: AsyncSession, event_id: UUID, scope: AuthorizedWorkObjectScope
) -> bool:
    predicate, params = child_visibility_sql(
        scope,
        visibility_expr=(
            "CASE WHEN e.event_model_version IS NULL AND (e.operation='complete' OR (wo.dispatc"
            "h_model_version IS NULL AND e.actor_ai_user_id=:principal_ai_user_id)) THEN 'share"
            "d' ELSE COALESCE(e.visibility,'department_internal') END"
        ),
    )
    visible = (
        await session.execute(
            sql_text(
                "SELECT e.event_id FROM work_object_lifecycle_events e JOIN work_objects wo "
                "ON wo.work_object_id=e.work_object_id AND wo.tenant_id=e.tenant_id "
                "LEFT JOIN work_object_handling_relations r ON r.relation_id=e.handling_rel"
                "ation_id "
                "AND r.work_object_id=e.work_object_id AND r.tenant_id=e.tenant_id "
                "WHERE e.event_id=:event AND " + predicate
            ),
            {**params, "event": event_id},
        )
    ).scalar_one_or_none()
    return visible is not None


async def load_event_in_session(session: AsyncSession, event_id: UUID) -> LifecycleEventRecord:
    row = (
        (
            await session.execute(
                sql_text("SELECT * FROM work_object_lifecycle_events WHERE event_id=:event"),
                {"event": event_id},
            )
        )
        .mappings()
        .one()
    )
    return LifecycleEventRecord.model_validate(dict(row))


def final_submission_status(record: InternalWorkObjectRecord) -> str:
    return "completed" if record.completion_mode == "submit_completes" else "awaiting_acceptance"


async def convert_legacy_in_session(
    session: AsyncSession,
    record: InternalWorkObjectRecord,
    scope: AuthorizedWorkObjectScope,
    now: datetime,
    prepared_relation: HandlingRelationRecord | None = None,
) -> tuple[InternalWorkObjectRecord, list[HandlingRelationRecord]]:
    # The application resolves old target/accepted identity from this request's fresh directory.
    relation = prepared_relation or legacy_relation_for_scope(record, scope)
    if relation is None:
        raise WorkDispatchError("work_object_legacy_conversion_blocked")
    await insert_relation_in_session(session, relation)
    changes = {
        "dispatch_model_version": 2,
        "dispatch_mode": "independent",
        "completion_mode": "submit_completes",
    }
    await update_root_in_session(session, record, changes)
    return record.model_copy(update=changes), [relation]


def legacy_relation_for_scope(
    record: InternalWorkObjectRecord,
    scope: AuthorizedWorkObjectScope,
) -> HandlingRelationRecord | None:
    assignee = record.assignee_directory_user_id
    if record.target_kind == "department":
        if (
            record.accepted_by_ai_user_id != scope.principal_ai_user_id
            or scope.principal_department_id != record.owner_department_id
        ):
            return None
        assignee = scope.principal_directory_user_id
    if not assignee or scope.principal_directory_user_id != assignee:
        return None
    if (
        record.accepted_by_ai_user_id is not None
        and record.accepted_by_ai_user_id != scope.principal_ai_user_id
    ):
        return None
    return HandlingRelationRecord(
        relation_id=uuid5(
            NAMESPACE_URL, "eternalai:legacy:" + str(record.tenant_id) + ":" + record.work_object_id
        ),
        tenant_id=cast(str, record.tenant_id),
        work_object_id=record.work_object_id,
        target_kind=cast(Literal["user", "department"], record.target_kind),
        target_department_id=cast(str, record.owner_department_id),
        target_directory_user_id=record.assignee_directory_user_id
        if record.target_kind == "user"
        else None,
        role="single",
        assignee_directory_user_id=assignee,
        assignee_ai_user_id=record.accepted_by_ai_user_id,
        status="in_progress" if record.accepted_at else "assigned",
        accepted_at=record.accepted_at,
        accepted_by_ai_user_id=record.accepted_by_ai_user_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
