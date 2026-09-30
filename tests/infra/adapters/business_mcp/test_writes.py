from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.event_loop import make_event_loop
from app.mcp.contracts import WRITE_TOOLS
from app.ports.capability_gateway import RequestOrgContext
from app.ports.mcp import McpTaskRuleEvidence
from app.ports.workflow_store import GovernedWorkflowAuthorization
from tests.infra.mcp.test_transport import serving
from tests.mcp.test_contracts import VALID
from tests.workflow.test_mcp_recovery import (
    BusinessPeer,
    SyntheticSubmitPrecondition,
    confirm,
    harness,
)


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_adapter_direct_call_cannot_bypass_durable_permission(migrated_database_url, tool):
    peer = BusinessPeer("2025-11-25")
    with serving(peer) as profile:

        async def run():
            async with harness(migrated_database_url, profile, tool) as h:
                op = h["op"]
                result = await h["adapter"].execute(
                    op.leaf_capability_id,
                    op.arguments,
                    {
                        "mcp_authorization": op.context.model_copy(
                            update={"workflow_authorization_ref": "forged"}
                        ),
                        "confirmed": True,
                    },
                )
                assert result.status != "success"
                assert peer.calls == [] and peer.effects == 0
                policy = await h["policy"].preview_capability(
                    ai_user_id="synthetic-user",
                    capability_id=op.leaf_capability_id,
                    request_context=RequestOrgContext(request_id="synthetic", tenant_id="default"),
                )
                assert policy == "exclude"

        asyncio.run(run(), loop_factory=make_event_loop)


@pytest.mark.parametrize("tool", ["clothing_plan_submit", "talk_record_submit"])
@pytest.mark.parametrize("missing", ["human_confirmation", "write_permit"])
def test_valid_external_submit_permission_never_replaces_local_gate(
    migrated_database_url, tool, missing
):
    peer = BusinessPeer("2025-11-25")
    with serving(peer) as profile:

        async def run():
            async with harness(migrated_database_url, profile, tool) as h:

                class VerifiedExternal(SyntheticSubmitPrecondition):
                    verified = 0

                    async def verify(self, context, name, arguments):
                        self.verified += 1
                        return await super().verify(context, name, arguments)

                provider = VerifiedExternal()
                h["adapter"].submit_preconditions[(profile.service_config_id, tool)] = provider
                gate_id = None
                if missing == "write_permit":
                    await confirm(h)
                    gate_id = (await h["workflows"].confirmation(h["op"])).request_id
                ready = await h["workflows"].transition(
                    h["op"], state="READY", gate_request_id=gate_id, attempt_id=uuid4().hex
                )
                context = {"mcp_authorization": ready.context}
                if missing == "human_confirmation":
                    context["workflow_authorization"] = GovernedWorkflowAuthorization(
                        operation_id=ready.operation_id,
                        attempt_id=ready.attempt_id,
                        expected_revision=ready.revision,
                    )
                async with h["workflows"].execution_guard(ready):
                    result = await h["adapter"].execute(
                        ready.leaf_capability_id, ready.arguments, context
                    )
                assert peer.effects == 0
                assert provider.verified == 1
                assert result.status == "error" and result.error_code == "adapter_payload_invalid"
                assert peer.calls == [] and peer.effects == 0
                saved = await h["workflows"].by_task(h["task"])
                assert (
                    saved.state == "READY"
                    and saved.send_started is False
                    and saved.revision == ready.revision
                )

        asyncio.run(run(), loop_factory=make_event_loop)


@pytest.mark.parametrize(
    "case", ["approved", "missing", "other_person", "other_service", "previous_week"]
)
def test_bound_record_requires_owned_company_rule_and_shanghai_week(migrated_database_url, case):
    peer = BusinessPeer("2025-11-25")
    with serving(peer) as profile:

        async def run():
            arguments = {**VALID["talk_record_draft_save"], "taskId": "a" * 32}
            async with harness(
                migrated_database_url, profile, "talk_record_draft_save", arguments=arguments
            ) as h:

                class Rules:
                    async def resolve(self, context, task_id):
                        assert task_id == arguments["taskId"]
                        if case == "missing":
                            return None
                        return McpTaskRuleEvidence(
                            tenant_id=context.tenant_id,
                            user_id=context.user_id,
                            service_config_id="other"
                            if case == "other_service"
                            else context.service_config_id,
                            task_id=task_id,
                            person_id="other" if case == "other_person" else arguments["personId"],
                            week_start="2026-09-21" if case == "previous_week" else "2026-09-28",
                            policy_version="synthetic-1",
                        )

                h["adapter"].task_rules = Rules()
                await confirm(h)
                result = await h["workflow"].resume(
                    task_id=h["task"], confirmed=True, expected_action_digest=h["op"].action_digest
                )
                assert result.output["state"] == (
                    "VERIFIED_SUCCESS" if case == "approved" else "CANCELLED"
                )
                assert peer.effects == (1 if case == "approved" else 0)
                assert len(peer.calls) > 0 if case == "approved" else peer.calls == []

        asyncio.run(run(), loop_factory=make_event_loop)
