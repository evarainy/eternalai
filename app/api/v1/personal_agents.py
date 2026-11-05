"""Owner-confirmed publication/grants and requester-only processing status."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.api.v1.csrf import PrincipalDependency
from app.personal_agents import PersonalAgentService
from app.ports.auth import Principal
from app.ports.im_context import IMContextError
from app.ports.personal_agents import (
    AgentView,
    GranteeView,
    GrantMaterial,
    GrantView,
    MaterialView,
    PersonalAgentError,
    PublishMaterial,
    RequestView,
)

_NO_STORE = {"Cache-Control": "no-store"}


class PersonalAgentRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                raise HTTPException(422, detail={"code": "personal_agent_request_invalid",
                                                "message": "助手资料请求格式无效。"},
                                    headers=_NO_STORE) from None
            except PersonalAgentError as error:
                raise HTTPException(error.status_code, detail={"code": error.code,
                                                             "message": "助手资料请求未完成。"},
                                    headers=_NO_STORE) from None
            except IMContextError as error:
                raise HTTPException(error.http_status, detail={"code": error.code,
                                                             "message": "消息来源核验未完成。"},
                                    headers=_NO_STORE) from None
            response.headers.update(_NO_STORE)
            return response

        return handle


class AgentList(BaseModel):
    agents: list[AgentView]


class MaterialList(BaseModel):
    materials: list[MaterialView]


class GrantList(BaseModel):
    grants: list[GrantView]


class GranteeList(BaseModel):
    recipients: list[GranteeView]


class RequestList(BaseModel):
    requests: list[RequestView]


def make_router(
    service: PersonalAgentService | None, require_principal: PrincipalDependency,
) -> APIRouter:
    router = APIRouter(route_class=PersonalAgentRoute)

    def configured() -> PersonalAgentService:
        if service is None:
            raise PersonalAgentError("personal_agent_unavailable", 503)
        return service

    @router.get("", response_model=AgentList, operation_id="listPersonalAgents")
    async def list_agents(principal: Principal = Depends(require_principal)) -> AgentList:
        return AgentList(agents=await configured().store.agents(principal))

    @router.get("/requests", response_model=RequestList, operation_id="listPersonalAgentRequests")
    async def requests(principal: Principal = Depends(require_principal)) -> RequestList:
        return RequestList(requests=await configured().store.requests(principal))

    @router.get("/{agent_id}/materials", response_model=MaterialList,
                operation_id="listPersonalAgentMaterials")
    async def materials(
        agent_id: UUID, principal: Principal = Depends(require_principal),
    ) -> MaterialList:
        return MaterialList(materials=await configured().store.materials(principal, agent_id))

    @router.post("/{agent_id}/materials", response_model=MaterialView,
                 operation_id="publishPersonalAgentMaterial")
    async def publish(
        agent_id: UUID, body: PublishMaterial, principal: Principal = Depends(require_principal),
    ) -> MaterialView:
        return await configured().store.publish(principal, agent_id, body)

    @router.get("/{agent_id}/grants", response_model=GrantList,
                operation_id="listPersonalAgentGrants")
    async def grants(
        agent_id: UUID, principal: Principal = Depends(require_principal),
    ) -> GrantList:
        return GrantList(grants=await configured().store.grants(principal, agent_id))

    @router.get("/{agent_id}/recipients", response_model=GranteeList,
                operation_id="listPersonalAgentRecipients")
    async def recipients(
        agent_id: UUID, principal: Principal = Depends(require_principal),
    ) -> GranteeList:
        return GranteeList(recipients=await configured().store.recipients(principal, agent_id))

    @router.post("/{agent_id}/grants", response_model=GrantView,
                 operation_id="grantPersonalAgentMaterial")
    async def grant(
        agent_id: UUID, body: GrantMaterial, principal: Principal = Depends(require_principal),
    ) -> GrantView:
        return await configured().store.grant(principal, agent_id, body)

    @router.post("/{agent_id}/grants/{grant_id}/revoke", response_model=GrantView,
                 operation_id="revokePersonalAgentGrant")
    async def revoke(
        agent_id: UUID, grant_id: UUID, principal: Principal = Depends(require_principal),
    ) -> GrantView:
        return await configured().store.revoke(principal, agent_id, grant_id)

    @router.post("/{agent_id}/materials/{version}/withdraw", response_model=MaterialView,
                 operation_id="withdrawPersonalAgentMaterial")
    async def withdraw(
        agent_id: UUID, version: int, principal: Principal = Depends(require_principal),
    ) -> MaterialView:
        return await configured().store.withdraw(principal, agent_id, version)

    return router
