"""PostgreSQL private attachments; every task owns its immutable content copy."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.persistence.work_object.authorization import (
    child_visibility_sql,
    recheck_scope,
    relation_participant_sql,
    root_visibility_sql,
)
from app.ports.work_object_attachment import (
    MAX_ATTACHMENTS_PER_REQUEST,
    MAX_TASK_ATTACHMENT_BYTES,
    AttachmentDownload,
    AttachmentLinkRequest,
    AttachmentMutationResult,
    AttachmentPurpose,
    AttachmentRecord,
    AttachmentView,
    AttachmentVisibility,
)
from app.ports.work_object_dispatch import PreparedDispatch, WorkDispatchError, fingerprint
from app.ports.work_object_lifecycle import LifecycleView
from app.ports.work_object_scope import AuthorizedWorkObjectScope

_LOGGER = logging.getLogger(__name__)
_META = (
    "attachment_id",
    "tenant_id",
    "owner_ai_user_id",
    "work_object_id",
    "handling_relation_id",
    "purpose",
    "visibility",
    "filename",
    "media_type",
    "size_bytes",
    "sha256",
    "version",
    "created_at",
    "linked_at",
    "detached_at",
    "idempotency_key",
    "request_fingerprint",
)
_SELECT_META = ", ".join("a." + name for name in _META)


def _record(row: Any) -> AttachmentRecord:
    return AttachmentRecord.model_validate(dict(row))


async def template_attachments_in_session(
    session: AsyncSession,
    *,
    tenant_id: str,
    owner_ai_user_id: str,
    attachment_ids: list[UUID] | tuple[UUID, ...],
) -> list[AttachmentView]:
    """Only immutable, owner-held staging sources may become template references."""
    if len(attachment_ids) > MAX_ATTACHMENTS_PER_REQUEST:
        raise WorkDispatchError("work_object_attachment_limit_exceeded")
    result: list[AttachmentView] = []
    for attachment_id in attachment_ids:
        row = (
            (
                await session.execute(
                    text(
                        f"SELECT {_SELECT_META} FROM work_object_attachments a "
                        "WHERE a.tenant_id=:tenant AND a.owner_ai_user_id=:owner "
                        "AND a.attachment_id=:id AND a.work_object_id IS NULL "
                        "AND a.detached_at IS NULL AND a.purpose='task_material'"
                    ),
                    {"tenant": tenant_id, "owner": owner_ai_user_id, "id": attachment_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise WorkDispatchError("work_object_attachment_invalid")
        result.append(_record(row).view())
    if sum(item.size_bytes for item in result) > MAX_TASK_ATTACHMENT_BYTES:
        raise WorkDispatchError("work_object_attachment_limit_exceeded")
    return result


async def link_attachments_in_session(
    session: AsyncSession,
    *,
    scope: AuthorizedWorkObjectScope,
    work_object_id: str,
    handling_relation_id: UUID | None,
    attachment_ids: list[UUID] | tuple[UUID, ...],
    purpose: AttachmentPurpose,
    visibility: AttachmentVisibility,
    now: datetime,
) -> tuple[AttachmentRecord, ...]:
    """Caller locks/authorizes the root; never changes or detaches staging sources."""
    recheck_scope(scope)
    if len(attachment_ids) > MAX_ATTACHMENTS_PER_REQUEST or len(set(attachment_ids)) != len(
        attachment_ids
    ):
        raise WorkDispatchError("work_object_attachment_limit_exceeded")
    tenant = scope.principal_tenant_id
    if handling_relation_id is not None:
        relation = (
            (
                await session.execute(
                    text(
                        "SELECT relation_id,target_kind "
                        "FROM work_object_handling_relations "
                        "WHERE tenant_id=:tenant "
                        "AND work_object_id=:task AND relation_id=:relation "
                        "AND superseded_at IS NULL"
                    ),
                    {"tenant": tenant, "task": work_object_id, "relation": handling_relation_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if relation is None:
            raise WorkDispatchError("work_object_action_forbidden")
        if (
            purpose == "result"
            and relation["target_kind"] == "department"
            and visibility != "department_internal"
        ):
            raise WorkDispatchError("work_object_action_forbidden")
    if visibility == "department_internal" and handling_relation_id is None:
        raise WorkDispatchError("work_object_attachment_invalid")
    total = int(
        (
            await session.execute(
                text(
                    "SELECT COALESCE(SUM(size_bytes),0) FROM work_object_attachments "
                    "WHERE tenant_id=:tenant AND work_object_id=:task AND detached_at IS NULL"
                ),
                {"tenant": tenant, "task": work_object_id},
            )
        ).scalar_one()
    )
    sources: list[dict[str, Any]] = []
    for attachment_id in attachment_ids:
        row = (
            (
                await session.execute(
                    text(
                        f"SELECT {_SELECT_META} FROM work_object_attachments a "
                        "WHERE a.tenant_id=:tenant "
                        "AND a.attachment_id=:id AND a.detached_at IS NULL "
                        "AND a.purpose=:purpose AND ((a.work_object_id IS NULL "
                        "AND a.owner_ai_user_id=:owner) OR (a.work_object_id=:task "
                        "AND a.handling_relation_id IS NOT DISTINCT FROM CAST(:relation AS UUID)))"
                    ),
                    {
                        "tenant": tenant,
                        "id": attachment_id,
                        "owner": scope.principal_ai_user_id,
                        "purpose": purpose,
                        "task": work_object_id,
                        "relation": handling_relation_id,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise WorkDispatchError("work_object_attachment_invalid")
        source = dict(row)
        sources.append(source)
        if source["work_object_id"] is None:
            total += source["size_bytes"]
    if total > MAX_TASK_ATTACHMENT_BYTES:
        raise WorkDispatchError("work_object_attachment_limit_exceeded")
    copied: list[AttachmentRecord] = []
    for source in sources:
        # New FK + bytes for each task; the versioned template's source remains immutable.
        new_id = uuid4()
        params = {
            "new_id": new_id,
            "tenant": tenant,
            "source": source["attachment_id"],
            "task": work_object_id,
            "relation": handling_relation_id,
            "purpose": purpose,
            "visibility": visibility,
            "now": now,
            "key": uuid5(new_id, "attachment-copy"),
            "fingerprint": fingerprint(
                {
                    "source": str(source["attachment_id"]),
                    "task": work_object_id,
                    "relation": str(handling_relation_id),
                    "purpose": purpose,
                    "visibility": visibility,
                }
            ),
        }
        if source["work_object_id"] is not None:
            row = (
                (
                    await session.execute(
                        text(
                            "UPDATE work_object_attachments "
                            "SET linked_at=:now,"
                            "version=version+1 WHERE tenant_id=:tenant AND work_object_id=:task "
                            "AND attachment_id=:source RETURNING " + ", ".join(_META)
                        ),
                        params,
                    )
                )
                .mappings()
                .one()
            )
            copied.append(_record(row))
            continue
        row = (
            (
                await session.execute(
                    text(
                        "INSERT INTO work_object_attachments (attachment_id,tenant_id,"
                        "owner_ai_user_id,work_object_id,handling_relation_id,purpose,visibility,"
                        "filename,media_type,size_bytes,sha256,payload,version,"
                        "created_at,linked_at,detached_at,idempotency_key,request_fingerprint) "
                        "SELECT :new_id,tenant_id,owner_ai_user_id,:task,:relation,"
                        ":purpose,:visibility,filename,"
                        "media_type,size_bytes,sha256,payload,1,:now,:now,NULL,:key,:fingerprint "
                        "FROM work_object_attachments "
                        "WHERE tenant_id=:tenant AND attachment_id=:source "
                        "RETURNING " + ", ".join(_META)
                    ),
                    params,
                )
            )
            .mappings()
            .one()
        )
        copied.append(_record(row))
    recheck_scope(scope)
    return tuple(copied)


async def copy_into_dispatch(session: AsyncSession, prepared: PreparedDispatch) -> None:
    for record in prepared.records:
        await link_attachments_in_session(
            session,
            scope=prepared.scope,
            work_object_id=record.work_object_id,
            handling_relation_id=None,
            attachment_ids=prepared.attachment_ids,
            purpose="task_material",
            visibility="shared",
            now=record.created_at,
        )


async def publish_relation_results_in_session(
    session: AsyncSession,
    *,
    scope: AuthorizedWorkObjectScope,
    work_object_id: str,
    handling_relation_id: UUID,
    now: datetime,
) -> None:
    """An explicitly approved department review publishes its current result files."""
    recheck_scope(scope)
    await session.execute(
        text(
            "UPDATE work_object_attachments a SET visibility='shared',version=a.version+1 "
            "FROM work_object_handling_relations r "
            "WHERE a.tenant_id=:tenant AND a.work_object_id=:task "
            "AND a.handling_relation_id=:relation AND r.tenant_id=a.tenant_id "
            "AND r.work_object_id=a.work_object_id AND r.relation_id=a.handling_relation_id "
            "AND r.superseded_at IS NULL AND a.linked_at=r.submitted_at "
            "AND a.purpose='result' AND a.detached_at IS NULL "
            "AND a.visibility='department_internal'"
        ),
        {
            "tenant": scope.principal_tenant_id,
            "task": work_object_id,
            "relation": handling_relation_id,
        },
    )


async def _root(
    session: AsyncSession,
    scope: AuthorizedWorkObjectScope,
    work_object_id: str,
    *,
    lock: bool = False,
) -> LifecycleView:
    # Local import avoids dispatch_insert -> attachment -> main store import cycles.
    from app.infra.persistence.work_object.postgresql import load_lifecycle_view_in_session

    return await load_lifecycle_view_in_session(session, work_object_id, scope, for_update=lock)


async def _mutation_rights(
    session: AsyncSession, scope: AuthorizedWorkObjectScope, task: str, relation_id: UUID | None
) -> None:
    recheck_scope(scope)
    root, params = root_visibility_sql(scope)
    params = {**params, "task": task, "relation": relation_id}
    predicate = (
        "EXISTS (SELECT 1 FROM work_object_handling_relations r WHERE "
        "r.tenant_id=wo.tenant_id AND r.work_object_id=wo.work_object_id AND "
        + relation_participant_sql("r")
        + (" AND r.relation_id=:relation" if relation_id is not None else "")
        + ")"
    )
    if relation_id is None:
        predicate = "(wo.initiator_ai_user_id=:principal_ai_user_id OR " + predicate + ")"
    allowed = (
        await session.execute(
            text(
                "SELECT 1 FROM work_objects wo WHERE wo.work_object_id=:task "
                f"AND {root} AND {predicate}"
            ),
            params,
        )
    ).scalar_one_or_none()
    if allowed is None:
        raise WorkDispatchError("work_object_action_forbidden")


async def _visible_rows(
    session: AsyncSession,
    scope: AuthorizedWorkObjectScope,
    task: str,
    *,
    attachment_id: UUID | None = None,
    include_detached: bool = False,
) -> list[AttachmentRecord]:
    predicate, params = child_visibility_sql(scope, visibility_expr="a.visibility")
    rows = (
        (
            await session.execute(
                text(
                    f"SELECT {_SELECT_META} FROM work_object_attachments a JOIN work_objects wo "
                    "ON wo.tenant_id=a.tenant_id AND wo.work_object_id=a.work_object_id "
                    "LEFT JOIN work_object_handling_relations r ON r.tenant_id=a.tenant_id "
                    "AND r.work_object_id=a.work_object_id "
                    "AND r.relation_id=a.handling_relation_id "
                    f"WHERE wo.work_object_id=:task AND {predicate} "
                    + ("AND a.detached_at IS NULL " if not include_detached else "")
                    + ("AND a.attachment_id=:attachment_id " if attachment_id is not None else "")
                    + "ORDER BY a.created_at,a.attachment_id"
                ),
                {**params, "task": task, "attachment_id": attachment_id},
            )
        )
        .mappings()
        .all()
    )
    recheck_scope(scope)
    return [_record(row) for row in rows]


class PostgreSQLWorkObjectAttachmentStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def upload(
        self,
        scope: AuthorizedWorkObjectScope,
        *,
        filename: str,
        media_type: str,
        purpose: AttachmentPurpose,
        payload: bytes,
        sha256: str,
        idempotency_key: UUID,
        now: datetime,
    ) -> AttachmentView:
        fp = fingerprint(
            {
                "filename": filename,
                "media_type": media_type,
                "purpose": purpose,
                "size_bytes": len(payload),
                "sha256": sha256,
            }
        )
        params = {
            "attachment_id": uuid4(),
            "tenant": scope.principal_tenant_id,
            "owner": scope.principal_ai_user_id,
            "purpose": purpose,
            "filename": filename,
            "media_type": media_type,
            "size": len(payload),
            "sha256": sha256,
            "payload": payload,
            "now": now,
            "key": idempotency_key,
            "fp": fp,
        }
        try:
            async with self._session_factory() as session, session.begin():
                row = (
                    (
                        await session.execute(
                            text(
                                "INSERT INTO work_object_attachments (attachment_id,tenant_id,"
                                "owner_ai_user_id,purpose,visibility,filename,media_type,"
                                "size_bytes,sha256,payload,version,created_at,idempotency_key,"
                                "request_fingerprint) VALUES (:attachment_id,:tenant,:owner,"
                                ":purpose,'shared',:filename,:media_type,:size,:sha256,:payload,"
                                "1,:now,:key,:fp) ON CONFLICT (tenant_id,owner_ai_user_id,"
                                "idempotency_key) DO NOTHING RETURNING " + ", ".join(_META)
                            ),
                            params,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    row = (
                        (
                            await session.execute(
                                text(
                                    f"SELECT {_SELECT_META} FROM work_object_attachments a "
                                    "WHERE a.tenant_id=:tenant "
                                    "AND a.owner_ai_user_id=:owner AND a.idempotency_key=:key"
                                ),
                                params,
                            )
                        )
                        .mappings()
                        .one()
                    )
                    if row["request_fingerprint"] != fp:
                        raise WorkDispatchError("idempotency_key_reused")
                return _record(row).view()
        except SQLAlchemyError as exc:
            _LOGGER.error("work_attachment stage=upload_failed code=work_object_store_unavailable")
            raise WorkDispatchError("work_object_store_unavailable") from exc

    async def list_for_task(
        self, scope: AuthorizedWorkObjectScope, work_object_id: str
    ) -> list[AttachmentView]:
        try:
            async with self._session_factory() as session:
                await _root(session, scope, work_object_id)
                return [row.view() for row in await _visible_rows(session, scope, work_object_id)]
        except SQLAlchemyError as exc:
            raise WorkDispatchError("work_object_store_unavailable") from exc

    async def _claim(
        self,
        session: AsyncSession,
        scope: AuthorizedWorkObjectScope,
        task: str,
        key: UUID,
        fp: str,
        operation: str,
        now: datetime,
    ) -> bool:
        params = {
            "tenant": scope.principal_tenant_id,
            "actor": scope.principal_ai_user_id,
            "key": key,
            "fp": fp,
            "result": {"operation": operation, "work_object_id": task},
            "summary": {},
            "now": now,
        }
        inserted = (
            await session.execute(
                text(
                    "INSERT INTO work_object_dispatch_receipts "
                    "(tenant_id,initiator_ai_user_id,operation,"
                    "idempotency_key,request_fingerprint,result,authorization_summary,created_at) "
                    "VALUES (:tenant,:actor,'dispatch',:key,:fp,:result,:summary,:now) "
                    "ON CONFLICT (tenant_id,initiator_ai_user_id,operation,idempotency_key) "
                    "DO NOTHING RETURNING idempotency_key"
                ).bindparams(bindparam("result", type_=JSONB), bindparam("summary", type_=JSONB)),
                params,
            )
        ).scalar_one_or_none()
        if inserted is not None:
            return False
        row = (
            (
                await session.execute(
                    text(
                        "SELECT request_fingerprint,result FROM work_object_dispatch_receipts "
                        "WHERE tenant_id=:tenant AND initiator_ai_user_id=:actor "
                        "AND operation='dispatch' AND idempotency_key=:key"
                    ),
                    params,
                )
            )
            .mappings()
            .one()
        )
        if row["request_fingerprint"] != fp or row["result"] != params["result"]:
            raise WorkDispatchError("idempotency_key_reused")
        return True

    async def link(
        self,
        scope: AuthorizedWorkObjectScope,
        work_object_id: str,
        *,
        request: AttachmentLinkRequest,
        if_match: str,
        idempotency_key: UUID,
        now: datetime,
    ) -> AttachmentMutationResult:
        return await self._mutate(
            scope,
            work_object_id,
            request=request,
            detach_id=None,
            if_match=if_match,
            key=idempotency_key,
            now=now,
        )

    async def detach(
        self,
        scope: AuthorizedWorkObjectScope,
        work_object_id: str,
        attachment_id: UUID,
        *,
        if_match: str,
        idempotency_key: UUID,
        now: datetime,
    ) -> AttachmentMutationResult:
        return await self._mutate(
            scope,
            work_object_id,
            request=None,
            detach_id=attachment_id,
            if_match=if_match,
            key=idempotency_key,
            now=now,
        )

    async def _mutate(
        self,
        scope: AuthorizedWorkObjectScope,
        task: str,
        *,
        request: AttachmentLinkRequest | None,
        detach_id: UUID | None,
        if_match: str,
        key: UUID,
        now: datetime,
    ) -> AttachmentMutationResult:
        operation = "attachment_link" if request is not None else "attachment_detach"
        fp = fingerprint(
            {
                "operation": operation,
                "work_object_id": task,
                "request": request.model_dump(mode="json")
                if request is not None
                else str(detach_id),
            }
        )
        try:
            async with self._session_factory() as session, session.begin():
                view = await _root(session, scope, task, lock=True)
                relation_id = request.handling_relation_id if request is not None else None
                if detach_id is not None:
                    rows = await _visible_rows(
                        session, scope, task, attachment_id=detach_id, include_detached=True
                    )
                    if not rows:
                        raise WorkDispatchError("work_object_action_forbidden")
                    relation_id = rows[0].handling_relation_id
                await _mutation_rights(session, scope, task, relation_id)
                replayed = await self._claim(session, scope, task, key, fp, operation, now)
                if not replayed:
                    if view.frozen:
                        raise WorkDispatchError("transition_invalid")
                    if not if_match:
                        raise WorkDispatchError("precondition_required")
                    if if_match != view.etag:
                        raise WorkDispatchError("version_conflict")
                    if request is not None and request.purpose == "result" and relation_id is None:
                        raise WorkDispatchError("work_object_action_forbidden")
                    if (
                        detach_id is not None
                        and rows[0].purpose == "result"
                        and view.status == "awaiting_acceptance"
                    ):
                        raise WorkDispatchError("transition_invalid")
                    if relation_id is not None:
                        status = (
                            await session.execute(
                                text(
                                    "SELECT status FROM work_object_handling_relations "
                                    "WHERE tenant_id=:tenant AND work_object_id=:task "
                                    "AND relation_id=:relation AND superseded_at IS NULL"
                                ),
                                {
                                    "tenant": scope.principal_tenant_id,
                                    "task": task,
                                    "relation": relation_id,
                                },
                            )
                        ).scalar_one()
                        if status in {"awaiting_department_review", "submitted"}:
                            raise WorkDispatchError("transition_invalid")
                    if request is not None:
                        await link_attachments_in_session(
                            session,
                            scope=scope,
                            work_object_id=task,
                            handling_relation_id=request.handling_relation_id,
                            attachment_ids=request.attachment_ids,
                            purpose=request.purpose,
                            visibility=request.visibility,
                            now=now,
                        )
                    else:
                        if rows[0].detached_at is not None:
                            raise WorkDispatchError("work_object_attachment_invalid")
                        await session.execute(
                            text(
                                "UPDATE work_object_attachments "
                                "SET detached_at=:now,version=version+1 WHERE tenant_id=:tenant "
                                "AND work_object_id=:task AND attachment_id=:id"
                            ),
                            {
                                "tenant": scope.principal_tenant_id,
                                "task": task,
                                "id": detach_id,
                                "now": now,
                            },
                        )
                    await session.execute(
                        text(
                            "UPDATE work_objects SET version=version+1,updated_at=:now "
                            "WHERE tenant_id=:tenant AND work_object_id=:task"
                        ),
                        {"tenant": scope.principal_tenant_id, "task": task, "now": now},
                    )
                recheck_scope(scope)
                current = await _root(session, scope, task)
                items = [row.view() for row in await _visible_rows(session, scope, task)]
                return AttachmentMutationResult(items=items, etag=current.etag, replayed=replayed)
        except SQLAlchemyError as exc:
            _LOGGER.error(
                "work_attachment stage=mutation_failed work_object_id=%s "
                "code=work_object_store_unavailable",
                task,
            )
            raise WorkDispatchError("work_object_store_unavailable") from exc

    async def download(
        self, scope: AuthorizedWorkObjectScope, work_object_id: str, attachment_id: UUID
    ) -> AttachmentDownload:
        try:
            async with self._session_factory() as session, session.begin():
                # Root lock makes revocation and payload release one serial authorization decision.
                await _root(session, scope, work_object_id, lock=True)
                rows = await _visible_rows(
                    session, scope, work_object_id, attachment_id=attachment_id
                )
                if not rows:
                    raise WorkDispatchError("work_object_action_forbidden")
                payload = (
                    await session.execute(
                        text(
                            "SELECT payload FROM work_object_attachments WHERE tenant_id=:tenant "
                            "AND work_object_id=:task AND attachment_id=:id AND detached_at IS NULL"
                        ),
                        {
                            "tenant": scope.principal_tenant_id,
                            "task": work_object_id,
                            "id": attachment_id,
                        },
                    )
                ).scalar_one()
                recheck_scope(scope)
                return AttachmentDownload(attachment=rows[0].view(), payload=bytes(payload))
        except SQLAlchemyError as exc:
            raise WorkDispatchError("work_object_store_unavailable") from exc
