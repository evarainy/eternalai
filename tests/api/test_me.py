"""``GET /api/v1/me`` and ``GET /api/v1/me/avatar``.

All names, departments, identifiers, cookies and avatar bytes below are
synthetic (AGENTS.md rule 4 and the 2026-09-02 personnel-information boundary).
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.v1.me import AVATAR_PATH
from app.infra.auth.crypto import HMACSessionToken
from app.main import create_app
from app.ports.auth import Principal, PrincipalOrgContext
from app.ports.user_profile import (
    UserAvatar,
    UserOrgProfile,
    UserProfileSnapshot,
)
from tests.auth_fakes import (
    AUTH_COOKIE_NAME,
    TEST_CSRF_ALLOWED_ORIGINS,
    StaticSessionTokens,
    auth_cookies,
)

ME_PATH = "/api/v1/me"
ALICE_ID = "usr_v1_" + "A" * 43
BOB_ID = "usr_v1_" + "B" * 43
ALICE_NAME = "甲用户"
BOB_NAME = "乙用户"
ALICE_DEPARTMENT = "部门甲"
BOB_DEPARTMENT = "部门乙"
ALICE_IMAGE = b"\x89PNG\r\n\x1a\nalice-synthetic"
BOB_IMAGE = b"\x89PNG\r\n\x1a\nbob-synthetic"


def principal(ai_user_id: str, display_name: str) -> Principal:
    return Principal(
        ai_user_id=ai_user_id,
        display_name=display_name,
        roles=("staff",),
        org_ctx=PrincipalOrgContext(),
    )


class StubUserProfile:
    """Returns whatever was registered for the exact ai_user_id and nothing else."""

    def __init__(
        self,
        profiles: dict[str, UserProfileSnapshot] | None = None,
        avatars: dict[str, UserAvatar] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.profiles = profiles or {}
        self.avatars = avatars or {}
        self.error = error
        self.profile_calls: list[str] = []
        self.avatar_calls: list[str] = []

    async def get_profile(self, ai_user_id: str) -> UserProfileSnapshot:
        self.profile_calls.append(ai_user_id)
        if self.error is not None:
            raise self.error
        return self.profiles.get(
            ai_user_id,
            UserProfileSnapshot(org_status="unavailable"),
        )

    async def get_avatar(self, ai_user_id: str) -> UserAvatar | None:
        self.avatar_calls.append(ai_user_id)
        if self.error is not None:
            raise self.error
        return self.avatars.get(ai_user_id)


def ok_snapshot(department_name: str) -> UserProfileSnapshot:
    return UserProfileSnapshot(
        org_status="ok",
        org=UserOrgProfile(
            department_name=department_name,
            department_id="22",
            unit_name="单位甲",
            unit_id="11",
        ),
        avatar_available=True,
    )


def build_client(
    user_profile: StubUserProfile | None,
    *,
    display_name: str = ALICE_NAME,
    ai_user_id: str = ALICE_ID,
) -> TestClient:
    tokens = StaticSessionTokens()
    tokens.principal = principal(ai_user_id, display_name)
    return TestClient(
        create_app(
            session_tokens=tokens,
            user_profile=user_profile,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        )
    )


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", [ME_PATH, AVATAR_PATH])
@pytest.mark.parametrize(
    "cookies",
    [
        {},
        {AUTH_COOKIE_NAME: "forged-token"},
        {AUTH_COOKIE_NAME: ""},
    ],
)
def test_unauthenticated_reads_are_rejected(
    path: str,
    cookies: dict[str, str],
) -> None:
    client = build_client(StubUserProfile())

    response = client.get(path, cookies=cookies)

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "authentication_required"


@pytest.mark.parametrize("path", [ME_PATH, AVATAR_PATH])
def test_expired_and_wrong_version_tokens_are_rejected(path: str) -> None:
    stale_clock = 1_000_000.0
    issuer = HMACSessionToken(
        signing_key=bytes(range(32)),
        ttl_seconds=60,
        clock=lambda: stale_clock,
    )
    live = HMACSessionToken(signing_key=bytes(range(32)), ttl_seconds=3600)
    other_key = HMACSessionToken(
        signing_key=bytes(reversed(range(32))),
        ttl_seconds=3600,
    )
    client = TestClient(
        create_app(
            session_tokens=live,
            user_profile=StubUserProfile(),
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        )
    )
    subject = principal(ALICE_ID, ALICE_NAME)

    for token in (issuer.issue(subject), other_key.issue(subject)):
        response = client.get(path, cookies={AUTH_COOKIE_NAME: token})
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "authentication_required"


def test_unauthenticated_request_never_reaches_the_profile_port() -> None:
    profile = StubUserProfile()
    client = build_client(profile)

    client.get(ME_PATH)
    client.get(AVATAR_PATH)

    assert profile.profile_calls == []
    assert profile.avatar_calls == []


# --------------------------------------------------------------------------
# Success shape
# --------------------------------------------------------------------------


def test_successful_read_returns_the_contract_shape() -> None:
    profile = StubUserProfile({ALICE_ID: ok_snapshot(ALICE_DEPARTMENT)})

    response = build_client(profile).get(ME_PATH, cookies=auth_cookies())

    assert response.status_code == 200
    assert response.json() == {
        "authenticated": True,
        "display_name": ALICE_NAME,
        "org": {
            "unit_name": "单位甲",
            "unit_id": "11",
            "department_name": ALICE_DEPARTMENT,
            "department_id": "22",
        },
        "org_status": "ok",
        "avatar_path": AVATAR_PATH,
    }
    assert profile.profile_calls == [ALICE_ID]


def test_response_carries_no_roles_or_internal_identifier() -> None:
    profile = StubUserProfile({ALICE_ID: ok_snapshot(ALICE_DEPARTMENT)})

    payload = build_client(profile).get(ME_PATH, cookies=auth_cookies()).json()

    assert "roles" not in payload
    assert "ai_user_id" not in payload
    assert ALICE_ID not in str(payload)


@pytest.mark.parametrize(
    ("status", "avatar_available"),
    [
        ("unbound", False),
        ("expired", False),
        ("unavailable", False),
        ("unparsable", False),
        ("unparsable", True),
    ],
)
def test_organization_failures_never_change_the_authentication_answer(
    status: str,
    avatar_available: bool,
) -> None:
    profile = StubUserProfile(
        {
            ALICE_ID: UserProfileSnapshot(
                org_status=status,  # type: ignore[arg-type]
                avatar_available=avatar_available,
            )
        }
    )

    response = build_client(profile).get(ME_PATH, cookies=auth_cookies())

    assert response.status_code == 200
    payload = response.json()
    # An OA outage degrades the page; it does not log the user out.
    assert payload["authenticated"] is True
    assert payload["display_name"] == ALICE_NAME
    assert payload["org"] is None
    assert payload["org_status"] == status
    assert payload["avatar_path"] == (AVATAR_PATH if avatar_available else None)


def test_failed_organization_read_invents_no_placeholder() -> None:
    profile = StubUserProfile(
        {ALICE_ID: UserProfileSnapshot(org_status="unparsable")}
    )

    body = build_client(profile).get(ME_PATH, cookies=auth_cookies()).text

    assert "部门" not in body
    assert "默认" not in body
    assert "未知" not in body
    assert '"org":null' in body.replace(" ", "")


def test_port_failure_degrades_to_unavailable_rather_than_500() -> None:
    profile = StubUserProfile(error=RuntimeError("synthetic-port-detail"))

    response = build_client(profile).get(ME_PATH, cookies=auth_cookies())

    assert response.status_code == 200
    assert response.json()["org_status"] == "unavailable"
    assert "synthetic-port-detail" not in response.text


def test_unconfigured_port_answers_503() -> None:
    client = build_client(None)

    for path in (ME_PATH, AVATAR_PATH):
        response = client.get(path, cookies=auth_cookies())
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "user_profile_unavailable"


# --------------------------------------------------------------------------
# Avatar proxy
# --------------------------------------------------------------------------


def test_avatar_is_served_with_our_own_media_type_and_hardening_headers() -> None:
    profile = StubUserProfile(
        avatars={ALICE_ID: UserAvatar(media_type="image/png", content=ALICE_IMAGE)}
    )

    response = build_client(profile).get(AVATAR_PATH, cookies=auth_cookies())

    assert response.status_code == 200
    assert response.content == ALICE_IMAGE
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, max-age=300"
    assert response.headers["content-disposition"] == "inline"


def test_missing_avatar_is_a_uniform_404() -> None:
    profile = StubUserProfile()

    response = build_client(profile).get(AVATAR_PATH, cookies=auth_cookies())

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "avatar_not_found"


def test_avatar_port_failure_is_the_same_404() -> None:
    profile = StubUserProfile(error=RuntimeError("synthetic-port-detail"))

    response = build_client(profile).get(AVATAR_PATH, cookies=auth_cookies())

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "avatar_not_found"
    assert "synthetic-port-detail" not in response.text


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------


def test_one_users_cookie_never_yields_another_users_identity() -> None:
    profile = StubUserProfile(
        {
            ALICE_ID: ok_snapshot(ALICE_DEPARTMENT),
            BOB_ID: ok_snapshot(BOB_DEPARTMENT),
        },
        avatars={
            ALICE_ID: UserAvatar(media_type="image/png", content=ALICE_IMAGE),
            BOB_ID: UserAvatar(media_type="image/png", content=BOB_IMAGE),
        },
    )
    tokens = HMACSessionToken(signing_key=bytes(range(32)), ttl_seconds=3600)
    client = TestClient(
        create_app(
            session_tokens=tokens,
            user_profile=profile,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        )
    )
    alice_cookie = {AUTH_COOKIE_NAME: tokens.issue(principal(ALICE_ID, ALICE_NAME))}
    bob_cookie = {AUTH_COOKIE_NAME: tokens.issue(principal(BOB_ID, BOB_NAME))}

    alice = client.get(ME_PATH, cookies=alice_cookie).json()
    alice_avatar = client.get(AVATAR_PATH, cookies=alice_cookie)
    bob = client.get(ME_PATH, cookies=bob_cookie).json()
    bob_avatar = client.get(AVATAR_PATH, cookies=bob_cookie)

    assert alice["display_name"] == ALICE_NAME
    assert alice["org"]["department_name"] == ALICE_DEPARTMENT
    assert BOB_NAME not in str(alice)
    assert BOB_DEPARTMENT not in str(alice)
    assert alice_avatar.content == ALICE_IMAGE
    assert BOB_IMAGE not in alice_avatar.content

    assert bob["display_name"] == BOB_NAME
    assert bob["org"]["department_name"] == BOB_DEPARTMENT
    assert ALICE_NAME not in str(bob)
    assert bob_avatar.content == BOB_IMAGE

    assert profile.profile_calls == [ALICE_ID, BOB_ID]
    assert profile.avatar_calls == [ALICE_ID, BOB_ID]


def test_neither_route_accepts_a_path_or_query_parameter() -> None:
    application = create_app(user_profile=StubUserProfile())
    routes = {
        route.path: route  # type: ignore[attr-defined]
        for route in application.routes
        if getattr(route, "path", "").startswith(ME_PATH)
    }

    assert set(routes) == {ME_PATH, AVATAR_PATH}
    for path, route in routes.items():
        dependant = route.dependant  # type: ignore[attr-defined]
        # "Read somebody else" is not expressible: there is nothing in the URL
        # to put another user's identifier into.
        assert dependant.path_params == [], path
        assert dependant.query_params == [], path
        assert dependant.body_params == [], path
        assert route.methods == {"GET"}  # type: ignore[attr-defined]


def test_query_string_cannot_smuggle_another_identity() -> None:
    profile = StubUserProfile({ALICE_ID: ok_snapshot(ALICE_DEPARTMENT)})
    client = build_client(profile)

    response = client.get(
        f"{ME_PATH}?ai_user_id={BOB_ID}&id={BOB_ID}",
        cookies=auth_cookies(),
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == ALICE_NAME
    assert profile.profile_calls == [ALICE_ID]


def test_self_reported_headers_and_body_are_ignored() -> None:
    profile = StubUserProfile({ALICE_ID: ok_snapshot(ALICE_DEPARTMENT)})
    client = build_client(profile)

    response = client.request(
        "GET",
        ME_PATH,
        cookies=auth_cookies(),
        headers={
            "X-EternalAI-Roles": "admin",
            "X-EternalAI-User": BOB_ID,
        },
        content=f'{{"ai_user_id": "{BOB_ID}"}}'.encode(),
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == ALICE_NAME
    assert profile.profile_calls == [ALICE_ID]


# --------------------------------------------------------------------------
# Leakage
# --------------------------------------------------------------------------


def test_no_credential_or_upstream_location_leaves_the_endpoint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    profile = StubUserProfile(
        {ALICE_ID: ok_snapshot(ALICE_DEPARTMENT)},
        avatars={ALICE_ID: UserAvatar(media_type="image/png", content=ALICE_IMAGE)},
    )
    client = build_client(profile)

    with caplog.at_level(logging.DEBUG):
        me = client.get(ME_PATH, cookies=auth_cookies())
        avatar = client.get(AVATAR_PATH, cookies=auth_cookies())

    for response in (me, avatar):
        assert "set-cookie" not in {key.casefold() for key in response.headers}
    body = me.text
    for forbidden in ("messagerurl", "orginfo", "oa.invalid", "/messager/"):
        assert forbidden not in body
    assert ALICE_DEPARTMENT not in caplog.text
    assert ALICE_NAME not in caplog.text
    assert "/messager/" not in caplog.text
    assert "alice-synthetic" not in caplog.text


def test_response_body_never_carries_markup() -> None:
    profile = StubUserProfile({ALICE_ID: ok_snapshot(ALICE_DEPARTMENT)})

    body = build_client(profile).get(ME_PATH, cookies=auth_cookies()).text

    assert "<" not in body
    assert ">" not in body


# --------------------------------------------------------------------------
# Published contract
# --------------------------------------------------------------------------


def test_openapi_declares_two_parameterless_reads() -> None:
    schema: dict[str, Any] = create_app().openapi()

    me_operation = schema["paths"][ME_PATH]["get"]
    avatar_operation = schema["paths"][AVATAR_PATH]["get"]

    assert set(schema["paths"][ME_PATH]) == {"get"}
    assert set(schema["paths"][AVATAR_PATH]) == {"get"}
    assert me_operation.get("parameters", []) == []
    assert avatar_operation.get("parameters", []) == []
    assert "requestBody" not in me_operation
    assert "requestBody" not in avatar_operation

    model = schema["components"]["schemas"]["MeResponse"]
    assert model["additionalProperties"] is False
    assert set(model["properties"]) == {
        "authenticated",
        "display_name",
        "org",
        "org_status",
        "avatar_path",
    }
    assert model["properties"]["authenticated"]["const"] is True
    assert set(model["properties"]["org_status"]["enum"]) == {
        "ok",
        "unbound",
        "expired",
        "unavailable",
        "unparsable",
    }
    # The published contract makes it impossible for an OA-side avatar location
    # to appear here: the only non-null value is our own constant path.
    assert model["properties"]["avatar_path"]["anyOf"] == [
        {"type": "string", "const": AVATAR_PATH},
        {"type": "null"},
    ]
    org_model = schema["components"]["schemas"]["MeOrg"]
    assert org_model["additionalProperties"] is False
    assert set(org_model["properties"]) == {
        "unit_name",
        "unit_id",
        "department_name",
        "department_id",
    }
    assert set(avatar_operation["responses"]["200"]["content"]) == {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    }
