"""API contract tests for Admin binding revoke and reset endpoints."""

from __future__ import annotations

from typing import Any, Literal, cast

import pytest
from fastapi.testclient import TestClient

from app.admin.actions import (
    ADMIN_AUDIT_READ_POLICY_CAPABILITY_IDS,
    ADMIN_LITE_POLICY_CAPABILITY_IDS,
    AUDIT_READER_ROLE,
)
from app.admin.registry import AdminRegistryService
from app.composition import build_admin_registry_service
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.main import create_app
from app.ports.capability_registry import CapabilityRegistryPort
from app.ports.identity_mapping import (
    IdentityCheckResult,
    IdentityMappingMutationError,
    IdentityMappingMutationResult,
    IdentityMappingPort,
)
from app.ports.task_store import TaskStorePort
from app.ports.trace import TraceEvent, TracePort, TraceQueryPort
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db
from tests.auth_fakes import (
    TEST_CSRF_ALLOWED_ORIGINS,
    TEST_CSRF_HEADERS,
    MemorySessionRevocations,
    StaticSessionTokens,
    auth_cookies,
    make_session_binder,
)

TARGET_AI_USER_ID = "usr_v1_" + ("c" * 43)
BINDING_ID = f"oa-session-v1:{TARGET_AI_USER_ID}"
ADMIN_COOKIES = auth_cookies()


class APIMutationPort:
    def __init__(
        self,
        outcome: IdentityMappingMutationResult
        | IdentityMappingMutationError
        | None,
    ) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, str]] = []

    async def revoke_mapping(
        self, binding_id: str, *, tenant_id: str
    ) -> IdentityMappingMutationResult | None:
        self.calls.append(("revoke", binding_id))
        return self._result()

    async def reset_mapping(
        self, binding_id: str, *, tenant_id: str
    ) -> IdentityMappingMutationResult | None:
        self.calls.append(("reset", binding_id))
        return self._result()

    def _result(self) -> IdentityMappingMutationResult | None:
        if isinstance(self.outcome, IdentityMappingMutationError):
            raise self.outcome
        return self.outcome


class RecordingTrace:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    async def record_event(self, event: TraceEvent) -> None:
        self.events.append(event)


def _outcome(*, changed: bool = True) -> IdentityMappingMutationResult:
    return IdentityMappingMutationResult(
        mapping=IdentityCheckResult(
            binding_id=BINDING_ID,
            target_system="oa",
            execution_identity="user_delegated",
            bind_status="revoked",
            reason_code="identity_revoked",
        ),
        previous_bind_status="active" if changed else "revoked",
        changed=changed,
    )


def _noop_outcome() -> IdentityMappingMutationResult:
    return IdentityMappingMutationResult(
        mapping=IdentityCheckResult(
            binding_id=BINDING_ID,
            target_system="oa",
            execution_identity="user_delegated",
            bind_status="active",
        ),
        previous_bind_status="active",
        changed=False,
    )


def _client(
    outcome: IdentityMappingMutationResult | IdentityMappingMutationError | None,
    *,
    roles: tuple[str, ...] = ("admin",),
) -> tuple[TestClient, APIMutationPort, RecordingTrace]:
    port = APIMutationPort(outcome)
    trace = RecordingTrace()
    service = build_admin_registry_service(
        capability_registry=cast(CapabilityRegistryPort, object()),
        task_store=cast(TaskStorePort, object()),
        identity_mapping=cast(IdentityMappingPort, port),
        trace_port=cast(TracePort, trace),
        trace_query=cast(TraceQueryPort, object()),
    )
    client = TestClient(
        create_app(
            admin_registry_service=service,
            session_revocations=MemorySessionRevocations(),
            session_tokens=StaticSessionTokens(roles=roles),
            session_binder=make_session_binder(),
            session_cookie_ttl_seconds=3600,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        ),
        base_url="https://testserver",
    )
    return client, port, trace


