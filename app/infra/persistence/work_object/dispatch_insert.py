"""Atomic insert/replay shared by ordinary dispatch and recurrence transactions."""

from __future__ import annotations

import time

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.persistence.work_object.authorization import recheck_scope
from app.infra.persistence.work_object.postgresql import (
    insert_relation_in_session,
    load_lifecycle_view_in_session,
    load_manual_root_in_session,
)
from app.ports.work_object import DispatchReceipt
from app.ports.work_object_dispatch import (
    DispatchResult,
    PreparedDispatch,
    WorkDispatchError,
    dispatch_public_item,
)
from app.ports.work_object_scope import AuthorizedWorkObjectScope


async def insert_prepared_dispatch(
    session: AsyncSession, prepared: PreparedDispatch
) -> DispatchResult:
    """Caller owns the transaction. No session factory, begin, commit or application import."""
    recheck_scope(prepared.scope)
    if time.monotonic() > prepared.valid_until:
        raise WorkDispatchError("organization_directory_stale")
    receipt = prepared.receipt
    params = receipt.model_dump()
    inserted = (
        await session.execute(
            text(
                "INSERT INTO work_object_dispatch_receipts(tenant_id,initiator_ai_user_id,o"
                "peration,"
                "idempotency_key,request_fingerprint,result,authorization_summary,created_a"
                "t) VALUES("
                ":tenant_id,:initiator_ai_user_id,:operation,:idempotency_key,:request_fing"
                "erprint,"
                ":result,:authorization_summary,:created_at) ON CONFLICT(tenant_id,initiato"
                "r_ai_user_id,operation,"
                "idempotency_key) DO NOTHING RETURNING idempotency_key"
            ).bindparams(
                bindparam("result", type_=JSONB), bindparam("authorization_summary", type_=JSONB)
            ),
            params,
        )
    ).scalar_one_or_none()
    if inserted is None:
        winner = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM work_object_dispatch_receip"
                        "ts WHERE tenant_id=:tenant_id "
                        "AND initiator_ai_user_id=:initiator_ai_user_id AND operation='disp"
                        "atch' AND idempotency_key=:idempotency_key"
                    ),
                    params,
                )
            )
            .mappings()
            .one()
        )
        committed_receipt = DispatchReceipt.model_validate(dict(winner))
        if committed_receipt.request_fingerprint != prepared.request_fingerprint:
            raise WorkDispatchError("idempotency_key_reused")
        return await replay_dispatch_receipt(session, committed_receipt, prepared.scope)
    for record in prepared.records:
        values = record.model_dump()
        await session.execute(
            text(
                "INSERT INTO work_objects("
                + ",".join(values)
                + ") VALUES("
                + ",".join(":" + k for k in values)
                + ")"
            ).bindparams(bindparam("reminder_choices", type_=JSONB)),
            values,
        )
    for relation in prepared.relations:
        await insert_relation_in_session(session, relation)
    if prepared.attachment_ids:
        from app.infra.persistence.work_object.attachments import copy_into_dispatch

        await copy_into_dispatch(session, prepared)
    items = []
    for record in prepared.records:
        lifecycle = await load_lifecycle_view_in_session(
            session, record.work_object_id, prepared.scope
        )
        items.append(
            dispatch_public_item(
                record.model_dump(mode="json"),
                [r.model_dump(mode="json") for r in lifecycle.relations],
            )
        )
    result = DispatchResult(items=items, created_count=len(items), replayed=False)
    await session.execute(
        text(
            "UPDATE work_object_dispatch_receipts SET result=:result WHERE tenant_id=:tenant_id "
            "AND initiator_ai_user_id=:initiator_ai_user_id AND operation='dispatch' AND id"
            "empotency_key=:idempotency_key"
        ).bindparams(bindparam("result", type_=JSONB)),
        {**params, "result": result.model_dump(mode="json")},
    )
    recheck_scope(prepared.scope)
    return result


async def replay_dispatch_receipt(
    session: AsyncSession,
    receipt: DispatchReceipt,
    scope: AuthorizedWorkObjectScope,
) -> DispatchResult:
    """Project committed task IDs through current root/child authority, not saved results."""
    if (
        receipt.tenant_id != scope.principal_tenant_id
        or receipt.initiator_ai_user_id != scope.principal_ai_user_id
    ):
        raise WorkDispatchError("work_object_not_found")
    items = []
    for item in receipt.result["items"]:
        root = await load_manual_root_in_session(
            session, item["work_object_id"], scope, for_update=True
        )
        lifecycle = await load_lifecycle_view_in_session(session, root.work_object_id, scope)
        items.append(
            dispatch_public_item(
                root.model_dump(mode="json"),
                [relation.model_dump(mode="json") for relation in lifecycle.relations],
            )
        )
    recheck_scope(scope)
    return DispatchResult(items=items, created_count=len(items), replayed=True)
