"""Shared thin assembly for the API and the separate IM delivery worker."""

import ssl

import httpx2
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import IMDeliverySettings
from app.im_deliveries import IMDeliveryService
from app.infra.communication.zulip import ZulipDeliveryAdapter
from app.infra.persistence.im_deliveries import PostgreSQLIMDeliveryStore


def build_im_delivery(
    settings: IMDeliverySettings | None, sessions: async_sessionmaker[AsyncSession],
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
        ),
    ), client
