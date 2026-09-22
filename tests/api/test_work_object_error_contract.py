"""List error contracts through real HTTP validation, HMAC and auth dependencies."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from app.api.v1.auth import make_require_principal
from app.api.v1.csrf import make_csrf_protected_principal, make_require_csrf
from app.api.v1.work_objects import WorkObjectListResponse, make_router
from app.event_loop import make_event_loop
from app.infra.auth.crypto import HMACSessionToken
from app.ports.auth import Principal, PrincipalOrgContext
from app.ports.work_object import OASyncStatusView
from tests.auth_fakes import TEST_CSRF_ALLOWED_ORIGINS, TEST_CSRF_HEADERS, MemorySessionRevocations

BASE = "/api/v1/work-objects"
FIXED = {
    "detail": {
        "code": "work_object_lifecycle_request_invalid",
        "message": "Invalid work object lifecycle request.",
    }
}


class RecordingService:
    def __init__(self):
        self.calls = []

    async def list_for_principal(self, principal, **kwargs):
        self.calls.append((principal, kwargs))
        return WorkObjectListResponse(
            items=[],
            oa_sync=OASyncStatusView(
                status="never",
                revision=0,
                attempt_revision=0,
                last_attempt_at=None,
                last_success_at=None,
                failure_code=None,
            ),
            limit=200,
            limit_exceeded=False,
        )

    async def get_for_principal(self, object_id, principal):
        self.calls.append((principal, {"object_id": object_id}))
        return None


class RecordingRevocations(MemorySessionRevocations):
    failed = False
    reads = 0

    async def is_revoked(self, fingerprint):
        self.reads += 1
        if self.failed:
            raise RuntimeError("SYNTHETIC_REVOCATION_READ_FAULT")
        return await super().is_revoked(fingerprint)


@pytest.fixture
def http():
    clock = [datetime.now(UTC).timestamp()]
    tokens = HMACSessionToken(
        signing_key=bytes(range(32)),
        ttl_seconds=3600,
        clock=lambda: clock[0],
    )
    principal = Principal(
        ai_user_id="ai-synthetic-error-contract",
        display_name="Synthetic",
        roles=("user",),
        org_ctx=PrincipalOrgContext(tenant_id="default"),
    )
    revocations = RecordingRevocations()
    service = RecordingService()
    app = FastAPI()
    app.include_router(
        make_router(
            service,
            make_csrf_protected_principal(
                make_require_principal(tokens, revocations),
                make_require_csrf(TEST_CSRF_ALLOWED_ORIGINS),
            ),
        ),
        prefix=BASE,
    )
    with TestClient(
        app, base_url="https://testserver", backend_options={"loop_factory": make_event_loop}
    ) as client:
        client.cookies.set("eternalai_session", tokens.issue(principal))
        yield client, service, revocations, tokens, clock, principal


def assert_fixed(response, service):
    assert response.status_code == 422
    assert response.json() == FIXED
    assert response.headers.get("cache-control") == "no-store"
    assert service.calls == []
    for marker in ('"input"', '"ctx"', '"loc"', "SYNTHETIC_"):
        assert marker not in response.text


@pytest.mark.parametrize("value", ["SYNTHETIC_UNKNOWN", "null", ""])
def test_completion_unknown_is_fixed_422(http, value):
    client, service, *_ = http
    response = client.get(BASE, params={"completion": value})
    assert response.json() == FIXED
    assert_fixed(response, service)


@pytest.mark.parametrize(
    "values",
    [
        ["active", "completed"],
        ["completed", "active"],
        ["active", "active"],
        ["completed", "completed"],
        ["active", "SYNTHETIC_UNKNOWN"],
        ["SYNTHETIC_UNKNOWN", "active"],
        ["active", "completed", "active"],
    ],
)
def test_completion_duplicate_is_fixed_422(http, values):
    client, service, *_ = http
    response = client.get(BASE, params=[("completion", value) for value in values])
    assert response.status_code == 422
    assert_fixed(response, service)


@pytest.mark.parametrize(
    "value",
    [
        "ACTIVE",
        "Completed",
        " active",
        "active ",
        "\tactive\t",
        "\u00a0active\u00a0",
        "\u3000active\u3000",
    ],
)
def test_completion_case_and_whitespace_are_not_normalized(http, value):
    client, service, *_ = http
    response = client.get(BASE, params={"completion": value})
    assert response.status_code == 422
    assert_fixed(response, service)


@pytest.mark.parametrize("value", ["active", "completed"])
def test_completion_valid_is_forwarded(http, value):
    client, service, *_, principal = http
    response = client.get(
        BASE, params={"completion": value, "q": "  synthetic   query ", "oa_view": "all"}
    )
    assert response.status_code == 200
    assert service.calls == [
        (
            principal,
            {
                "completion": value,
                "search_term": "synthetic query",
                "oa_view": "all",
            },
        )
    ]
    assert "cache-control" not in response.headers
    full = client.app.openapi()
    schema = full["paths"][BASE]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    Draft202012Validator({**schema, "components": full["components"]}).validate(response.json())


def test_completion_omitted_preserves_default(http):
    client, service, *_, principal = http
    assert client.get(BASE, params={"q": "  synthetic   query "}).status_code == 200
    assert service.calls == [
        (
            principal,
            {
                "completion": None,
                "search_term": "synthetic query",
                "oa_view": "active",
            },
        )
    ]
    service.calls.clear()
    for value in ("", "null"):
        assert_fixed(client.get(BASE, params={"completion": value}), service)


@pytest.mark.parametrize("values", [["SYNTHETIC_UNKNOWN"], ["active", "completed"]])
def test_completion_mixed_invalid_parameters_are_fixed(http, values):
    client, service, *_ = http
    params = [("completion", value) for value in values] + [("oa_view", "SYNTHETIC_BAD_OA")]
    response = client.get(BASE, params=params)
    assert response.json() == FIXED
    assert_fixed(response, service)


@pytest.mark.parametrize("completion", [None, "active", "completed"])
def test_oa_view_only_error_contract_is_preserved(http, completion):
    client, service, *_ = http
    params = {"oa_view": "SYNTHETIC_BAD_OA"}
    if completion is not None:
        params["completion"] = completion
    response = client.get(BASE, params=params)
    assert response.status_code == 422
    assert response.json() == {
        "detail": [
            {
                "type": "literal_error",
                "loc": ["query", "oa_view"],
                "msg": "Input should be 'active', 'unconfirmed' or 'all'",
                "input": "SYNTHETIC_BAD_OA",
                "ctx": {"expected": "'active', 'unconfirmed' or 'all'"},
            }
        ]
    }
    assert "cache-control" not in response.headers
    assert service.calls == []


@pytest.mark.parametrize("mode", ["missing", "invalid", "expired", "revoked", "unavailable"])
@pytest.mark.parametrize("values", [["SYNTHETIC_UNKNOWN"], ["active", "completed"]])
def test_completion_error_does_not_preempt_authentication(http, mode, values):
    client, service, revocations, tokens, clock, _ = http
    if mode == "missing":
        client.cookies.clear()
    elif mode == "invalid":
        client.cookies.set("eternalai_session", "SYNTHETIC_INVALID")
    elif mode == "expired":
        clock[0] += 3601
    elif mode == "revoked":
        revocations.revoked.add(tokens.inspect(client.cookies.get("eternalai_session")).fingerprint)
    else:
        revocations.failed = True
    response = client.get(BASE, params=[("completion", value) for value in values])
    assert response.status_code == (503 if mode == "unavailable" else 401)
    assert response.json() == {
        "detail": {
            "code": "authentication_unavailable"
            if mode == "unavailable"
            else "authentication_required",
            "message": "Authentication is temporarily unavailable."
            if mode == "unavailable"
            else "Valid authentication is required.",
        }
    }
    assert service.calls == []
    assert revocations.reads == (1 if mode in {"revoked", "unavailable"} else 0)
    if mode == "unavailable":
        assert response.headers["cache-control"] == "no-store"
    else:
        assert response.headers["www-authenticate"] == "Session"


def test_completion_error_matches_runtime_openapi(http):
    client, service, *_ = http
    full = client.app.openapi()
    operation = full["paths"][BASE]["get"]
    assert operation["operationId"] == "list_work_objects_api_v1_work_objects_get"
    assert [p["name"] for p in operation["parameters"]] == ["q", "oa_view", "completion"]
    completion = operation["parameters"][2]
    assert completion == {
        "name": "completion",
        "in": "query",
        "required": False,
        "schema": {
            "anyOf": [{"enum": ["active", "completed"], "type": "string"}, {"type": "null"}],
            "title": "Completion",
        },
    }

    def check_refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                target = full
                for part in value["$ref"].removeprefix("#/").split("/"):
                    key = part.replace("~1", "/").replace("~0", "~")
                    assert key in target, value["$ref"]
                    target = target[key]
            for child in value.values():
                check_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_refs(child)

    check_refs(full)
    response_schema = operation["responses"]["422"]
    schema = response_schema["content"]["application/json"]["schema"]
    assert response_schema["headers"]["Cache-Control"] == {
        "description": "Present on fixed completion validation failures.",
        "schema": {"type": "string", "const": "no-store"},
    }
    for query in ({"completion": "SYNTHETIC_UNKNOWN"}, {"oa_view": "SYNTHETIC_BAD_OA"}):
        response = client.get(BASE, params=query)
        assert response.status_code == 422
        if "completion" in query:
            assert_fixed(response, service)
        document = {**schema, "components": full["components"]}
        Draft202012Validator(document).validate(response.json())
        assert (
            sum(
                Draft202012Validator({**branch, "components": full["components"]}).is_valid(
                    response.json()
                )
                for branch in schema["oneOf"]
            )
            == 1
        )
    assert schema == {
        "oneOf": [
            {"$ref": "#/components/schemas/WorkObjectError"},
            {"$ref": "#/components/schemas/HTTPValidationError"},
        ]
    }


@pytest.mark.parametrize("values", [["SYNTHETIC_UNKNOWN"], ["active", "active"]])
def test_fixed_completion_error_is_no_store(http, values):
    client, service, *_ = http
    response = client.get(BASE, params=[("completion", value) for value in values])
    assert response.headers.get("cache-control") == "no-store"
    assert_fixed(response, service)


def test_other_routes_keep_their_validation_contracts(http):
    client, service, *_ = http
    detail = client.get(BASE + "/synthetic-missing", params={"completion": "SYNTHETIC_UNKNOWN"})
    assert detail.status_code == 404
    assert detail.json() == {
        "detail": {"code": "work_object_not_found", "message": "Work Object was not found."}
    }
    assert len(service.calls) == 1
    service.calls.clear()
    dispatch = client.post(BASE + "/dispatch", json={}, headers=TEST_CSRF_HEADERS)
    assert dispatch.status_code == 422
    assert dispatch.json() == {
        "detail": {"code": "dispatch_request_invalid", "message": "Dispatch request is invalid."}
    }
    options = client.get(BASE + "/dispatch-options", params={"kind": "SYNTHETIC_UNKNOWN"})
    assert options.status_code == 422
    assert options.json() == {
        "detail": {
            "code": "dispatch_options_request_invalid",
            "message": "Dispatch options request is invalid.",
        }
    }
    assert options.headers["cache-control"] == "no-store"
    events = client.get(BASE + "/synthetic/lifecycle/events", params={"limit": "SYNTHETIC_UNKNOWN"})
    assert_fixed(events, service)
    patch = client.patch(
        BASE + "/synthetic/handling-mark",
        json={"mark": "SYNTHETIC_UNKNOWN"},
        headers=TEST_CSRF_HEADERS,
    )
    assert patch.status_code == 422
    assert isinstance(patch.json()["detail"], list)
    assert patch.json()["detail"][0]["loc"] == ["body", "mark"]
    assert service.calls == []
