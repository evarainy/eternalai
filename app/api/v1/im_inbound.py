"""Bot-authenticated callbacks ACK only after Inbox and queue share one commit."""

from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.api.v1.personal_agents import PersonalAgentRoute
from app.personal_agents import PersonalAgentService
from app.ports.personal_agents import PersonalAgentError


class InboundAck(BaseModel):
    response_not_required: bool = True
    inbox_id: UUID | None
    result_code: str


def make_router(service: PersonalAgentService | None) -> APIRouter:
    router = APIRouter(route_class=PersonalAgentRoute)

    @router.post("/{agent_id}", response_model=InboundAck, operation_id="receivePersonalAgentIM")
    async def receive(agent_id: UUID, request: Request) -> InboundAck:
        if service is None:
            raise PersonalAgentError("personal_agent_unavailable", 503)
        try:
            payload = await request.json()
        except ValueError:
            raise PersonalAgentError("im_callback_invalid", 422) from None
        if not isinstance(payload, dict):
            raise PersonalAgentError("im_callback_invalid", 422)
        result = await service.receive(agent_id, payload)
        return InboundAck(inbox_id=result.inbox_id, result_code=result.result_code)

    return router
