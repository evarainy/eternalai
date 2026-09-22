"""Synthetic lifecycle requests through actual HMAC authentication and PostgreSQL."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.event_loop import make_event_loop
from app.infra.auth.crypto import HMACSessionToken
from app.infra.auth.session_revocations import PostgreSQLSessionRevocationStore
from app.main import create_app
from app.ports.auth import Principal, PrincipalOrgContext
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db
from tests.auth_fakes import TEST_CSRF_ALLOWED_ORIGINS, TEST_CSRF_HEADERS, make_session_binder


@pytest.fixture
def lifecycle_http(dispatch_db):
    db = dispatch_db
    clock = [datetime.now(UTC).timestamp()]
    tokens = HMACSessionToken(
        signing_key=bytes(range(32)), ttl_seconds=3600, clock=lambda: clock[0]
    )
    client = TestClient(
        create_app(
            work_object_service=db.service,
            session_revocations=PostgreSQLSessionRevocationStore(db.factory),
            session_tokens=tokens,
            session_binder=make_session_binder(),
            session_cookie_ttl_seconds=3600,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        ),
        base_url="https://testserver",
        backend_options={"loop_factory": make_event_loop},
    )

    def actor(user, department="office-a", tenant="default", roles=("user",), *, join=True):
        db.membership(user, department, "75" if user == "sender" else None)
        principal = Principal(
            ai_user_id="ai-" + user,
            display_name="Synthetic lifecycle user",
            roles=roles,
            org_ctx=PrincipalOrgContext(
                tenant_id=tenant,
                directory_user_id=user if join else None,
                department_id="untrusted-token-department",
            ),
        )
        client.cookies.clear()
        client.cookies.set("eternalai_session", tokens.issue(principal))
        return principal

    actor("sender")
    yield db, client, actor, clock
    client.close()


def publish(client, *, department=False, key=None):
    target = (
        {"kind": "department", "department_id": "office-a"}
        if department
        else {"kind": "user", "directory_user_id": "local-recipient", "department_id": "office-a"}
    )
    response = client.post(
        "/api/v1/work-objects/dispatch",
        json={
            "kind": "工作任务",
            "title": "Synthetic lifecycle",
            "requirement": "",
            "receipt_requirement": "Synthetic evidence",
            "due_at": None,
            "reminder_choices": [],
            "targets": [target],
        },
        headers={**TEST_CSRF_HEADERS, "Idempotency-Key": key or str(uuid4())},
    )
    assert response.status_code == 201
    return response.json()


def command(client, object_id, operation, *, message=None, key=None, etag=None):
    base = f"/api/v1/work-objects/{object_id}/lifecycle"
    if etag is None:
        view = client.get(base)
        assert view.status_code == 200
        etag = view.headers["etag"]
    body = {"operation": operation}
    if message is not None:
        body["text"] = message
    return client.post(
        base + "/commands",
        json=body,
        headers={
            **TEST_CSRF_HEADERS,
            "Idempotency-Key": key or str(uuid4()),
            "If-Match": etag,
        },
    )


def assert_error(response, status, code):
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert set(response.json()["detail"]) == {"code", "message"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("method,suffix", [("GET", ""), ("GET", "/events"), ("POST", "/commands")])
def test_authentication_store_read_failure_is_503_without_business_calls(
    lifecycle_http, monkeypatch, caplog, method, suffix
):
    db, client, actor, _ = lifecycle_http
    object_id = publish(client)["items"][0]["work_object_id"]
    actor("local-recipient")
    assert command(client, object_id, "accept").status_code == 200
    base = f"/api/v1/work-objects/{object_id}/lifecycle"
    spies = []
    for name in (
        "get_lifecycle_for_principal", "list_lifecycle_events_for_principal",
        "command_lifecycle_for_principal",
    ):
        spy = Mock(wraps=getattr(db.service, name))
        monkeypatch.setattr(db.service, name, spy)
        spies.append(spy)

    def request_arguments():
        view = client.get(base)
        assert view.status_code == 200
        return {} if method == "GET" else {
            "json": {"operation": "feedback", "text": "SYNTHETIC_FEEDBACK_INPUT"},
            "headers": {**TEST_CSRF_HEADERS, "Idempotency-Key": str(uuid4()),
                        "If-Match": view.headers["etag"]},
        }

    healthy = client.request(method, base + suffix, **request_arguments())
    assert healthy.status_code == 200
    target = {"": 0, "/events": 1, "/commands": 2}[suffix]
    assert spies[target].call_count >= 1
    arguments = request_arguments()
    before = db.rows("work_objects")

    def event_count():
        with db.sql.connect() as connection:
            return connection.execute(text(
                "SELECT count(*) FROM work_object_lifecycle_events WHERE work_object_id=:id"
            ), {"id": object_id}).scalar_one()

    count_before = event_count()
    for spy in spies:
        spy.reset_mock()
    hits = []

    def fail_revocation_read(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("SELECT 1 FROM auth_session_revocations WHERE token_fingerprint ="):
            hits.append(True)
            raise RuntimeError("SYNTHETIC_REVOCATION_READ_FAULT")

    event.listen(db.engine.sync_engine, "before_cursor_execute", fail_revocation_read)
    try:
        failed = client.request(method, base + suffix, **arguments)
        assert failed.status_code == 503
        assert failed.json() == {"detail": {
            "code": "authentication_unavailable",
            "message": "Authentication is temporarily unavailable.",
        }}
        assert failed.headers.get("cache-control") == "no-store"
        assert hits == [True]
        assert [spy.call_count for spy in spies] == [0, 0, 0]
        assert db.rows("work_objects") == before
        assert event_count() == count_before
        observed = failed.text + str(dict(failed.headers)) + caplog.text
        for marker in ("SYNTHETIC_REVOCATION_READ_FAULT", "SYNTHETIC_FEEDBACK_INPUT",
                       client.cookies.get("eternalai_session")):
            assert marker not in observed
    finally:
        event.remove(db.engine.sync_engine, "before_cursor_execute", fail_revocation_read)
    restored = client.request(method, base + suffix, **arguments)
    assert restored.status_code == 200
    assert spies[target].call_count == 1
    assert event_count() == count_before + (1 if method == "POST" else 0)
    if method == "POST":
        assert restored.json()["replayed"] is False
        replay = client.request(method, base + suffix, **arguments)
        assert replay.status_code == 200
        assert replay.json() == {**restored.json(), "replayed": True}
        assert event_count() == count_before + 1
    else:
        assert db.rows("work_objects") == before


def test_logout_replay_of_original_key_cannot_read_event(lifecycle_http):
    db, client, actor, _ = lifecycle_http
    object_id = publish(client)["items"][0]["work_object_id"]
    actor("local-recipient")
    base = f"/api/v1/work-objects/{object_id}/lifecycle"
    before = client.get(base)
    assert before.status_code == 200
    key, etag = str(uuid4()), before.headers["etag"]
    accepted = command(client, object_id, "accept", key=key, etag=etag)
    assert accepted.status_code == 200
    replay = command(client, object_id, "accept", key=key, etag=etag)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert client.get(base + "/events").status_code == 200
    original = client.cookies.get("eternalai_session")
    assert client.post("/api/v1/auth/logout", headers=TEST_CSRF_HEADERS).status_code == 200
    client.cookies.clear()
    client.cookies.set("eternalai_session", original)
    statements = []

    def record(_c, _cur, statement, _params, _ctx, _many):
        if "work_object" in statement.lower():
            statements.append(statement)

    event.listen(db.engine.sync_engine, "before_cursor_execute", record)
    try:
        for response in (
            client.get(base),
            client.get(base + "/events"),
            command(client, object_id, "accept", key=key, etag=etag),
        ):
            assert_error(response, 401, "authentication_required")
            assert response.headers["www-authenticate"] == "Session"
            assert set(response.json()) == {"detail"}
        assert statements == []
    finally:
        event.remove(db.engine.sync_engine, "before_cursor_execute", record)
    with db.sql.connect() as c:
        count = c.execute(
            text("SELECT count(*) FROM work_object_lifecycle_events WHERE work_object_id=:id"),
            {"id": object_id},
        ).scalar_one()
    assert count == 1
    actor("local-recipient")
    assert client.get(base).status_code == 200
    assert client.get(base + "/events").status_code == 200
    assert command(client, object_id, "accept", key=key, etag=etag).json()["replayed"] is True


def test_event_insert_failure_returns_503_and_rolls_back(lifecycle_http):
    db, client, actor, _clock = lifecycle_http
    object_id = publish(client)["items"][0]["work_object_id"]
    actor("local-recipient")
    before = db.rows("work_objects")

    def reject_event(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO work_object_lifecycle_events"):
            raise RuntimeError("Synthetic HTTP event insert failure")

    event.listen(db.engine.sync_engine, "before_cursor_execute", reject_event)
    try:
        assert_error(command(client, object_id, "accept"), 503, "work_object_lifecycle_failed")
    finally:
        event.remove(db.engine.sync_engine, "before_cursor_execute", reject_event)
    assert db.rows("work_objects") == before
    with db.sql.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM work_object_lifecycle_events")
            ).scalar_one()
            == 0
        )
    assert command(client, object_id, "accept").status_code == 200
    with db.sql.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM work_object_lifecycle_events")
            ).scalar_one()
            == 1
        )


def test_real_http_publish_accept_feedback_complete_and_initiator_readback(lifecycle_http):
    db, client, actor, _clock = lifecycle_http
    publication_key = str(uuid4())
    initial = publish(client, key=publication_key)
    object_id = initial["items"][0]["work_object_id"]
    assert "accepted_at" not in initial["items"][0]
    actor("local-recipient")
    base = f"/api/v1/work-objects/{object_id}/lifecycle"
    first = client.get(base)
    assert first.json()["available_commands"] == ["accept"]
    digest = hashlib.sha256(
        json.dumps(first.json(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert first.headers["etag"] == '"wolc-' + digest + '"'
    accept_key = str(uuid4())
    accepted = command(client, object_id, "accept", key=accept_key, etag=first.headers["etag"])
    assert accepted.status_code == 200
    assert accepted.json()["event"]["result_version"] == 2
    feedback = command(client, object_id, "feedback", message="合成进展 <b>text</b>")
    assert feedback.status_code == 200
    assert feedback.json()["event"]["result_version"] == 3
    completed = command(client, object_id, "complete", message="合成办结说明")
    assert completed.status_code == 200
    assert completed.json()["event"]["result_version"] == 4
    replay = command(client, object_id, "accept", key=accept_key, etag=first.headers["etag"])
    assert replay.status_code == 200
    assert replay.json() == {**accepted.json(), "replayed": True}
    assert_error(
        command(client, object_id, "complete", message="Again"),
        409,
        "work_object_transition_invalid",
    )
    actor("sender")
    view = client.get(base)
    assert view.json()["status"] == "completed"
    assert view.json()["available_commands"] == []
    assert view.json()["completed_at"].endswith("Z")
    events = client.get(base + "/events").json()
    assert [item["operation"] for item in events["items"]] == ["accept", "feedback", "complete"]
    assert [item["text"] for item in events["items"]] == [
        None,
        "合成进展 <b>text</b>",
        "合成办结说明",
    ]
    assert all(
        set(item)
        == {
            "event_id",
            "work_object_id",
            "operation",
            "from_status",
            "to_status",
            "result_version",
            "occurred_at",
            "text",
            "actor_role",
        }
        for item in events["items"]
    )
    detail = client.get(f"/api/v1/work-objects/{object_id}")
    assert detail.json()["status"] == "completed"
    assert "accepted_by_ai_user_id" not in detail.json()
    assert "completed_by_ai_user_id" not in detail.json()
    replay_publication = client.post(
        "/api/v1/work-objects/dispatch",
        json={
            "kind": "工作任务",
            "title": "Synthetic lifecycle",
            "requirement": "",
            "receipt_requirement": "Synthetic evidence",
            "due_at": None,
            "reminder_choices": [],
            "targets": [
                {
                    "kind": "user",
                    "directory_user_id": "local-recipient",
                    "department_id": "office-a",
                }
            ],
        },
        headers={**TEST_CSRF_HEADERS, "Idempotency-Key": publication_key},
    )
    assert replay_publication.status_code == 200
    assert replay_publication.json() == {**initial, "replayed": True}
    with db.sql.connect() as connection:
        row = connection.execute(
            text(
                "SELECT status, version, accepted_by_ai_user_id, "
                "completed_by_ai_user_id FROM work_objects WHERE work_object_id=:id"
            ),
            {"id": object_id},
        ).one()
    assert tuple(row) == ("completed", 4, "ai-local-recipient", "ai-local-recipient")
    active = client.get("/api/v1/work-objects?completion=active")
    done = client.get("/api/v1/work-objects?completion=completed")
    assert active.status_code == done.status_code == 200
    assert object_id not in [item["work_object_id"] for item in active.json()["items"]]
    assert object_id in [item["work_object_id"] for item in done.json()["items"]]


def test_events_http_cursor_and_fresh_scope_each_page(lifecycle_http):
    db, client, actor, _clock = lifecycle_http
    object_id = publish(client)["items"][0]["work_object_id"]
    actor("local-recipient")
    assert command(client, object_id, "accept").status_code == 200
    assert command(client, object_id, "feedback", message="First").status_code == 200
    endpoint = f"/api/v1/work-objects/{object_id}/lifecycle/events"
    first = client.get(endpoint, params={"limit": 1})
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert [item["result_version"] for item in first.json()["items"]] == [2]
    assert first.json()["has_more"] is True
    assert first.json()["next_after_version"] == 2
    assert command(client, object_id, "feedback", message="Appended").status_code == 200
    second = client.get(endpoint, params={"after_version": 2, "limit": 2})
    assert second.status_code == 200
    assert [item["result_version"] for item in second.json()["items"]] == [3, 4]
    assert second.json()["has_more"] is False
    assert second.json()["next_after_version"] is None
    db.execute(
        "UPDATE organization_user_memberships SET department_id='office-b' "
        "WHERE user_id='local-recipient'"
    )
    assert_error(client.get(endpoint, params={"after_version": 2}), 404, "work_object_not_found")


def test_user_target_only_named_recipient_may_accept(lifecycle_http):
    db, client, actor, _clock = lifecycle_http
    object_id = publish(client)["items"][0]["work_object_id"]
    for user in ("sender", "coworker"):
        actor(user)
        assert_error(command(client, object_id, "accept"), 403, "work_object_action_forbidden")
    actor("local-recipient")
    assert command(client, object_id, "accept").status_code == 200
    actor("coworker")
    assert_error(
        command(client, object_id, "feedback", message="No"), 403, "work_object_action_forbidden"
    )
    with db.sql.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM work_object_lifecycle_events")
            ).scalar_one()
            == 1
        )


def test_department_claim_has_one_current_handler(lifecycle_http):
    _db, client, actor, _clock = lifecycle_http
    object_id = publish(client, department=True)["items"][0]["work_object_id"]
    actor("member-b")
    assert command(client, object_id, "accept").status_code == 200
    actor("member-c")
    assert_error(command(client, object_id, "accept"), 409, "work_object_transition_invalid")
    for operation in ("feedback", "complete"):
        assert_error(
            command(client, object_id, operation, message="No"), 403, "work_object_action_forbidden"
        )
    actor("member-b")
    assert command(client, object_id, "complete", message="Done").status_code == 200


def test_all_new_routes_require_auth_and_command_csrf(lifecycle_http):
    _db, client, actor, clock = lifecycle_http
    object_id = publish(client)["items"][0]["work_object_id"]
    base = f"/api/v1/work-objects/{object_id}/lifecycle"
    for mode in ("missing", "invalid", "expired"):
        actor("local-recipient")
        if mode == "expired":
            clock[0] += 3601
        else:
            client.cookies.clear()
            if mode == "invalid":
                client.cookies.set("eternalai_session", "synthetic-invalid")
        for method, suffix in (("get", ""), ("get", "/events"), ("post", "/commands")):
            response = client.request(method, base + suffix)
            assert_error(response, 401, "authentication_required")
            assert response.headers["www-authenticate"] == "Session"
    actor("local-recipient")
    for headers in ({}, {"Origin": "https://wrong.invalid", "X-EternalAI-CSRF": "1"}):
        response = client.post(base + "/commands", json={"operation": "accept"}, headers=headers)
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"
    for name in ("Origin", "X-EternalAI-CSRF"):
        headers = list(TEST_CSRF_HEADERS.items()) + [(name, TEST_CSRF_HEADERS[name])]
        response = client.post(base + "/commands", json={"operation": "accept"}, headers=headers)
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"
    assert command(client, object_id, "accept").status_code == 200


def test_headers_body_errors_are_closed_and_do_not_echo_input(lifecycle_http, caplog):
    db, client, actor, _ = lifecycle_http
    object_id = publish(client)["items"][0]["work_object_id"]
    actor("local-recipient")
    base = f"/api/v1/work-objects/{object_id}/lifecycle"
    tag = client.get(base).headers["etag"]
    headers = {**TEST_CSRF_HEADERS, "Idempotency-Key": str(uuid4()), "If-Match": tag}
    for body in (
        {"operation": "accept", "text": "extra"},
        {"operation": "feedback", "text": " "},
        {"operation": "complete", "text": "x" * 2001},
        {"operation": "accept", "actor": "forged"},
        {"operation": "feedback", "text": "access_token=synthetic-not-real-value"},
    ):
        response = client.post(base + "/commands", json=body, headers=headers)
        assert_error(response, 422, "work_object_lifecycle_request_invalid")
        assert "synthetic-not-real-value" not in response.text + caplog.text
    for name, value in (
        ("If-Match", "*"),
        ("If-Match", "W/" + tag),
        ("If-Match", tag + "," + tag),
        ("Idempotency-Key", "invalid"),
    ):
        assert_error(
            client.post(
                base + "/commands", json={"operation": "accept"}, headers={**headers, name: value}
            ),
            422,
            "work_object_lifecycle_request_invalid",
        )
    for name in ("If-Match", "Idempotency-Key"):
        duplicate = list(headers.items()) + [(name, headers[name])]
        assert_error(
            client.post(base + "/commands", json={"operation": "accept"}, headers=duplicate),
            422,
            "work_object_lifecycle_request_invalid",
        )
    assert_error(
        client.post(
            base + "/commands",
            json={"operation": "accept"},
            headers={k: v for k, v in headers.items() if k != "If-Match"},
        ),
        428,
        "work_object_precondition_required",
    )
    for query in (
        "limit=0",
        "limit=101",
        "after_version=-1",
        "after_version=1.0",
        "limit=1&limit=2",
        "extra=1",
    ):
        assert_error(
            client.get(base + "/events?" + query), 422, "work_object_lifecycle_request_invalid"
        )
    assert command(client, object_id, "accept").status_code == 200
    assert command(client, object_id, "feedback", message="x" * 2000).status_code == 200
    with db.sql.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM work_object_lifecycle_events")
            ).scalar_one()
            == 2
        )


def test_invisible_missing_cross_tenant_and_external_boundaries(lifecycle_http):
    from tests.api.test_work_object_dispatch import insert_synthetic_row
    from tests.db.test_internal_work_object_dispatch_migration import _legacy_row

    db, client, actor, _ = lifecycle_http
    object_id = publish(client)["items"][0]["work_object_id"]
    for user, dept, tenant in (
        ("outsider", "office-b", "default"),
        ("local-recipient", "office-a", "other"),
    ):
        actor(user, dept, tenant, roles=("admin",))
        for suffix in ("", "/events"):
            invisible = client.get(f"/api/v1/work-objects/{object_id}/lifecycle{suffix}")
            missing = client.get(f"/api/v1/work-objects/nonexistent/lifecycle{suffix}")
            assert_error(invisible, 404, "work_object_not_found")
            assert invisible.json() == missing.json()
    actor("local-recipient")
    for external in (True, False):
        row = _legacy_row(external=external)
        row["assignee_ai_user_id"] = "ai-local-recipient"
        insert_synthetic_row(db, row)
        for suffix in ("", "/events"):
            assert_error(
                client.get(f"/api/v1/work-objects/{row['work_object_id']}/lifecycle{suffix}"),
                409,
                "work_object_lifecycle_unsupported",
            )
        assert_error(
            command(client, row["work_object_id"], "accept", etag='"wolc-' + "0" * 64 + '"'),
            409,
            "work_object_lifecycle_unsupported",
        )


def test_same_key_replay_reauthorizes_current_actor(lifecycle_http, monkeypatch):
    from tests.api.test_work_object_dispatch import expire_directory

    db, client, actor, _ = lifecycle_http
    object_id = publish(client)["items"][0]["work_object_id"]
    actor("local-recipient")
    tag = client.get(f"/api/v1/work-objects/{object_id}/lifecycle").headers["etag"]
    key = str(uuid4())
    assert command(client, object_id, "accept", key=key, etag=tag).status_code == 200
    assert_error(
        command(client, object_id, "feedback", message="changed", key=key, etag=tag),
        409,
        "idempotency_key_reused",
    )
    assert_error(
        command(client, object_id, "accept", key=key, etag='"wolc-' + "0" * 64 + '"'),
        409,
        "idempotency_key_reused",
    )
    db.execute(
        (
            "UPDATE organization_user_memberships SET department_id='office-b"
            "' WHERE user_id='local-recipient'"
        )
    )
    assert_error(
        command(client, object_id, "accept", key=key, etag=tag), 404, "work_object_not_found"
    )
    db.execute(
        (
            "UPDATE organization_user_memberships SET department_id='office-a"
            "' WHERE user_id='local-recipient'"
        )
    )
    assert command(client, object_id, "accept", key=key, etag=tag).json()["replayed"] is True
    # Advance the request-local clock while the real receipt query runs.
    monotonic = [100.0]
    monkeypatch.setattr(db.service, "_monotonic", lambda: monotonic[0])
    original = db.store.get_lifecycle_event_for_scope

    async def delayed(*args, **kwargs):
        result = await original(*args, **kwargs)
        monotonic[0] += 172801
        return result

    monkeypatch.setattr(db.store, "get_lifecycle_event_for_scope", delayed)
    assert_error(
        command(client, object_id, "accept", key=key, etag=tag), 503, "organization_directory_stale"
    )
    expire_directory(db)
    assert_error(
        command(client, object_id, "accept", key=key, etag=tag), 503, "organization_directory_stale"
    )


def test_lost_response_and_audit_failure_preserve_committed_result(lifecycle_http, monkeypatch):
    db, client, actor, _ = lifecycle_http
    object_id = publish(client)["items"][0]["work_object_id"]
    actor("local-recipient")
    tag = client.get(f"/api/v1/work-objects/{object_id}/lifecycle").headers["etag"]
    original = db.store.apply_lifecycle_command_for_scope
    committed = []

    async def lose_reply(*args, **kwargs):
        result = await original(*args, **kwargs)
        committed.append(result.event)
        raise RuntimeError("Synthetic lost commit reply")

    monkeypatch.setattr(db.store, "apply_lifecycle_command_for_scope", lose_reply)
    key = str(uuid4())
    assert_error(
        command(client, object_id, "accept", key=key, etag=tag), 503, "work_object_lifecycle_failed"
    )

    async def audit_failure(*args, **kwargs):
        raise RuntimeError("Synthetic audit failure")

    monkeypatch.setattr(db.trace, "record_event", audit_failure)
    replay = command(client, object_id, "accept", key=key, etag=tag)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["event"]["event_id"] == str(committed[0].event_id)
    assert_error(
        command(client, object_id, "feedback", message="Not committed"),
        503,
        "work_object_audit_unavailable",
    )
    with db.sql.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version FROM work_objects WHERE work_object_id=:id"), {"id": object_id}
            ).scalar_one()
            == 2
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM work_object_lifecycle_events")
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("missing", "organization_directory_missing"),
        ("stale", "organization_directory_stale"),
        ("unavailable", "organization_directory_unavailable"),
        ("zero", "directory_membership_missing"),
        ("multiple", "directory_membership_ambiguous"),
        ("no_join", "directory_membership_missing"),
    ],
)
def test_freshness_membership_and_no_token_department_fallback(
    lifecycle_http, monkeypatch, mode, expected
):
    from tests.api.test_work_object_dispatch import expire_directory

    db, client, actor, _ = lifecycle_http
    # Self visibility remains while directory writes fail closed.
    object_id = publish(client)["items"][0]["work_object_id"]
    db.execute(
        "UPDATE work_objects SET assignee_directory_user_id='sender' WHERE work_object_id=:id",
        id=object_id,
    )
    base = f"/api/v1/work-objects/{object_id}/lifecycle"
    tag = client.get(base).headers["etag"]
    if mode == "missing":
        db.execute(
            (
                "UPDATE organization_directory_sync_state SET snapshot_version=0,"
                "source_fetched_at=NULL,last_success_at=NULL,last_attempt_status="
                "'never',last_attempt_started_at=NULL,last_attempt_finished_at=NU"
                "LL,last_error_code=NULL"
            )
        )
    elif mode == "stale":
        expire_directory(db)
    elif mode == "zero":
        db.execute("DELETE FROM organization_user_memberships WHERE user_id='sender'")
    elif mode == "multiple":
        db.membership("sender", "office-b")
    elif mode == "no_join":
        actor("sender", join=False)
    else:

        async def changed():
            raise RuntimeError("Synthetic directory failure")

        monkeypatch.setattr(db.directory, "read_view", changed)
    status = 403 if expected.startswith("directory_membership") else 503
    assert_error(command(client, object_id, "accept", etag=tag), status, expected)
    response = client.get(base)
    if mode == "unavailable":
        assert_error(response, 503, expected)
    else:
        assert response.status_code == 200
        assert response.json()["available_commands"] == []
        assert response.json()["unavailable_reason"] == expected
        assert client.get(base + "/events").json()["items"] == []
    with db.sql.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version FROM work_objects WHERE work_object_id=:id"), {"id": object_id}
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM work_object_lifecycle_events")
            ).scalar_one()
            == 0
        )


def test_etag_includes_state_and_permission_projection(lifecycle_http):
    import re

    db, client, actor, _ = lifecycle_http
    object_id = publish(client)["items"][0]["work_object_id"]
    base = f"/api/v1/work-objects/{object_id}/lifecycle"
    sender_tag = client.get(base).headers["etag"]
    actor("local-recipient")
    initial = client.get(base)
    assert initial.headers["etag"] != sender_tag
    assert command(client, object_id, "accept", etag=initial.headers["etag"]).status_code == 200
    db.execute(
        (
            "UPDATE work_objects SET accepted_at=date_trunc('second',accepted"
            "_at) + interval '1 microsecond', updated_at=GREATEST(updated_at,"
            "date_trunc('second',accepted_at) + interval '1 microsecond') WHE"
            "RE work_object_id=:id"
        ),
        id=object_id,
    )
    view = client.get(base)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.000001Z", view.json()["accepted_at"])
    independent = hashlib.sha256(
        json.dumps(view.json(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert view.headers["etag"] == '"wolc-' + independent + '"'
    assert (
        command(
            client, object_id, "feedback", message="Progress", etag=view.headers["etag"]
        ).status_code
        == 200
    )
    assert_error(
        command(client, object_id, "complete", message="Done", etag=view.headers["etag"]),
        412,
        "work_object_version_conflict",
    )
    assert command(client, object_id, "complete", message="Done").status_code == 200
