"""Validate before dispatch; separate deterministic validation and visibility."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from app.infra.adapters.business_mcp.writes import validate_bound_task
from app.mcp.contracts import SUBMIT_TOOLS, WRITE_TOOLS, OutputContract, validate_input
from app.mcp.models import McpFailure, ServiceConfig, digest
from app.ports.adapter import AdapterResult
from app.ports.mcp import (
    McpAuthorizationContext,
    McpDriverPort,
    McpSubmitPreconditionPort,
    McpTaskRulesPort,
    McpValidatedOutcome,
    McpValidatedRead,
)
from app.ports.mcp_store import McpStorePort
from app.ports.workflow_store import GovernedWorkflowAuthorization, WorkflowStorePort


class BusinessMcpAdapter:
    def __init__(
        self,
        driver: McpDriverPort,
        store: McpStorePort,
        contracts: dict[tuple[str, str], OutputContract],
        *,
        isolated_test_contracts: bool = False,
        workflows: WorkflowStorePort | None = None,
        profiles: dict[str, ServiceConfig] | None = None,
        task_rules: McpTaskRulesPort | None = None,
        submit_preconditions: dict[tuple[str, str], McpSubmitPreconditionPort] | None = None,
    ) -> None:
        if not isolated_test_contracts:
            for contract in contracts.values():
                contract.require_production()
        self.driver, self.store, self.contracts = driver, store, contracts
        self.workflows, self.profiles = workflows, profiles or {}
        self.task_rules = task_rules
        self.submit_preconditions = dict(submit_preconditions or {})
        if (
            any(p.synthetic for p in self.submit_preconditions.values())
            and not isolated_test_contracts
        ):
            raise McpFailure("mcp_submit_contract_unconfirmed")

    async def _submit_allowed(
        self, context: McpAuthorizationContext, tool: str, arguments: dict[str, Any]
    ) -> None:
        provider = self.submit_preconditions.get((context.service_config_id, tool))
        if provider is None or not provider.approved or not provider.version:
            raise McpFailure("mcp_submit_contract_unconfirmed")
        operation = (
            await self.workflows.load(
                context.operation_id or "", tenant_id=context.tenant_id, user_id=context.user_id
            )
            if self.workflows is not None
            else None
        )
        if (
            operation is None
            or operation.context != context
            or operation.state not in {"READY", "SENDING"}
            or operation.canonical_args_digest != digest(arguments)
            or operation.expires_at <= datetime.now(UTC)
            or (
                operation.artifact_expires_at is not None
                and operation.artifact_expires_at <= datetime.now(UTC)
            )
        ):
            raise McpFailure("mcp_submit_precondition_denied")
        permit = await provider.verify(context, tool, arguments)
        if (
            permit is None
            or permit.context != context
            or context.operation_id is None
            or permit.remote_tool != tool
            or permit.arguments_digest != digest(arguments)
            or permit.policy_version != provider.version
            or len(permit.evidence_digest) != 64
            or any(c not in "0123456789abcdef" for c in permit.evidence_digest)
            or permit.valid_until.tzinfo is None
            or permit.valid_until <= datetime.now(UTC)
        ):
            raise McpFailure("mcp_submit_precondition_denied")

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        context = execution_context.get("mcp_authorization")
        if type(context) is not McpAuthorizationContext or context.capability_id != capability_id:
            return AdapterResult(status="permission_denied", error_code="policy_denied")
        mapping = await self.store.mapping(capability_id, context.capability_version)
        if mapping is None or mapping.service_config_id != context.service_config_id:
            return AdapterResult(status="permission_denied", error_code="policy_denied")
        tool = mapping.remote_tool
        write = tool in WRITE_TOOLS
        if write and (not mapping.internal_only or not context.workflow_authorization_ref):
            return AdapterResult(status="permission_denied", error_code="policy_denied")
        policy = self.contracts.get((mapping.service_config_id, tool))
        if policy is None or policy.version != mapping.output_contract_version:
            return AdapterResult(status="error", error_code="mcp_contract_unconfirmed")
        called = False
        try:
            validate_input(tool, arguments, now=datetime.now(UTC))

            async def before_send() -> None:
                await self._submit_allowed(context, tool, arguments)

            if tool in SUBMIT_TOOLS:
                await before_send()
            if tool == "talk_record_draft_save" and "taskId" in arguments:
                rule = (
                    await self.task_rules.resolve(context, arguments["taskId"])
                    if self.task_rules
                    else None
                )
                validate_bound_task(context, arguments, rule)
            if write:
                authorization = execution_context.get("workflow_authorization")
                if (
                    type(authorization) is not GovernedWorkflowAuthorization
                    or self.workflows is None
                ):
                    raise McpFailure("mcp_workflow_authorization_invalid")
                await self.workflows.consume(
                    authorization, context, capability_id=capability_id, arguments=arguments
                )
            result = await self.driver.call(
                context,
                tool,
                arguments,
                input_digest=mapping.input_digest,
                safety_digest=mapping.safety_digest,
                write=write,
                before_send=before_send if tool in SUBMIT_TOOLS else None,
            )
            called = True
            validated = policy.validate(result)
            outcome = None
            if write:
                review_url = policy.external_confirmation(validated)
                if review_url is not None:
                    profile = self.profiles.get(context.service_config_id)
                    parsed = urlsplit(review_url)
                    origin = urlsplit(profile.issuer) if profile else None
                    if (
                        origin is None
                        or parsed.scheme != origin.scheme
                        or parsed.netloc != origin.netloc
                        or parsed.username
                        or parsed.password
                        or parsed.fragment
                        or any(ord(char) < 33 for char in review_url)
                    ):
                        raise McpFailure("mcp_confirmation_url_invalid", may_have_sent=True)
                    outcome = McpValidatedOutcome(
                        state="WAITING_EXTERNAL_CONFIRM",
                        persistence=policy.project(validated, "persistence"),
                        review_url=review_url,
                    )
                elif policy.postcondition(validated):
                    outcome = McpValidatedOutcome(
                        state="VERIFIED_SUCCESS",
                        persistence=policy.project(validated, "persistence"),
                    )
                else:
                    raise McpFailure("mcp_postcondition_unconfirmed", may_have_sent=True)
            # Runtime's current response serves both model and UI: expose their intersection only.
            model_data, ui_data = (
                policy.project(validated, "model"),
                policy.project(validated, "ui"),
            )
            return AdapterResult(
                status="success",
                data={k: v for k, v in model_data.items() if k in ui_data},
                mcp_outcome=outcome,
                mcp_read=McpValidatedRead(data=validated) if not write else None,
            )
        except McpFailure as exc:
            return AdapterResult(
                status="error",
                error_code="mcp_outcome_unknown"
                if write and (called or exc.may_have_sent)
                else "adapter_payload_invalid",
            )
