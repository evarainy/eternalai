"""Explicit published-text grants, atomic IM Inbox and the existing delivery Outbox."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import fields
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.jobs.queue_sql import GENERAL_QUEUE, QUEUE_SCHEMA, defer_job
from app.infra.persistence.im_deliveries import _record as delivery_record
from app.ports.auth import Principal
from app.ports.communication import DeliveryMessage, InboundEvent
from app.ports.im_deliveries import DeliveryBinding, DeliveryRecord
from app.ports.personal_agents import (
    AgentView,
    AuthorizedMaterial,
    GranteeView,
    GrantMaterial,
    GrantView,
    InboxContext,
    MaterialView,
    PersonalAgent,
    PersonalAgentError,
    PublishMaterial,
    ReceiveResult,
    ReplyView,
    RequestView,
)

_BINDING_COLUMNS = ", ".join(item.name for item in fields(DeliveryBinding))
_EVENT_KEY = (
    "tenant_id=:tenant_id AND provider=:provider AND im_instance_id=:im_instance_id "
    "AND realm_id=:realm_id AND target_bot_id=:target_bot_id "
    "AND source_message_id=:source_message_id AND trigger_kind=:trigger_kind"
)


def _agent(row: RowMapping) -> PersonalAgent:
    values = dict(row)
    values["owner_binding_version"] = bytes(values["owner_binding_version"])
    return PersonalAgent(**values)


def _material(row: RowMapping) -> MaterialView:
    return MaterialView(**{name: row[name] for name in MaterialView.model_fields
                           if name != "content_sha256"},
                        content_sha256=bytes(row["content_sha256"]).hex())


class PostgreSQLPersonalAgentStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @asynccontextmanager
    async def _session(self, *, write: bool = False) -> AsyncIterator[AsyncSession]:
        try:
            async with self._sessions() as session:
                yield session
                if write:
                    await session.commit()
        except SQLAlchemyError:
            raise PersonalAgentError("personal_agent_unavailable", 503) from None

    @staticmethod
    async def _binding(session: AsyncSession, binding_id: UUID) -> DeliveryBinding:
        row = (await session.execute(text(
            f"SELECT {_BINDING_COLUMNS} FROM public.im_account_bindings WHERE binding_id=:id"
        ), {"id": binding_id})).mappings().one()
        return DeliveryBinding(**dict(row))

    async def _owned(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, *, lock: bool = False,
    ) -> PersonalAgent:
        row = (await session.execute(text(
            "SELECT a.* FROM public.personal_agents a JOIN public.im_account_bindings b "
            "ON b.binding_id=a.owner_binding_id WHERE a.agent_id=:id AND a.tenant_id=:tenant "
            "AND b.tenant_id=a.tenant_id AND b.ai_user_id=:user AND b.directory_user_id=:directory "
            "AND b.status='active'" + (" FOR UPDATE OF a" if lock else "")
        ), {"id": agent_id, "tenant": principal.org_ctx.tenant_id,
            "user": principal.ai_user_id, "directory": principal.org_ctx.directory_user_id}
        )).mappings().one_or_none()
        if row is None or principal.org_ctx.tenant_id != "default":
            raise PersonalAgentError("personal_agent_not_found", 404)
        agent = _agent(row)
        owner = await self._binding(session, agent.owner_binding_id)
        if (agent.status != "active" or owner.version != agent.owner_binding_version
                or (owner.provider, owner.im_instance_id, owner.realm_id)
                != (agent.provider, agent.im_instance_id, agent.realm_id)):
            raise PersonalAgentError("personal_agent_mapping_changed")
        return agent

    async def agents(self, principal: Principal) -> list[AgentView]:
        async with self._session() as session:
            ids = (await session.execute(text(
                "SELECT a.agent_id FROM public.personal_agents a JOIN public.im_account_bindings b "
                "ON b.binding_id=a.owner_binding_id WHERE a.tenant_id=:tenant "
                "AND b.tenant_id=a.tenant_id AND b.ai_user_id=:user "
                "AND b.directory_user_id=:directory ORDER BY a.display_name, a.agent_id"
            ), {"tenant": principal.org_ctx.tenant_id, "user": principal.ai_user_id,
                "directory": principal.org_ctx.directory_user_id})).scalars().all()
            result = []
            for agent_id in ids:
                agent = await self._owned(session, principal, agent_id)
                result.append(AgentView(**{name: getattr(agent, name)
                                           for name in AgentView.model_fields}))
            return result

    async def agent(self, agent_id: UUID) -> PersonalAgent:
        async with self._session() as session:
            row = (await session.execute(text(
                "SELECT * FROM public.personal_agents WHERE agent_id=:id "
                "AND tenant_id='default' AND status='active'"
            ), {"id": agent_id})).mappings().one_or_none()
            if row is None:
                raise PersonalAgentError("personal_agent_not_found", 404)
            return _agent(row)

    async def materials(self, principal: Principal, agent_id: UUID) -> list[MaterialView]:
        async with self._session() as session:
            await self._owned(session, principal, agent_id)
            rows = (await session.execute(text(
                "SELECT * FROM public.personal_agent_materials WHERE agent_id=:id "
                "ORDER BY version DESC"
            ), {"id": agent_id})).mappings().all()
            return [_material(row) for row in rows]

    async def publish(
        self, principal: Principal, agent_id: UUID, request: PublishMaterial,
    ) -> MaterialView:
        async with self._session(write=True) as session:
            await self._owned(session, principal, agent_id, lock=True)
            row = (await session.execute(text(
                "INSERT INTO public.personal_agent_materials "
                "(agent_id,version,title,body,content_sha256,published_at,expires_at) "
                "SELECT :id,COALESCE(max(version),0)+1,:title,:body,:hash,"
                "clock_timestamp(),:expiry "
                "FROM public.personal_agent_materials WHERE agent_id=:id "
                "HAVING :expiry > clock_timestamp() RETURNING *"
            ), {"id": agent_id, "title": request.title, "body": request.body,
                "hash": hashlib.sha256(request.body.encode()).digest(),
                "expiry": request.expires_at})).mappings().one_or_none()
            if row is None:
                raise PersonalAgentError("personal_agent_expiry_invalid", 422)
            return _material(row)

    @staticmethod
    async def _grant_view(session: AsyncSession, grant_id: UUID) -> GrantView:
        row = (await session.execute(text(
            "SELECT g.grant_id,g.material_version,b.ai_user_id AS grantee_ai_user_id,"
            "b.ai_user_id AS grantee_display_name,g.expires_at,g.revoked_at "
            "FROM public.personal_agent_grants g JOIN public.im_account_bindings b "
            "ON b.binding_id=g.grantee_binding_id WHERE g.grant_id=:id"
        ), {"id": grant_id})).mappings().one()
        return GrantView(**dict(row))

    async def grants(self, principal: Principal, agent_id: UUID) -> list[GrantView]:
        async with self._session() as session:
            await self._owned(session, principal, agent_id)
            ids = (await session.execute(text(
                "SELECT grant_id FROM public.personal_agent_grants WHERE agent_id=:id "
                "ORDER BY created_at DESC,grant_id"
            ), {"id": agent_id})).scalars().all()
            return [await self._grant_view(session, grant_id) for grant_id in ids]

    async def recipients(self, principal: Principal, agent_id: UUID) -> list[GranteeView]:
        async with self._session() as session:
            agent = await self._owned(session, principal, agent_id)
            rows = (await session.execute(text(
                "SELECT ai_user_id,ai_user_id AS display_name FROM public.im_account_bindings "
                "WHERE tenant_id=:tenant AND provider=:provider AND im_instance_id=:instance "
                "AND realm_id=:realm AND status='active' ORDER BY ai_user_id"
            ), {"tenant": agent.tenant_id, "provider": agent.provider,
                "instance": agent.im_instance_id, "realm": agent.realm_id})).mappings().all()
            return [GranteeView(**dict(row)) for row in rows]

    async def grant(
        self, principal: Principal, agent_id: UUID, request: GrantMaterial,
    ) -> GrantView:
        async with self._session(write=True) as session:
            agent = await self._owned(session, principal, agent_id, lock=True)
            material = (await session.execute(text(
                "SELECT version,expires_at,withdrawn_at FROM public.personal_agent_materials "
                "WHERE agent_id=:id AND version=:version FOR UPDATE"
            ), {"id": agent_id, "version": request.material_version})).mappings().one_or_none()
            now = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
            if (material is None or material["withdrawn_at"] is not None
                    or not now < request.expires_at <= material["expires_at"]):
                raise PersonalAgentError("personal_agent_expiry_invalid", 422)
            row = (await session.execute(text(
                f"SELECT {_BINDING_COLUMNS} FROM public.im_account_bindings "
                "WHERE tenant_id=:tenant AND ai_user_id=:user AND provider=:provider "
                "AND im_instance_id=:instance AND realm_id=:realm AND status='active'"
            ), {"tenant": agent.tenant_id, "user": request.grantee_ai_user_id,
                "provider": agent.provider, "instance": agent.im_instance_id,
                "realm": agent.realm_id})).mappings().one_or_none()
            if row is None:
                raise PersonalAgentError("personal_agent_grantee_unavailable", 422)
            grantee = DeliveryBinding(**dict(row))
            await session.execute(text(
                "UPDATE public.personal_agent_grants SET revoked_at=clock_timestamp() "
                "WHERE agent_id=:id AND grantee_binding_id=:binding AND revoked_at IS NULL"
            ), {"id": agent_id, "binding": grantee.binding_id})
            grant_id = uuid4()
            await session.execute(text(
                "INSERT INTO public.personal_agent_grants (grant_id,agent_id,material_version,"
                "grantee_binding_id,grantee_binding_version,created_at,expires_at) "
                "VALUES (:grant,:agent,:version,:binding,:hash,clock_timestamp(),:expiry)"
            ), {"grant": grant_id, "agent": agent_id, "version": request.material_version,
                "binding": grantee.binding_id, "hash": grantee.version,
                "expiry": request.expires_at})
            return await self._grant_view(session, grant_id)

    async def revoke(
        self, principal: Principal, agent_id: UUID, grant_id: UUID,
    ) -> GrantView:
        async with self._session(write=True) as session:
            await self._owned(session, principal, agent_id, lock=True)
            grant = (await session.execute(text(
                "SELECT material_version FROM public.personal_agent_grants "
                "WHERE agent_id=:agent AND grant_id=:id"
            ), {"agent": agent_id, "id": grant_id})).mappings().one_or_none()
            if grant is None:
                raise PersonalAgentError("personal_agent_grant_not_found", 404)
            await session.execute(text(
                "SELECT version FROM public.personal_agent_materials WHERE agent_id=:agent "
                "AND version=:version FOR UPDATE"
            ), {"agent": agent_id, "version": grant["material_version"]})
            await session.execute(text(
                "UPDATE public.personal_agent_grants SET revoked_at="
                "COALESCE(revoked_at,clock_timestamp()) WHERE grant_id=:id"
            ), {"id": grant_id})
            return await self._grant_view(session, grant_id)

    async def withdraw(
        self, principal: Principal, agent_id: UUID, version: int,
    ) -> MaterialView:
        async with self._session(write=True) as session:
            await self._owned(session, principal, agent_id, lock=True)
            row = (await session.execute(text(
                "UPDATE public.personal_agent_materials SET withdrawn_at="
                "COALESCE(withdrawn_at,clock_timestamp()) WHERE agent_id=:id AND version=:version "
                "RETURNING *"
            ), {"id": agent_id, "version": version})).mappings().one_or_none()
            if row is None:
                raise PersonalAgentError("personal_agent_material_not_found", 404)
            return _material(row)

    @staticmethod
    def _event_params(agent: PersonalAgent, event: InboundEvent) -> dict[str, Any]:
        return {"tenant_id": agent.tenant_id,
                **{item.name: getattr(event, item.name) for item in fields(event)}}

    async def _existing(
        self, session: AsyncSession, params: dict[str, Any],
    ) -> ReceiveResult | None:
        row = (await session.execute(text(
            "SELECT inbox_id,payload_fingerprint FROM public.im_inbox "
            f"WHERE {_EVENT_KEY} FOR UPDATE"
        ), params)).mappings().one_or_none()
        if row is None:
            return None
        return ReceiveResult(row["inbox_id"], "duplicate" if bytes(row["payload_fingerprint"])
                             == params["payload_fingerprint"] else "conflict")

    async def accept(self, agent_id: UUID, event: InboundEvent) -> ReceiveResult:
        agent = await self.agent(agent_id)
        params = self._event_params(agent, event)
        if (event.provider, event.im_instance_id, event.realm_id, event.target_bot_id) != (
            agent.provider, agent.im_instance_id, agent.realm_id, agent.bot_user_id,
        ):
            return ReceiveResult(None, "target_unavailable")
        try:
            async with self._sessions() as session:
                existing = await self._existing(session, params)
                if existing is not None:
                    await session.commit()
                    return existing
                # Agent is the common serialization point with grants and revocation.
                active = (await session.execute(text(
                    "SELECT agent_id FROM public.personal_agents WHERE agent_id=:id "
                    "AND status='active' FOR UPDATE"
                ), {"id": agent_id})).scalar_one_or_none()
                if active is None:
                    return ReceiveResult(None, "target_unavailable")
                existing = await self._existing(session, params)
                if existing is not None:
                    await session.commit()
                    return existing
                row = (await session.execute(text(
                    f"SELECT {_BINDING_COLUMNS} FROM public.im_account_bindings "
                    "WHERE tenant_id=:tenant_id AND provider=:provider "
                    "AND im_instance_id=:im_instance_id AND realm_id=:realm_id "
                    "AND im_user_id=:sender_id AND status='active'"
                ), params)).mappings().one_or_none()
                if row is None:
                    return ReceiveResult(None, "actor_unavailable")
                actor = DeliveryBinding(**dict(row))
                grant_id = (await session.execute(text(
                    "SELECT g.grant_id FROM public.personal_agent_grants g "
                    "JOIN public.personal_agent_materials m ON m.agent_id=g.agent_id "
                    "AND m.version=g.material_version WHERE g.agent_id=:agent "
                    "AND g.grantee_binding_id=:binding AND g.grantee_binding_version=:hash "
                    "AND g.revoked_at IS NULL AND m.withdrawn_at IS NULL "
                    "AND g.expires_at>clock_timestamp() AND m.expires_at>clock_timestamp()"
                ), {"agent": agent_id, "binding": actor.binding_id,
                    "hash": actor.version})).scalar_one_or_none()
                inbox_id = uuid4()
                queue_id = await defer_job(session, queue_name=GENERAL_QUEUE,
                                           task_name="personal_agents.answer",
                                           args={"inbox_id": str(inbox_id)})
                await session.execute(text(
                    "INSERT INTO public.im_inbox "
                    "(inbox_id,tenant_id,provider,im_instance_id,realm_id,"
                    "target_bot_id,agent_id,source_message_id,trigger_kind,payload_fingerprint,"
                    "actor_binding_id,actor_binding_version,conversation_ref,conversation_kind,"
                    "received_at,queue_job_id,grant_id,state,updated_at) VALUES "
                    "(:id,:tenant_id,:provider,:im_instance_id,:realm_id,:target_bot_id,:agent,"
                    ":source_message_id,:trigger_kind,:payload_fingerprint,:actor,:hash,"
                    ":conversation_ref,:conversation_kind,clock_timestamp(),:queue,:grant,"
                    "'queued',clock_timestamp())"
                ), {**params, "id": inbox_id, "agent": agent_id, "actor": actor.binding_id,
                    "hash": actor.version, "queue": queue_id, "grant": grant_id})
                await session.commit()
                return ReceiveResult(inbox_id, "queued")
        except (IntegrityError, SQLAlchemyError):
            # Includes an uncertain COMMIT: acknowledge only a confirmed durable row.
            async with self._session() as session:
                existing = await self._existing(session, params)
                if existing is not None:
                    return existing
            raise PersonalAgentError("personal_agent_receive_unavailable", 503) from None

    async def _context(self, session: AsyncSession, inbox_id: UUID) -> InboxContext:
        row = (await session.execute(text(
            "SELECT * FROM public.im_inbox WHERE inbox_id=:id"
        ), {"id": inbox_id})).mappings().one()
        agent_row = (await session.execute(text(
            "SELECT * FROM public.personal_agents WHERE agent_id=:id"
        ), {"id": row["agent_id"]})).mappings().one()
        agent = _agent(agent_row)
        actor = await self._binding(session, row["actor_binding_id"])
        owner = await self._binding(session, agent.owner_binding_id)
        event = InboundEvent(
            **{name: row[name] for name in (
                "provider", "im_instance_id", "realm_id", "target_bot_id", "source_message_id",
                "trigger_kind", "conversation_ref", "conversation_kind",
            )}, sender_id=actor.im_user_id,
            payload_fingerprint=bytes(row["payload_fingerprint"]),
        )
        return InboxContext(
            inbox_id, agent, actor, owner, bytes(row["actor_binding_version"]), event,
            row["grant_id"], row["state"],
        )

    @staticmethod
    def _identities_valid(context: InboxContext) -> bool:
        agent, actor, owner = context.agent, context.actor, context.owner
        return (
            agent.tenant_id == actor.tenant_id == owner.tenant_id == "default"
            and agent.status == actor.status == owner.status == "active"
            and actor.version == context.actor_binding_version
            and owner.version == agent.owner_binding_version
            and (agent.provider, agent.im_instance_id, agent.realm_id)
            == (actor.provider, actor.im_instance_id, actor.realm_id)
            == (owner.provider, owner.im_instance_id, owner.realm_id)
            and (context.event.provider, context.event.im_instance_id,
                 context.event.realm_id, context.event.target_bot_id)
            == (agent.provider, agent.im_instance_id, agent.realm_id, agent.bot_user_id)
        )

    async def claim_inbox(self, inbox_id: UUID) -> InboxContext | None:
        async with self._session(write=True) as session:
            changed = (await session.execute(text(
                "UPDATE public.im_inbox SET state='running',updated_at=clock_timestamp() "
                "WHERE inbox_id=:id AND state='queued' RETURNING inbox_id"
            ), {"id": inbox_id})).scalar_one_or_none()
            if changed is None:
                return None
            context = await self._context(session, inbox_id)
            if not self._identities_valid(context):
                await session.execute(text(
                    "UPDATE public.im_inbox SET state='failed',result_code='mapping_changed',"
                    "updated_at=clock_timestamp() WHERE inbox_id=:id"
                ), {"id": inbox_id})
                return None
            return context

    async def _locked(
        self, session: AsyncSession, context: InboxContext,
    ) -> tuple[InboxContext, RowMapping | None]:
        await session.execute(text(
            "SELECT agent_id FROM public.personal_agents WHERE agent_id=:id FOR UPDATE"
        ), {"id": context.agent.agent_id})
        grant = None
        if context.grant_id is not None:
            version = (await session.execute(text(
                "SELECT material_version FROM public.personal_agent_grants WHERE grant_id=:id"
            ), {"id": context.grant_id})).scalar_one()
            await session.execute(text(
                "SELECT version FROM public.personal_agent_materials WHERE agent_id=:id "
                "AND version=:version FOR UPDATE"
            ), {"id": context.agent.agent_id, "version": version})
            grant = (await session.execute(text(
                "SELECT * FROM public.personal_agent_grants WHERE grant_id=:id FOR UPDATE"
            ), {"id": context.grant_id})).mappings().one()
        await session.execute(text(
            "SELECT inbox_id FROM public.im_inbox WHERE inbox_id=:id FOR UPDATE"
        ), {"id": context.inbox_id})
        return await self._context(session, context.inbox_id), grant

    async def _authorized(
        self, session: AsyncSession, context: InboxContext, grant: RowMapping | None,
    ) -> AuthorizedMaterial | None:
        if not self._identities_valid(context) or grant is None:
            return None
        if (grant["agent_id"] != context.agent.agent_id
                or grant["grantee_binding_id"] != context.actor.binding_id
                or bytes(grant["grantee_binding_version"]) != context.actor.version
                or grant["revoked_at"] is not None):
            return None
        now = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
        if grant["expires_at"] <= now:
            return None
        row = (await session.execute(text(
            "SELECT * FROM public.personal_agent_materials WHERE agent_id=:agent "
            "AND version=:version AND withdrawn_at IS NULL AND expires_at>:now"
        ), {"agent": context.agent.agent_id, "version": grant["material_version"],
            "now": now})).mappings().one_or_none()
        return None if row is None else AuthorizedMaterial(grant["grant_id"], _material(row))

    async def material(self, context: InboxContext) -> AuthorizedMaterial | None:
        async with self._session() as session:
            current, grant = await self._locked(session, context)
            if current.state != "running":
                return None
            return await self._authorized(session, current, grant)

    @staticmethod
    async def _enqueue(
        session: AsyncSession, context: InboxContext, delivery_id: UUID,
        message: DeliveryMessage, slot: str,
    ) -> None:
        exists = (await session.execute(text(
            "SELECT delivery_id FROM public.im_deliveries "
            "WHERE origin_inbox_id=:id AND reply_slot=:slot"
        ), {"id": context.inbox_id, "slot": slot})).scalar_one_or_none()
        if exists is not None:
            return
        queue_id = await defer_job(
            session, queue_name=GENERAL_QUEUE, task_name="im_deliveries.send",
            args={"delivery_id": str(delivery_id)},
        )
        private = slot == "private"
        await session.execute(text(
            "INSERT INTO public.im_deliveries (delivery_id,tenant_id,initiator_ai_user_id,"
            "recipient_ai_user_id,recipient_binding_id,recipient_mapping_version,provider,"
            "im_instance_id,realm_id,sender_bot_id,target_conversation_ref,card_content,note,"
            "idempotency_key,state,created_at,queue_job_id,updated_at,origin_inbox_id,reply_slot) "
            "VALUES (:id,:tenant,:actor,:recipient,:binding,:hash,:provider,:instance,:realm,"
            ":bot,:conversation,:content,'',:key,'ready',clock_timestamp(),:queue,"
            "clock_timestamp(),:inbox,:slot)"
        ), {"id": delivery_id, "tenant": context.agent.tenant_id,
            "actor": context.actor.ai_user_id,
            "recipient": context.actor.ai_user_id if private else None,
            "binding": context.actor.binding_id if private else None,
            "hash": context.actor.version if private else None,
            "provider": message.provider, "instance": message.im_instance_id,
            "realm": message.realm_id, "bot": message.sender_bot_id,
            "conversation": message.target_conversation_ref, "content": message.card_content,
            "key": uuid4(), "queue": queue_id, "inbox": context.inbox_id, "slot": slot})

    async def enqueue_status(
        self, context: InboxContext, delivery_id: UUID, message: DeliveryMessage,
    ) -> None:
        async with self._session(write=True) as session:
            current, _ = await self._locked(session, context)
            if current.state == "running" and self._identities_valid(current):
                await self._enqueue(session, current, delivery_id, message, "group_status")

    async def complete(
        self, context: InboxContext, delivery_id: UUID, answer: DeliveryMessage | None,
        refusal: DeliveryMessage, result_code: str,
    ) -> None:
        async with self._session(write=True) as session:
            current, grant = await self._locked(session, context)
            if current.state != "running":
                return
            if not self._identities_valid(current):
                await session.execute(text(
                    "UPDATE public.im_inbox SET state='failed',result_code='mapping_changed',"
                    "updated_at=clock_timestamp() WHERE inbox_id=:id AND state='running'"
                ), {"id": current.inbox_id})
                return
            authorized = await self._authorized(session, current, grant)
            allowed = answer is not None and authorized is not None
            await self._enqueue(session, current, delivery_id,
                                answer if allowed and answer is not None else refusal, "private")
            await session.execute(text(
                "UPDATE public.im_inbox SET state=:state,result_code=:code,"
                "updated_at=clock_timestamp() WHERE inbox_id=:id AND state='running'"
            ), {"id": current.inbox_id, "state": "completed" if allowed else "refused",
                "code": result_code if allowed else "authorization_unavailable"})

    async def fail(self, inbox_id: UUID, code: str) -> None:
        async with self._session(write=True) as session:
            await session.execute(text(
                "UPDATE public.im_inbox SET state='failed',result_code=:code,"
                "updated_at=clock_timestamp() WHERE inbox_id=:id AND state IN ('queued','running')"
            ), {"id": inbox_id, "code": code})

    async def reply_context(self, delivery_id: UUID) -> InboxContext | None:
        async with self._session() as session:
            inbox_id = (await session.execute(text(
                "SELECT origin_inbox_id FROM public.im_deliveries WHERE delivery_id=:id "
                "AND state='ready' AND origin_inbox_id IS NOT NULL"
            ), {"id": delivery_id})).scalar_one_or_none()
            return None if inbox_id is None else await self._context(session, inbox_id)

    async def claim_reply(
        self, context: InboxContext, delivery_id: UUID, *, source_valid: bool,
        refusal: DeliveryMessage,
    ) -> tuple[DeliveryRecord, DeliveryMessage] | None:
        async with self._session(write=True) as session:
            current, grant = await self._locked(session, context)
            row = (await session.execute(text(
                "SELECT * FROM public.im_deliveries WHERE delivery_id=:id "
                "AND origin_inbox_id=:inbox AND state='ready' FOR UPDATE"
            ), {"id": delivery_id, "inbox": context.inbox_id})).mappings().one_or_none()
            if row is None:
                return None
            if not source_valid or not self._identities_valid(current):
                await session.execute(text(
                    "UPDATE public.im_deliveries SET state='cancelled',"
                    "result_code='source_unavailable',updated_at=clock_timestamp() "
                    "WHERE delivery_id=:id"
                ), {"id": delivery_id})
                return None
            private = row["reply_slot"] == "private"
            if private and current.state == "completed":
                if await self._authorized(session, current, grant) is None:
                    await session.execute(text(
                        "UPDATE public.im_inbox SET state='refused',"
                        "result_code='authorization_unavailable',updated_at=clock_timestamp() "
                        "WHERE inbox_id=:id AND state='completed'"
                    ), {"id": context.inbox_id})
                    await session.execute(text(
                        "UPDATE public.im_deliveries SET card_content=:content "
                        "WHERE delivery_id=:id AND state='ready'"
                    ), {"id": delivery_id, "content": refusal.card_content})
            elif private and current.state != "refused":
                return None
            row = (await session.execute(text(
                "UPDATE public.im_deliveries SET state='sending',send_attempt_id=:attempt,"
                "attempt_started_at=clock_timestamp(),updated_at=clock_timestamp() "
                "WHERE delivery_id=:id AND state='ready' RETURNING *"
            ), {"id": delivery_id, "attempt": uuid4()})).mappings().one()
            record = delivery_record(row)
            message = DeliveryMessage(
                record.provider, record.im_instance_id, record.realm_id, record.sender_bot_id,
                record.target_conversation_ref, record.card_content,
                kind="private" if private else "group_status",
            )
            return record, message

    async def requests(self, principal: Principal) -> list[RequestView]:
        async with self._session() as session:
            rows = (await session.execute(text(
                "SELECT i.*,a.display_name AS agent_display_name FROM public.im_inbox i "
                "JOIN public.personal_agents a ON a.agent_id=i.agent_id "
                "JOIN public.im_account_bindings b ON b.binding_id=i.actor_binding_id "
                "WHERE i.tenant_id=:tenant AND b.tenant_id=i.tenant_id "
                "AND b.ai_user_id=:user AND b.directory_user_id=:directory AND b.status='active' "
                "ORDER BY i.received_at DESC,i.inbox_id"
            ), {"tenant": principal.org_ctx.tenant_id, "user": principal.ai_user_id,
                "directory": principal.org_ctx.directory_user_id})).mappings().all()
            result = []
            for row in rows:
                replies = (await session.execute(text(
                    "SELECT delivery_id,reply_slot,state,result_code,remote_message_id "
                    "FROM public.im_deliveries WHERE origin_inbox_id=:id ORDER BY created_at"
                ), {"id": row["inbox_id"]})).mappings().all()
                # Status-only surface deliberately returns no cached answer body.
                result.append(RequestView(
                    **{name: row[name] for name in RequestView.model_fields
                       if name not in ("deliveries", "answer")},
                    deliveries=[ReplyView(**dict(reply)) for reply in replies],
                ))
            return result

    async def maintain_inbox(self) -> None:
        async with self._session(write=True) as session:
            # Pinned Procrastinate 3.10.0 worker ownership and heartbeat semantics.
            # A doing job is interrupted only if its worker is absent/stalled.
            await session.execute(text(
                "UPDATE public.im_inbox i SET state='failed',result_code='worker_interrupted',"
                "updated_at=clock_timestamp() WHERE i.state IN ('queued','running') AND ("
                f"NOT EXISTS (SELECT 1 FROM {QUEUE_SCHEMA}.procrastinate_jobs j "
                "WHERE j.id=i.queue_job_id) OR EXISTS ("
                f"SELECT 1 FROM {QUEUE_SCHEMA}.procrastinate_jobs j "
                f"LEFT JOIN {QUEUE_SCHEMA}.procrastinate_workers w ON w.id=j.worker_id "
                "WHERE j.id=i.queue_job_id AND j.task_name='personal_agents.answer' AND ("
                "j.status NOT IN ('todo','doing') OR (j.status='doing' AND (w.id IS NULL "
                "OR w.last_heartbeat < clock_timestamp() - interval '30 seconds')))))"
            ))
