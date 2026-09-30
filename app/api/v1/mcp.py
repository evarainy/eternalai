"""Authenticated, service-scoped MCP connection and durable recovery endpoints."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.auth import PrincipalDependency
from app.mcp.models import McpFailure, OperationState, digest
from app.mcp.oauth import OAuthConnections
from app.mcp.operations import GovernedOperations
from app.ports.auth import AuthenticatedSessionContext, Principal, authenticated_session
from app.ports.human_gate import HumanGateDecisionRecord
from app.ports.workflow_engine import WorkflowEnginePort
from app.ports.workflow_store import WorkflowOperation


class ServiceView(BaseModel):
    service_config_id: str
    display_name: str
    enabled: bool


class ConnectionView(BaseModel):
    connection_id: str
    service_config_id: str
    state: str
    expires_at: datetime | None


class AuthorizationView(BaseModel):
    authorization_url: str


class OperationView(BaseModel):
    operation_id: str
    service_config_id: str
    state: OperationState
    revision: int
    expires_at: datetime
    action: str
    service_name: str
    argument_preview: dict[str, str | int | float | bool | None]
    preview_digest: str
    review_url: str | None = None
    recovery_action: Literal[
        "confirm", "recover", "read_original", "manual_reconcile", "takeover", "none"
    ]


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["confirm", "cancel", "recover", "reconcile", "takeover"]
    expected_revision: int = Field(ge=1)
    preview_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class McpApiService:
    def __init__(
        self, oauth: OAuthConnections, operations: GovernedOperations, workflows: WorkflowEnginePort
    ) -> None:
        self.oauth, self.operations, self.workflows = oauth, operations, workflows

    async def operation(
        self, operation_id: str, session: AuthenticatedSessionContext
    ) -> WorkflowOperation:
        op = await self.operations.store.load(
            operation_id,
            tenant_id=session.principal.org_ctx.tenant_id,
            user_id=session.principal.ai_user_id,
        )
        if op is None:
            raise McpFailure("mcp_operation_unavailable")
        await self.operations.owned(op)
        return op

    def view(self, op: WorkflowOperation, *, takeover: bool = False) -> OperationView:
        profile = self.oauth.configs.get(op.context.service_config_id)
        if (
            profile is None
            or not profile.enabled
            or profile.tenant_id != op.context.tenant_id
            or profile.service_config_version != op.context.service_config_version
        ):
            raise McpFailure("mcp_service_unavailable")
        snapshot = {
            "operation_id": op.operation_id,
            "service_config_id": op.context.service_config_id,
            "service_name": profile.display_name,
            "action": op.remote_tool,
            "arguments": op.argument_preview,
            "revision": op.revision,
            "action_digest": op.action_digest,
            "arguments_digest": op.canonical_args_digest,
        }
        return OperationView(
            operation_id=op.operation_id,
            service_config_id=op.context.service_config_id,
            state=op.state,
            revision=op.revision,
            expires_at=op.expires_at,
            action=op.remote_tool,
            service_name=profile.display_name,
            argument_preview=op.argument_preview,
            preview_digest=digest(snapshot),
            review_url=None if takeover else op.review_url,
            recovery_action="takeover"
            if takeover
            else "recover"
            if op.state in {"READY", "SENDING"}
            else "confirm"
            if op.state == "WAITING_LOCAL_CONFIRM"
            else "read_original"
            if op.state in {"UNKNOWN", "WAITING_EXTERNAL_CONFIRM"}
            and op.remote_tool in {"clothing_plan_submit", "talk_record_submit", "talk_task_claim"}
            else "manual_reconcile"
            if op.state == "UNKNOWN"
            else "none",
        )

    async def resume(self, op: WorkflowOperation, body: ResumeRequest) -> OperationView:
        if (
            op.revision != body.expected_revision
            or body.preview_digest != self.view(op).preview_digest
        ):
            raise McpFailure("mcp_operation_conflict")
        if body.action == "cancel":
            await self.workflows.discard_checkpoint(op.context.task_id)
        elif body.action == "confirm":
            request = await self.operations.store.confirmation(op)
            if request is None or op.state != "WAITING_LOCAL_CONFIRM":
                raise McpFailure("mcp_confirmation_unavailable")
            await self.operations.gates.record_decision(
                HumanGateDecisionRecord(
                    request_id=request.request_id,
                    task_id=op.context.task_id,
                    decided_by_ai_user_id=op.context.user_id,
                    decided_session_id=op.context.chat_session_id,
                    decided_tenant_id=op.context.tenant_id,
                    decision="confirmed",
                    request_digest=request.request_digest,
                    binding_manifest_digest=request.binding_manifest_digest,
                    decided_at=datetime.now(UTC),
                )
            )
            await self.workflows.resume(
                task_id=op.context.task_id, confirmed=True, expected_action_digest=op.action_digest
            )
        elif body.action == "reconcile":
            await self.operations.reconcile(op)
        elif body.action == "recover":
            await self.operations.recover(op)
        elif body.action == "takeover":
            await self.operations.takeover(op)
        updated = await self.operations.store.load(
            op.operation_id, tenant_id=op.context.tenant_id, user_id=op.context.user_id
        )
        if updated is None:
            raise McpFailure("mcp_operation_unavailable")
        return self.view(updated)

    async def readable_view(self, op: WorkflowOperation) -> OperationView:
        # Enforce a current owner/service grant before exposing even a safe summary.
        mapping = await self.operations.contexts.store.mapping(
            op.leaf_capability_id, op.leaf_version
        )
        if mapping is None:
            raise McpFailure("mcp_authorization_invalid")
        await self.operations.contexts.build(
            task_id=op.context.task_id,
            chat_session_id=op.context.chat_session_id,
            user_id=op.context.user_id,
            tenant_id=op.context.tenant_id,
            mapping=mapping,
        )
        try:
            await self.operations.owned(op)
        except McpFailure as exc:
            if exc.code != "mcp_authorization_invalid":
                raise
            return self.view(op, takeover=True)
        return self.view(op)


class _CallbackAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if (
                isinstance(args[2], str)
                and args[2].split("?", 1)[0] == "/api/v1/mcp/oauth/callback"
            ):
                args[2] = "/api/v1/mcp/oauth/callback"
                record.args = tuple(args)
        return True


class _SafeMcpRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def safe(request: Request) -> Response:
            try:
                return await handler(request)
            except HTTPException:
                raise
            except RequestValidationError:
                code, status = "mcp_request_invalid", 422
            except McpFailure:
                code, status = "mcp_action_unavailable", 409
            except Exception:
                code, status = "mcp_unavailable", 503
            return JSONResponse(
                status_code=status,
                content={
                    "detail": {
                        "code": code,
                        "message": "The requested operation could not be completed.",
                    }
                },
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )

        return safe


def make_router(
    service: McpApiService | None, principal_dependency: PrincipalDependency
) -> APIRouter:
    router = APIRouter(tags=["mcp"], route_class=_SafeMcpRoute)
    logging.getLogger("uvicorn.access").addFilter(_CallbackAccessFilter())

    async def session(
        response: Response, principal: Principal = Depends(principal_dependency)
    ) -> AuthenticatedSessionContext:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        context = authenticated_session.get()
        if context is None or context.principal != principal:
            raise HTTPException(401, detail={"code": "authentication_required"})
        return context

    def available() -> McpApiService:
        if service is None:
            raise HTTPException(503, detail={"code": "mcp_unavailable"})
        return service

    @router.get("/services", response_model=list[ServiceView])
    async def services(
        context: AuthenticatedSessionContext = Depends(session),
    ) -> list[ServiceView]:
        return [
            ServiceView(
                service_config_id=p.service_config_id,
                display_name=p.display_name,
                enabled=p.enabled,
            )
            for p in available().oauth.configs.values()
            if p.tenant_id == context.principal.org_ctx.tenant_id
        ]

    @router.get("/connections", response_model=list[ConnectionView])
    async def connections(
        context: AuthenticatedSessionContext = Depends(session),
    ) -> list[ConnectionView]:
        rows = await available().oauth.store.list_connections(
            tenant_id=context.principal.org_ctx.tenant_id, user_id=context.principal.ai_user_id
        )
        return [
            ConnectionView(
                connection_id=row.connection_id,
                service_config_id=row.service_config_id,
                state=row.state,
                expires_at=row.expires_at,
            )
            for row in rows
        ]

    @router.post("/connections/{service_config_id}/authorize", response_model=AuthorizationView)
    async def authorize(
        service_config_id: str, context: AuthenticatedSessionContext = Depends(session)
    ) -> AuthorizationView:
        current = available()
        await current.oauth.store.configure(current.oauth.service(service_config_id, context))
        return AuthorizationView(
            authorization_url=await current.oauth.authorize(service_config_id, context)
        )

    @router.get("/oauth/callback", response_model=Literal["authorization_received"])
    async def callback(
        request: Request, context: AuthenticatedSessionContext = Depends(session)
    ) -> str:
        query = request.query_params
        if set(query) != {"code", "state", "iss"} or any(len(query.getlist(k)) != 1 for k in query):
            raise McpFailure("mcp_authorization_invalid")
        await available().oauth.callback(
            context,
            state=query["state"],
            code=query["code"],
            issuer=query["iss"],
            callback_uri=str(request.url.replace(query="")),
        )
        return "authorization_received"

    @router.post("/connections/{connection_id}/disconnect", response_model=Literal["disconnected"])
    async def disconnect(
        connection_id: str, context: AuthenticatedSessionContext = Depends(session)
    ) -> str:
        changed = await available().oauth.store.disconnect(
            tenant_id=context.principal.org_ctx.tenant_id,
            user_id=context.principal.ai_user_id,
            connection_id=connection_id,
        )
        if not changed:
            raise McpFailure("mcp_connection_unavailable")
        return "disconnected"

    @router.get("/operations", response_model=list[OperationView])
    async def operations(
        context: AuthenticatedSessionContext = Depends(session),
    ) -> list[OperationView]:
        current = available()
        rows = await current.operations.store.list_owned(
            tenant_id=context.principal.org_ctx.tenant_id,
            user_id=context.principal.ai_user_id,
            service_config_ids=tuple(
                p.service_config_id
                for p in current.oauth.configs.values()
                if p.enabled and p.tenant_id == context.principal.org_ctx.tenant_id
            ),
        )
        result = []
        for op in rows:
            try:
                result.append(await current.readable_view(op))
            except McpFailure:
                continue
        return result

    @router.get("/operations/{operation_id}", response_model=OperationView)
    async def operation(
        operation_id: str, context: AuthenticatedSessionContext = Depends(session)
    ) -> OperationView:
        current = available()
        op = await current.operations.store.load(
            operation_id,
            tenant_id=context.principal.org_ctx.tenant_id,
            user_id=context.principal.ai_user_id,
        )
        if op is None:
            raise McpFailure("mcp_operation_unavailable")
        return await current.readable_view(op)

    @router.post("/operations/{operation_id}/resume", response_model=OperationView)
    async def resume(
        operation_id: str,
        body: ResumeRequest,
        context: AuthenticatedSessionContext = Depends(session),
    ) -> OperationView:
        current = available()
        if body.action == "takeover":
            op = await current.operations.store.load(
                operation_id,
                tenant_id=context.principal.org_ctx.tenant_id,
                user_id=context.principal.ai_user_id,
            )
            if op is None:
                raise McpFailure("mcp_operation_unavailable")
        else:
            op = await current.operation(operation_id, context)
        return await current.resume(op, body)

    return router
