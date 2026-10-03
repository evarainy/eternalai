"""Regression checks for validation/auth failures before browser admission."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.v1.runtime import make_router
from app.ports.auth import Principal, SessionBindingError


def _principal() -> Principal:
    return Principal(ai_user_id="synthetic_user", display_name="Synthetic",
                     roles=(), org_ctx={"tenant_id": "synthetic_tenant"})


def _client(
    principal: Callable[[], Principal] = _principal,
    binder: Callable[[Principal, str], str] | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(make_router(None, principal, binder), prefix="/runtime")
    return TestClient(app)


def _body() -> dict[str, object]:
    return {"channel": "web", "session_id": "synthetic_session", "message": "read",
            "client_capabilities": {"browser_async_v1": True}, "client_request_id": "request"}


@pytest.mark.parametrize("field", ["channel", "message", "client_request_id", "extra"])
def test_handle_validation_never_reflects_private_input(field: str) -> None:
    marker = "synthetic_private_input_marker"
    body = _body()
    body[field] = marker if field in {"channel", "extra"} else {"private": marker}
    with _client() as client:
        response = client.post("/runtime/handle", json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "runtime_request_input_invalid"}}
    assert marker not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


@pytest.mark.parametrize("failure", ["authentication", "binding", "missing_binder"])
def test_handle_early_authorization_errors_are_not_cacheable(failure: str) -> None:
    def principal() -> Principal:
        if failure == "authentication":
            raise HTTPException(401, {"code": "authentication_required"})
        return _principal()

    def binder(principal: Principal, session: str) -> str:
        raise SessionBindingError("synthetic binding failure")

    with _client(principal, None if failure == "missing_binder" else binder) as client:
        response = client.post("/runtime/handle", json=_body())
    assert response.status_code == (404 if failure == "binding" else 401)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
