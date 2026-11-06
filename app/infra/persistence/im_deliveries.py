"""PostgreSQL manual delivery snapshots, one-attempt Outbox and recipient receipts."""

from __future__ import annotations

import builtins
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import fields
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.jobs.queue_sql import GENERAL_QUEUE, defer_job, read_job_state
from app.ports.auth import Principal
from app.ports.communication import DeliveryMessage, DeliveryResult
from app.ports.im_deliveries import (
    DeliveryBinding,
    DeliveryFileView,
    DeliveryRecipient,
    DeliveryRecord,
    DeliveryVerification,
    DeliveryView,
    IMDeliveryError,
    VerificationView,
    confirmation_digest,
)

_COLUMNS = tuple(field.name for field in fields(DeliveryRecord))
_BINDING_COLUMNS = ", ".join(field.name for field in fields(DeliveryBinding))
_INITIATOR = "origin_inbox_id IS NULL AND tenant_id=:tenant AND initiator_ai_user_id=:user"
_PARTICIPANT = (
    "origin_inbox_id IS NULL AND tenant_id=:tenant AND (initiator_ai_user_id=:user OR "
    "(recipient_ai_user_id=:user AND confirmed_at IS NOT NULL "
    "AND state NOT IN ('cancelled','expired') "
    "AND (state <> 'ready' OR send_expires_at > now())))"
)
_SEND_TASK = "im_deliveries.send"


def _record(row: RowMapping) -> DeliveryRecord:
    values = dict(row)
    for name in ("recipient_mapping_version", "confirmation_sha256", "artifact_sha256"):
        if values[name] is not None:
            values[name] = bytes(values[name])
    return DeliveryRecord(**values)


def _owner(principal: Principal) -> dict[str, str]:
    return {"tenant": principal.org_ctx.tenant_id, "user": principal.ai_user_id}


