"""Chat parsing adapter for the controlled browser submission service."""

from __future__ import annotations

from typing import Any, Literal

from app.browser_skill.submission import (
    BrowserSubmissionService,
)
from app.browser_skill.submission import (
    _validate_schema as _validate_schema,
)
from app.ports.auth import Principal
from app.ports.browser_chat import BrowserChatParserPort, BrowserRunResponse
from app.ports.capability_registry import CapabilitySpec
from app.ports.response_envelope import ResponseEnvelope


class BrowserChatService:
    def __init__(
        self, submission: BrowserSubmissionService, parser: BrowserChatParserPort,
    ) -> None:
        self.submission = submission
        self._parser = parser

    @property
    def skill_id(self) -> str:
        return self.submission.skill_id

    def owner_cache_scope(self, principal: Principal) -> tuple[str, str]:
        return self.submission.owner_cache_scope(principal)

    async def start(
        self, *, channel: Literal["web", "cli", "api", "mock"], principal: Principal,
        bound_session: str, message: str, client_capabilities: dict[str, Any],
        client_request_id: str, skill_id: str,
    ) -> ResponseEnvelope:
        async def parse(capability: CapabilitySpec) -> dict[str, Any]:
            return await self._parser.parse(principal, message, capability)

        return await self.submission.submit_chat(
            channel=channel, principal=principal, bound_session=bound_session,
            message=message, client_capabilities=client_capabilities,
            client_request_id=client_request_id, skill_id=skill_id,
            arguments_for=parse,
        )

    async def get(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str,
    ) -> BrowserRunResponse:
        return await self.submission.get(principal, bound_session, task_id, run_id)

    async def cancel(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str,
    ) -> BrowserRunResponse:
        return await self.submission.cancel(principal, bound_session, task_id, run_id)

    async def decide_write(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str,
        decision: Literal["confirmed", "rejected"],
    ) -> BrowserRunResponse:
        return await self.submission.decide_write(
            principal, bound_session, task_id, run_id, decision,
        )

    async def read_artifact(
        self, principal: Principal, bound_session: str, task_id: str,
        run_id: str, reference: str,
    ) -> tuple[str, bytes]:
        return await self.submission.read_artifact(
            principal, bound_session, task_id, run_id, reference,
        )
