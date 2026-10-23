"""Rule history and occurrence transactions around the ordinary dispatch insert seam."""

from __future__ import annotations

import base64
import json
import logging
from datetime import date, datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.persistence.work_object.attachments import template_attachments_in_session
from app.infra.persistence.work_object.authorization import recheck_scope, root_visibility_sql
from app.infra.persistence.work_object.dispatch_insert import insert_prepared_dispatch
from app.ports.work_object_dispatch import DispatchSubject, WorkDispatchError, fingerprint
from app.ports.work_object_recurrence import (
    GenerationSummary,
    PrepareOccurrence,
    SeriesHistoryEntry,
    SeriesHistoryPage,
    SeriesMutationResult,
    SeriesOccurrenceItem,
    SeriesOccurrencePage,
    SeriesOccurrenceView,
    SeriesPage,
    SeriesRequest,
    SeriesView,
    next_scheduled,
    occurrence_request,
)
from app.ports.work_object_scope import AuthorizedWorkObjectScope

_LOGGER = logging.getLogger(__name__)
_REQUEST_FIELDS = set(SeriesRequest.model_fields)
_BLOCKED_CODES = frozenset(
    {
        "organization_directory_missing",
        "organization_directory_stale",
        "directory_membership_missing",
        "directory_membership_ambiguous",
        "dispatch_target_department_missing",
        "dispatch_target_membership_missing",
        "dispatch_target_membership_ambiguous",
        "dispatch_department_head_missing",
        "dispatch_department_head_ambiguous",
        "cross_department_dispatch_denied",
        "not_department_head",
        "work_object_action_forbidden",
        "work_object_attachment_invalid",
        "work_object_attachment_limit_exceeded",
        "work_object_series_local_time_invalid",
    }
)


def _params(subject: DispatchSubject, series_id: UUID | None = None) -> dict[str, Any]:
    return {"tenant": subject.tenant_id, "actor": subject.ai_user_id, "series": series_id}


def _request(view: SeriesView) -> SeriesRequest:
    return SeriesRequest.model_validate(view.model_dump(include=_REQUEST_FIELDS))


