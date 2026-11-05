"""One explicitly shared text, one verified IM requester and one tool-free answer."""

from __future__ import annotations

import logging
from dataclasses import fields
from uuid import UUID, uuid4

from app.ports.auth import Principal, PrincipalOrgContext
from app.ports.communication import (
    CommunicationDeliveryPort,
    CommunicationInboundPort,
    DeliveryMessage,
    IMAccountBinding,
    InboundEvent,
)
from app.ports.im_context import IMContextError
from app.ports.im_deliveries import DeliveryBinding, DeliveryRecord
from app.ports.personal_agent_runtime import (
    PersonalAgentRuntimeError,
    PersonalAgentRuntimePort,
    PublishedMaterialRequest,
)
from app.ports.personal_agents import (
    REFUSAL_TEXT,
    InboxContext,
    PersonalAgent,
    PersonalAgentError,
    PersonalAgentStorePort,
    ReceiveResult,
)
from app.ports.request_context import RequestOrgContext

_LOGGER = logging.getLogger("uvicorn.error.personal_agents")


def _reader_binding(binding: DeliveryBinding) -> IMAccountBinding:
    return IMAccountBinding(**{
        item.name: getattr(binding, item.name) for item in fields(IMAccountBinding)
    })


class PersonalAgentService:
    def __init__(
        self, store: PersonalAgentStorePort, inbound: CommunicationInboundPort,
        sender: CommunicationDeliveryPort, runtime: PersonalAgentRuntimePort | None = None,
    ) -> None:
        self.store = store
        self._inbound = inbound
        self._sender = sender
        self._runtime = runtime

    async def receive(self, agent_id: UUID, payload: dict[str, object]) -> ReceiveResult:
        agent = await self.store.agent(agent_id)
        authenticated = self._inbound.authenticate_callback(
            target_bot_id=agent.bot_user_id, payload=payload,
        )
        event = authenticated.event
        delegate_agent_id = None
        if (event.conversation_kind == "group" and event.trigger_kind == "mention"
                and "委托" in authenticated.text):
            actor = await self.store.inbound_actor(agent, event)
            if actor is None:
                return ReceiveResult(None, "actor_unavailable")
            agents = await self._delegation(actor, event)
            if agents is not None:
                source, target = agents
                await self._inbound.reread_trigger(
                    _reader_binding(actor), event,
                    delegate_bot_id=(target if agent.agent_id == source.agent_id
                                     else source).bot_user_id,
                )
                if agent.agent_id != source.agent_id:
                    _LOGGER.info(
                        "personal_agent_delegation source_agent_id=%s target_agent_id=%s "
                        "actor=%s result_code=not_delegation_source stage=receive",
                        source.agent_id, target.agent_id, actor.ai_user_id,
                    )
                    return ReceiveResult(None, "not_delegation_source")
                delegate_agent_id = target.agent_id
        result = await self.store.accept(agent_id, event, delegate_agent_id=delegate_agent_id)
        _LOGGER.info(
            "personal_agent_received inbox_id=%s source_agent_id=%s target_agent_id=%s "
            "result_code=%s stage=receive",
            result.inbox_id, agent_id, delegate_agent_id, result.result_code,
        )
        return result

    async def _delegation(
        self, actor: DeliveryBinding, event: InboundEvent,
    ) -> tuple[PersonalAgent, PersonalAgent] | None:
        bot_ids = await self._inbound.delegation_mentions(_reader_binding(actor), event)
        if bot_ids is None:
            return None
        return await self.store.delegation_agents(actor, bot_ids)

    def _refusal(self, context: InboxContext, delivery_id: UUID) -> DeliveryMessage:
        return self._sender.prepare_agent_reply(
            delivery_id=delivery_id, sender_bot_id=context.execution_agent.bot_user_id,
            recipient_im_user_id=context.actor.im_user_id, text=REFUSAL_TEXT,
        )

    async def _question(self, context: InboxContext) -> str:
        if context.delegation is None:
            verified = await self._inbound.reread_trigger(
                _reader_binding(context.actor), context.event,
            )
        else:
            agents = await self._delegation(context.actor, context.event)
            if (agents is None or agents[0].agent_id != context.agent.agent_id
                    or agents[1].agent_id != context.execution_agent.agent_id):
                raise IMContextError("im_source_changed")
            verified = await self._inbound.reread_trigger(
                _reader_binding(context.actor), context.event,
                delegate_bot_id=context.execution_agent.bot_user_id,
            )
            await self._inbound.check_identity(_reader_binding(context.execution_owner))
        await self._inbound.check_identity(_reader_binding(context.owner))
        return verified.question

    async def answer(self, inbox_id: UUID) -> None:
        try:
            context = await self.store.claim_inbox(inbox_id)
            if context is None:
                return
            if context.delegation is not None:
                _LOGGER.info(
                    "personal_agent_delegation inbox_id=%s source_agent_id=%s "
                    "target_agent_id=%s actor=%s stage=source_reread",
                    inbox_id, context.agent.agent_id, context.execution_agent.agent_id,
                    context.actor.ai_user_id,
                )
            question = await self._question(context)
            if context.event.conversation_kind == "group":
                status_id = uuid4()
                status = self._sender.prepare_group_status(
                    delivery_id=status_id, sender_bot_id=context.agent.bot_user_id,
                    conversation_ref=context.event.conversation_ref,
                    delegate_display_name=(context.execution_agent.display_name
                                           if context.delegation is not None else None),
                )
                await self.store.enqueue_status(context, status_id, status)
            authorized = await self.store.material(context)
            delivery_id = uuid4()
            answer = None
            code = "authorization_unavailable"
            if authorized is not None:
                if self._runtime is None:
                    raise PersonalAgentError("personal_agent_runtime_unavailable", 503)
                material = authorized.material
                actor = context.actor
                principal = Principal(
                    ai_user_id=actor.ai_user_id, display_name=actor.ai_user_id, roles=(),
                    org_ctx=PrincipalOrgContext(
                        tenant_id=actor.tenant_id, directory_user_id=actor.directory_user_id,
                    ),
                )
                _LOGGER.info(
                    "personal_agent_model inbox_id=%s initiator=%s execution_subject=%s "
                    "target_agent_id=%s grant_id=%s version=%s",
                    inbox_id, actor.ai_user_id, actor.ai_user_id, context.execution_agent.agent_id,
                    authorized.grant_id, material.version,
                )
                result = await self._runtime.answer_published_material(PublishedMaterialRequest(
                    inbox_id=inbox_id, principal=principal,
                    target_agent_id=context.execution_agent.agent_id, grant_id=authorized.grant_id,
                    material_version=material.version,
                    content_sha256=bytes.fromhex(material.content_sha256),
                    question=question, material=material.body,
                    org_context=RequestOrgContext(
                        request_id=str(inbox_id), tenant_id=actor.tenant_id, channel="im",
                    ),
                ))
                text = (
                    f"AI 根据{context.execution_agent.display_name}的发布资料代答，"
                    "未经本人核实。\n\n"
                    f"{result.text}\n\n资料：{material.title} · v{material.version}\n"
                    f"SHA256：{material.content_sha256}"
                )
                answer = self._sender.prepare_agent_reply(
                    delivery_id=delivery_id, sender_bot_id=context.execution_agent.bot_user_id,
                    recipient_im_user_id=actor.im_user_id, text=text,
                )
                code = result.result_code
            await self.store.complete(
                context, delivery_id, answer, self._refusal(context, delivery_id), code,
            )
            _LOGGER.info("personal_agent_finished inbox_id=%s", inbox_id)
        except (PersonalAgentError, PersonalAgentRuntimeError, IMContextError) as error:
            await self.store.fail(inbox_id, error.code)
            _LOGGER.info("personal_agent_failed inbox_id=%s code=%s", inbox_id, error.code)

    async def claim_reply(
        self, delivery_id: UUID,
    ) -> tuple[DeliveryRecord, DeliveryMessage] | None:
        context = await self.store.reply_context(delivery_id)
        if context is None:
            return None
        valid = True
        try:
            await self._question(context)
        except IMContextError:
            valid = False
        return await self.store.claim_reply(
            context, delivery_id, source_valid=valid, refusal=self._refusal(context, delivery_id),
        )

    async def maintain_inbox(self) -> None:
        await self.store.maintain_inbox()
