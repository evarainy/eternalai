from __future__ import annotations

import asyncio

from app.event_loop import make_event_loop
from app.infra.identity.mcp_identity import McpIdentityMapping
from app.infra.identity.unconfigured import UnconfiguredIdentityMapping
from app.ports.capability_gateway import RequestOrgContext
from tests.infra.persistence.test_mcp_store import bind, config, database


def test_identity_requires_explicit_service_and_preserves_password_mapping_boundary(
    migrated_database_url,
):
    async def run():
        async with database(migrated_database_url) as (store, _, _):
            await bind(store, config())
            identity = McpIdentityMapping(UnconfiguredIdentityMapping(), store)
            context = RequestOrgContext(request_id="synthetic", tenant_id="default")

            async def resolve(service=None, user="synthetic-user", target="business_platform"):
                return await identity.resolve_execution_identity(
                    user, target, "user_delegated", context, service_config_id=service
                )

            assert (await resolve("service-a")).bind_status == "active"
            assert (await resolve()).bind_status == "unbound"
            assert (await resolve("service-b")).bind_status == "unbound"
            assert (await resolve("service-a", user="other-user")).bind_status == "unbound"
            assert (
                await identity.get_mapping(
                    "synthetic-user", "business_platform", tenant_id="default"
                )
                is None
            )
            assert (
                await identity.list_mappings(
                    "synthetic-user", "business_platform", tenant_id="default"
                )
                == []
            )
            previous = await UnconfiguredIdentityMapping().resolve_execution_identity(
                "synthetic-user", "oa", "user_delegated", context
            )
            assert await resolve(target="oa") == previous
            connection = await store.connection(
                tenant_id="default", user_id="synthetic-user", service_config_id="service-a"
            )
            await store.disconnect(
                tenant_id="default",
                user_id="synthetic-user",
                connection_id=connection.connection_id,
            )
            assert (await resolve("service-a")).bind_status == "unbound"

    asyncio.run(run(), loop_factory=make_event_loop)
