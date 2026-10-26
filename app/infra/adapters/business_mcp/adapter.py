"""Validate before dispatch; separate deterministic validation and visibility."""

from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import anyio

from app.infra.adapters.business_mcp.fixed_query_contracts import (
    approved_fixed_query_projection,
    record_fixed_query_failure,
)
from app.infra.adapters.business_mcp.writes import validate_bound_task
from app.infra.sdui.response_projection import (
    project_approved_response_data,
    project_response_data,
)
from app.mcp.contracts import SUBMIT_TOOLS, WRITE_TOOLS, OutputContract, validate_input
from app.mcp.diagnostics import McpLogRefs, mcp_trace_scope, record_mcp_event
from app.mcp.models import McpFailure, ServiceConfig, digest
from app.ports.adapter import AdapterResult, AdapterTraceMetadata
from app.ports.mcp import (
    McpAuthorizationContext,
    McpDriverPort,
    McpSubmitPreconditionPort,
    McpTaskRulesPort,
    McpValidatedOutcome,
    McpValidatedRead,
)
from app.ports.mcp_store import McpStorePort
from app.ports.response_projection_contract import ApprovedResponseProjectionError
from app.ports.workflow_store import GovernedWorkflowAuthorization, WorkflowStorePort

if TYPE_CHECKING:
    from app.infra.adapters.business_mcp.fixed_read_profile import FixedReadProfile


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
        fixed_read: FixedReadProfile | None = None,
    ) -> None:
        if not isolated_test_contracts:
            for contract in contracts.values():
                contract.require_production()
        self.driver, self.store, self.contracts = driver, store, contracts
        self.workflows, self.profiles = workflows, profiles or {}
        self.task_rules = task_rules
        self.submit_preconditions = dict(submit_preconditions or {})
        self.fixed_read = fixed_read
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

    def _read_commit_lock(
        self, context: McpAuthorizationContext, write: bool
    ) -> anyio.Lock | None:
        fixed = self.fixed_read
        if (
            fixed is None
            or write
            or context.service_config_id != fixed.service.service_config_id
            or fixed.approval.local_owner_source != "authenticated_session"
        ):
            return None
        return fixed.authorization_lock

    async def _read_still_authorized(
        self, context: McpAuthorizationContext, tool: str, profile: ServiceConfig | None
    ) -> None:
        """A rebind after sending voids this read's result; the request itself stays sent."""
        assert self.fixed_read is not None
        try:
            if not self.fixed_read.permits(
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                service_config_id=context.service_config_id,
                tool=tool,
                require_ready=True,
            ):
                raise McpFailure("mcp_authorization_invalid")
            await self.store.resolve(context)
        except McpFailure:
            record_mcp_event(
                "fixed_read_commit",
                "failed",
                profile=profile,
                tool=tool,
                task_id=context.task_id,
                connection_id=context.connection_id,
                error_code="fixed_read_authorization_changed",
            )
            raise

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        trace_id = execution_context.get("trace_id", "")
        with mcp_trace_scope(trace_id if isinstance(trace_id, str) else ""):
            return await self._execute(capability_id, arguments, execution_context)

    async def _execute(
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
        try:
            # Exact factory rules for this service/tool only; resolved before sending.
            projection = (
                None
                if write
                else approved_fixed_query_projection(mapping.service_config_id, tool, policy)
            )
        except McpFailure:
            return AdapterResult(status="error", error_code="mcp_contract_unconfirmed")
        called = False
        commit_lock: anyio.Lock | None = None
        started = monotonic()
        profile = self.profiles.get(context.service_config_id)
        refs: McpLogRefs = {"task_id": context.task_id, "connection_id": context.connection_id}
        record_mcp_event("adapter_execution", "started", profile=profile, tool=tool, **refs)
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
            if not write and result.get("isError", False) is not False:
                structured = result.get("structuredContent")
                error = structured.get("error") if isinstance(structured, dict) else None
                remote_code = error.get("code") if isinstance(error, dict) else None
                # These fixed-read recovery codes are documented by the provider.
                # Reject only the grant frozen in this call's authorization context.
                if tool == "business_context_get" and remote_code in (
                    "GRANT_EXPIRED_OR_REVOKED",
                    "INVALID_GRANT",
                    "SESSION_EXPIRED",
                    "AUTH_REQUIRED",
                ):
                    await self.store.reject(context)
                    raise McpFailure("mcp_authorization_rejected")
                # Neither the provider's message nor unknown code enters logs.
                raise McpFailure(
                    "mcp_permission_denied"
                    if remote_code == "PERMISSION_DENIED"
                    else "mcp_tool_rejected"
                )
            lock = self._read_commit_lock(context, write)
            if lock is not None:
                # Held through the synchronous projection and result below,
                # so a rebind is ordered either wholly before or after it.
                await lock.acquire()
                commit_lock = lock
                await self._read_still_authorized(context, tool, profile)
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
                        public_result=project_response_data(
                            policy.public_result(validated),
                            policy.public_result_schema(),
                        ),
                    )
                else:
                    raise McpFailure("mcp_postcondition_unconfirmed", may_have_sent=True)
            if write:
                # Preserve the governed write outcome path.
                model_data, ui_data = (
                    policy.project(validated, "model"),
                    policy.project(validated, "ui"),
                )
                public_data: dict[str, Any] | None = {
                    k: v for k, v in model_data.items() if k in ui_data
                }
            else:
                # Every read uses the same recursive, credential-aware public
                # projection before Runtime or a formatter can see its data.
                public_schema = policy.public_result_schema()
                if projection is not None:
                    try:
                        public_data = project_approved_response_data(
                            policy.public_result(validated), public_schema, projection
                        )
                    except ApprovedResponseProjectionError as rejected:
                        projection_failure = rejected
                        record_fixed_query_failure(
                            "fixed_query_projection",
                            lambda: f"projection_rejected:{projection_failure.reason}",
                            profile=profile,
                            tool=tool,
                            **refs,
                        )
                        raise McpFailure("mcp_output_projection_invalid") from None
                else:
                    public_data = project_response_data(
                        policy.public_result(validated), public_schema
                    )
                if public_data is None:
                    raise McpFailure("mcp_output_projection_invalid")
            record_mcp_event(
                "adapter_projection",
                "success",
                profile=profile,
                tool=tool,
                started_at=started,
                **refs,
            )
            return AdapterResult(
                status="success",
                data=public_data,
                mcp_outcome=outcome,
                mcp_read=McpValidatedRead(data=validated) if not write else None,
            )
        except McpFailure as exc:
            record_mcp_event(
                "adapter_execution",
                "failed",
                profile=profile,
                tool=tool,
                error_code=exc.code,
                started_at=started,
                **refs,
            )
            if not write:
                metadata = AdapterTraceMetadata(
                    failure_stage="normalization" if called else "provider_transport",
                )
                if exc.code in {"mcp_timeout", "mcp_queue_timeout"}:
                    return AdapterResult(
                        status="timeout", error_code="adapter_timeout", trace_metadata=metadata
                    )
                if exc.code == "mcp_permission_denied":
                    return AdapterResult(
                        status="permission_denied",
                        error_code="upstream_permission_denied",
                        trace_metadata=metadata,
                    )
                if exc.code in {"mcp_authorization_rejected", "mcp_authorization_invalid"}:
                    return AdapterResult(
                        status="permission_denied",
                        error_code="identity_expired",
                        trace_metadata=metadata,
                    )
                if exc.code in {
                    "mcp_tool_rejected",
                    "mcp_http_failed",
                    "mcp_transport_failed",
                    "mcp_queue_full",
                }:
                    return AdapterResult(
                        status="error", error_code="adapter_error", trace_metadata=metadata
                    )
            return AdapterResult(
                status="error",
                error_code="mcp_outcome_unknown"
                if write and (called or exc.may_have_sent)
                else "adapter_payload_invalid",
            )
        finally:
            if commit_lock is not None:
                commit_lock.release()
