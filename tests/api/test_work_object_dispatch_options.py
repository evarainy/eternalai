"""Candidate API contracts exercised through real sessions, HTTP and PostgreSQL."""

from __future__ import annotations

import base64
import json

import pytest

from app.api.v1 import work_objects as api
from tests.api.test_work_object_dispatch import assert_error, expire_directory, request_body
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db


def options(db, query="kind=department"):
    result = db.client.get("/api/v1/work-objects/dispatch-options?" + query)
    assert result.headers["Cache-Control"] == "no-store"
    if result.status_code != 200:
        messages = {
            "authentication_required": "Valid authentication is required.",
            "directory_scope_denied": "Directory access is not permitted for this identity.",
            "directory_membership_missing": "Work Object operation is not permitted.",
            "directory_membership_ambiguous": "Work Object operation is not permitted.",
            "not_department_head": "Work Object operation is not permitted.",
            "cross_department_dispatch_denied": "Work Object operation is not permitted.",
            "dispatch_target_not_found": "Dispatch target was not found.",
            "organization_directory_snapshot_changed": (
                "Organization directory has changed; reload the selection."
            ),
            "dispatch_options_request_invalid": "Dispatch options request is invalid.",
            "organization_directory_missing": (
                "Organization directory has not completed its first successful synchronization."
            ),
            "organization_directory_stale": (
                "Organization directory is stale; directory-dependent authorization is unavailable."
            ),
            "organization_directory_unavailable": "Organization directory is unavailable.",
        }
        code = result.json()["detail"]["code"]
        assert result.json() == {"detail": {"code": code, "message": messages[code]}}
    return result


def name(db, user, value="Synthetic person"):
    db.execute(
        "UPDATE organization_user_memberships SET display_name=:name WHERE user_id=:user",
        name=value,
        user=user,
    )