def _encode_cursor(values: list[Any]) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps(values, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def _decode_cursor(cursor: str | None, count: int) -> list[Any] | None:
    if cursor is None:
        return None
    try:
        values = json.loads(
            base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        )
        if not isinstance(values, list) or len(values) != count:
            raise ValueError
        return values
    except (ValueError, UnicodeDecodeError) as exc:
        raise WorkDispatchError("work_object_series_request_invalid") from exc


def _limit(limit: int) -> None:
    if not 1 <= limit <= 100:
        raise WorkDispatchError("work_object_series_request_invalid")


async def _owned(
    session: AsyncSession, subject: DispatchSubject, series_id: UUID, *, lock: bool = False
) -> Any:
    row = (
        (
            await session.execute(
                text(
                    "SELECT * FROM work_object_series WHERE tenant_id=:tenant "
                    "AND creator_ai_user_id=:actor "
                    "AND series_id=:series" + (" FOR UPDATE" if lock else "")
                ),
                _params(subject, series_id),
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise WorkDispatchError("work_object_series_not_found")
    return row


async def _view(session: AsyncSession, row: Any) -> SeriesView:
    attachments = await template_attachments_in_session(
        session,
        tenant_id=row["tenant_id"],
        owner_ai_user_id=row["creator_ai_user_id"],
        attachment_ids=[UUID(value) for value in row["template"].get("attachment_ids", [])],
    )
    values = {name: row[name] for name in _REQUEST_FIELDS}
    values.update(
        series_id=row["series_id"],
        version=row["current_version"],
        next_due_at=row["next_due_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        attachments=[item.model_dump(mode="json") for item in attachments],
    )
    return SeriesView.model_validate_json(json.dumps(values, default=str))


async def _record_version(
    session: AsyncSession,
    subject: DispatchSubject,
    view: SeriesView,
    *,
    operation: str,
    key: UUID,
    fp: str,
    now: datetime,
) -> None:
    await session.execute(
        text(
            "INSERT INTO work_object_series_versions (tenant_id,series_id,version,"
            "operation,actor_ai_user_id,"
            "occurred_at,snapshot,idempotency_key,request_fingerprint) "
            "VALUES (:tenant,:series,:version,:operation,:actor,:now,:snapshot,:key,:fp)"
        ).bindparams(bindparam("snapshot", type_=JSONB)),
        {
            **_params(subject, view.series_id),
            "version": view.version,
            "operation": operation,
            "now": now,
            "snapshot": view.model_dump(mode="json"),
            "key": key,
            "fp": fp,
        },
    )


async def _replay(
    session: AsyncSession,
    subject: DispatchSubject,
    series_id: UUID,
    *,
    operation: str,
    key: UUID,
    fp: str,
) -> SeriesMutationResult | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT operation,request_fingerprint,snapshot "
                    "FROM work_object_series_versions WHERE tenant_id=:tenant "
                    "AND series_id=:series AND actor_ai_user_id=:actor AND idempotency_key=:key"
                ),
                {**_params(subject, series_id), "key": key},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    if row["operation"] != operation or row["request_fingerprint"] != fp:
        raise WorkDispatchError("idempotency_key_reused")
    # The caller has already locked and reauthorized the owned rule.
    return SeriesMutationResult(
        series=SeriesView.model_validate_json(json.dumps(row["snapshot"])), replayed=True
    )


class PostgreSQLWorkObjectSeriesStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def series_id_for_task(
        self,
        scope: AuthorizedWorkObjectScope,
        work_object_id: str,
    ) -> UUID | None:
        from app.infra.persistence.work_object.postgresql import load_manual_root_in_session

        try:
            async with self._session_factory() as session:
                await load_manual_root_in_session(session, work_object_id, scope)
                result = (
                    await session.execute(
                        text(
                            "SELECT o.series_id FROM work_object_series_occurrence_items oi "
                            "JOIN work_object_series_occurrences o ON o.tenant_id=oi.tenant_id "
                            "AND o.occurrence_id=oi.occurrence_id WHERE oi.tenant_id=:tenant "
                            "AND oi.work_object_id=:task"
                        ),
                        {"tenant": scope.principal_tenant_id, "task": work_object_id},
                    )
                ).scalar_one_or_none()
                recheck_scope(scope)
                return result
        except SQLAlchemyError as exc:
            raise WorkDispatchError("work_object_store_unavailable") from exc

    async def create(
        self, subject: DispatchSubject, request: SeriesRequest, idempotency_key: UUID, now: datetime
    ) -> SeriesMutationResult:
        if not subject.directory_user_id:
            raise WorkDispatchError("directory_membership_missing")
        series_id = uuid5(
            NAMESPACE_URL, json.dumps([subject.tenant_id, subject.ai_user_id, str(idempotency_key)])
        )
        fp = fingerprint({"operation": "create", "request": request.model_dump(mode="json")})
        try:
            async with self._session_factory() as session, session.begin():
                await template_attachments_in_session(
                    session,
                    tenant_id=subject.tenant_id,
                    owner_ai_user_id=subject.ai_user_id,
                    attachment_ids=request.template.attachment_ids,
                )
                values = request.model_dump(mode="python")
                values.update(
                    _params(subject, series_id),
                    directory=subject.directory_user_id,
                    now=now,
                    next_due=next_scheduled(request, now, inclusive=True)
                    if request.enabled
                    else None,
                    template=request.template.model_dump(mode="json"),
                    deadline_spec=request.deadline_spec.model_dump(mode="json"),
                )
                inserted = (
                    await session.execute(
                        text(
                            "INSERT INTO work_object_series (series_id,tenant_id,"
                            "creator_ai_user_id,"
                            "creator_directory_user_id,enabled,current_version,weekdays,local_time,"
                            "timezone,start_date,deadline_spec,template,next_due_at,created_at,"
                            "updated_at) VALUES (:series,:tenant,:actor,:directory,:enabled,1,"
                            ":weekdays,:local_time,"
                            ":timezone,:start_date,:deadline_spec,:template,:next_due,:now,:now) "
                            "ON CONFLICT (tenant_id,series_id) DO NOTHING RETURNING series_id"
                        ).bindparams(
                            bindparam("weekdays", type_=JSONB),
                            bindparam("deadline_spec", type_=JSONB),
                            bindparam("template", type_=JSONB),
                        ),
                        values,
                    )
                ).scalar_one_or_none()
                row = await _owned(session, subject, series_id, lock=True)
                if inserted is None:
                    replay = await _replay(
                        session, subject, series_id, operation="create", key=idempotency_key, fp=fp
                    )
                    if replay is None:
                        raise WorkDispatchError("idempotency_key_reused")
                    return replay
                view = await _view(session, row)
                await _record_version(
                    session, subject, view, operation="create", key=idempotency_key, fp=fp, now=now
                )
                return SeriesMutationResult(series=view, replayed=False)
        except SQLAlchemyError as exc:
            _LOGGER.error("work_series stage=create_failed code=work_object_store_unavailable")
            raise WorkDispatchError("work_object_store_unavailable") from exc

    async def get(self, subject: DispatchSubject, series_id: UUID) -> SeriesView:
        try:
            async with self._session_factory() as session:
                return await _view(session, await _owned(session, subject, series_id))
        except SQLAlchemyError as exc:
            raise WorkDispatchError("work_object_store_unavailable") from exc

    async def list(self, subject: DispatchSubject, *, cursor: str | None, limit: int) -> SeriesPage:
        _limit(limit)
        last = _decode_cursor(cursor, 2)
        try:
            last_time = datetime.fromisoformat(last[0]) if last else None
            last_id = UUID(last[1]) if last else None
        except (ValueError, TypeError) as exc:
            raise WorkDispatchError("work_object_series_request_invalid") from exc
        try:
            async with self._session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT * FROM work_object_series WHERE tenant_id=:tenant "
                                "AND creator_ai_user_id=:actor "
                                + (
                                    "AND (created_at,series_id)<(:last_time,:last_id) "
                                    if last
                                    else ""
                                )
                                + "ORDER BY created_at DESC,series_id DESC LIMIT :limit"
                            ),
                            {
                                **_params(subject),
                                "last_time": last_time,
                                "last_id": last_id,
                                "limit": limit + 1,
                            },
                        )
                    )
                    .mappings()
                    .all()
                )
                items = [await _view(session, row) for row in rows[:limit]]
                next_cursor = (
                    _encode_cursor([items[-1].created_at.isoformat(), str(items[-1].series_id)])
                    if len(rows) > limit
                    else None
                )
                return SeriesPage(items=items, next_cursor=next_cursor)
        except SQLAlchemyError as exc:
            raise WorkDispatchError("work_object_store_unavailable") from exc

    async def update(
        self,
        subject: DispatchSubject,
        series_id: UUID,
        request: SeriesRequest,
        if_match: str,
        idempotency_key: UUID,
        now: datetime,
    ) -> SeriesMutationResult:
        return await self._change(
            subject,
            series_id,
            request=request,
            enabled=None,
            if_match=if_match,
            key=idempotency_key,
            now=now,
        )

    async def set_enabled(
        self,
        subject: DispatchSubject,
        series_id: UUID,
        enabled: bool,
        if_match: str,
        idempotency_key: UUID,
        now: datetime,
    ) -> SeriesMutationResult:
        return await self._change(
            subject,
            series_id,
            request=None,
            enabled=enabled,
            if_match=if_match,
            key=idempotency_key,
            now=now,
        )

    async def _change(
        self,
        subject: DispatchSubject,
        series_id: UUID,
        *,
        request: SeriesRequest | None,
        enabled: bool | None,
        if_match: str,
        key: UUID,
        now: datetime,
    ) -> SeriesMutationResult:
        operation: Literal["update", "enable", "disable"] = (
            "update" if request is not None else "enable" if enabled else "disable"
        )
        fp = fingerprint(
            {
                "operation": operation,
                "series_id": str(series_id),
                "request": request.model_dump(mode="json") if request is not None else enabled,
            }
        )
        try:
            async with self._session_factory() as session, session.begin():
                old = await _view(session, await _owned(session, subject, series_id, lock=True))
                replay = await _replay(
                    session, subject, series_id, operation=operation, key=key, fp=fp
                )
                if replay is not None:
                    return replay
                if not if_match:
                    raise WorkDispatchError("precondition_required")
                if if_match != old.etag:
                    raise WorkDispatchError("version_conflict")
                if request is None:
                    request = _request(old).model_copy(update={"enabled": enabled})
                await template_attachments_in_session(
                    session,
                    tenant_id=subject.tenant_id,
                    owner_ai_user_id=subject.ai_user_id,
                    attachment_ids=request.template.attachment_ids,
                )
                next_due = (
                    old.next_due_at
                    if operation == "enable" and old.enabled
                    else next_scheduled(request, now)
                    if request.enabled
                    else None
                )
                values = request.model_dump(mode="python")
                values.update(
                    _params(subject, series_id),
                    now=now,
                    next_due=next_due,
                    template=request.template.model_dump(mode="json"),
                    deadline_spec=request.deadline_spec.model_dump(mode="json"),
                )
                row = (
                    (
                        await session.execute(
                            text(
                                "UPDATE work_object_series SET enabled=:enabled,"
                                "current_version=current_version+1,weekdays=:weekdays,"
                                "local_time=:local_time,timezone=:timezone,start_date=:start_date,"
                                "deadline_spec=:deadline_spec,"
                                "template=:template,next_due_at=:next_due,updated_at=:now "
                                "WHERE tenant_id=:tenant AND creator_ai_user_id=:actor "
                                "AND series_id=:series RETURNING *"
                            ).bindparams(
                                bindparam("weekdays", type_=JSONB),
                                bindparam("deadline_spec", type_=JSONB),
                                bindparam("template", type_=JSONB),
                            ),
                            values,
                        )
                    )
                    .mappings()
                    .one()
                )
                view = await _view(session, row)
                await _record_version(
                    session, subject, view, operation=operation, key=key, fp=fp, now=now
                )
                return SeriesMutationResult(series=view, replayed=False)
        except SQLAlchemyError as exc:
            _LOGGER.error(
                "work_series stage=change_failed series_id=%s code=work_object_store_unavailable",
                series_id,
            )
            raise WorkDispatchError("work_object_store_unavailable") from exc

    async def history(
        self, subject: DispatchSubject, series_id: UUID, *, cursor: str | None, limit: int
    ) -> SeriesHistoryPage:
        _limit(limit)
        last = _decode_cursor(cursor, 1)
        if last is not None and (not isinstance(last[0], int) or last[0] < 1):
            raise WorkDispatchError("work_object_series_request_invalid")
        try:
            async with self._session_factory() as session:
                await _owned(session, subject, series_id)
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT version,operation,occurred_at,snapshot "
                                "FROM work_object_series_versions "
                                "WHERE tenant_id=:tenant AND series_id=:series "
                                + ("AND version<:last " if last else "")
                                + "ORDER BY version DESC LIMIT :limit"
                            ),
                            {
                                **_params(subject, series_id),
                                "last": last[0] if last else None,
                                "limit": limit + 1,
                            },
                        )
                    )
                    .mappings()
                    .all()
                )
                items = [
                    SeriesHistoryEntry(
                        version=row["version"],
                        operation=row["operation"],
                        occurred_at=row["occurred_at"],
                        snapshot=SeriesView.model_validate_json(json.dumps(row["snapshot"])),
                    )
                    for row in rows[:limit]
                ]
                return SeriesHistoryPage(
                    items=items,
                    next_cursor=_encode_cursor([items[-1].version]) if len(rows) > limit else None,
                )
        except SQLAlchemyError as exc:
            raise WorkDispatchError("work_object_store_unavailable") from exc

    async def occurrences(
        self,
        subject: DispatchSubject,
        scope: AuthorizedWorkObjectScope,
        series_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> SeriesOccurrencePage:
        _limit(limit)
        last = _decode_cursor(cursor, 2)
        try:
            last_time = datetime.fromisoformat(last[0]) if last else None
            last_id = UUID(last[1]) if last else None
        except (ValueError, TypeError) as exc:
            raise WorkDispatchError("work_object_series_request_invalid") from exc
        predicate, auth = root_visibility_sql(scope)
        try:
            async with self._session_factory() as session:
                owner = (
                    await session.execute(
                        text(
                            "SELECT creator_ai_user_id FROM work_object_series "
                            "WHERE tenant_id=:tenant AND series_id=:series"
                        ),
                        _params(subject, series_id),
                    )
                ).scalar_one_or_none()
                is_creator = owner == subject.ai_user_id
                visible_occurrence = (
                    "EXISTS (SELECT 1 FROM work_object_series_occurrence_items oi "
                    "JOIN work_objects wo ON wo.tenant_id=oi.tenant_id "
                    "AND wo.work_object_id=oi.work_object_id WHERE oi.tenant_id=o.tenant_id "
                    f"AND oi.occurrence_id=o.occurrence_id AND {predicate})"
                )
                if owner is None:
                    raise WorkDispatchError("work_object_series_not_found")
                if not is_creator:
                    accessible = (
                        await session.execute(
                            text(
                                "SELECT 1 FROM work_object_series_occurrences o "
                                "WHERE o.tenant_id=:tenant AND o.series_id=:series "
                                f"AND {visible_occurrence} LIMIT 1"
                            ),
                            {**auth, **_params(subject, series_id)},
                        )
                    ).scalar_one_or_none()
                    if accessible is None:
                        raise WorkDispatchError("work_object_series_not_found")
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT o.* FROM work_object_series_occurrences o "
                                "WHERE o.tenant_id=:tenant AND o.series_id=:series "
                                + (f"AND {visible_occurrence} " if not is_creator else "")
                                + (
                                    "AND (scheduled_at,occurrence_id)<(:last_time,:last_id) "
                                    if last
                                    else ""
                                )
                                + "ORDER BY scheduled_at DESC,occurrence_id DESC LIMIT :limit"
                            ),
                            {
                                **auth,
                                **_params(subject, series_id),
                                "last_time": last_time,
                                "last_id": last_id,
                                "limit": limit + 1,
                            },
                        )
                    )
                    .mappings()
                    .all()
                )
                items: list[SeriesOccurrenceView] = []
                for row in rows[:limit]:
                    tasks = (
                        (
                            await session.execute(
                                text(
                                    "SELECT wo.work_object_id,wo.title,wo.status,wo.due_at "
                                    "FROM work_object_series_occurrence_items oi "
                                    "JOIN work_objects wo "
                                    "ON wo.tenant_id=oi.tenant_id "
                                    "AND wo.work_object_id=oi.work_object_id "
                                    "WHERE oi.tenant_id=:tenant "
                                    f"AND oi.occurrence_id=:occurrence AND {predicate} "
                                    "ORDER BY wo.work_object_id"
                                ),
                                {
                                    **auth,
                                    "tenant": subject.tenant_id,
                                    "occurrence": row["occurrence_id"],
                                },
                            )
                        )
                        .mappings()
                        .all()
                    )
                    visible = [SeriesOccurrenceItem.model_validate(dict(task)) for task in tasks]
                    items.append(
                        SeriesOccurrenceView(
                            occurrence_id=row["occurrence_id"],
                            scheduled_at=row["scheduled_at"],
                            series_version=row["series_version"],
                            state=row["state"],
                            blocked_code=row["blocked_code"],
                            generated_at=row["generated_at"],
                            items=visible,
                            visible_count=len(visible),
                        )
                    )
                recheck_scope(scope)
                return SeriesOccurrencePage(
                    items=items,
                    next_cursor=_encode_cursor(
                        [items[-1].scheduled_at.isoformat(), str(items[-1].occurrence_id)]
                    )
                    if len(rows) > limit
                    else None,
                )
        except SQLAlchemyError as exc:
            raise WorkDispatchError("work_object_store_unavailable") from exc

    async def generate_due(self, now: datetime, prepare: PrepareOccurrence) -> GenerationSummary:
        generated = blocked = 0
        failed_series: set[UUID] = set()
        while True:
            row = None
            try:
                async with self._session_factory() as session, session.begin():
                    row = (
                        (
                            await session.execute(
                                text(
                                    "SELECT * FROM work_object_series "
                                    "WHERE enabled AND next_due_at<=:now "
                                    "AND NOT(series_id=ANY(CAST(:failed_series AS UUID[]))) "
                                    "ORDER BY next_due_at,series_id LIMIT 1 FOR UPDATE SKIP LOCKED"
                                ),
                                {"now": now, "failed_series": list(failed_series)},
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if row is None:
                        break
                    view = await _view(session, row)
                    scheduled = row["next_due_at"]
                    subject = DispatchSubject(
                        row["tenant_id"],
                        row["creator_ai_user_id"],
                        row["creator_directory_user_id"],
                    )
                    occurrence_id = uuid4()
                    existing = (
                        await session.execute(
                            text(
                                "SELECT occurrence_id FROM work_object_series_occurrences "
                                "WHERE tenant_id=:tenant "
                                "AND series_id=:series AND scheduled_at=:scheduled"
                            ),
                            {
                                "tenant": subject.tenant_id,
                                "series": view.series_id,
                                "scheduled": scheduled,
                            },
                        )
                    ).scalar_one_or_none()
                    if existing is None:
                        code: str | None = None
                        prepared = None
                        try:
                            request = occurrence_request(_request(view), scheduled)
                            key = uuid5(view.series_id, scheduled.isoformat())
                            prepared = await prepare(subject, request, key, scheduled)
                        except WorkDispatchError as exc:
                            if exc.code not in _BLOCKED_CODES:
                                raise
                            code = exc.code
                        # Occurrence, tasks, attachments and progress share this outer transaction.
                        await session.execute(
                            text(
                                "INSERT INTO work_object_series_occurrences "
                                "(occurrence_id,tenant_id,series_id,scheduled_at,"
                                "series_version,state,blocked_code,generated_at) "
                                "VALUES (:id,:tenant,:series,:scheduled,:version,"
                                ":state,:code,:generated_at)"
                            ),
                            {
                                "id": occurrence_id,
                                "tenant": subject.tenant_id,
                                "series": view.series_id,
                                "scheduled": scheduled,
                                "version": view.version,
                                "state": "blocked" if code else "generated",
                                "code": code,
                                "generated_at": None if code else now,
                            },
                        )
                        if prepared is not None and code is None:
                            try:
                                # Authorization can expire during a long insert. Roll back the
                                # entire candidate, then persist only this occurrence's blockage.
                                async with session.begin_nested():
                                    result = await insert_prepared_dispatch(session, prepared)
                                    for item in result.items:
                                        await session.execute(
                                            text(
                                                "INSERT INTO work_object_series_occurrence_items "
                                                "(tenant_id,occurrence_id,work_object_id) "
                                                "VALUES (:tenant,:occurrence,:task)"
                                            ),
                                            {
                                                "tenant": subject.tenant_id,
                                                "occurrence": occurrence_id,
                                                "task": item["work_object_id"],
                                            },
                                        )
                            except WorkDispatchError as exc:
                                if exc.code not in _BLOCKED_CODES:
                                    raise
                                code = exc.code
                                await session.execute(
                                    text(
                                        "UPDATE work_object_series_occurrences SET state='blocked',"
                                        "blocked_code=:code,generated_at=NULL "
                                        "WHERE tenant_id=:tenant "
                                        "AND occurrence_id=:occurrence"
                                    ),
                                    {
                                        "code": code,
                                        "tenant": subject.tenant_id,
                                        "occurrence": occurrence_id,
                                    },
                                )
                    # Advance separately: a future calendar gap is not this period's failure.
                    skipped_dates: list[date] = []
                    next_due = next_scheduled(
                        _request(view),
                        scheduled,
                        skip_nonexistent=True,
                        skipped_dates=skipped_dates,
                    )
                    await session.execute(
                        text(
                            "UPDATE work_object_series SET next_due_at=:next_due "
                            "WHERE tenant_id=:tenant AND series_id=:series"
                        ),
                        {
                            "tenant": subject.tenant_id,
                            "series": view.series_id,
                            "next_due": next_due,
                        },
                    )
                for skipped_day in skipped_dates:
                    _LOGGER.warning(
                        "work_series stage=calendar_date_skipped series_id=%s local_date=%s "
                        "code=work_object_series_local_time_invalid",
                        view.series_id,
                        skipped_day.isoformat(),
                    )
                if existing is None:
                    if code is not None:
                        blocked += 1
                    else:
                        generated += 1
                    _LOGGER.info(
                        "work_series stage=occurrence_committed series_id=%s occurrence_id=%s "
                        "state=%s code=%s",
                        view.series_id,
                        occurrence_id,
                        "blocked" if code else "generated",
                        code,
                    )
            except SQLAlchemyError as exc:
                _LOGGER.error(
                    "work_series stage=transaction_rolled_back series_id=%s "
                    "code=work_object_store_unavailable",
                    row["series_id"] if row else None,
                )
                if row is None:
                    raise WorkDispatchError("work_object_store_unavailable") from exc
                failed_series.add(row["series_id"])
            except WorkDispatchError as exc:
                _LOGGER.error(
                    "work_series stage=transaction_rolled_back series_id=%s code=%s",
                    row["series_id"] if row else None,
                    exc.code,
                )
                if row is None:
                    raise
                failed_series.add(row["series_id"])
            except Exception as exc:
                # Unknown defects do not create business blocked facts or hold up other rules.
                _LOGGER.error(
                    "work_series stage=series_defect_rolled_back series_id=%s error_type=%s",
                    row["series_id"] if row else None,
                    type(exc).__name__,
                )
                if row is None:
                    raise
                failed_series.add(row["series_id"])
        return GenerationSummary(generated=generated, blocked=blocked)
