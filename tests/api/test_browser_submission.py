"""Structured submission reuses authenticated admission without a Chat parser."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.api.v1.browser_runs import make_router
from app.main import create_app
from app.ports.auth import (
    AuthenticatedSessionContext,
    Principal,
    SessionBindingError,
    authenticated_session,
)
from app.ports.browser_chat import BrowserChatError, BrowserRunResponse
from app.ports.response_envelope import ResponseEnvelope, UIComponent
from tests.auth_fakes import (
    TEST_CSRF_ALLOWED_ORIGINS,
    TEST_CSRF_HEADERS,
    TEST_ORIGIN,
    MemorySessionRevocations,
    StaticSessionTokens,
    auth_cookies,
    make_session_binder,
)

_URL = "/api/v1/browser-runs"
_REQUEST_SESSION = "synthetic_session"
_BOUND_SESSION = "sid_v1.synthetic"
_OWNER = Principal(
    ai_user_id="synthetic_user", display_name="Synthetic", roles=(),
    org_ctx={"tenant_id": "synthetic_tenant"},
)
_HEADERS = {"x-test-csrf": "ok"}
_CANARY = "SYNTHETIC_PRIVATE_INPUT_CANARY"


def _body() -> dict[str, Any]:
    return {
        "session_id": _REQUEST_SESSION, "skill_id": "second_skill", "skill_version": "v2",
        "arguments": {"query": "synthetic", "filter": {"active": True}, "limit": 2},
        "client_request_id": "synthetic_request_1",
    }


def _envelope(
    status: Literal["running", "failed"] = "running", data: dict[str, Any] | None = None,
) -> ResponseEnvelope:
    return ResponseEnvelope(
        response_id="synthetic_response", task_id="synthetic_task", session_id=_BOUND_SESSION,
        status=status, message="Synthetic response", fallback_text="Synthetic response",
        ui=UIComponent(component_type="none", action="none"), trace_id="synthetic_trace",
        data=({"task_id": "synthetic_task", "run_id": "synthetic_run", "state_revision": 1}
              if data is None else data),
    )


class _Submission:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.contexts: list[AuthenticatedSessionContext | None] = []
        self.response = _envelope()
        self.error: Exception | None = None

    async def submit(
        self, *, principal: Principal, bound_session: str, skill_id: str,
        skill_version: str, arguments: dict[str, Any], client_request_id: str,
    ) -> ResponseEnvelope:
        self.calls.append({
            "principal": principal, "bound_session": bound_session, "skill_id": skill_id,
            "skill_version": skill_version, "arguments": arguments,
            "client_request_id": client_request_id,
        })
        self.contexts.append(authenticated_session.get())
        if self.error is not None:
            raise self.error
        return self.response

    async def get(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str,
    ) -> BrowserRunResponse:
        raise AssertionError("Submission must not perform a Run read")

    async def cancel(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str,
    ) -> BrowserRunResponse:
        raise AssertionError("Submission must not cancel a Run")

    async def decide_write(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str,
        decision: Literal["confirmed", "rejected"],
    ) -> BrowserRunResponse:
        raise AssertionError("Submission must not confirm a write")

    async def read_artifact(
        self, principal: Principal, bound_session: str, task_id: str,
        run_id: str, reference: str,
    ) -> tuple[str, bytes]:
        raise AssertionError("Submission must not read an artifact")


def _bind(principal: Principal, requested: str) -> str:
    if principal != _OWNER or requested != _REQUEST_SESSION:
        raise SessionBindingError("synthetic wrong session")
    return _BOUND_SESSION


def _client(
    submission: _Submission | None,
    *, binder: Callable[[Principal, str], str] | None = _bind,
    access: _Submission | None = None,
) -> TestClient:
    async def require_principal(request: Request) -> Principal:
        if request.headers.get("x-test-csrf") != "ok":
            raise HTTPException(403, {"code": "csrf_rejected"})
        return _OWNER

    app = FastAPI()
    app.include_router(
        make_router(access, require_principal, binder, submission=submission), prefix="/api/v1",
    )
    return TestClient(app)


def _assert_private(response: Any) -> None:
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert _CANARY not in response.text


def test_submit_delegates_exact_named_version_and_bound_subject_without_run_access() -> None:
    submission = _Submission()
    body = _body()
    with _client(submission) as client:
        response = client.post(_URL, headers=_HEADERS, json=body)
    assert response.status_code == 200
    assert response.json() == submission.response.model_dump(mode="json")
    assert submission.calls == [{
        "principal": _OWNER, "bound_session": _BOUND_SESSION, "skill_id": "second_skill",
        "skill_version": "v2", "arguments": body["arguments"],
        "client_request_id": "synthetic_request_1",
    }]
    assert "arguments" not in response.json()["data"]
    _assert_private(response)


@pytest.mark.parametrize("status,data", [
    ("running", {"kind": "pending", "task_id": "synthetic_task"}),
    ("failed", {"kind": "failed", "error_code": "browser_request_input_invalid"}),
])
def test_submit_keeps_pending_and_failed_envelopes_without_inventing_run_ids(
    status: Literal["running", "failed"], data: dict[str, Any],
) -> None:
    submission = _Submission()
    submission.response = _envelope(status, data)
    with _client(submission) as client:
        response = client.post(_URL, headers=_HEADERS, json=_body())
    assert response.status_code == 200
    assert response.json() == submission.response.model_dump(mode="json")
    assert "run_id" not in response.json()["data"]
    assert len(submission.calls) == 1
    _assert_private(response)


def test_repeated_submission_leaves_idempotency_and_version_selection_to_service() -> None:
    submission = _Submission()
    submission.response = _envelope(data={"kind": "pending", "task_id": "synthetic_task"})
    with _client(submission) as client:
        pending = client.post(_URL, headers=_HEADERS, json=_body())
        submission.response = _envelope()
        admitted = client.post(_URL, headers=_HEADERS, json=_body())
    assert pending.json()["data"] == {"kind": "pending", "task_id": "synthetic_task"}
    assert admitted.json()["data"]["run_id"] == "synthetic_run"
    assert len(submission.calls) == 2
    assert submission.calls[0] == submission.calls[1]
    _assert_private(pending)
    _assert_private(admitted)


@pytest.mark.parametrize("field", ["skill_id", "skill_version", "client_request_id"])
@pytest.mark.parametrize("value", ["", "invalid.dot", "x" * 97, 12, True, {"private": _CANARY}])
def test_submission_identifiers_are_strict_opaque_ids(field: str, value: object) -> None:
    submission = _Submission()
    body = _body()
    body[field] = value
    with _client(submission) as client:
        response = client.post(_URL, headers=_HEADERS, json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "browser_request_input_invalid"}}
    assert submission.calls == []
    _assert_private(response)


@pytest.mark.parametrize("value", ["", "x" * 193, 12, True, {"private": _CANARY}])
def test_submission_session_is_a_bounded_nonempty_string(value: object) -> None:
    submission = _Submission()
    body = _body()
    body["session_id"] = value
    with _client(submission) as client:
        response = client.post(_URL, headers=_HEADERS, json=body)
    assert response.status_code == 422
    assert submission.calls == []
    _assert_private(response)


@pytest.mark.parametrize("field", [
    "session_id", "skill_id", "skill_version", "arguments", "client_request_id",
])
def test_submission_requires_each_of_the_five_fields(field: str) -> None:
    submission = _Submission()
    body = _body()
    body.pop(field)
    with _client(submission) as client:
        response = client.post(_URL, headers=_HEADERS, json=body)
    assert response.status_code == 422
    assert submission.calls == []
    _assert_private(response)


@pytest.mark.parametrize("field", [
    "tenant_id", "user_id", "role", "principal", "authorization_evidence", "extra",
])
def test_submission_rejects_caller_authored_authority_and_extra_fields(field: str) -> None:
    submission = _Submission()
    body = _body()
    body[field] = _CANARY
    with _client(submission) as client:
        response = client.post(_URL, headers=_HEADERS, json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "browser_request_input_invalid"}}
    assert submission.calls == []
    _assert_private(response)


@pytest.mark.parametrize("value", [None, [], "private", 12, True])
def test_submission_arguments_must_be_a_json_object(value: object) -> None:
    submission = _Submission()
    body = _body()
    body["arguments"] = value
    with _client(submission) as client:
        response = client.post(_URL, headers=_HEADERS, json=body)
    assert response.status_code == 422
    assert submission.calls == []
    _assert_private(response)


@pytest.mark.parametrize("value,expected", [
    ("x" * (16_384 - 12), 200),
    ("x" * (16_384 - 11), 422),
    ("\u6c49" * 2728, 200),
    ("\u6c49" * 2729, 422),
])
def test_submission_argument_budget_matches_canonical_json_including_unicode(
    value: str, expected: int,
) -> None:
    submission = _Submission()
    body = _body()
    body["arguments"] = {"value": value}
    with _client(submission) as client:
        response = client.post(_URL, headers=_HEADERS, json=body)
    assert response.status_code == expected
    assert len(submission.calls) == (1 if expected == 200 else 0)
    if expected == 200:
        assert submission.calls[0]["arguments"] == {"value": value}
    else:
        assert response.json() == {"detail": {"code": "browser_request_input_invalid"}}
    _assert_private(response)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_submission_rejects_nonfinite_json_numbers(value: float) -> None:
    submission = _Submission()
    body = _body()
    body["arguments"] = {"value": value, "private": _CANARY}
    with _client(submission) as client:
        response = client.post(
            _URL, headers={**_HEADERS, "content-type": "application/json"},
            content=json.dumps(body, allow_nan=True),
        )
    assert response.status_code == 422
    assert submission.calls == []
    _assert_private(response)


def test_submission_malformed_json_never_reflects_body_values() -> None:
    submission = _Submission()
    with _client(submission) as client:
        response = client.post(
            _URL, headers={**_HEADERS, "content-type": "application/json"},
            content='{"arguments": "' + _CANARY + '",',
        )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "browser_request_input_invalid"}}
    assert submission.calls == []
    _assert_private(response)


@pytest.mark.parametrize("code,http_status", [
    ("authentication_required", 401),
    ("browser_binding_unavailable", 403),
    ("session_not_found", 404),
    ("browser_skill_not_configured", 409),
    ("browser_request_conflict", 409),
    ("browser_request_input_invalid", 422),
    ("browser_publication_unavailable", 503),
])
def test_submission_preserves_service_error_code_and_http_status(
    code: str, http_status: int,
) -> None:
    submission = _Submission()
    submission.error = BrowserChatError(code, http_status=http_status)
    with _client(submission) as client:
        response = client.post(_URL, headers=_HEADERS, json=_body())
    assert response.status_code == http_status
    assert response.json() == {"detail": {
        "code": code, "message": "Browser request was not accepted.",
    }}
    assert len(submission.calls) == 1
    _assert_private(response)


def test_submission_unexpected_service_failure_is_private_and_fail_closed() -> None:
    submission = _Submission()
    submission.error = RuntimeError(_CANARY)
    with _client(submission) as client:
        response = client.post(_URL, headers=_HEADERS, json=_body())
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "browser_chat_unavailable"}}
    assert len(submission.calls) == 1
    _assert_private(response)


def test_submission_requires_explicit_typed_injection_even_when_access_has_submit() -> None:
    access = _Submission()
    with _client(None, access=access) as client:
        response = client.post(_URL, headers=_HEADERS, json=_body())
    assert response.status_code == 503
    assert access.calls == []
    _assert_private(response)


@pytest.mark.parametrize("failure,expected", [
    ("missing_binder", 401), ("other_session", 404), ("binder_error", 503),
])
def test_submission_binding_failures_precede_admission(failure: str, expected: int) -> None:
    submission = _Submission()
    body = _body()

    def broken_binder(principal: Principal, requested: str) -> str:
        raise RuntimeError(_CANARY)

    binder = None if failure == "missing_binder" else (
        broken_binder if failure == "binder_error" else _bind
    )
    if failure == "other_session":
        body["session_id"] = "other_session"
    with _client(submission, binder=binder) as client:
        response = client.post(_URL, headers=_HEADERS, json=body)
    assert response.status_code == expected
    assert submission.calls == []
    _assert_private(response)


def test_create_app_wires_no_chat_submission_to_real_auth_csrf_and_session_binder() -> None:
    submission = _Submission()
    tokens = StaticSessionTokens(roles=())
    binder = make_session_binder()
    app = create_app(
        session_tokens=tokens, session_revocations=MemorySessionRevocations(),
        session_binder=binder, csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        browser_submission=submission,
    )
    with TestClient(app, base_url=TEST_ORIGIN) as client:
        client.cookies.update(auth_cookies())
        response = client.post(_URL, headers=TEST_CSRF_HEADERS, json=_body())
    assert response.status_code == 200
    assert response.json() == submission.response.model_dump(mode="json")
    assert len(submission.calls) == 1
    assert submission.calls[0]["principal"] == tokens.principal
    assert submission.calls[0]["bound_session"] == binder(tokens.principal, _REQUEST_SESSION)
    assert len(submission.contexts) == 1
    assert submission.contexts[0] is not None
    assert submission.contexts[0].principal == tokens.principal
    _assert_private(response)


@pytest.mark.parametrize("failure,expected", [
    ("no_auth", 401), ("revoked", 401), ("no_csrf", 403), ("wrong_origin", 403),
    ("other_owner_session", 404),
])
def test_create_app_rejects_auth_csrf_and_cross_owner_session_before_submission(
    failure: str, expected: int,
) -> None:
    submission = _Submission()
    tokens = StaticSessionTokens(roles=())
    revocations = MemorySessionRevocations()
    binder = make_session_binder()
    headers = dict(TEST_CSRF_HEADERS)
    body = _body()
    if failure == "revoked":
        revocations.revoked.add(tokens.inspect(auth_cookies()["eternalai_session"]).fingerprint)
    if failure == "no_csrf":
        headers.pop("X-EternalAI-CSRF")
    if failure == "wrong_origin":
        headers["Origin"] = "https://synthetic-attacker.invalid"
    if failure == "other_owner_session":
        body["session_id"] = binder(_OWNER, _REQUEST_SESSION)
    app = create_app(
        session_tokens=tokens, session_revocations=revocations,
        session_binder=binder, csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        browser_submission=submission,
    )
    with TestClient(app, base_url=TEST_ORIGIN) as client:
        if failure != "no_auth":
            client.cookies.update(auth_cookies())
        response = client.post(_URL, headers=headers, json=body)
    assert response.status_code == expected
    assert submission.calls == []
    _assert_private(response)