@pytest.mark.parametrize(
    "role,department,job",
    [("office", "office-a", "75"), ("prison", "572", "75"), ("nonhead", "office-a", None)],
)
@pytest.mark.parametrize("kind", ["department", "user"])
def test_dispatch_permission_precedes_target_existence(
    dispatch_db, monkeypatch, role, department, job, kind
):
    db = dispatch_db
    db.actor("synthetic-matrix-actor", department, job)
    db.membership("synthetic-own-target", department)
    name(db, "synthetic-own-target")
    name(db, "recipient")
    real = api.compute_dispatch_authorization
    calls = []

    def observed(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(api, "compute_dispatch_authorization", observed)
    response = options(db)
    if role == "nonhead":
        assert_error(response, 403, "not_department_head")
    else:
        assert response.status_code == 200
        expected = {"office-a", "office-b", "office-c", "572", "575"}
        assert {item["department_id"] for item in response.json()["items"]} == (
            expected if role == "office" else {department}
        )
        assert {call["target_department_id"] for call in calls[1:]} == expected

    denied_bodies = []
    for target, user in (
        (department, "synthetic-own-target"),
        ("office-b", "recipient"),
        ("synthetic-nonexistent", "synthetic-absent-user"),
    ):
        before = db.counts()
        get = options(db, "kind=user&department_id=" + target)
        assert db.counts() == before
        payload = {"kind": kind, "department_id": target}
        if kind == "user":
            payload["directory_user_id"] = user
        post = db.post(request_body(targets=[payload]))
        if role == "nonhead":
            for result in (get, post):
                assert_error(result, 403, "not_department_head")
            assert get.json() == post.json()
        elif role == "prison" and target != department:
            for result in (get, post):
                assert_error(result, 403, "cross_department_dispatch_denied")
                denied_bodies.append(result.json())
        elif target == "synthetic-nonexistent":
            for result in (get, post):
                assert_error(result, 404, "dispatch_target_not_found")
            assert get.json() == post.json()
        else:
            assert get.status_code == 200, get.text
            assert user in {item["directory_user_id"] for item in get.json()["items"]}
            assert post.status_code == 201, post.text
            assert post.json()["created_count"] == 1
            assert post.json()["items"][0]["owner_department_id"] == target
        assert db.counts() == (
            (before[0] + 1, before[1] + 1) if post.status_code == 201 else before
        )
    if role == "prison":
        assert denied_bodies == [{"detail": {
            "code": "cross_department_dispatch_denied",
            "message": "Work Object operation is not permitted.",
        }}] * 4
    assert all(call["dispatcher_membership"].user_id == "synthetic-matrix-actor" for call in calls)
    assert all(call["dispatcher_department"].department_id == department for call in calls)


@pytest.mark.parametrize("target_state", ["present", "missing", "ambiguous"])
def test_denied_batch_hides_target_membership_and_other_missing_targets(dispatch_db, target_state):
    db = dispatch_db
    db.actor("synthetic-prison-head", "572", "75")
    user = "synthetic-absent-user" if target_state == "missing" else "recipient"
    if target_state == "ambiguous":
        db.membership("recipient", "office-c")
    cross = {"kind": "user", "directory_user_id": user, "department_id": "office-b"}
    own_missing = {
        "kind": "user", "directory_user_id": "synthetic-missing-own", "department_id": "572",
    }
    for targets in ([cross], [own_missing, cross], [cross, own_missing]):
        response = db.post(request_body(targets=targets))
        assert_error(response, 403, "cross_department_dispatch_denied")
        assert response.json() == {"detail": {
            "code": "cross_department_dispatch_denied",
            "message": "Work Object operation is not permitted.",
        }}
        assert db.counts() == (0, 0)
    assert {event["attributes"]["reason_code"] for event in db.rows("trace_events")} == {
        "cross_department_dispatch_denied"
    }


@pytest.mark.parametrize(
    "state,code,status",
    [
        ("actor-missing", "directory_membership_missing", 403),
        ("actor-ambiguous", "directory_membership_ambiguous", 403),
        ("directory-missing", "organization_directory_missing", 503),
        ("directory-stale", "organization_directory_stale", 503),
        ("directory-unavailable", "organization_directory_unavailable", 503),
    ],
)
def test_dispatch_prerequisite_errors_do_not_expose_targets(
    dispatch_db, monkeypatch, state, code, status
):
    db = dispatch_db
    if state == "actor-missing":
        db.tokens.principal = db.tokens.principal.model_copy(update={
            "org_ctx": db.tokens.principal.org_ctx.model_copy(update={
                "directory_user_id": "synthetic-absent-actor",
            }),
        })
    elif state == "actor-ambiguous":
        db.membership("sender", "office-c", "75")
    elif state == "directory-missing":
        db.execute(
            "UPDATE organization_directory_sync_state SET snapshot_version=0, "
            "source_fetched_at=NULL, last_success_at=NULL, last_attempt_started_at=NULL, "
            "last_attempt_finished_at=NULL, last_attempt_status='never', last_error_code=NULL"
        )
    elif state == "directory-stale":
        expire_directory(db)
    else:
        async def unavailable():
            raise RuntimeError("synthetic directory failure")

        monkeypatch.setattr(db.directory, "read_view", unavailable)
    responses = [options(db)]
    for target in ("office-a", "office-b", "synthetic-nonexistent"):
        responses.append(options(db, "kind=user&department_id=" + target))
        for kind in ("department", "user"):
            payload = {"kind": kind, "department_id": target}
            if kind == "user":
                payload["directory_user_id"] = "recipient"
            responses.append(db.post(request_body(targets=[payload])))
    for response in responses:
        assert_error(response, status, code)
        assert response.json() == responses[0].json()
    assert db.counts() == (0, 0)


def test_office_and_prison_options_match_dispatch_rules(dispatch_db, monkeypatch):
    db = dispatch_db
    name(db, "recipient")
    original = api.compute_dispatch_authorization
    calls = []

    def decision(**kwargs):
        calls.append(kwargs)
        value = original(**kwargs)
        if kwargs["target_department_id"] == "office-c":
            return value.model_copy(
                update={"decision": "deny", "reason_code": "cross_department_dispatch_denied"}
            )
        return value

    monkeypatch.setattr(api, "compute_dispatch_authorization", decision)
    response = options(db)
    assert response.status_code == 200
    assert {item["department_id"] for item in response.json()["items"]} == {
        "office-a",
        "office-b",
        "572",
        "575",
    }
    assert len(calls) == 6
    assert {call["dispatcher_membership"].user_id for call in calls} == {"sender"}
    assert_error(
        options(db, "kind=user&department_id=office-c"), 403, "cross_department_dispatch_denied"
    )
    db.actor("synthetic-prison-head", "572", "75")
    assert [item["department_id"] for item in options(db).json()["items"]] == ["572"]
    assert_error(
        options(db, "kind=user&department_id=office-b"), 403, "cross_department_dispatch_denied"
    )
    assert_error(db.post(request_body()), 403, "cross_department_dispatch_denied")


def test_other_tenant_and_forged_scope_cannot_read_directory(dispatch_db, monkeypatch):
    db = dispatch_db
    reads = []
    original = db.directory.read_view

    async def read():
        reads.append(True)
        return await original()

    monkeypatch.setattr(db.directory, "read_view", read)
    db.actor("sender", "office-a", "75", tenant="synthetic-other-tenant")
    assert_error(options(db), 403, "directory_scope_denied")
    assert reads == []
    db.actor("sender", "office-a", "75")
    for forged in ("role=admin", "tenant=default", "job_title=75"):
        assert_error(
            options(db, "kind=department&" + forged), 422, "dispatch_options_request_invalid"
        )
    assert reads == []
    assert options(db).status_code == 200
    assert len(reads) == 1


def test_global_membership_ambiguity_and_missing_names_are_unselectable(dispatch_db):
    db = dispatch_db
    name(db, "recipient", "Synthetic same")
    db.membership("synthetic-same", "office-b")
    name(db, "synthetic-same", "Synthetic same")
    db.membership("synthetic-multiple", "office-a")
    db.membership("synthetic-multiple", "office-b")
    name(db, "synthetic-multiple")
    db.membership("synthetic-nameless", "office-b")
    result = options(db, "kind=user&department_id=office-b").json()
    assert [item["directory_user_id"] for item in result["items"]] == [
        "recipient",
        "synthetic-same",
    ]
    assert result["unselectable_count"] == 2
    assert "synthetic-multiple" not in json.dumps(result)
    assert_error(
        db.post(
            request_body(
                targets=[
                    {
                        "kind": "user",
                        "directory_user_id": "synthetic-multiple",
                        "department_id": "office-b",
                    }
                ]
            )
        ),
        403,
        "dispatch_target_membership_ambiguous",
    )


def test_cursor_pages_are_stable_and_version_bound(dispatch_db):
    db = dispatch_db
    for index in range(101):
        user = f"synthetic-page-{index:03}"
        db.membership(user, "office-b")
        name(db, user)
    pages = []
    cursor = None
    for count in (50, 50, 1):
        query = "kind=user&department_id=office-b" + ("&cursor=" + cursor if cursor else "")
        body = options(db, query).json()
        assert len(body["items"]) == count
        pages += [item["directory_user_id"] for item in body["items"]]
        cursor = body["next_cursor"]
        assert body["has_more"] is (cursor is not None)
    assert pages == [f"synthetic-page-{index:03}" for index in range(101)]
    cursor = options(db, "kind=user&department_id=office-b").json()["next_cursor"]
    db.execute("UPDATE organization_directory_sync_state SET snapshot_version=snapshot_version+1")
    assert_error(
        options(db, "kind=user&department_id=office-b&cursor=" + cursor),
        409,
        "organization_directory_snapshot_changed",
    )
    duplicate = base64.urlsafe_b64encode(b'{"v":1,"v":1}').decode().rstrip("=")
    assert_error(
        options(db, "kind=department&cursor=" + duplicate), 422, "dispatch_options_request_invalid"
    )


@pytest.mark.parametrize(
    "query",
    [
        "",
        "kind=bad",
        "kind=department&kind=department",
        "kind=department&department_id=x",
        "kind=user",
        "kind=user&department_id=",
        "kind=department&limit=0",
        "kind=department&limit=101",
        "kind=department&limit=1.0",
        "kind=department&limit=01",
        "kind=department&cursor=",
        "kind=user&department_id=" + "x" * 129,
    ],
)
def test_error_dto_is_closed_and_never_echoes_input(dispatch_db, query):
    response = options(dispatch_db, query)
    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "dispatch_options_request_invalid",
            "message": "Dispatch options request is invalid.",
        }
    }