@pytest.mark.parametrize(
    ("operation", "changed", "next_action"),
    [("revoke", True, "none"), ("reset", False, "reauthenticate")],
)
def test_mutation_endpoints_return_the_fixed_response_contract(
    operation: Literal["revoke", "reset"],
    changed: bool,
    next_action: Literal["none", "reauthenticate"],
) -> None:
    client, port, trace = _client(_outcome(changed=changed))

    response = client.post(
        f"/api/v1/admin/bindings/{BINDING_ID}/{operation}",
        headers=TEST_CSRF_HEADERS,
        cookies=ADMIN_COOKIES,
    )

    assert response.status_code == 200
    assert response.json() == {
        "action": operation,
        "binding": {
            "binding_id": BINDING_ID,
            "target_system": "oa",
            "execution_identity": "user_delegated",
            "bind_status": "revoked",
            "binding_scope": None,
            "account_set_id": None,
            "device_domain_id": None,
            "reason_code": "identity_revoked",
        },
        "changed": changed,
        "next_action": next_action,
    }
    assert port.calls == [(operation, BINDING_ID)]
    assert trace.events[0].attributes["action"] == f"bindings_{operation}"


def test_revoke_noop_result_cannot_report_http_success() -> None:
    client, port, trace = _client(_noop_outcome())

    response = client.post(
        f"/api/v1/admin/bindings/{BINDING_ID}/revoke",
        headers=TEST_CSRF_HEADERS,
        cookies=ADMIN_COOKIES,
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "binding_mutation_unavailable",
            "message": "Binding mutation provider is unavailable.",
        }
    }
    assert port.calls == [("revoke", BINDING_ID)]
    assert trace.events[0].status == "failed"
    assert trace.events[0].attributes["reason_code"] == "binding_mutation_unavailable"


@pytest.mark.parametrize("roles", [("employee",), (AUDIT_READER_ROLE,)])
@pytest.mark.parametrize("operation", ["revoke", "reset"])
def test_non_admin_cross_user_mutation_is_403_and_never_calls_the_port(
    operation: Literal["revoke", "reset"],
    roles: tuple[str, ...],
) -> None:
    client, port, trace = _client(_outcome(), roles=roles)

    response = client.post(
        f"/api/v1/admin/bindings/{BINDING_ID}/{operation}",
        headers=TEST_CSRF_HEADERS,
        cookies=ADMIN_COOKIES,
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "code": "role_not_allowed",
            "message": "Management role is required.",
        }
    }
    assert port.calls == []
    assert trace.events[0].status == "blocked"


def test_missing_binding_returns_404_without_a_success_body() -> None:
    client, port, trace = _client(None)

    response = client.post(
        f"/api/v1/admin/bindings/{BINDING_ID}/revoke",
        headers=TEST_CSRF_HEADERS,
        cookies=ADMIN_COOKIES,
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "binding_not_found",
            "message": "Binding was not found.",
        }
    }
    assert port.calls == [("revoke", BINDING_ID)]
    assert trace.events[0].attributes["reason_code"] == "binding_not_found"


def test_storage_failure_returns_safe_503() -> None:
    client, port, trace = _client(IdentityMappingMutationError("safe failure"))

    response = client.post(
        f"/api/v1/admin/bindings/{BINDING_ID}/reset",
        headers=TEST_CSRF_HEADERS,
        cookies=ADMIN_COOKIES,
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "binding_mutation_unavailable",
            "message": "Binding mutation provider is unavailable.",
        }
    }
    assert port.calls == [("reset", BINDING_ID)]
    assert trace.events[0].attributes["reason_code"] == "binding_mutation_unavailable"


def test_missing_mutation_composition_returns_distinct_503() -> None:
    client = TestClient(
        create_app(
            admin_registry_service=None,
            session_revocations=MemorySessionRevocations(),
            session_tokens=StaticSessionTokens(),
            session_binder=make_session_binder(),
            session_cookie_ttl_seconds=3600,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        ),
        base_url="https://testserver",
    )

    response = client.post(
        f"/api/v1/admin/bindings/{BINDING_ID}/revoke",
        headers=TEST_CSRF_HEADERS,
        cookies=ADMIN_COOKIES,
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "admin_binding_mutation_unavailable",
            "message": "Admin binding mutation provider is not configured.",
        }
    }


