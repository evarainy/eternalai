"""Runtime HTTP response-contract regression tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.contracts.sdui.models import UserAction
from app.main import create_app
from app.ports.auth import Principal
from app.ports.response_envelope import ResponseEnvelope, UIComponent
from tests.auth_fakes import (
    TEST_CSRF_ALLOWED_ORIGINS,
    TEST_CSRF_HEADERS,
    StaticSessionTokens,
    auth_cookies,
)

BaselineCase = Literal["null-optionals", "rich-payload"]

_LEGACY_RESPONSE_BYTES: dict[BaselineCase, bytes] = {
    "null-optionals": (
        b'{"schema_version":"phase0.sdui.v1","response_id":"response-null",'
        b'"task_id":"task-null","session_id":"sid-contract","status":"completed",'
        b'"message":"ok","fallback_text":"ok","ui":{"component_type":"none",'
        b'"action":null,"target_system":null,"reason_code":null,"payload":{}},'
        b'"data":null,"trace_id":"trace-null","trace_summary":null}'
    ),
    "rich-payload": (
        b'{"schema_version":"phase0.sdui.v1","response_id":"response-rich",'
        b'"task_id":"task-rich","session_id":"sid-contract",'
        b'"status":"waiting_user","message":"\xe8\xaf\xb7\xe7\xa1\xae\xe8\xae\xa4",'
        b'"fallback_text":"please confirm","ui":{"component_type":"confirm_card",'
        b'"action":"confirm","target_system":"oa",'
        b'"reason_code":"needs_confirmation","payload":{"nested":{"items":'
        b'[1,true,null]},"when":"2026-08-02T01:02:03Z","amount":"12.50",'
        b'"marker":"ready"}},"data":{"count":2,"rows":[{"name":"\xe7\x94\xb2"},'
        b'{"name":"\xe4\xb9\x99"}]},"trace_id":"trace-rich",'
        b'"trace_summary":"summary"}'
    ),
}


class PayloadMarker(str, Enum):
    READY = "ready"


class BaselineRuntime:
    async def handle_user_message(
        self,
        channel: str,
        ai_user_id: str,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
    ) -> ResponseEnvelope:
        del channel, ai_user_id, client_capabilities
        if message == "null-optionals":
            return ResponseEnvelope(
                response_id="response-null",
                task_id="task-null",
                session_id=session_id,
                status="completed",
                message="ok",
                fallback_text="ok",
                ui=UIComponent(component_type="none"),
                data=None,
                trace_id="trace-null",
                trace_summary=None,
            )
        return ResponseEnvelope(
            response_id="response-rich",
            task_id="task-rich",
            session_id=session_id,
            status="waiting_user",
            message="请确认",
            fallback_text="please confirm",
            ui=UIComponent(
                component_type="confirm_card",
                action="confirm",
                target_system="oa",
                reason_code="needs_confirmation",
                payload={
                    "nested": {"items": [1, True, None]},
                    "when": datetime(2026, 8, 2, 1, 2, 3, tzinfo=timezone.utc),
                    "amount": Decimal("12.50"),
                    "marker": PayloadMarker.READY,
                },
            ),
            data={"count": 2, "rows": [{"name": "甲"}, {"name": "乙"}]},
            trace_id="trace-rich",
            trace_summary="summary",
        )

    async def handle_user_action(
        self,
        channel: str,
        principal: Principal,
        session_id: str,
        action: UserAction,
    ) -> ResponseEnvelope:
        del channel, principal, action
        return await self.handle_user_message(
            "api",
            "user-action",
            session_id,
            "null-optionals",
            {},
        )


def _bind_session(_principal: Principal, _session_id: str) -> str:
    return "sid-contract"


def _runtime_route(app: FastAPI) -> APIRoute:
    route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/v1/runtime/handle"
    )
    assert isinstance(route, APIRoute)
    return route


@pytest.mark.parametrize("message", tuple(_LEGACY_RESPONSE_BYTES))
def test_runtime_handle_declares_envelope_without_changing_response_bytes(
    message: BaselineCase,
) -> None:
    app = create_app(
        runtime=BaselineRuntime(),
        session_tokens=StaticSessionTokens(),
        session_binder=_bind_session,
        session_cookie_ttl_seconds=3600,
        csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
    )
    client = TestClient(app, base_url="https://testserver")
    client.cookies.update(auth_cookies())

    response = client.post(
        "/api/v1/runtime/handle",
        headers=TEST_CSRF_HEADERS,
        json={
            "channel": "web",
            "session_id": "browser-session",
            "message": message,
            "client_capabilities": {},
        },
    )

    assert response.status_code == 200
    assert response.content == _LEGACY_RESPONSE_BYTES[message]
    assert _runtime_route(app).response_model is ResponseEnvelope