def test_no_job_actor_denied_but_no_job_target_selectable(dispatch_db):
    db = dispatch_db
    name(db, "recipient")
    assert [
        item["directory_user_id"]
        for item in options(db, "kind=user&department_id=office-b").json()["items"]
    ] == ["recipient"]
    assert db.post().status_code == 201
    db.actor("recipient", "office-b", None)
    for query in ("kind=department", "kind=user&department_id=office-b"):
        assert_error(options(db, query), 403, "not_department_head")


def test_authentication_precedes_parameter_validation(dispatch_db):
    db = dispatch_db
    db.client.cookies.clear()
    response = options(db, "kind=synthetic-private-marker")
    assert_error(response, 401, "authentication_required")
    assert response.json()["detail"]["message"] == "Valid authentication is required."
    assert response.headers["www-authenticate"] == "Session"


@pytest.mark.parametrize(
    "case,code,status_code,message",
    [
        (
            "missing-actor",
            "directory_membership_missing",
            403,
            "Work Object operation is not permitted.",
        ),
        (
            "ambiguous-actor",
            "directory_membership_ambiguous",
            403,
            "Work Object operation is not permitted.",
        ),
        (
            "missing-directory",
            "organization_directory_missing",
            503,
            "Organization directory has not completed its first successful synchronization.",
        ),
        (
            "stale",
            "organization_directory_stale",
            503,
            "Organization directory is stale; directory-dependent authorization is unavailable.",
        ),
        (
            "query-error",
            "organization_directory_unavailable",
            503,
            "Organization directory is unavailable.",
        ),
        ("missing-target", "dispatch_target_not_found", 404, "Dispatch target was not found."),
    ],
)
def test_remaining_error_paths_match_exact_wire_contract(
    dispatch_db, monkeypatch, case, code, status_code, message
):
    from tests.api.test_work_object_dispatch import expire_directory

    db = dispatch_db
    query = "kind=department"
    if case == "missing-actor":
        db.execute("DELETE FROM organization_user_memberships WHERE user_id='sender'")
    elif case == "ambiguous-actor":
        db.membership("sender", "office-b", "75")
    elif case == "missing-directory":
        db.execute(
            "UPDATE organization_directory_sync_state SET snapshot_version=0,"
            "source_fetched_at=NULL,"
            "last_success_at=NULL,last_attempt_started_at=NULL,last_attempt_finished_at=NULL,"
            "last_attempt_status='never',last_error_code=NULL"
        )
    elif case == "stale":
        expire_directory(db)
    elif case == "query-error":

        async def failed():
            raise RuntimeError("Synthetic-private-query")

        monkeypatch.setattr(db.directory, "read_view", failed)
    else:
        query = "kind=user&department_id=" + "x" * 128
    response = options(db, query)
    assert response.status_code == status_code
    assert response.json() == {"detail": {"code": code, "message": message}}


def test_unconfigured_service_precedes_invalid_parameters(dispatch_db):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.auth_fakes import auth_cookies, make_session_binder

    db = dispatch_db
    with TestClient(
        create_app(
            session_tokens=db.tokens,
            session_binder=make_session_binder(),
            session_cookie_ttl_seconds=3600,
        ),
        base_url="https://testserver",
    ) as client:
        client.cookies.update(auth_cookies())
        response = client.get("/api/v1/work-objects/dispatch-options?kind=invalid")
    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "work_object_unavailable",
            "message": "Work Object provider is not configured.",
        }
    }
    assert response.headers["Cache-Control"] == "no-store"