class PostgreSQLIMDeliveryStore:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        provider: str,
        im_instance_id: str,
        realm_id: str,
        recipients: tuple[tuple[str, str], ...],
    ) -> None:
        self._sessions = sessions
        self._provider = provider
        self._instance = im_instance_id
        self._realm = realm_id
        self._recipients = dict(recipients)

    @asynccontextmanager
    async def _session(self, *, write: bool = False) -> AsyncIterator[AsyncSession]:
        try:
            async with self._sessions() as session:
                yield session
                if write:
                    await session.commit()
        except SQLAlchemyError:
            raise IMDeliveryError("im_delivery_unavailable", 503) from None

    async def _binding(
        self,
        session: AsyncSession,
        tenant: str,
        user: str,
        record: DeliveryRecord | None = None,
    ) -> DeliveryBinding | None:
        row = (
            await session.execute(
                text(
                    f"SELECT {_BINDING_COLUMNS} FROM public.im_account_bindings "
                    "WHERE tenant_id=:tenant AND ai_user_id=:user "
                    "AND provider=:provider AND im_instance_id=:instance "
                    "AND realm_id=:realm AND status='active'"
                ),
                {
                    "tenant": tenant,
                    "user": user,
                    "provider": self._provider if record is None else record.provider,
                    "instance": self._instance if record is None else record.im_instance_id,
                    "realm": self._realm if record is None else record.realm_id,
                },
            )
        ).mappings().one_or_none()
        return None if row is None else DeliveryBinding(**dict(row))

    async def _initiator(
        self, session: AsyncSession, principal: Principal,
        record: DeliveryRecord | None = None,
    ) -> DeliveryBinding:
        if (principal.org_ctx.tenant_id != "default"
                or not principal.org_ctx.directory_user_id):
            raise IMDeliveryError("im_delivery_mapping_unavailable")
        binding = await self._binding(
            session, principal.org_ctx.tenant_id, principal.ai_user_id, record
        )
        if (binding is None
                or binding.directory_user_id != principal.org_ctx.directory_user_id):
            raise IMDeliveryError("im_delivery_mapping_unavailable")
        return binding

    async def _recipient(
        self, session: AsyncSession, tenant: str, user: str,
        record: DeliveryRecord | None = None,
    ) -> DeliveryBinding:
        if user not in self._recipients:
            raise IMDeliveryError("im_delivery_recipient_unavailable")
        binding = await self._binding(session, tenant, user, record)
        if binding is None:
            raise IMDeliveryError("im_delivery_recipient_unavailable")
        return binding

    async def recipients(self, principal: Principal) -> list[DeliveryRecipient]:
        async with self._session() as session:
            await self._initiator(session, principal)
            result = []
            for user, label in self._recipients.items():
                binding = await self._binding(session, principal.org_ctx.tenant_id, user)
                if binding is not None:
                    result.append(DeliveryRecipient(
                        ai_user_id=user, display_name=label, im_user_id=binding.im_user_id,
                    ))
            return result

    async def bindings(
        self, principal: Principal, recipient_ai_user_id: str,
    ) -> tuple[DeliveryBinding, DeliveryBinding]:
        async with self._session() as session:
            initiator = await self._initiator(session, principal)
            recipient = await self._recipient(
                session, principal.org_ctx.tenant_id, recipient_ai_user_id
            )
            return initiator, recipient

    async def find_request(self, principal: Principal, key: UUID) -> DeliveryRecord | None:
        async with self._session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM public.im_deliveries "
                        f"WHERE {_INITIATOR} AND idempotency_key=:key"
                    ),
                    {**_owner(principal), "key": key},
                )
            ).mappings().one_or_none()
            return None if row is None else _record(row)

    async def prepare(self, record: DeliveryRecord) -> DeliveryRecord:
        async with self._session(write=True) as session:
            row = (
                await session.execute(
                    text(
                        f"INSERT INTO public.im_deliveries ({', '.join(_COLUMNS)}) "
                        f"VALUES ({', '.join(':' + name for name in _COLUMNS)}) "
                        "ON CONFLICT (tenant_id, initiator_ai_user_id, idempotency_key) "
                        "DO NOTHING RETURNING *"
                    ),
                    vars(record),
                )
            ).mappings().one_or_none()
            if row is None:
                row = (
                    await session.execute(
                        text(
                            "SELECT * FROM public.im_deliveries "
                            f"WHERE {_INITIATOR} AND idempotency_key=:key"
                        ),
                        {
                            "tenant": record.tenant_id,
                            "user": record.initiator_ai_user_id,
                            "key": record.idempotency_key,
                        },
                    )
                ).mappings().one()
            return _record(row)

    async def confirm(
        self, principal: Principal, delivery_id: UUID, confirmation_sha256: bytes,
    ) -> DeliveryRecord:
        async with self._session(write=True) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM public.im_deliveries "
                        f"WHERE {_INITIATOR} AND delivery_id=:id FOR UPDATE"
                    ),
                    {**_owner(principal), "id": delivery_id},
                )
            ).mappings().one_or_none()
            if row is None:
                raise IMDeliveryError("im_delivery_not_found", 404)
            record = _record(row)
            if record.artifact_id is not None and (
                record.grant_revoked_at is not None
                or cast(datetime, record.grant_expires_at) <= datetime.now(UTC)
            ):
                raise IMDeliveryError("im_delivery_not_confirmable")
            if record.confirmation_sha256 != confirmation_sha256:
                raise IMDeliveryError("im_delivery_preview_changed")
            if record.state in ("cancelled", "expired"):
                raise IMDeliveryError("im_delivery_not_confirmable")
            if record.confirmed_at is not None:
                return record
            if cast(datetime, record.send_expires_at) <= datetime.now(UTC):
                row = (
                    await session.execute(
                        text(
                            "UPDATE public.im_deliveries SET state='expired', updated_at=now() "
                            f"WHERE {_INITIATOR} AND delivery_id=:id RETURNING *"
                        ),
                        {**_owner(principal), "id": delivery_id},
                    )
                ).mappings().one()
                return _record(row)
            initiator = await self._initiator(session, principal, record)
            recipient = await self._recipient(
                session, record.tenant_id, cast(str, record.recipient_ai_user_id), record
            )
            if confirmation_digest(record, initiator, recipient) != record.confirmation_sha256:
                raise IMDeliveryError("im_delivery_mapping_changed")
            queue_id = await defer_job(
                session, queue_name=GENERAL_QUEUE, task_name=_SEND_TASK,
                args={"delivery_id": str(delivery_id)},
            )
            row = (
                await session.execute(
                    text(
                        "UPDATE public.im_deliveries SET state='ready', confirmed_at=now(), "
                        "queue_job_id=:queue_id, updated_at=now() "
                        f"WHERE {_INITIATOR} AND delivery_id=:id AND state='prepared' "
                        "RETURNING *"
                    ),
                    {**_owner(principal), "id": delivery_id, "queue_id": queue_id},
                )
            ).mappings().one()
            return _record(row)

    async def _view(
        self, session: AsyncSession, principal: Principal, record: DeliveryRecord,
    ) -> DeliveryView:
        receipts = (
            await session.execute(
                text(
                    "SELECT verification_id, result, comment, created_at "
                    "FROM public.im_verifications WHERE delivery_id=:id "
                    "ORDER BY created_at, verification_id"
                ),
                {"id": record.delivery_id},
            )
        ).mappings().all()
        source = vars(record)
        values: dict[str, Any] = {
            name: source[name] for name in DeliveryView.model_fields if name in source
        }
        values["confirmation_sha256"] = cast(bytes, record.confirmation_sha256).hex()
        values["viewer_role"] = (
            "initiator" if principal.ai_user_id == record.initiator_ai_user_id else "recipient"
        )
        if (record.state in ("prepared", "ready")
                and cast(datetime, record.send_expires_at) <= datetime.now(UTC)):
            values["state"] = "expired"
        values["file"] = None
        if record.artifact_id is not None:
            file = (await session.execute(text(
                "SELECT f.display_filename, f.size_bytes FROM public.im_deliveries d "
                "JOIN public.file_artifacts f ON f.artifact_id=d.artifact_id "
                "WHERE d.delivery_id=:id"
            ), {"id": record.delivery_id})).mappings().one()
            values["file"] = DeliveryFileView(
                artifact_id=record.artifact_id,
                sha256=cast(bytes, record.artifact_sha256).hex(),
                filename=file["display_filename"], size_bytes=file["size_bytes"],
                grant_expires_at=cast(datetime, record.grant_expires_at),
                grant_revoked_at=record.grant_revoked_at,
            )
        values["verifications"] = [VerificationView(**dict(row)) for row in receipts]
        return DeliveryView(**values)

    async def get(self, principal: Principal, delivery_id: UUID) -> DeliveryView:
        async with self._session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM public.im_deliveries "
                        f"WHERE {_PARTICIPANT} AND delivery_id=:id"
                    ),
                    {**_owner(principal), "id": delivery_id},
                )
            ).mappings().one_or_none()
            if row is None:
                raise IMDeliveryError("im_delivery_not_found", 404)
            return await self._view(session, principal, _record(row))

    async def list(self, principal: Principal) -> list[DeliveryView]:
        async with self._session() as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT * FROM public.im_deliveries WHERE {_PARTICIPANT} "
                        "ORDER BY created_at DESC, delivery_id DESC"
                    ),
                    _owner(principal),
                )
            ).mappings().all()
            return [await self._view(session, principal, _record(row)) for row in rows]

    async def cancel(self, principal: Principal, delivery_id: UUID) -> None:
        async with self._session(write=True) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM public.im_deliveries "
                        f"WHERE {_INITIATOR} AND delivery_id=:id FOR UPDATE"
                    ),
                    {**_owner(principal), "id": delivery_id},
                )
            ).mappings().one_or_none()
            if row is None:
                raise IMDeliveryError("im_delivery_not_found", 404)
            if row["artifact_id"] is not None and row["confirmed_at"] is not None:
                await session.execute(text(
                    "UPDATE public.im_deliveries SET "
                    "grant_revoked_at=COALESCE(grant_revoked_at, now()), "
                    "state=CASE WHEN state='ready' THEN 'cancelled' ELSE state END, "
                    "updated_at=now() WHERE delivery_id=:id"
                ), {"id": delivery_id})
            elif row["state"] in ("prepared", "ready"):
                await session.execute(
                    text(
                        "UPDATE public.im_deliveries SET state='cancelled', updated_at=now() "
                        f"WHERE {_INITIATOR} AND delivery_id=:id"
                    ),
                    {**_owner(principal), "id": delivery_id},
                )

    async def verify(
        self, principal: Principal, delivery_id: UUID, request: DeliveryVerification,
    ) -> None:
        async with self._session(write=True) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM public.im_deliveries WHERE tenant_id=:tenant "
                        "AND recipient_ai_user_id=:user AND delivery_id=:id "
                        "AND origin_inbox_id IS NULL "
                        "AND confirmed_at IS NOT NULL AND state NOT IN ('cancelled','expired') "
                        "AND (state <> 'ready' OR send_expires_at > now()) "
                        "AND (artifact_id IS NULL OR (grant_revoked_at IS NULL "
                        "AND grant_expires_at > now())) FOR UPDATE"
                    ),
                    {**_owner(principal), "id": delivery_id},
                )
            ).mappings().one_or_none()
            if row is None:
                raise IMDeliveryError("im_delivery_not_found", 404)
            receipt = (
                await session.execute(
                    text(
                        "SELECT result, comment FROM public.im_verifications "
                        "WHERE delivery_id=:id AND idempotency_key=:key"
                    ),
                    {"id": delivery_id, "key": request.idempotency_key},
                )
            ).mappings().one_or_none()
            if receipt is not None:
                if receipt["result"] != request.result or receipt["comment"] != request.comment:
                    raise IMDeliveryError("im_delivery_idempotency_conflict")
                return
            await session.execute(
                text(
                    "INSERT INTO public.im_verifications (verification_id, delivery_id, "
                    "result, comment, idempotency_key, created_at) "
                    "VALUES (:verification_id, :id, :result, :comment, :key, now())"
                ),
                {
                    "verification_id": uuid4(), "id": delivery_id,
                    "result": request.result, "comment": request.comment,
                    "key": request.idempotency_key,
                },
            )

    @staticmethod
    def _card(record: DeliveryRecord) -> DeliveryMessage:
        return DeliveryMessage(
            provider=record.provider, im_instance_id=record.im_instance_id,
            realm_id=record.realm_id, sender_bot_id=record.sender_bot_id,
            target_conversation_ref=record.target_conversation_ref,
            card_content=record.card_content,
            kind="group_status" if record.reply_slot == "group_status" else "private",
        )

    async def origin_inbox(self, delivery_id: UUID) -> UUID | None:
        async with self._session() as session:
            return cast(UUID | None, (await session.execute(
                text("SELECT origin_inbox_id FROM public.im_deliveries WHERE delivery_id=:id"),
                {"id": delivery_id},
            )).scalar_one_or_none())

    async def claim(self, delivery_id: UUID) -> tuple[DeliveryRecord, DeliveryMessage] | None:
        async with self._session(write=True) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM public.im_deliveries "
                        "WHERE delivery_id=:id AND state='ready' "
                        "AND origin_inbox_id IS NULL FOR UPDATE"
                    ),
                    {"id": delivery_id},
                )
            ).mappings().one_or_none()
            if row is None:
                return None
            record = _record(row)
            if cast(datetime, record.send_expires_at) <= datetime.now(UTC):
                await session.execute(
                    text(
                        "UPDATE public.im_deliveries SET state='expired', updated_at=now() "
                        "WHERE delivery_id=:id"
                    ),
                    {"id": delivery_id},
                )
                return None
            initiator = await self._binding(
                session, record.tenant_id, record.initiator_ai_user_id, record
            )
            recipient = await self._binding(
                session, record.tenant_id, cast(str, record.recipient_ai_user_id), record
            )
            if (record.tenant_id != "default" or initiator is None or recipient is None
                    or record.recipient_ai_user_id not in self._recipients
                    or confirmation_digest(record, initiator, recipient)
                    != record.confirmation_sha256):
                await session.execute(
                    text(
                        "UPDATE public.im_deliveries SET state='cancelled', "
                        "result_code='im_delivery_mapping_changed', updated_at=now() "
                        "WHERE delivery_id=:id"
                    ),
                    {"id": delivery_id},
                )
                return None
            row = (
                await session.execute(
                    text(
                        "UPDATE public.im_deliveries SET state='sending', "
                        "send_attempt_id=:attempt, attempt_started_at=now(), updated_at=now() "
                        "WHERE delivery_id=:id AND state='ready' RETURNING *"
                    ),
                    {"id": delivery_id, "attempt": uuid4()},
                )
            ).mappings().one()
            claimed = _record(row)
            return claimed, self._card(claimed)

    async def finish(
        self, delivery_id: UUID, attempt_id: UUID, result: DeliveryResult,
    ) -> None:
        async with self._session(write=True) as session:
            await session.execute(
                text(
                    "UPDATE public.im_deliveries SET state=:state, "
                    "remote_message_id=:message_id, "
                    "accepted_at=CASE WHEN :state='accepted' THEN now() ELSE NULL END, "
                    "result_code=:code, updated_at=now() WHERE delivery_id=:id "
                    "AND send_attempt_id=:attempt AND state IN ('sending','unknown')"
                ),
                {
                    "state": result.state, "message_id": result.remote_message_id,
                    "code": result.result_code, "id": delivery_id, "attempt": attempt_id,
                },
            )

    async def uncertain(self, delivery_id: UUID) -> tuple[DeliveryRecord, DeliveryMessage] | None:
        async with self._session(write=True) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM public.im_deliveries "
                        "WHERE delivery_id=:id AND state IN ('sending','unknown') FOR UPDATE"
                    ),
                    {"id": delivery_id},
                )
            ).mappings().one_or_none()
            if row is None:
                return None
            record = _record(row)
            if record.state == "sending":
                row = (
                    await session.execute(
                        text(
                            "UPDATE public.im_deliveries SET state='unknown', "
                            "result_code='im_delivery_unconfirmed', updated_at=now() "
                            "WHERE delivery_id=:id RETURNING *"
                        ),
                        {"id": delivery_id},
                    )
                ).mappings().one()
                record = _record(row)
            return record, self._card(record)

    async def recover_ready(self) -> None:
        async with self._session(write=True) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM public.im_deliveries WHERE state='ready' "
                        "ORDER BY created_at, delivery_id FOR UPDATE SKIP LOCKED"
                    )
                )
            ).mappings().all()
            for row in rows:
                record = _record(row)
                if (record.origin_inbox_id is None
                        and cast(datetime, record.send_expires_at) <= datetime.now(UTC)):
                    await session.execute(
                        text(
                            "UPDATE public.im_deliveries SET state='expired', updated_at=now() "
                            "WHERE delivery_id=:id"
                        ),
                        {"id": record.delivery_id},
                    )
                    continue
                queue = await read_job_state(session, cast(int, record.queue_job_id))
                # A queue worker can die after marking doing and before claiming
                # this ready row. Old and replacement tasks still compete for
                # the same ready -> sending transition, so only one can send.
                if queue is not None and queue.status == "todo":
                    continue
                queue_id = await defer_job(
                    session, queue_name=GENERAL_QUEUE, task_name=_SEND_TASK,
                    args={"delivery_id": str(record.delivery_id)},
                )
                await session.execute(
                    text(
                        "UPDATE public.im_deliveries SET queue_job_id=:queue_id, updated_at=now() "
                        "WHERE delivery_id=:id AND state='ready'"
                    ),
                    {"id": record.delivery_id, "queue_id": queue_id},
                )

    async def pending_reconciliation(self) -> builtins.list[UUID]:
        async with self._session() as session:
            return list((
                await session.execute(
                    text(
                        "SELECT delivery_id FROM public.im_deliveries "
                        "WHERE state IN ('sending','unknown') ORDER BY updated_at, delivery_id"
                    )
                )
            ).scalars().all())
