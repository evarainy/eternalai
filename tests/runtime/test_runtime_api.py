"""Runtime API router tests."""

from __future__ import annotations

from typing import Any, get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.auth import make_require_principal
from app.api.v1.runtime import (
    ActionResponseData,
    ActionResponseEnvelope,
    make_router,
)
from app.contracts.sdui.models import UserAction
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.main import create_app
from app.ports.auth import Principal
from app.ports.response_envelope import ResponseEnvelope
from app.ports.runtime import UserActionOutcome
from tests.auth_fakes import (
    TEST_CSRF_ALLOWED_ORIGINS,
    TEST_CSRF_HEADERS,
    StaticSessionTokens,
    auth_cookies,
    make_session_binder,
)


class FakeRuntime:
    def __init__(self, action_data: dict[str, Any] | None = None) -> None:
        self.calls = 0
        self.ai_user_ids: list[str] = []
        self.session_ids: list[str] = []
        self.action_data = (
            {
                "action_outcome": "accepted",
                "result": None,
            }
            if action_data is None
            else action_data
        )

    async def handle_user_message(
        self,
        channel: str,
        ai_user_id: str,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
    ) -> ResponseEnvelope:
        self.calls += 1
        self.ai_user_ids.append(ai_user_id)
        self.session_ids.append(session_id)
        return ResponseEnvelopeBuilder().build_message(
            response_id="response-1",
            task_id="task-1",
            session_id=session_id,
            message="ok",
            fallback_text="ok",
            trace_id="trace-1",
            status="completed",
        )

    async def handle_user_action(
        self,
        channel: str,
        principal: Principal,
        session_id: str,
        action: UserAction,
    ) -> ResponseEnvelope:
        del channel, action
        self.calls += 1
        self.ai_user_ids.append(principal.ai_user_id)
        self.session_ids.append(session_id)
        return ResponseEnvelopeBuilder().build_message(
            response_id="response-action",
            task_id="task-action",
            session_id=session_id,
            message="accepted",
            fallback_text="accepted",
            trace_id="trace-action",
            data=self.action_data,
        )


def _client(runtime: FakeRuntime | None = None) -> TestClient:
    session_tokens = StaticSessionTokens()
    app = FastAPI()
    app.include_router(
        make_router(
            runtime or FakeRuntime(),
            make_require_principal(session_tokens),
            make_session_binder(),
        ),
        prefix="/api/v1/runtime",
    )
    client = TestClient(app, base_url="https://testserver")
    client.cookies.update(auth_cookies())
    return client


def _valid_body() -> dict[str, Any]:
    return {
        "channel": "web",
        "session_id": "session-1",
        "message": "hello",
        "client_capabilities": {},
    }


def _valid_action_body() -> dict[str, Any]:
    return {
        "channel": "web",
        "session_id": "session-1",
        "action": {
            "action_type": "confirm",
            "response_id": "response-1",
            "confirmed": True,
        },
    }


def test_runtime_handle_endpoint_returns_response_envelope_json_with_injected_runtime() -> None:
    runtime = FakeRuntime()
    response = _client(runtime).post("/api/v1/runtime/handle", json=_valid_body())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["schema_version"] == "phase0.sdui.v1"
    assert body["task_id"] == "task-1"
    assert body["session_id"].startswith("sid_v1.")
    assert body["trace_id"] == "trace-1"
    assert runtime.ai_user_ids == ["usr_v1_synthetic"]
    assert runtime.session_ids == [body["session_id"]]


