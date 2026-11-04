"""Shared thin assembly for the API and the separate IM delivery worker."""

import ssl

import httpx2
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import IMDeliverySettings, PersonalAgentSettings
from app.im_deliveries import IMDeliveryService
from app.infra.communication.zulip import (
    ZulipCommunicationAdapter,
    ZulipDeliveryAdapter,
    ZulipInboundAdapter,
)
from app.infra.persistence.im_deliveries import PostgreSQLIMDeliveryStore
from app.infra.persistence.personal_agents import PostgreSQLPersonalAgentStore
from app.personal_agents import PersonalAgentService
from app.ports.communication import CommunicationDeliveryPort
from app.ports.personal_agent_runtime import PersonalAgentRuntimePort


def build_im_delivery(
    settings: IMDeliverySettings | None, sessions: async_sessionmaker[AsyncSession],
    personal_settings: PersonalAgentSettings | None = None,
) -> tuple[IMDeliveryService | None, httpx2.AsyncClient | None]:
    if settings is None:
        return None, None
    client = httpx2.AsyncClient(
        timeout=20.0, follow_redirects=False, trust_env=False,
        verify=ssl.create_default_context(cafile=settings.tls_ca_file),
    )
    return IMDeliveryService(
        PostgreSQLIMDeliveryStore(
            sessions, provider="zulip", im_instance_id=settings.im_instance_id,
            realm_id=settings.realm_id, recipients=settings.recipients,
        ),
        ZulipDeliveryAdapter(
            client=client, base_url=settings.origin, provider="zulip",
            im_instance_id=settings.im_instance_id, realm_id=settings.realm_id,
            sender_bot_id=settings.sender_bot_id, bot_email=settings.bot_email,
            bot_api_key=settings.bot_api_key, host_origin=settings.host_origin,
            bots=None if personal_settings is None else {
                bot.bot_id: (bot.bot_email, bot.bot_api_key) for bot in personal_settings.bots
            },
        ),
    ), client


def build_personal_agents(
    settings: PersonalAgentSettings | None, sessions: async_sessionmaker[AsyncSession],
    sender: CommunicationDeliveryPort,
    runtime: PersonalAgentRuntimePort | None = None,
) -> PersonalAgentService | None:
    if settings is None:
        return None
    zulip = settings.zulip
    bots = {bot.bot_id: (bot.bot_email, bot.bot_api_key) for bot in settings.bots}
    return PersonalAgentService(
        store=PostgreSQLPersonalAgentStore(sessions),
        inbound=ZulipInboundAdapter(
            ZulipCommunicationAdapter(zulip),
            webhook_tokens={bot.bot_id: bot.webhook_token for bot in settings.bots},
            bot_credentials=bots,
        ),
        sender=sender, runtime=runtime,
    )
