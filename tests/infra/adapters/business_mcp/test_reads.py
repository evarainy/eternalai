from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.event_loop import make_event_loop
from app.mcp.contracts import INPUT_SCHEMAS, WRITE_TOOLS
from app.ports.capability_gateway import RequestOrgContext
from app.ports.human_gate import build_task_version_binding_manifest
from app.ports.task_store import TaskRecord
from app.version_binding import capability_version_bindings
from tests.infra.mcp.test_transport import serving
from tests.mcp.test_contracts import VALID
from tests.workflow.test_mcp_recovery import BusinessPeer, harness


@pytest.mark.parametrize("tool", sorted(set(INPUT_SCHEMAS) - WRITE_TOOLS))
def test_seven_reads_use_real_identity_gateway_sdk_and_exact_parameters(
    migrated_database_url, tool
):
    peer = BusinessPeer("2026-07-28")
    with serving(peer) as profile:

        async def run():
            async with harness(migrated_database_url, profile, "talk_preparation_save") as h:
                task = uuid4().hex
                capability_id = f"business.{profile.service_config_id}.{tool}"
                spec = await h["registry"].get(capability_id)
                await h["tasks"].create_task(
                    TaskRecord(
                        task_id=task,
                        session_id="synthetic-chat",
                        ai_user_id="synthetic-user",
                        tenant_id="default",
                        status="running",
                    )
                )
                await h["gates"].bind_task(
                    build_task_version_binding_manifest(
                        task_id=task,
                        bindings=capability_version_bindings(spec),
                        locked_at=datetime.now(UTC),
                    )
                )
                result = await h["gateway"].execute_capability(
                    task,
                    "synthetic-chat",
                    "synthetic-user",
                    capability_id,
                    VALID[tool],
                    RequestOrgContext(request_id=uuid4().hex, tenant_id="default"),
                )
                assert result.status == "completed" and result.data == {"synthetic": True}
                calls = [body for _, _, body in peer.calls if body["method"] == "tools/call"]
                assert len(calls) == 1 and calls[0]["params"]["name"] == tool
                assert calls[0]["params"]["arguments"] == VALID[tool]
                assert peer.effects == 1
                bad = await h["gateway"].execute_capability(
                    task,
                    "synthetic-chat",
                    "synthetic-user",
                    capability_id,
                    VALID[tool],
                    RequestOrgContext(request_id="wrong-tenant", tenant_id="other"),
                )
                assert bad.status == "denied" and peer.effects == 1

        asyncio.run(run(), loop_factory=make_event_loop)