def test_plain_registry_service_type_mismatch_returns_distinct_503() -> None:
    port = APIMutationPort(_outcome())
    trace = RecordingTrace()
    service = AdminRegistryService(
        capability_registry=cast(CapabilityRegistryPort, object()),
        task_store=cast(TaskStorePort, object()),
        identity_mapping=cast(IdentityMappingPort, port),
        policy_guard=MinimalPolicyGuard(
            admin_capability_ids=ADMIN_LITE_POLICY_CAPABILITY_IDS,
            audit_read_capability_ids=ADMIN_AUDIT_READ_POLICY_CAPABILITY_IDS,
        ),
        trace_port=cast(TracePort, trace),
        trace_query=cast(TraceQueryPort, object()),
    )
    client = TestClient(
        create_app(
            admin_registry_service=service,
            session_revocations=MemorySessionRevocations(),
            session_tokens=StaticSessionTokens(),
            session_binder=make_session_binder(),
            session_cookie_ttl_seconds=3600,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        ),
        base_url="https://testserver",
    )

    response = client.post(
        f"/api/v1/admin/bindings/{BINDING_ID}/reset",
        headers=TEST_CSRF_HEADERS,
        cookies=ADMIN_COOKIES,
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == (
        "admin_binding_mutation_unavailable"
    )
    assert port.calls == []
    assert trace.events == []


def test_authentication_runs_before_mutation_composition_or_port_access() -> None:
    client, port, trace = _client(_outcome())

    response = client.post(f"/api/v1/admin/bindings/{BINDING_ID}/revoke")

    assert response.status_code == 401
    assert port.calls == []
    assert trace.events == []


def test_mutation_routes_do_not_accept_a_client_supplied_actor_payload() -> None:
    client, port, trace = _client(_outcome(), roles=("employee",))
    untrusted_payload: dict[str, Any] = {
        "ai_user_id": TARGET_AI_USER_ID,
        "roles": ["admin"],
    }

    response = client.post(
        f"/api/v1/admin/bindings/{BINDING_ID}/revoke",
        headers=TEST_CSRF_HEADERS,
        json=untrusted_payload,
        cookies=ADMIN_COOKIES,
    )

    assert response.status_code == 403
    assert port.calls == []
    assert trace.events[0].attributes["role_claim_source"] == "authenticated_principal"


def _exercise_tenant_mutation(db, operation, local_exists):
    from datetime import UTC, datetime, timedelta

    from pydantic import SecretStr
    from sqlalchemy import event, text

    from app.event_loop import make_event_loop
    from app.infra.auth.crypto import HMACSessionToken
    from app.infra.auth.postgresql import PostgreSQLCredentialStore
    from app.infra.identity.postgresql import PostgreSQLOAIdentityMapping
    from app.ports.auth import OASessionCredential, Principal, PrincipalOrgContext
    from tests.api.test_work_object_dispatch import run

    store = PostgreSQLCredentialStore(session_factory=db.factory, encryption_key=bytes(range(32)))
    tenants = ("synthetic-TA", "synthetic-TB") if local_exists else ("synthetic-TB",)
    for tenant in tenants:
        run(
            store.store(
                TARGET_AI_USER_ID,
                "oa",
                OASessionCredential(
                    oa_user_id=SecretStr("synthetic"),
                    cookies={},
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
                tenant_id=tenant,
            )
        )
    service = build_admin_registry_service(
        capability_registry=cast(CapabilityRegistryPort, object()),
        task_store=cast(TaskStorePort, object()),
        identity_mapping=PostgreSQLOAIdentityMapping(session_factory=db.factory),
        trace_port=cast(TracePort, RecordingTrace()),
        trace_query=cast(TraceQueryPort, object()),
    )
    tokens = HMACSessionToken(
        signing_key=bytes(range(32)), ttl_seconds=3600, tenant_id="synthetic-TA"
    )
    principal = Principal(
        ai_user_id="synthetic-admin",
        display_name="Synthetic",
        roles=("admin",),
        org_ctx=PrincipalOrgContext(tenant_id="synthetic-TA"),
    )
    updates = []

    def observed(_connection, cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("UPDATE OA_SESSION_CREDENTIALS"):
            updates.append(cursor.rowcount)

    event.listen(db.engine.sync_engine, "after_cursor_execute", observed)
    try:
        with TestClient(
            create_app(
                admin_registry_service=service,
                session_tokens=tokens,
                session_revocations=MemorySessionRevocations(),
                csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
            ),
            base_url="https://testserver",
            backend_options={"loop_factory": make_event_loop},
        ) as client:
            client.cookies.set("eternalai_session", tokens.issue(principal))
            response = client.post(
                f"/api/v1/admin/bindings/{BINDING_ID}/{operation}", headers=TEST_CSRF_HEADERS
            )
        assert response.status_code == (200 if local_exists else 404)
        if local_exists:
            assert response.json()["changed"] is True
            assert response.json()["binding"]["bind_status"] == "revoked"
        else:
            assert response.json()["detail"]["code"] == "binding_not_found"
        assert sum(updates) == int(local_exists)
        with db.sql.connect() as connection:
            rows = dict(
                connection.execute(
                    text("SELECT tenant_id, revoked_at FROM oa_session_credentials")
                ).all()
            )
        assert rows["synthetic-TB"] is None
        assert set(rows) == set(tenants)
        if local_exists:
            assert rows["synthetic-TA"] is not None
    finally:
        event.remove(db.engine.sync_engine, "after_cursor_execute", observed)
    return response.status_code


@pytest.mark.parametrize("operation", ["revoke", "reset"])
def test_only_foreign_binding_not_found(dispatch_db, operation):
    assert _exercise_tenant_mutation(dispatch_db, operation, False) == 404


@pytest.mark.parametrize("operation", ["revoke", "reset"])
def test_equal_reference_mutates_local_binding_only(dispatch_db, operation):
    assert _exercise_tenant_mutation(dispatch_db, operation, True) == 200


@pytest.mark.parametrize(
    "tenant,roles,expected",
    [
        ("synthetic-TA", ("audit_reader",), "active"),
        ("synthetic-TB", ("audit_reader",), None),
        ("synthetic-TA", ("admin",), "denied"),
    ],
)
def test_binding_audit_reads_local_tenant_only(dispatch_db, tenant, roles, expected):
    from datetime import UTC, datetime, timedelta

    from pydantic import SecretStr

    from app.event_loop import make_event_loop
    from app.infra.auth.crypto import HMACSessionToken
    from app.infra.auth.postgresql import PostgreSQLCredentialStore
    from app.infra.identity.postgresql import PostgreSQLOAIdentityMapping
    from app.infra.persistence.task_store.postgresql import PostgreSQLTaskStore
    from app.ports.auth import OASessionCredential, Principal, PrincipalOrgContext
    from app.ports.task_store import TaskRecord
    from tests.api.test_work_object_dispatch import run

    tasks = PostgreSQLTaskStore(dispatch_db.factory)
    store = PostgreSQLCredentialStore(
        session_factory=dispatch_db.factory, encryption_key=bytes(range(32))
    )
    # Both tenants have a Task for the target; the credential predicate must decide visibility.
    for scope in ("synthetic-TA", "synthetic-TB"):
        run(
            tasks.create_task(
                TaskRecord(
                    task_id=scope,
                    session_id="synthetic-shared",
                    ai_user_id=TARGET_AI_USER_ID,
                    tenant_id=scope,
                    status="completed",
                )
            )
        )
    run(
        store.store(
            TARGET_AI_USER_ID,
            "oa",
            OASessionCredential(
                oa_user_id=SecretStr("synthetic"),
                cookies={},
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
            tenant_id="synthetic-TA",
        )
    )
    service = build_admin_registry_service(
        capability_registry=cast(CapabilityRegistryPort, object()),
        task_store=tasks,
        identity_mapping=PostgreSQLOAIdentityMapping(session_factory=dispatch_db.factory),
        trace_port=cast(TracePort, RecordingTrace()),
        trace_query=cast(TraceQueryPort, object()),
    )
    tokens = HMACSessionToken(signing_key=bytes(range(32)), ttl_seconds=3600, tenant_id=tenant)
    actor = Principal(
        ai_user_id="synthetic-other-reader",
        display_name="Synthetic",
        roles=roles,
        org_ctx=PrincipalOrgContext(tenant_id=tenant),
    )
    with TestClient(
        create_app(
            admin_registry_service=service,
            session_tokens=tokens,
            session_revocations=MemorySessionRevocations(),
        ),
        base_url="https://testserver",
        backend_options={"loop_factory": make_event_loop},
    ) as client:
        client.cookies.set("eternalai_session", tokens.issue(actor))
        response = client.get("/api/v1/admin/bindings", params={"ai_user_id": TARGET_AI_USER_ID})
    if expected == "denied":
        assert response.status_code == 403
    else:
        assert response.status_code == 200
        assert [item["bind_status"] for item in response.json()["items"]] == (
            [] if expected is None else [expected]
        )