def test_runtime_action_endpoint_dispatches_only_structured_user_action() -> None:
    runtime = FakeRuntime()
    response = _client(runtime).post(
        "/api/v1/runtime/action",
        json=_valid_action_body(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == {"action_outcome": "accepted", "result": None}
    assert runtime.ai_user_ids == ["usr_v1_synthetic"]
    assert runtime.session_ids == [body["session_id"]]


def test_runtime_action_endpoint_returns_exact_rejection_data_shape() -> None:
    runtime = FakeRuntime(
        action_data={
            "action_outcome": "action_reference_mismatch",
            "result": None,
        }
    )

    response = _client(runtime).post(
        "/api/v1/runtime/action",
        json=_valid_action_body(),
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "action_outcome": "action_reference_mismatch",
        "result": None,
    }


def test_runtime_action_endpoint_serializes_projected_result_object() -> None:
    runtime = FakeRuntime(
        action_data={
            "action_outcome": "accepted",
            "result": {"safe": "ok"},
        }
    )

    response = _client(runtime).post(
        "/api/v1/runtime/action",
        json=_valid_action_body(),
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "action_outcome": "accepted",
        "result": {"safe": "ok"},
    }


def test_action_response_data_accepts_every_outcome_and_rejects_unknown() -> None:
    outcomes = get_args(UserActionOutcome.__value__)

    for outcome in outcomes:
        assert (
            ActionResponseData(action_outcome=outcome, result=None).action_outcome
            == outcome
        )

    with pytest.raises(ValidationError):
        ActionResponseData(action_outcome="unknown", result=None)


@pytest.mark.parametrize(
    "invalid_data",
    (
        {"action_outcome": "accepted"},
        {"result": None},
        {
            "action_outcome": "accepted",
            "result": None,
            "unexpected": "blocked",
        },
        {
            "action_outcome": "accepted",
            "result": None,
            "business_key": "must-stay-inside-result",
        },
    ),
)
def test_action_response_envelope_rejects_missing_extra_or_flattened_data(
    invalid_data: dict[str, Any],
) -> None:
    valid_envelope = ResponseEnvelopeBuilder().build_message(
        response_id="response-action-invalid",
        task_id="task-action-invalid",
        session_id="session-action-invalid",
        message="invalid",
        fallback_text="invalid",
        trace_id="trace-action-invalid",
        data=invalid_data,
    )

    with pytest.raises(ValidationError):
        ActionResponseEnvelope.model_validate(valid_envelope.model_dump())


def test_runtime_action_endpoint_rejects_free_text_and_extra_fields() -> None:
    runtime = FakeRuntime()
    body = _valid_action_body()
    body["message"] = "confirm response-1"

    response = _client(runtime).post("/api/v1/runtime/action", json=body)

    assert response.status_code == 422
    assert runtime.calls == 0


def test_runtime_handle_endpoint_rejects_extra_fields() -> None:
    body = _valid_body()
    body["extra_field"] = "not allowed"

    response = _client().post("/api/v1/runtime/handle", json=body)

    assert response.status_code == 422


def test_runtime_handle_endpoint_cannot_inject_roles() -> None:
    runtime = FakeRuntime()
    body = _valid_body()
    body["roles"] = ["admin"]

    response = _client(runtime).post("/api/v1/runtime/handle", json=body)

    assert response.status_code == 422
    assert runtime.calls == 0


def test_formal_app_runtime_route_fails_closed_without_provider() -> None:
    session_tokens = StaticSessionTokens()
    client = TestClient(
        create_app(
            session_tokens=session_tokens,
            session_binder=make_session_binder(),
            session_cookie_ttl_seconds=3600,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        ),
        base_url="https://testserver",
    )
    client.cookies.update(auth_cookies())
    response = client.post(
        "/api/v1/runtime/handle",
        headers=TEST_CSRF_HEADERS,
        json=_valid_body(),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "runtime_unavailable",
            "message": "Runtime provider is not configured.",
        }
    }


def test_formal_app_runtime_route_validates_before_unavailable() -> None:
    body = _valid_body()
    body["extra_field"] = "not allowed"

    session_tokens = StaticSessionTokens()
    client = TestClient(
        create_app(
            session_tokens=session_tokens,
            session_binder=make_session_binder(),
            session_cookie_ttl_seconds=3600,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        ),
        base_url="https://testserver",
    )
    client.cookies.update(auth_cookies())
    response = client.post(
        "/api/v1/runtime/handle",
        headers=TEST_CSRF_HEADERS,
        json=body,
    )

    assert response.status_code == 422


def test_missing_principal_precedes_body_validation() -> None:
    body = _valid_body()
    body["extra_field"] = "not allowed"

    response = TestClient(
        create_app(csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS)
    ).post(
        "/api/v1/runtime/handle",
        json=body,
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "authentication_required"
