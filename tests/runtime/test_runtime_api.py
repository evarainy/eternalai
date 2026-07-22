"""Runtime API router tests."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.runtime import make_router
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.main import create_app
from app.ports.response_envelope import ResponseEnvelope


class FakeRuntime:
    def __init__(self) -> None:
        self.calls = 0

    async def handle_user_message(
        self,
        channel: str,
        ai_user_id: str,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
    ) -> ResponseEnvelope:
        self.calls += 1
        return ResponseEnvelopeBuilder().build_message(
            response_id="response-1",
            task_id="task-1",
            session_id=session_id,
            message="ok",
            fallback_text="ok",
            trace_id="trace-1",
            status="completed",
        )


def _client(runtime: FakeRuntime | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(make_router(runtime or FakeRuntime()), prefix="/api/v1/runtime")
    return TestClient(app)


def _valid_body() -> dict[str, Any]:
    return {
        "channel": "web",
        "ai_user_id": "ai-user-1",
        "session_id": "session-1",
        "message": "hello",
        "client_capabilities": {},
    }


def test_runtime_handle_endpoint_returns_response_envelope_json_with_injected_runtime() -> None:
    response = _client().post("/api/v1/runtime/handle", json=_valid_body())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["schema_version"] == "phase0.sdui.v1"
    assert body["task_id"] == "task-1"
    assert body["session_id"] == "session-1"
    assert body["trace_id"] == "trace-1"


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
    response = TestClient(create_app()).post(
        "/api/v1/runtime/handle",
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

    response = TestClient(create_app()).post(
        "/api/v1/runtime/handle",
        json=body,
    )

    assert response.status_code == 422
